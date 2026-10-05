from flask import Flask, render_template, jsonify, request
import pandas as pd
import sqlite3
import json
import openai
from pdfminer.high_level import extract_text
from flask_cors import CORS
from datetime import datetime
from db_schema import ensure_schema
import job_intake
from main import load_config

config = load_config('config.json')
app = Flask(__name__)
CORS(app)
app.config['TEMPLATES_AUTO_RELOAD'] = True
# Room for screenshots and for the full page text the bookmarklet sends (Flask's form default is 500 KB)
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024
app.config['MAX_FORM_MEMORY_SIZE'] = 10 * 1024 * 1024

# Optional: point the openai client at an OpenAI-compatible provider
# (e.g. OpenRouter's https://openrouter.ai/api/v1) instead of api.openai.com.
if config.get("OpenAI_Base_URL"):
    openai.api_base = config["OpenAI_Base_URL"]

def read_pdf(file_path):
    try:
        text = extract_text(file_path)
        return text
    except FileNotFoundError:
        print(f"Error: The file '{file_path}' was not found.")
        return None
    except Exception as e:
        print(f"An error occurred while reading the PDF: {e}")
        return None

# db = load_config('config.json')['db_path']
# try:
#     api_key = load_config('config.json')['OpenAI_API_KEY']
#     print("API key found")
# except:
#     print("No OpenAI API key found. Please add one to config.json")

# try:
#     gpt_model = load_config('config.json')['OpenAI_Model']
#     print("Model found")
# except:
#     print("No OpenAI Model found or it's incorrectly specified in the config. Please add one to config.json")

@app.route('/')
def home():
    jobs = read_jobs_from_db()
    return render_template('jobs.html', jobs=jobs)

@app.route('/applications')
def applications():
    # Every job with any application status set, including hidden ones - hiding a job
    # you applied to shouldn't drop it from the tally.
    conn = sqlite3.connect(config["db_path"])
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, title, company, location, date, job_url, source, applied_date, interview, rejected "
        "FROM jobs WHERE applied = 1 OR interview = 1 OR rejected = 1"
    ).fetchall()
    total_scraped = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    hidden = conn.execute("SELECT COUNT(*) FROM jobs WHERE hidden = 1").fetchone()[0]
    conn.close()

    apps = []
    for row in rows:
        app_row = dict(row)
        # A rejection after an interview is still a rejection, so it wins
        app_row['outcome'] = 'Rejected' if row['rejected'] == 1 else 'Interview' if row['interview'] == 1 else 'Waiting'
        apps.append(app_row)
    # Most recently applied first; applications from before applied_date existed go last, newest id first
    apps.sort(key=lambda a: (a['applied_date'] or '', a['id']), reverse=True)

    counts = {
        'applied': len(apps),
        'waiting': sum(a['outcome'] == 'Waiting' for a in apps),
        'interview': sum(a['outcome'] == 'Interview' for a in apps),
        'rejected': sum(a['outcome'] == 'Rejected' for a in apps),
        'total_scraped': total_scraped,
        'hidden': hidden,
    }
    sources = sorted({a['source'] or 'Other' for a in apps})
    return render_template('applications.html', apps=apps, counts=counts, sources=sources)

@app.route('/add_job')
def add_job_page():
    return render_template('add_job.html')

