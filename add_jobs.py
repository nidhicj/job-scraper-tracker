"""Save specific jobs to the DB from their links: LinkedIn, XING, or any job page that
embeds schema.org JobPosting data (Personio, Join, most company career sites).

Usage:
    python add_jobs.py <url> [<url> ...]
    python add_jobs.py < links.txt          # one URL per line

These are jobs you picked by hand, so the title/language filters from config.json are
NOT applied - every link that can be read is saved, unless it's already in the DB.
"""
import re
import sqlite3
import sys
import time as tm
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup

from db_schema import ensure_schema, source_from_url
from main import load_config, make_job, parse_jobposting_page, transform_job

def parse_relative_date(text):
    # LinkedIn's public page only says '3 days ago' / '1 week ago' - turn that into a date
    match = re.search(r'(\d+)\s+(minute|hour|day|week|month|year)', text or '')
    if not match:
        return datetime.now().strftime('%Y-%m-%d')
    n, unit = int(match.group(1)), match.group(2)
    days = {'minute': 0, 'hour': 0, 'day': 1, 'week': 7, 'month': 30, 'year': 365}[unit] * n
    return (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

def linkedin_job_id(url):
    # Handles /jobs/view/123, /jobs/view/title-at-company-123 and search pages with ?currentJobId=123
    parsed = urlparse(url)
    current = parse_qs(parsed.query).get('currentJobId')
    if current and current[0].isdigit():
        return current[0]
    match = re.search(r'/jobs/view/(?:[^/]*?-)?(\d+)', parsed.path)
    return match.group(1) if match else None

def parse_linkedin_job(url, headers):
    job_id = linkedin_job_id(url)
    if not job_id:
        return None
    # The guest endpoint serves the public job page without login
    r = requests.get(f'https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}', headers=headers, timeout=15)
    if r.status_code != 200:
        print(f"LinkedIn returned HTTP {r.status_code} for job {job_id}")
        return None
    soup = BeautifulSoup(r.content, 'html.parser')
    title = soup.select_one('h2.top-card-layout__title')
    if title is None:
        return None
    company = soup.select_one('a.topcard__org-name-link') or soup.select_one('span.topcard__flavor')
    location = soup.select_one('span.topcard__flavor.topcard__flavor--bullet')
    posted = soup.select_one('span.posted-time-ago__text')
    return make_job(
        title=title.get_text(strip=True),
        company=company.get_text(strip=True) if company else '',
        location=location.get_text(strip=True) if location else '',
        date_value=parse_relative_date(posted.get_text(strip=True) if posted else ''),
        # Same canonical form the LinkedIn search scraper stores, so duplicates are caught
        job_url=f'https://www.linkedin.com/jobs/view/{job_id}/',
        job_description=transform_job(soup),
        source='LinkedIn'
    )

def clean_url(url):
    # Drop tracking parameters (utm_*, trk, sc_o, ...) so the same job always gets the same stored URL
    parsed = urlparse(url)
    tracking = {'trk', 'sc_o', 'ref', 'gclid', 'fbclid'}
    query = [(k, v) for k, v in parse_qs(parsed.query, keep_blank_values=True).items()
             if not k.lower().startswith('utm_') and k.lower() not in tracking]
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True), fragment=''))

def fetch_job(url, headers):
    if 'linkedin.' in urlparse(url).netloc.lower():
        return parse_linkedin_job(url, headers)
    url = clean_url(url)
    job = parse_jobposting_page(url, headers, source_from_url(url))
    if job is None:
        raise ValueError('no job data found on this page (expired, removed, or a site that builds the page with JavaScript)')
    return job

def save_job(conn, job):
    # Returns (id, is_new). A job counts as saved already if its URL or title+company match.
    row = conn.execute(
        "SELECT id FROM jobs WHERE job_url = ? OR (title = ? AND company = ?)",
        (job['job_url'], job['title'], job['company'])
    ).fetchone()
    if row:
        return row[0], False
    job = dict(job, date_loaded=str(datetime.now()))
    columns = ', '.join(f'"{c}"' for c in job)
    cursor = conn.execute(f'INSERT INTO jobs ({columns}) VALUES ({", ".join("?" for _ in job)})', list(job.values()))
    conn.commit()
    return cursor.lastrowid, True

def main(urls):
    config = load_config('config.json')
    ensure_schema(config['db_path'])
    headers = dict(config.get('headers', {}))
    headers.setdefault('User-Agent', 'Mozilla/5.0')
    conn = sqlite3.connect(config['db_path'])
    added = 0
    for i, url in enumerate(urls):
        if i:
            tm.sleep(1)  # be polite between requests
        try:
            job = fetch_job(url, headers)
        except Exception as e:
            print(f"FAILED  {url}: {e}")
            continue
        if not job:
            print(f"FAILED  {url}: could not read the job from this page (expired or removed?)")
            continue
        job_id, is_new = save_job(conn, job)
        added += is_new
        status = 'ADDED  ' if is_new else 'EXISTS '
        print(f"{status} #{job_id}  [{job['source']}] {job['title']} - {job['company']} ({job['date']})")
    conn.close()
    print(f"Done: {added} new, {len(urls) - added} skipped or failed")

if __name__ == '__main__':
    links = sys.argv[1:] or [line.strip() for line in sys.stdin if line.strip()]
    if not links:
        print(__doc__)
        sys.exit(1)
    main(links)
