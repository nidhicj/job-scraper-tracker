import sqlite3
from urllib.parse import urlparse

# Columns added after the original schema. Shared by main.py (scraper) and app.py (UI)
# so whichever runs first brings the DB up to date.
EXTRA_COLUMNS = {
    'jobs': {'cover_letter': 'TEXT', 'resume': 'TEXT', 'source': 'TEXT', 'applied_date': 'TEXT'},
    'filtered_jobs': {'source': 'TEXT'},
}

def source_from_url(url):
    # Infer the job source from the posting URL (used to backfill rows scraped before 'source' existed)
    host = urlparse(url or '').netloc.lower()
    if 'linkedin.' in host:
        return 'LinkedIn'
    if 'remoteok.' in host:
        return 'RemoteOK'
    if 'arbeitnow.' in host:
        return 'Arbeitnow'
    if 'greenhouse.io' in host:
        return 'Greenhouse'
    if 'lever.co' in host:
        return 'Lever'
    if 'xing.' in host:
        return 'XING'
    if 'personio.' in host:
        return 'Personio'
    if 'join.com' in host:
        return 'Join'
    # Anything else: the site's own name, e.g. careers.acme.com -> 'Acme'
    parts = [p for p in host.split('.') if p and p != 'www']
    return parts[-2].capitalize() if len(parts) >= 2 else 'Other'

def ensure_schema(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for table, columns in EXTRA_COLUMNS.items():
        existing = [row[1] for row in cursor.execute(f'PRAGMA table_info("{table}")')]
        if not existing:
            continue  # table not created yet - main.py creates it on first scrape
        for column, col_type in columns.items():
            if column not in existing:
                cursor.execute(f'ALTER TABLE "{table}" ADD COLUMN {column} {col_type}')
                print(f"Added {column} column to {table} table")
        if 'source' in columns:
            rows = cursor.execute(f'SELECT id, job_url FROM "{table}" WHERE source IS NULL OR source = ""').fetchall()
            cursor.executemany(f'UPDATE "{table}" SET source = ? WHERE id = ?',
                               [(source_from_url(url), job_id) for job_id, url in rows])
            if rows:
                print(f"Backfilled source for {len(rows)} rows in {table}")
    conn.commit()
    conn.close()
