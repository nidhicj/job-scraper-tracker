import requests
import json
import sqlite3
import sys
from sqlite3 import Error
from bs4 import BeautifulSoup
import time as tm
from itertools import groupby
from datetime import datetime, timedelta, time
import pandas as pd
from urllib.parse import quote
from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException


def load_config(file_name):
    # Load the config file
    with open(file_name) as f:
        return json.load(f)

def get_with_retry(url, config, retries=3, delay=1):
    # Get the URL with retries and delay
    for i in range(retries):
        try:
            if len(config['proxies']) > 0:
                r = requests.get(url, headers=config['headers'], proxies=config['proxies'], timeout=5)
            else:
                r = requests.get(url, headers=config['headers'], timeout=5)
            return BeautifulSoup(r.content, 'html.parser')
        except requests.exceptions.Timeout:
            print(f"Timeout occurred for URL: {url}, retrying in {delay}s...")
            tm.sleep(delay)
        except Exception as e:
            print(f"An error occurred while retrieving the URL: {url}, error: {e}")
    return None

def transform(soup):
    # Parsing the job card info (title, company, location, date, job_url) from the beautiful soup object
    joblist = []
    try:
        divs = soup.find_all('div', class_='base-search-card__info')
    except:
        print("Empty page, no jobs found")
        return joblist
    for item in divs:
        title = item.find('h3').text.strip()
        company = item.find('a', class_='hidden-nested-link')
        location = item.find('span', class_='job-search-card__location')
        parent_div = item.parent
        entity_urn = parent_div['data-entity-urn']
        job_posting_id = entity_urn.split(':')[-1]
        job_url = 'https://www.linkedin.com/jobs/view/'+job_posting_id+'/'

        date_tag_new = item.find('time', class_ = 'job-search-card__listdate--new')
        date_tag = item.find('time', class_='job-search-card__listdate')
        date = date_tag['datetime'] if date_tag else date_tag_new['datetime'] if date_tag_new else ''
        job_description = ''
        job = {
            'title': title,
            'company': company.text.strip().replace('\n', ' ') if company else '',
            'location': location.text.strip() if location else '',
            'date': date,
            'job_url': job_url,
            'job_description': job_description,
            'applied': 0,
            'hidden': 0,
            'interview': 0,
            'rejected': 0
        }
        joblist.append(job)
    return joblist

def transform_job(soup):
    div = soup.find('div', class_='description__text description__text--rich')
    if div:
        # Remove unwanted elements
        for element in div.find_all(['span', 'a']):
            element.decompose()

        # Replace bullet points
        for ul in div.find_all('ul'):
            for li in ul.find_all('li'):
                li.insert(0, '-')

        text = div.get_text(separator='\n').strip()
        text = text.replace('\n\n', '')
        text = text.replace('::marker', '-')
        text = text.replace('-\n', '- ')
        text = text.replace('Show less', '').replace('Show more', '')
        return text
    else:
        return "Could not find Job Description"

def normalize_date(value):
    # Convert a date value from any job source (ISO string, unix seconds, unix ms) into 'YYYY-MM-DD'
    if not value:
        return ''
    if isinstance(value, (int, float)):
        ts = value / 1000 if value > 1e12 else value
        return datetime.utcfromtimestamp(ts).strftime('%Y-%m-%d')
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).strftime('%Y-%m-%d')
    except ValueError:
        return str(value)[:10]

def html_to_text(html):
    if not html:
        return ''
    return BeautifulSoup(html, 'html.parser').get_text(separator='\n').strip()

def make_job(title, company, location, date_value, job_url, job_description=''):
    return {
        'title': title,
        'company': company,
        'location': location,
        'date': normalize_date(date_value),
        'job_url': job_url,
        'job_description': job_description,
        'applied': 0,
        'hidden': 0,
        'interview': 0,
        'rejected': 0
    }

def get_remoteok_jobs(config):
    # Public, unauthenticated JSON feed - no proxy or LinkedIn ToS risk
    headers = dict(config.get('headers', {}))
    headers.setdefault('User-Agent', 'Mozilla/5.0')
    jobs = []
    try:
        r = requests.get('https://remoteok.com/api', headers=headers, timeout=10)
        data = r.json()
    except Exception as e:
        print(f"An error occurred while fetching RemoteOK jobs: {e}")
        return jobs
    for item in data:
        if not isinstance(item, dict) or not item.get('position'):
            continue
        jobs.append(make_job(
            title=item.get('position', ''),
            company=item.get('company', ''),
            location=item.get('location') or 'Remote',
            date_value=item.get('date'),
            job_url=item.get('url', ''),
            job_description=html_to_text(item.get('description'))
        ))
    print(f"RemoteOK: fetched {len(jobs)} jobs")
    return jobs

