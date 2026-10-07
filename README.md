# Job Scraper & Application Tracker

A personal, self-hosted job-hunting tool. It **collects job postings** from several job boards into one local SQLite database, **filters out the noise** with your own keyword rules, and gives you a small **web app** to read postings, track your applications, and generate an AI-tailored resume or cover letter for any job.

Everything runs on your own machine. Your data stays in a local SQLite file.

![Jobs page](./screenshot/screenshot1.png)

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [How it fits together](#how-it-fits-together)
- [Quick start](#quick-start)
- [Configuration](#configuration)
  - [Secrets in `.env`](#secrets-in-env)
  - [`config.json` reference](#configjson-reference)
- [Getting jobs in](#getting-jobs-in)
  - [1. Scraper (`main.py`)](#1-scraper-mainpy)
  - [2. Hand-picked links (`add_jobs.py`)](#2-hand-picked-links-add_jobspy)
  - [3. Add job page (link / screenshot / pasted text)](#3-add-job-page-link--screenshot--pasted-text)
  - [4. "Save to tracker" bookmarklet](#4-save-to-tracker-bookmarklet)
  - [5. Job-alert emails (`email_alerts.py`)](#5-job-alert-emails-email_alertspy)
- [Using the web app](#using-the-web-app)
- [Database](#database)
- [Running it on a schedule](#running-it-on-a-schedule)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Legal & ethics](#legal--ethics)
- [Roadmap](#roadmap)
- [License](#license)

---

## Why this exists

Job boards show you the same postings again and again, mix in sponsored and irrelevant results, and sort by what *they* think is relevant. This tool:

- pulls from many sources into **one list, newest first**;
- **removes duplicates** (same title + company, or same URL) across sources and across runs;
- **drops irrelevant jobs** by title, description, company, and language, using rules you write once;
- **remembers** what you've applied to, interviewed for, been rejected from, or hidden.

---

## Features

| Area | What you get |
|---|---|
| **Sources** | RemoteOK, Arbeitnow, XING, any company's Greenhouse or Lever board, and (opt-in) LinkedIn |
| **Filtering** | Include/exclude title words, exclude description words, exclude companies, keep only chosen languages, max posting age |
| **Manual intake** | Add a job from a link, a **screenshot** (read by a vision model), or pasted text |
| **Browser bookmarklet** | One click on any job page saves it, even on sites that block scrapers |
| **Email alerts** | Reads LinkedIn/StepStone/Indeed/Glassdoor/... alert emails from Gmail and saves the jobs in them |
| **Tracking** | Applied / Interview / Rejected / Hidden flags, applied date, Applications dashboard with tallies |
| **AI writing** | Tailored resume and two-pass cover letter per job, using any OpenAI-compatible API (OpenAI, OpenRouter, ...) |

---

## How it fits together

```
                ┌───────────────────────── sources ─────────────────────────┐
  main.py  ───► │ RemoteOK · Arbeitnow · XING · Greenhouse · Lever · LinkedIn │
                └─────────────────────────────┬─────────────────────────────┘
                                              │ dedupe + keyword/language filters
  add_jobs.py ───── links ──────────┐         ▼
  email_alerts.py ─ Gmail alerts ───┼──►  SQLite (data/my_database.db)
  Add job page / bookmarklet ───────┘      ├─ jobs            (kept)
                                           ├─ filtered_jobs   (rejected by filters, so they aren't re-scraped)
                                           └─ processed_emails
                                              │
                                              ▼
                                  app.py  (Flask, http://127.0.0.1:5099)
                                  Jobs · Applications · Add job · AI resume / cover letter
```

---

## Quick start

**Requirements:** Python 3.9+ and `pip`. (An OpenAI-compatible API key is only needed for the AI features, screenshots, pasted text, and email alerts.)

```bash
# 1. Get the code and create a virtual environment
git clone https://github.com/nidhicj/job-scraper-tracker.git
cd job-scraper-tracker
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your config files from the examples
cp config_example.json config.json
cp .env_example .env              # then fill in your keys
mkdir -p data                     # the SQLite file lives here

# 4. Edit config.json: your search words, filters, and sources (see below)

# 5. Run the scraper once to create and fill the database
python main.py

# 6. Start the web app
python app.py
```

Then open **http://127.0.0.1:5099**.

> Run `main.py` **before** `app.py` the first time. The scraper is what creates the `jobs` table.

`config.json`, `.env`, the database, and the CSV exports are all git-ignored, so your keys and job data never get committed.

---

## Configuration

Settings live in two files:

- **`.env`**: secrets and model choice. Never committed.
- **`config.json`**: everything else (sources, filters, paths). Also never committed; `config_example.json` is the template.

Any value set in `.env` **overrides** the matching value in `config.json`.

### Secrets in `.env`

```dotenv
# Any OpenAI-compatible provider. This example uses OpenRouter.
OPENAI_API_KEY=sk-or-v1-...
OPENAI_MODEL=openai/gpt-4o-mini
OPENAI_BASE_URL=https://openrouter.ai/api/v1

# Optional: a model that can read images (for screenshots). Defaults to OPENAI_MODEL.
VISION_MODEL=openai/gpt-4o-mini

# Gmail job alerts. Use a 16-character app password from
# https://myaccount.google.com/apppasswords, NOT your normal Gmail password.
EMAIL_ALERTS_USERNAME=you@gmail.com
EMAIL_ALERTS_APP_PASSWORD=abcdefghijklmnop
```

Format: `NAME=value`, no quotes, no spaces around `=`. Leave a line out to fall back to `config.json`.

| `.env` variable | Overrides `config.json` key |
|---|---|
| `OPENAI_API_KEY` | `OpenAI_API_KEY` |
| `OPENAI_MODEL` | `OpenAI_Model` |
| `OPENAI_BASE_URL` | `OpenAI_Base_URL` |
| `VISION_MODEL` | `Vision_Model` |
| `EMAIL_ALERTS_USERNAME` | `email_alerts.username` |
| `EMAIL_ALERTS_APP_PASSWORD` | `email_alerts.app_password` |

To use OpenAI directly, leave `OPENAI_BASE_URL` empty and use an OpenAI model name such as `gpt-4o-mini`.

### `config.json` reference

#### Sources

```json
"job_sources": {
  "linkedin": false,
  "remoteok": true,
  "arbeitnow": true,
  "xing": true,
  "greenhouse_boards": ["stripe", "robinhood"],
  "lever_boards": ["leverdemo"]
},
"xing": { "max_pages": 50, "delay_seconds": 2 }
```

| Key | What it does |
|---|---|
| `remoteok` | Public RemoteOK JSON feed (remote jobs worldwide). No auth, no proxy. |
| `arbeitnow` | Public Arbeitnow feed (mostly Germany/EU). Paginated; reads up to `pages_to_scrape` pages. |
| `xing` | XING has no public API. The scraper reads XING's job **sitemap**, keeps URLs whose slug matches `title_include` (and not `title_exclude`), then reads the structured job data on each page. Only the sitemap and job pages are fetched, both allowed by XING's `robots.txt`. |
| `xing.max_pages` | Max XING job pages to fetch per run (newest first). |
| `xing.delay_seconds` | Pause between XING requests. Keep it polite. |
| `greenhouse_boards` | Company slugs from `boards.greenhouse.io/<slug>`. |
| `lever_boards` | Company slugs from `jobs.lever.co/<slug>`. |
| `linkedin` | The original LinkedIn guest-search scraper. **Off by default.** It goes against LinkedIn's terms and needs a proxy to avoid blocks. See [Legal & ethics](#legal--ethics). |

All sources go through the same deduplication and filters.

#### Filters

| Key | Type | Meaning |
|---|---|---|
| `title_include` | list | Keep a job **only if** its title contains at least one of these words. Empty list = no title requirement. |
| `title_exclude` | list | Drop a job if its title contains **any** of these words. |
| `desc_words` | list | Drop a job if its description contains **any** of these words/phrases. |
| `company_exclude` | list | Drop jobs from companies whose name contains any of these. |
| `languages` | list | Keep only descriptions in these languages (auto-detected), e.g. `["en"]`, `["en", "de"]`. Empty = any language. |
| `days_to_scrape` | int | Ignore postings older than this many days. |

All matches are case-insensitive substring matches. Short words can over-match: `"BE"` in `title_include` also matches "Be**be**rt" or "Mem**be**r". Prefer full words.

#### LinkedIn-only settings

Only used when `job_sources.linkedin` is `true`.

| Key | Meaning |
|---|---|
| `search_queries` | List of `{ "keywords": "...", "location": "...", "f_WT": "" }`. `f_WT`: `0` onsite, `1` hybrid, `2` remote, `""` any. |
| `timespan` | Posting age filter: `"r"` + seconds. `r86400` = 24 h, `r604800` = 7 days. |
| `pages_to_scrape` | Result pages per query (25 jobs each). Also caps Arbeitnow pages. |
| `rounds` | Run every query this many times. LinkedIn returns slightly different results each time. |
| `proxies` | `requests`-style proxy dict, e.g. `{"http": "socks5://...", "https": "socks5://..."}`. Test it with `python tests/test_proxy_connection.py`. |
| `headers` | Extra HTTP headers, mainly `User-Agent`. |

#### Storage, AI, and email

| Key | Meaning |
|---|---|
| `db_path` | SQLite file path. Default `./data/my_database.db`. |
| `jobs_tablename` | Table for kept jobs. Default `jobs`. The web app expects `jobs`. |
| `filtered_jobs_tablename` | Table for jobs your filters rejected, so they're never re-fetched. Default `filtered_jobs`. |
| `resume_path` | Full path to your resume **PDF**, used by the AI resume/cover-letter buttons. A single-column layout without images parses best. |
| `OpenAI_API_KEY`, `OpenAI_Model`, `OpenAI_Base_URL`, `Vision_Model` | Better set in `.env` (see above). |
| `email_alerts.imap_host` | IMAP server. Default `imap.gmail.com`. |
| `email_alerts.folder` | Mailbox folder to scan. Default `INBOX`. |
| `email_alerts.days` | How many days back to look on each run. Default `2`. |
| `email_alerts.senders` | Sender addresses or fragments that identify alert emails, e.g. `"stepstone"`, `"jobalerts-noreply@linkedin.com"`. |

---

## Getting jobs in

There are five ways to add jobs. They all write to the same `jobs` table and all skip jobs that are already saved.

### 1. Scraper (`main.py`)

```bash
python main.py                  # uses config.json
python main.py other_config.json  # uses a different config
```

What one run does:

1. Fetches from every enabled source.
2. Removes duplicates (same title + company).
3. Applies title / company / language filters.
4. Drops jobs already in `jobs` or `filtered_jobs` (same URL, or same title + company + date).
5. Drops jobs older than `days_to_scrape`; fetches missing descriptions (LinkedIn only).
6. Applies `desc_words`. Survivors go to `jobs`; rejected ones go to `filtered_jobs`.
7. Also writes `linkedin_jobs.csv` and `linkedin_jobs_filtered.csv` for that run.

### 2. Hand-picked links (`add_jobs.py`)

For specific jobs you found yourself. Works with LinkedIn job links, XING, and any page that includes schema.org `JobPosting` data (Personio, Join, Greenhouse, Lever, and most company career sites).

```bash
python add_jobs.py https://www.linkedin.com/jobs/view/1234567890/ https://jobs.example.com/abc
python add_jobs.py < links.txt    # one URL per line
```

Your filters are **not** applied here, since you chose these jobs. Tracking parameters (`utm_*`, `trk`, ...) are stripped so the same job always has the same URL.

### 3. Add job page (link / screenshot / pasted text)

Open **Add job** in the web app (`/add_job`). Give it any of:

- **A link.** Read the same way as `add_jobs.py`. Many boards (StepStone, Indeed, Glassdoor) block this; if so, the page asks you for a screenshot or text.
- **A screenshot** of the posting. A vision model reads it (`VISION_MODEL`, or `OPENAI_MODEL` if not set).
- **Pasted text.** Select all on the job page, copy, paste. The model pulls out title, company, location, and description.

If you give a link *and* a screenshot or text, the screenshot/text is read and the link is stored as the job's URL. Tick **"I've applied"** to mark it applied right away.

### 4. "Save to tracker" bookmarklet

The easiest way to save jobs from sites that block scrapers. Your browser has already loaded the page, so nothing gets blocked.

1. Open the **Add job** page.
2. **Drag** the **Save to tracker** link to your bookmarks bar. (Clicking it does nothing on purpose.)
3. On any job posting, click the bookmark. A new tab confirms the save, with an **"I've applied to this"** button.

It uses the page's structured `JobPosting` data when there is any, and otherwise has the AI model read the visible text. The web app must be running at the address it was dragged from.

### 5. Job-alert emails (`email_alerts.py`)

Many boards that block scraping will still email you new jobs for a saved search. This script reads those alert emails from Gmail over IMAP, has the AI model list the jobs in each, and saves them.

```bash
python email_alerts.py              # last `email_alerts.days` days (default 2)
python email_alerts.py --days 14    # look further back, e.g. the first time
python email_alerts.py --dry-run    # show what would be saved, save nothing
```

- Setup: enable IMAP in Gmail, create an **app password**, and put it in `.env`.
- The inbox is opened **read-only**, so emails stay unread.
- Each email is processed only once (tracked in the `processed_emails` table).
- Alert emails have title, company, location, and link but no description. Where the site allows it, the full posting is fetched. Otherwise the job is saved without a description; open it later and use the bookmarklet to fill it in.

---

## Using the web app

```bash
python app.py     # http://127.0.0.1:5099
```

### Jobs (`/`)

All non-hidden jobs, newest first, with a sort toggle. Click a job to see its full description. Per job you can:

| Button | Effect |
|---|---|
| **Applied** | Toggles the applied flag and records today as the applied date |
| **Interview** / **Rejected** | Toggle those flags (cards are coloured by status) |
| **Hide** | Removes it from the list (still counted on Applications if you applied) |
| **Delete** | Removes the row from the database |
| **Resume** | AI rewrites your resume PDF for this job |
| **Cover letter** | AI drafts a cover letter, then a second pass refines it |

Every status button is a toggle, so a misclick can be undone. Generated resumes and cover letters are saved in the database with the job.

### Applications (`/applications`)

Every job you've applied to, interviewed for, or been rejected from, including hidden ones. Shows tally cards (Applied / Waiting / Interview / Rejected, plus totals scraped and hidden). Click a card to filter, filter by source, and change a job's outcome from the table. "Rejected" keeps the interview flag, so an interview that ended in rejection still shows that history.

### Add job (`/add_job`)

See [Add job page](#3-add-job-page-link--screenshot--pasted-text) and the [bookmarklet](#4-save-to-tracker-bookmarklet).

---

## Database

One SQLite file (default `data/my_database.db`). Open it with any SQLite tool, e.g. [DB Browser for SQLite](https://sqlitebrowser.org/) or `sqlite3 data/my_database.db`.

**`jobs`** (and `filtered_jobs`, same shape minus the AI columns):

| Column | Notes |
|---|---|
| `id` | Primary key |
| `title`, `company`, `location` | |
| `date` | Posting date, `YYYY-MM-DD` |
| `job_url` | Canonical link, tracking parameters removed |
| `job_description` | Plain text |
| `source` | `LinkedIn`, `RemoteOK`, `Arbeitnow`, `XING`, `Greenhouse`, `Lever`, `Personio`, `Join`, `Manual`, or the site's name |
| `applied`, `interview`, `rejected`, `hidden` | `0` / `1` |
| `applied_date` | Set when you mark it applied |
| `date_loaded` | When it entered the database |
| `resume`, `cover_letter` | AI output, if generated |

New columns are added automatically: `db_schema.py` runs on every start of `main.py` and `app.py` and upgrades older databases in place (including backfilling `source` from the URL). **Back up the `.db` file** before experimenting; it's your whole application history.

---

## Running it on a schedule

Example crontab (`crontab -e`): scrape every 2 hours in the daytime, read alert emails twice a day.

```cron
0 8-20/2 * * *  cd /path/to/LinkedIn_Scraper && ./venv/bin/python main.py         >> scrape.log 2>&1
30 9,18  * * *  cd /path/to/LinkedIn_Scraper && ./venv/bin/python email_alerts.py >> email.log  2>&1
```

The `cd` matters: scripts look for `config.json` and `.env` in the current directory.

---

## Project layout

```
.
├── main.py               # Scraper: all sources, filtering, dedupe, DB writes
├── app.py                # Flask web app (Jobs, Applications, Add job, AI endpoints)
├── add_jobs.py           # CLI: save specific job links
├── job_intake.py         # Shared intake logic: link / screenshot / text / bookmarklet -> job
├── email_alerts.py       # CLI: save jobs from Gmail job-alert emails
├── db_schema.py          # Adds missing columns, infers `source` from URLs
├── config_example.json   # Template for config.json
├── .env_example          # Template for .env
├── requirements.txt
├── templates/            # jobs.html, applications.html, add_job.html, capture_result.html, ...
├── static/job_actions.js # Jobs page buttons, sorting, detail panel
├── tests/test_proxy_connection.py  # Checks your proxy changes your IP
├── screenshot/           # Images used in this README
└── data/                 # SQLite database (git-ignored)
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `no such table: jobs` when opening the app | Run `python main.py` once first. |
| `FileNotFoundError: config.json` | `cp config_example.json config.json`, and run commands from the project folder. |
| `OpenAI_API_KEY is empty` | Set `OPENAI_API_KEY` in `.env`. |
| `openai` errors such as `ChatCompletion` not found | The code uses the legacy client. Install the pinned version: `pip install openai==0.28.1`. |
| Screenshot intake fails or returns nonsense | Your model can't read images. Set `VISION_MODEL` to one that can (e.g. `openai/gpt-4o-mini`). |
| A link "couldn't be read" | The site blocks automated access or builds the page with JavaScript. Use the bookmarklet, a screenshot, or pasted text. |
| Scraper finds 0 jobs | Your filters are probably too strict. Loosen `title_include`, `desc_words`, `languages`, or `days_to_scrape`. |
| LinkedIn returns empty pages / HTTP 429 | You're rate-limited. Use a proxy, lower `pages_to_scrape`/`rounds`, or rely on the other sources. |
| Gmail login fails | Use an app password (needs 2-step verification), and make sure IMAP is enabled in Gmail settings. |
| Resume/cover letter is poor | Use a single-column PDF without images, and a stronger model. |

---

## Legal & ethics

- **LinkedIn** prohibits scraping in its User Agreement. The LinkedIn source is **off by default**; enabling it is at your own risk and may get your IP or account restricted.
- The other sources are public APIs (RemoteOK, Arbeitnow, Greenhouse, Lever) or pages allowed by the site's `robots.txt` (XING sitemap and job pages). Keep request rates low.
- Check that sites' terms allow what you do, and use this for **personal job searching** only, not to republish listings.
- AI-generated resumes and cover letters can be wrong. **Read and edit them** before sending; never claim experience you don't have.

---

## Roadmap

- [ ] Configure searches and run the scraper from the web UI
- [ ] Sort by date added to the database (some postings show up days after they were posted)
- [ ] Notes and interview dates per application
- [ ] Export applications to CSV from the UI

Contributions welcome. For bigger changes, open an issue first to discuss.

---

## License

MIT
