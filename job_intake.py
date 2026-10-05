"""Get a job into the DB when the scrapers can't reach it: from a link, a screenshot, pasted
job text, or a page sent by the "Save to tracker" bookmarklet. Used by app.py (Add job page,
/capture) and email_alerts.py.
"""
import base64
import json
import re
from datetime import datetime

import openai

from add_jobs import clean_url, fetch_job, save_job
from db_schema import source_from_url
from main import job_from_jobposting_html, make_job

EXTRACT_PROMPT = (
    "Extract the job posting from the content below. Respond with JSON only, no other text, "
    "using exactly these keys: "
    '{"title": "", "company": "", "location": "", "date_posted": "YYYY-MM-DD or empty", '
    '"job_url": "the posting URL if one is visible, else empty", '
    '"description": "the full job description text, copied as written - do not summarize or shorten"}. '
    "Use an empty string for anything not shown. If the content is not a job posting, respond with "
    '{"error": "not a job posting"}.'
)

def chat(config, content, model=None):
    # One chat completion against the configured OpenAI-compatible provider (OpenRouter etc.)
    if not config.get("OpenAI_API_KEY"):
        raise ValueError("OpenAI_API_KEY is empty in config.json")
    openai.api_key = config["OpenAI_API_KEY"]
    if config.get("OpenAI_Base_URL"):
        openai.api_base = config["OpenAI_Base_URL"]
    completion = openai.ChatCompletion.create(
        model=model or config["OpenAI_Model"],
        messages=[{"role": "user", "content": content}],
    )
    return completion.choices[0].message.content

def parse_json_reply(reply):
    # Models often wrap JSON in ```json fences or add a sentence around it - take the JSON part
    reply = re.sub(r'^```(?:json)?\s*|\s*```$', '', reply.strip())
    try:
        return json.loads(reply)
    except ValueError:
        match = re.search(r'(\{.*\}|\[.*\])', reply, re.S)
        if not match:
            raise ValueError(f"model did not return JSON: {reply[:200]}")
        return json.loads(match.group(1))

def job_from_fields(fields, url='', source=''):
    if fields.get('error'):
        raise ValueError(fields['error'])
    if not fields.get('title'):
        raise ValueError("couldn't find a job title in it")
    url = url or fields.get('job_url') or ''
    return make_job(
        title=fields.get('title', '').strip(),
        company=(fields.get('company') or '').strip(),
        location=(fields.get('location') or '').strip(),
        date_value=fields.get('date_posted') or datetime.now().strftime('%Y-%m-%d'),
        job_url=clean_url(url) if url else '',
        job_description=(fields.get('description') or '').strip(),
        source=source or (source_from_url(url) if url else 'Manual'),
    )

def job_from_screenshot(config, image_bytes, image_type, url=''):
    data_url = f"data:{image_type};base64,{base64.b64encode(image_bytes).decode()}"
    content = [
        {"type": "text", "text": EXTRACT_PROMPT},
        {"type": "image_url", "image_url": {"url": data_url}},
    ]
    # Reading a screenshot needs a vision-capable model; it can differ from the text model
    reply = chat(config, content, model=config.get("Vision_Model") or config["OpenAI_Model"])
    return job_from_fields(parse_json_reply(reply), url)

def job_from_text(config, text, url=''):
    reply = chat(config, EXTRACT_PROMPT + "\n\nContent:\n" + text[:60000])
    return job_from_fields(parse_json_reply(reply), url)

def job_from_link(config, url):
    headers = dict(config.get('headers', {}))
    headers.setdefault('User-Agent', 'Mozilla/5.0')
    job = fetch_job(url, headers)
    if not job:
        raise ValueError('could not read the job from this page (expired or removed?)')
    return job

def job_from_capture(config, url, ld_scripts, page_text):
    # The bookmarklet sends what the browser already loaded, so bot checks don't matter here.
    # Prefer the page's structured JobPosting data; fall back to the model reading the visible text.
    page_html = ''.join(f'<script type="application/ld+json">{s}</script>' for s in ld_scripts)
    job = job_from_jobposting_html(page_html, clean_url(url), source_from_url(url)) if page_html else None
    if job and job['title']:
        return job
    return job_from_text(config, page_text, url)

def mark_applied(conn, job_id):
    # Unlike the Jobs page toggle, this only ever turns applied on, and keeps an earlier applied date
    conn.execute(
        "UPDATE jobs SET applied = 1, applied_date = COALESCE(applied_date, ?) WHERE id = ?",
        (datetime.now().strftime('%Y-%m-%d'), job_id),
    )
    conn.commit()