def get_arbeitnow_jobs(config):
    # Public, unauthenticated, paginated JSON feed
    jobs = []
    url = 'https://www.arbeitnow.com/api/job-board-api'
    pages_fetched = 0
    max_pages = config.get('pages_to_scrape', 1)
    while url and pages_fetched < max_pages:
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
        except Exception as e:
            print(f"An error occurred while fetching Arbeitnow jobs: {e}")
            break
        for item in data.get('data', []):
            jobs.append(make_job(
                title=item.get('title', ''),
                company=item.get('company_name', ''),
                location=item.get('location', ''),
                date_value=item.get('created_at'),
                job_url=item.get('url', ''),
                job_description=html_to_text(item.get('description'))
            ))
        url = data.get('links', {}).get('next')
        pages_fetched += 1
    print(f"Arbeitnow: fetched {len(jobs)} jobs")
    return jobs

def get_greenhouse_jobs(board_token, config):
    # Public, unauthenticated per-company board. board_token = the company's Greenhouse board slug
    jobs = []
    try:
        r = requests.get(f'https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs', timeout=10)
        listing = r.json().get('jobs', [])
    except Exception as e:
        print(f"An error occurred while fetching Greenhouse jobs for {board_token}: {e}")
        return jobs
    for item in listing:
        description = ''
        try:
            job_id = item.get('id')
            rd = requests.get(f'https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?content=true', timeout=10)
            description = html_to_text(rd.json().get('content', ''))
        except Exception as e:
            print(f"Could not fetch description for Greenhouse job {item.get('id')}: {e}")
        jobs.append(make_job(
            title=item.get('title', ''),
            company=item.get('company_name') or board_token,
            location=(item.get('location') or {}).get('name', ''),
            date_value=item.get('first_published'),
            job_url=item.get('absolute_url', ''),
            job_description=description
        ))
    print(f"Greenhouse ({board_token}): fetched {len(jobs)} jobs")
    return jobs

def get_lever_jobs(board_token, config):
    # Public, unauthenticated per-company board. board_token = the company's Lever slug
    jobs = []
    try:
        r = requests.get(f'https://api.lever.co/v0/postings/{board_token}?mode=json', timeout=10)
        listing = r.json()
    except Exception as e:
        print(f"An error occurred while fetching Lever jobs for {board_token}: {e}")
        return jobs
    for item in listing:
        jobs.append(make_job(
            title=item.get('text', ''),
            company=board_token,
            location=(item.get('categories') or {}).get('location', ''),
            date_value=item.get('createdAt'),
            job_url=item.get('hostedUrl', ''),
            job_description=item.get('descriptionPlain') or html_to_text(item.get('description'))
        ))
    print(f"Lever ({board_token}): fetched {len(jobs)} jobs")
    return jobs

def safe_detect(text):
    try:
        return detect(text)
    except LangDetectException:
        return 'en'

def remove_irrelevant_jobs(joblist, config):
    #Filter out jobs based on description, title, and language. Set up in config.json.
    new_joblist = [job for job in joblist if not any(word.lower() in job['job_description'].lower() for word in config['desc_words'])]   
    new_joblist = [job for job in new_joblist if not any(word.lower() in job['title'].lower() for word in config['title_exclude'])] if len(config['title_exclude']) > 0 else new_joblist
    new_joblist = [job for job in new_joblist if any(word.lower() in job['title'].lower() for word in config['title_include'])] if len(config['title_include']) > 0 else new_joblist
    new_joblist = [job for job in new_joblist if safe_detect(job['job_description']) in config['languages']] if len(config['languages']) > 0 else new_joblist
    new_joblist = [job for job in new_joblist if not any(word.lower() in job['company'].lower() for word in config['company_exclude'])] if len(config['company_exclude']) > 0 else new_joblist

    return new_joblist

def remove_duplicates(joblist, config):
    # Remove duplicate jobs in the joblist. Duplicate is defined as having the same title and company.
    joblist.sort(key=lambda x: (x['title'], x['company']))
    joblist = [next(g) for k, g in groupby(joblist, key=lambda x: (x['title'], x['company']))]
    return joblist

def convert_date_format(date_string):
    """
    Converts a date string to a date object. 
    
    Args:
        date_string (str): The date in string format.

    Returns:
        date: The converted date object, or None if conversion failed.
    """
    date_format = "%Y-%m-%d"
    try:
        job_date = datetime.strptime(date_string, date_format).date()
        return job_date
    except ValueError:
        print(f"Error: The date for job {date_string} - is not in the correct format.")
        return None