@app.route('/add_job', methods=['POST'])
def add_job():
    # A screenshot or pasted text wins over the link; the link is then just stored as the job's URL.
    url = request.form.get('url', '').strip()
    text = request.form.get('text', '').strip()
    screenshot = request.files.get('screenshot')
    has_shot = bool(screenshot and screenshot.filename)
    print(f"[add_job] screenshot={'yes' if has_shot else 'no'} text={len(text)} chars "
          f"url={url or '-'} applied={request.form.get('applied') == 'on'}")
    try:
        if has_shot:
            print(f"[add_job] reading screenshot with {config.get('Vision_Model') or config.get('OpenAI_Model')}")
            job = job_intake.job_from_screenshot(config, screenshot.read(), screenshot.mimetype or 'image/png', url)
        elif text:
            print(f"[add_job] reading pasted text with {config.get('OpenAI_Model')}")
            job = job_intake.job_from_text(config, text, url)
        elif url:
            print("[add_job] fetching link")
            try:
                job = job_intake.job_from_link(config, url)
            except Exception as e:
                print(f"[add_job] link failed: {e} - asked for a screenshot or text")
                return jsonify({"success": False, "needs_more": True, "error":
                                f"Couldn't read that page ({e}). Many boards block automated access - "
                                "keep the link in the box and add a screenshot or paste the job text."}), 422
        else:
            return jsonify({"success": False, "error": "Give a link, a screenshot, or the job text."}), 400
    except Exception as e:
        print(f"[add_job] FAILED: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    return jsonify(store_job(job, request.form.get('applied') == 'on')), 200

@app.route('/capture', methods=['POST'])
def capture():
    # Target of the "Save to tracker" bookmarklet: the page the user is looking at, as their browser loaded it
    url = request.form.get('url', '')
    try:
        ld_scripts = json.loads(request.form.get('ld') or '[]')
        print(f"[bookmark] {url} - {len(ld_scripts)} JSON-LD blocks, {len(request.form.get('text', ''))} chars of page text")
        job = job_intake.job_from_capture(config, url, ld_scripts, request.form.get('text', ''))
        result = store_job(job, False)
    except Exception as e:
        print(f"[bookmark] FAILED: {e}")
        result = {"success": False, "error": str(e), "url": url}
    return render_template('capture_result.html', result=result)

@app.route('/set_applied/<int:job_id>', methods=['POST'])
def set_applied(job_id):
    conn = sqlite3.connect(config["db_path"])
    job_intake.mark_applied(conn, job_id)
    conn.close()
    print(f"[set_applied] #{job_id} marked as applied")
    return jsonify({"success": True}), 200

def store_job(job, applied):
    conn = sqlite3.connect(config["db_path"])
    job_id, is_new = job_intake.save_job(conn, job)
    if applied:
        job_intake.mark_applied(conn, job_id)
    conn.close()
    description = job['job_description'].strip()
    print(f"[save_job] {'ADDED' if is_new else 'EXISTS'} #{job_id} [{job['source']}] {job['title']} - {job['company']}"
          f" ({job['location'] or 'no location'}) | description: {len(description)} chars"
          f"{' | marked applied' if applied else ''}")
    return {"success": True, "id": job_id, "is_new": is_new, "applied": applied, "title": job['title'],
            "company": job['company'], "location": job['location'], "source": job['source'],
            "has_description": bool(job['job_description'].strip())}

@app.route('/job/<int:job_id>')
def job(job_id):
    jobs = read_jobs_from_db()
    return render_template('./templates/job_description.html', job=jobs[job_id])

@app.route('/get_all_jobs')
def get_all_jobs():
    conn = sqlite3.connect(config["db_path"])
    query = "SELECT * FROM jobs"
    df = pd.read_sql_query(query, conn)
    df = df.sort_values(by='id', ascending=False)
    df.reset_index(drop=True, inplace=True)
    jobs = df.to_dict('records')
    return jsonify(jobs)

@app.route('/job_details/<int:job_id>')
def job_details(job_id):
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    job_tuple = cursor.fetchone()
    conn.close()
    if job_tuple is not None:
        # Get the column names from the cursor description
        column_names = [column[0] for column in cursor.description]
        # Create a dictionary mapping column names to row values
        job = dict(zip(column_names, job_tuple))
        return jsonify(job)
    else:
        return jsonify({"error": "Job not found"}), 404

def toggle_column(job_id, column):
    """Flip a 0/1 status column for a job and return the new value."""
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute(f"UPDATE jobs SET {column} = 1 - {column} WHERE id = ?", (job_id,))
    conn.commit()
    cursor.execute(f"SELECT {column} FROM jobs WHERE id = ?", (job_id,))
    new_value = cursor.fetchone()[0]
    conn.close()
    return new_value

@app.route('/hide_job/<int:job_id>', methods=['POST'])
def hide_job(job_id):
    new_value = toggle_column(job_id, "hidden")
    return jsonify({"success": True, "hidden": bool(new_value)}), 200


@app.route('/mark_applied/<int:job_id>', methods=['POST'])
def mark_applied(job_id):
    new_value = toggle_column(job_id, "applied")
    # Stamp when it was switched on; clear it if the click was undone
    applied_date = datetime.now().strftime('%Y-%m-%d') if new_value else None
    conn = sqlite3.connect(config["db_path"])
    conn.execute("UPDATE jobs SET applied_date = ? WHERE id = ?", (applied_date, job_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "applied": bool(new_value), "applied_date": applied_date}), 200

@app.route('/mark_interview/<int:job_id>', methods=['POST'])
def mark_interview(job_id):
    new_value = toggle_column(job_id, "interview")
    return jsonify({"success": True, "interview": bool(new_value)}), 200

@app.route('/mark_rejected/<int:job_id>', methods=['POST'])
def mark_rejected(job_id):
    new_value = toggle_column(job_id, "rejected")
    return jsonify({"success": True, "rejected": bool(new_value)}), 200

@app.route('/set_outcome/<int:job_id>/<outcome>', methods=['POST'])
def set_outcome(job_id, outcome):
    # Sets the flags behind the Applications page outcome directly, rather than toggling.
    # Rejected leaves the interview flag alone so an interview that ended in rejection keeps its history.
    # applied stays 1 so the job can't drop off the Applications page.
    updates = {
        'Waiting': "interview = 0, rejected = 0",
        'Interview': "interview = 1, rejected = 0",
        'Rejected': "rejected = 1",
    }
    if outcome not in updates:
        return jsonify({"success": False, "error": "Unknown outcome"}), 400
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute(f"UPDATE jobs SET applied = 1, {updates[outcome]} WHERE id = ?", (job_id,))
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return jsonify({"success": updated, "outcome": outcome}), (200 if updated else 404)

@app.route('/delete_job/<int:job_id>', methods=['POST'])
def delete_job(job_id):
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    deleted = cursor.rowcount > 0
    conn.close()
    return jsonify({"success": deleted}), (200 if deleted else 404)

@app.route('/get_cover_letter/<int:job_id>')
def get_cover_letter(job_id):
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute("SELECT cover_letter FROM jobs WHERE id = ?", (job_id,))
    cover_letter = cursor.fetchone()
    conn.close()
    if cover_letter is not None:
        return jsonify({"cover_letter": cover_letter[0]})
    else:
        return jsonify({"error": "Cover letter not found"}), 404

@app.route('/get_resume/<int:job_id>', methods=['POST'])
def get_resume(job_id):
    print("Resume clicked!")
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()
    cursor.execute("SELECT job_description, title, company FROM jobs WHERE id = ?", (job_id,))
    job_tuple = cursor.fetchone()
    if job_tuple is not None:
        # Get the column names from the cursor description
        column_names = [column[0] for column in cursor.description]
        # Create a dictionary mapping column names to row values
        job = dict(zip(column_names, job_tuple))
    resume = read_pdf(config["resume_path"])

    # Check if OpenAI API key is empty
    if not config["OpenAI_API_KEY"]:
        print("Error: OpenAI API key is empty.")
        return jsonify({"error": "OpenAI API key is empty."}), 400

    openai.api_key = config["OpenAI_API_KEY"]
    consideration = ""
    user_prompt = ("You are a career coach with a client that is applying for a job as a " 
                   + job['title'] + " at " + job['company'] 
                   + ". They have a resume that you need to review and suggest how to tailor it for the job. "
                   "Approach this task in the following steps: \n 1. Highlight three to five most important responsibilities for this role based on the job description. "
                   "\n2. Based on these most important responsibilities from the job description, please tailor the resume for this role. Do not make information up. "
                   "Respond with the final resume only. \n\n Here is the job description: " 
                   + job['job_description'] + "\n\n Here is the resume: " + resume)
    if consideration:
        user_prompt += "\nConsider incorporating that " + consideration

    try:
        completion = openai.ChatCompletion.create(
            model=config["OpenAI_Model"],
            messages=[
                {"role": "user", "content": user_prompt},
            ],
        )
        response = completion.choices[0].message.content
    except Exception as e:
        print(f"Error connecting to OpenAI: {e}")
        return jsonify({"error": f"Error connecting to OpenAI: {e}"}), 500

    query = "UPDATE jobs SET resume = ? WHERE id = ?"
    print(f'Executing query: {query} with job_id: {job_id} and resume: {response}')
    cursor.execute(query, (response, job_id))
    conn.commit()
    conn.close()
    return jsonify({"resume": response}), 200

@app.route('/get_CoverLetter/<int:job_id>', methods=['POST'])
def get_CoverLetter(job_id):
    print("CoverLetter clicked!")
    conn = sqlite3.connect(config["db_path"])
    cursor = conn.cursor()

    def get_chat_gpt(prompt):
        try:
            completion = openai.ChatCompletion.create(
                model=config["OpenAI_Model"],
                messages=[
                    {"role": "user", "content": prompt},
                ],
            )
            return completion.choices[0].message.content
        except Exception as e:
            print(f"Error connecting to OpenAI: {e}")
            return None

    cursor.execute("SELECT job_description, title, company FROM jobs WHERE id = ?", (job_id,))
    job_tuple = cursor.fetchone()
    if job_tuple is not None:
        column_names = [column[0] for column in cursor.description]
        job = dict(zip(column_names, job_tuple))
    
    resume = read_pdf(config["resume_path"])

    # Check if resume is None
    if resume is None:
        print("Error: Resume not found or couldn't be read.")
        return jsonify({"error": "Resume not found or couldn't be read."}), 400

    # Check if OpenAI API key is empty
    if not config["OpenAI_API_KEY"]:
        print("Error: OpenAI API key is empty.")
        return jsonify({"error": "OpenAI API key is empty."}), 400

    openai.api_key = config["OpenAI_API_KEY"]
    consideration = ""
    user_prompt = ("You are a career coach with over 15 years of experience helping job seekers land their dream jobs in tech. You are helping a candidate to write a cover letter for the below role. Approach this task in three steps. Step 1. Identify main challenges someone in this position would face day to day. Step 2. Write an attention grabbing hook for your cover letter that highlights your experience and qualifications in a way that shows you empathize and can successfully take on challenges of the role. Consider incorporating specific examples of how you tackled these challenges in your past work, and explore creative ways to express your enthusiasm for the opportunity. Put emphasis on how the candidate can contribute to company as opposed to just listing accomplishments. Keep your hook within 100 words or less. Step 3. Finish writing the cover letter based on the resume and keep it within 250 words. Respond with final cover letter only. \n job description: " + job['job_description'] + "\n company: " + job['company'] + "\n title: " + job['title'] + "\n resume: " + resume)
    if consideration:
        user_prompt += "\nConsider incorporating that " + consideration

    response = get_chat_gpt(user_prompt)
    if response is None:
        return jsonify({"error": "Failed to get a response from OpenAI."}), 500

    user_prompt2 = ("You are young but experienced career coach helping job seekers land their dream jobs in tech. I need your help crafting a cover letter. Here is a job description: " + job['job_description'] + "\nhere is my resume: " + resume + "\nHere's the cover letter I got so far: " + response + "\nI need you to help me improve it. Let's approach this in following steps. \nStep 1. Please set the formality scale as follows: 1 is conversational English, my initial Cover letter draft is 10. Step 2. Identify three to five ways this cover letter can be improved, and elaborate on each way with at least one thoughtful sentence. Step 4. Suggest an improved cover letter based on these suggestions with the Formality Score set to 7. Avoid subjective qualifiers such as drastic, transformational, etc. Keep the final cover letter within 250 words. Please respond with the final cover letter only.")
    if user_prompt2:
        response = get_chat_gpt(user_prompt2)
        if response is None:
            return jsonify({"error": "Failed to get a response from OpenAI."}), 500

    query = "UPDATE jobs SET cover_letter = ? WHERE id = ?"
    print(f'Executing query: {query} with job_id: {job_id} and cover letter: {response}')
    cursor.execute(query, (response, job_id))
    conn.commit()
    conn.close()
    return jsonify({"cover_letter": response}), 200

def read_jobs_from_db():
    conn = sqlite3.connect(config["db_path"])
    query = "SELECT * FROM jobs WHERE hidden = 0"
    df = pd.read_sql_query(query, conn)
    df = df.sort_values(by='id', ascending=False)
    # df.reset_index(drop=True, inplace=True)
    return df.to_dict('records')

def verify_db_schema():
    # Adds cover_letter/resume/source/applied_date columns if missing and backfills source
    ensure_schema(config["db_path"])

if __name__ == "__main__":
    verify_db_schema()  # Verify the DB schema before running the app
    app.run(debug=True, port=5099)
