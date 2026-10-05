"""Save jobs from job-alert emails (StepStone, Indeed, Glassdoor, LinkedIn, ...) into the DB.

Those boards block scraping but happily email you new jobs for a saved search. This reads
the alert emails over IMAP, has the model list the jobs in each one, and saves them.

Usage:
    python email_alerts.py              # emails from the last `days` days (config, default 2)
    python email_alerts.py --days 14    # look further back, e.g. the first time
    python email_alerts.py --dry-run    # show what would be saved, save nothing

Each email is only processed once (remembered in the processed_emails table). Alert emails
carry title/company/location and a link but no description; where the link's site allows it
the full posting is fetched, otherwise the job is saved without one. Use the "Save to tracker"
button on that job's page later to fill the description in.
"""
import argparse
import email
import imaplib
import sqlite3
import time as tm
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import job_intake
from add_jobs import save_job
from db_schema import ensure_schema, source_from_url
from main import load_config

DEFAULT_SENDERS = [
    "jobalerts-noreply@linkedin.com", "stepstone", "indeed", "glassdoor",
    "nationalevacaturebank", "intermediair", "wellfound", "xing", "monster",
]

# Sites that reject plain requests - don't spend 15 seconds per job trying to fetch the posting
BLOCKED_HOSTS = ["stepstone", "indeed", "glassdoor", "nationalevacaturebank", "intermediair", "monster", "wellfound"]

LIST_PROMPT = (
    "This is an email from a job site. If it is a job alert / job recommendation email, list every "
    "job in it. Respond with a JSON array only, no other text, one object per job: "
    '[{"title": "", "company": "", "location": "", "job_url": "the link to that job"}]. '
    "Use an empty string for anything not shown. If the email is not a job alert, respond with []."
)

def email_body_text(msg):
    # Prefer the HTML part, written out as text with each link's URL after it so the model can see them
    html_part = text_part = None
    for part in msg.walk():
        kind = part.get_content_type()
        if kind == 'text/html' and html_part is None:
            html_part = part
        elif kind == 'text/plain' and text_part is None:
            text_part = part
    part = html_part or text_part
    if part is None:
        return ''
    raw = part.get_payload(decode=True) or b''
    body = raw.decode(part.get_content_charset() or 'utf-8', errors='replace')
    if part is html_part:
        soup = BeautifulSoup(body, 'html.parser')
        for tag in soup(['style', 'script', 'head']):
            tag.decompose()
        for a in soup.find_all('a', href=True):
            if a['href'].startswith('http'):
                a.append(f" <{a['href']}>")
        body = soup.get_text(separator='\n')
    lines = (line.strip() for line in body.splitlines())
    return '\n'.join(line for line in lines if line)

def fetch_full_job(config, listed, source):
    # Try the posting itself for sites that allow it (LinkedIn, XING, company pages); else keep the email's summary
    url = listed.get('job_url') or ''
    host = urlparse(url).netloc.lower()
    if url and not any(blocked in host for blocked in BLOCKED_HOSTS):
        try:
            job = job_intake.job_from_link(config, url)
            if job and job['title']:
                return job
        except Exception:
            pass
    return job_intake.job_from_fields(dict(listed, description=''), url, source)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    config = load_config('config.json')
    settings = config.get('email_alerts') or {}
    if not settings.get('username') or not settings.get('app_password'):
        raise SystemExit('Fill in "email_alerts" -> username and app_password in config.json (see config_example.json)')
    days = args.days or settings.get('days', 2)
    senders = settings.get('senders') or DEFAULT_SENDERS

    ensure_schema(config['db_path'])
    conn = sqlite3.connect(config['db_path'])
    conn.execute("CREATE TABLE IF NOT EXISTS processed_emails (message_id TEXT PRIMARY KEY, processed_at TEXT)")

    imap = imaplib.IMAP4_SSL(settings.get('imap_host', 'imap.gmail.com'))
    imap.login(settings['username'], settings['app_password'])
    imap.select(settings.get('folder', 'INBOX'), readonly=True)  # read-only: leaves emails unread
    since = (datetime.now() - timedelta(days=days)).strftime('%d-%b-%Y')
    message_nums = set()
    for sender in senders:
        status, data = imap.search(None, 'SINCE', since, 'FROM', f'"{sender}"')
        if status == 'OK':
            message_nums.update(data[0].split())
    print(f"{len(message_nums)} alert emails in the last {days} days")

    added = existing = 0
    for num in sorted(message_nums, key=int):
        status, data = imap.fetch(num, '(RFC822)')
        if status != 'OK':
            continue
        msg = email.message_from_bytes(data[0][1])
        message_id = msg.get('Message-ID') or f"{msg.get('From')}|{msg.get('Date')}|{msg.get('Subject')}"
        if conn.execute("SELECT 1 FROM processed_emails WHERE message_id = ?", (message_id,)).fetchone():
            continue
        subject = str(make_header(decode_header(msg.get('Subject', ''))))
        sender_domain = parseaddr(msg.get('From', ''))[1].split('@')[-1]
        # Job links in alerts often go through click-tracking hosts, so name the source after the sender
        source = source_from_url('https://' + sender_domain)
        try:
            listed_jobs = job_intake.parse_json_reply(job_intake.chat(config, LIST_PROMPT + "\n\nEmail:\n" + email_body_text(msg)[:60000]))
        except Exception as e:
            print(f"FAILED  [{source}] {subject}: {e}")
            continue
        if not isinstance(listed_jobs, list):
            listed_jobs = []
        print(f"[{source}] {subject}: {len(listed_jobs)} jobs")
        for listed in listed_jobs:
            if not isinstance(listed, dict) or not listed.get('title'):
                continue
            job = fetch_full_job(config, listed, source)
            if args.dry_run:
                print(f"   would save: {job['title']} - {job['company']} ({job['location']})")
                continue
            job_id, is_new = save_job(conn, job)
            added += is_new
            existing += not is_new
            print(f"   {'ADDED  ' if is_new else 'EXISTS '} #{job_id} {job['title']} - {job['company']}")
            tm.sleep(0.5)
        if not args.dry_run:
            conn.execute("INSERT INTO processed_emails VALUES (?, ?)", (message_id, str(datetime.now())))
            conn.commit()
    imap.logout()
    conn.close()
    print(f"Done: {added} new jobs, {existing} already saved")

if __name__ == '__main__':
    main()