def create_connection(config):
    # Create a database connection to a SQLite database
    conn = None
    path = config['db_path']
    try:
        conn = sqlite3.connect(path) # creates a SQL database in the 'data' directory
        #print(sqlite3.version)
    except Error as e:
        print(e)

    return conn

def create_table(conn, df, table_name):
    ''''
    # Create a new table with the data from the dataframe
    df.to_sql(table_name, conn, if_exists='replace', index=False)
    print (f"Created the {table_name} table and added {len(df)} records")
    '''
    # Create a new table with the data from the DataFrame
    # Prepare data types mapping from pandas to SQLite
    type_mapping = {
        'int64': 'INTEGER',
        'float64': 'REAL',
        'datetime64[ns]': 'TIMESTAMP',
        'object': 'TEXT',
        'str': 'TEXT',  # pandas >= 3.0 reports pure-string columns as 'str', not 'object'
        'bool': 'INTEGER'
    }
    
    # Prepare a string with column names and their types
    columns_with_types = ', '.join(
        f'"{column}" {type_mapping[str(df.dtypes[column])]}'
        for column in df.columns
    )
    
    # Prepare SQL query to create a new table
    create_table_sql = f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            {columns_with_types}
        );
    """
    
    # Execute SQL query
    cursor = conn.cursor()
    cursor.execute(create_table_sql)
    
    # Commit the transaction
    conn.commit()

    # Insert DataFrame records one by one
    insert_sql = f"""
        INSERT INTO "{table_name}" ({', '.join(f'"{column}"' for column in df.columns)})
        VALUES ({', '.join(['?' for _ in df.columns])})
    """
    for record in df.to_dict(orient='records'):
        cursor.execute(insert_sql, list(record.values()))
    
    # Commit the transaction
    conn.commit()

    print(f"Created the {table_name} table and added {len(df)} records")

def update_table(conn, df, table_name):
    # Update the existing table with new records.
    df_existing = pd.read_sql(f'select * from {table_name}', conn)

    # Create a dataframe with unique records in df that are not in df_existing
    df_new_records = pd.concat([df, df_existing, df_existing]).drop_duplicates(['title', 'company', 'date'], keep=False)

    # If there are new records, append them to the existing table
    if len(df_new_records) > 0:
        df_new_records.to_sql(table_name, conn, if_exists='append', index=False)
        print (f"Added {len(df_new_records)} new records to the {table_name} table")
    else:
        print (f"No new records to add to the {table_name} table")

def table_exists(conn, table_name):
    # Check if the table already exists in the database
    cur = conn.cursor()
    cur.execute(f"SELECT count(name) FROM sqlite_master WHERE type='table' AND name='{table_name}'")
    if cur.fetchone()[0]==1 :
        return True
    return False

def job_exists(df, job):
    # Check if the job already exists in the dataframe
    if df.empty:
        return False
    #return ((df['title'] == job['title']) & (df['company'] == job['company']) & (df['date'] == job['date'])).any()
    #The job exists if there's already a job in the database that has the same URL
    return ((df['job_url'] == job['job_url']).any() | (((df['title'] == job['title']) & (df['company'] == job['company']) & (df['date'] == job['date'])).any()))

def get_jobcards(config):
    #Function to get the job cards, from whichever sources are enabled in config['job_sources']
    all_jobs = []
    sources = config.get('job_sources', {})

    if sources.get('linkedin'):
        for k in range(0, config['rounds']):
            for query in config['search_queries']:
                keywords = quote(query['keywords']) # URL encode the keywords
                location = quote(query['location']) # URL encode the location
                for i in range (0, config['pages_to_scrape']):
                    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={keywords}&location={location}&f_TPR=&f_WT={query['f_WT']}&geoId=&f_TPR={config['timespan']}&start={25*i}"
                    soup = get_with_retry(url, config)
                    jobs = transform(soup)
                    all_jobs = all_jobs + jobs
                    print("Finished scraping page: ", url)

    if sources.get('remoteok'):
        all_jobs += get_remoteok_jobs(config)

    if sources.get('arbeitnow'):
        all_jobs += get_arbeitnow_jobs(config)

    for board_token in sources.get('greenhouse_boards', []):
        all_jobs += get_greenhouse_jobs(board_token, config)

    for board_token in sources.get('lever_boards', []):
        all_jobs += get_lever_jobs(board_token, config)

    print ("Total job cards scraped: ", len(all_jobs))
    all_jobs = remove_duplicates(all_jobs, config)
    print ("Total job cards after removing duplicates: ", len(all_jobs))
    all_jobs = remove_irrelevant_jobs(all_jobs, config)
    print ("Total job cards after removing irrelevant jobs: ", len(all_jobs))
    return all_jobs

def find_new_jobs(all_jobs, conn, config):
    # From all_jobs, find the jobs that are not already in the database. Function checks both the jobs and filtered_jobs tables.
    jobs_tablename = config['jobs_tablename']
    filtered_jobs_tablename = config['filtered_jobs_tablename']
    jobs_db = pd.DataFrame()
    filtered_jobs_db = pd.DataFrame()    
    if conn is not None:
        if table_exists(conn, jobs_tablename):
            query = f"SELECT * FROM {jobs_tablename}"
            jobs_db = pd.read_sql_query(query, conn)
        if table_exists(conn, filtered_jobs_tablename):
            query = f"SELECT * FROM {filtered_jobs_tablename}"
            filtered_jobs_db = pd.read_sql_query(query, conn)

    new_joblist = [job for job in all_jobs if not job_exists(jobs_db, job) and not job_exists(filtered_jobs_db, job)]
    return new_joblist

def main(config_file):
    start_time = tm.perf_counter()
    job_list = []

    config = load_config(config_file)
    jobs_tablename = config['jobs_tablename'] # name of the table to store the "approved" jobs
    filtered_jobs_tablename = config['filtered_jobs_tablename'] # name of the table to store the jobs that have been filtered out based on description keywords (so that in future they are not scraped again)
    #Scrape search results page and get job cards. This step might take a while based on the number of pages and search queries.
    all_jobs = get_jobcards(config)
    conn = create_connection(config)
    #filtering out jobs that are already in the database
    all_jobs = find_new_jobs(all_jobs, conn, config)
    print ("Total new jobs found after comparing to the database: ", len(all_jobs))

    if len(all_jobs) > 0:

        for job in all_jobs:
            job_date = convert_date_format(job['date'])
            job_date = datetime.combine(job_date, time())
            #if job is older than a week, skip it
            if job_date < datetime.now() - timedelta(days=config['days_to_scrape']):
                continue
            print('Found new job: ', job['title'], 'at ', job['company'], job['job_url'])
            if not job['job_description']:
                # Only LinkedIn jobs land here with no description yet - other sources
                # (RemoteOK, Arbeitnow, Greenhouse, Lever) already fill it in at fetch time.
                desc_soup = get_with_retry(job['job_url'], config)
                job['job_description'] = transform_job(desc_soup)
            language = safe_detect(job['job_description'])
            if language not in config['languages']:
                print('Job description language not supported: ', language)
                #continue
            job_list.append(job)
        #Final check - removing jobs based on job description keywords words from the config file
        jobs_to_add = remove_irrelevant_jobs(job_list, config)
        print ("Total jobs to add: ", len(jobs_to_add))
        #Create a list for jobs removed based on job description keywords - they will be added to the filtered_jobs table
        filtered_list = [job for job in job_list if job not in jobs_to_add]
        df = pd.DataFrame(jobs_to_add)
        df_filtered = pd.DataFrame(filtered_list)
        df['date_loaded'] = datetime.now()
        df_filtered['date_loaded'] = datetime.now()
        df['date_loaded'] = df['date_loaded'].astype(str)
        df_filtered['date_loaded'] = df_filtered['date_loaded'].astype(str)        
        
        if conn is not None:
            #Update or Create the database table for the job list
            if table_exists(conn, jobs_tablename):
                update_table(conn, df, jobs_tablename)
            else:
                create_table(conn, df, jobs_tablename)
                
            #Update or Create the database table for the filtered out jobs
            if table_exists(conn, filtered_jobs_tablename):
                update_table(conn, df_filtered, filtered_jobs_tablename)
            else:
                create_table(conn, df_filtered, filtered_jobs_tablename)
        else:
            print("Error! cannot create the database connection.")
        
        df.to_csv('linkedin_jobs.csv', index=False, encoding='utf-8')
        df_filtered.to_csv('linkedin_jobs_filtered.csv', index=False, encoding='utf-8')
    else:
        print("No jobs found")
    
    end_time = tm.perf_counter()
    print(f"Scraping finished in {end_time - start_time:.2f} seconds")


if __name__ == "__main__":
    config_file = 'config.json'  # default config file
    if len(sys.argv) == 2:
        config_file = sys.argv[1]
        
    main(config_file)