#!/usr/bin/env python3
# ============================================
# GDPR ENFORCEMENT DATABASE — PIPELINE
# File: pharos.py
#
# Builds gdpr.db (SQLite) from two public sources, both CC BY-NC-SA 4.0:
#   - enforcementtracker.com, provided by CMS
#   - GDPRhub (noyb)
#
# Standard library only — nothing to install.
#
# Usage:
#   python pharos.py migrate                 one-off: import gdpr_database.json, keeping your IDs
#   python pharos.py fix-ids                 one-off: re-number IDs whose year/country is wrong (history kept)
#   python pharos.py cms                     all Enforcement Tracker cases (a single page download)
#   python pharos.py cms-summaries [--limit N]  summaries from each case page, politely, newest first
#   python pharos.py gdprhub                 GDPRhub decisions via its public API
#   python pharos.py link                    link the same decision across the two sources
#   python pharos.py normalise               articles, sectors, violation categories, outcomes
#   python pharos.py export                  exports/cases.csv, cases.json, stats.json
#   python pharos.py stats                   quick overview
#   python pharos.py update                  cms + gdprhub + link + normalise + export
# ============================================

import argparse
import csv
import html
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(HERE, 'gdpr.db')
SCHEMA_FILE = os.path.join(HERE, 'schema.sql')
LEGACY_FILE = os.path.join(HERE, 'gdpr_database.json')
EXPORT_DIR = os.path.join(HERE, 'exports')
# The Pharos website reads a compact copy of the database from its data/ folder
REVIEW_DIR = os.path.join(HERE, 'review')
GROUPS_FILE = os.path.join(HERE, 'organisations.csv')
SAME_FILE = os.path.join(HERE, 'same_company.csv')
PARTY_DESC_FILE = os.path.join(HERE, 'party_descriptions.csv')  # who an unnamed decision is about, in the source's words
SITE_DATA_DIR = os.path.normpath(os.path.join(HERE, '..', 'site', 'data'))
SUMMARY_SHARD = 250  # summaries are split into files of this many cases, loaded only when a case is opened

USER_AGENT = 'PharosPrivacy/1.0 (+https://pharosprivacy.com; angelillolorenzo@gmail.com)'
CMS_URL = 'https://www.enforcementtracker.com/'
GDPRHUB_API = 'https://gdprhub.eu/api.php'

ATTRIBUTION = {
    'cms_tracker': 'enforcementtracker.com, provided by CMS (CC BY-NC-SA 4.0)',
    'gdprhub': 'GDPRhub, noyb (CC BY-NC-SA 4.0)',
}

# ----- COUNTRIES -----
COUNTRIES = {
    'AT': 'Austria', 'BE': 'Belgium', 'BG': 'Bulgaria', 'HR': 'Croatia', 'CY': 'Cyprus',
    'CZ': 'Czech Republic', 'DK': 'Denmark', 'EE': 'Estonia', 'FI': 'Finland', 'FR': 'France',
    'DE': 'Germany', 'GR': 'Greece', 'HU': 'Hungary', 'IS': 'Iceland', 'IE': 'Ireland',
    'IT': 'Italy', 'LV': 'Latvia', 'LI': 'Liechtenstein', 'LT': 'Lithuania', 'LU': 'Luxembourg',
    'MT': 'Malta', 'NL': 'Netherlands', 'NO': 'Norway', 'PL': 'Poland', 'PT': 'Portugal',
    'RO': 'Romania', 'SK': 'Slovakia', 'SI': 'Slovenia', 'ES': 'Spain', 'SE': 'Sweden',
    'GB': 'United Kingdom', 'IM': 'Isle of Man', 'CH': 'Switzerland', 'EU': 'European Union',
}
COUNTRY_ALIASES = {
    'czechia': 'CZ', 'the netherlands': 'NL', 'uk': 'GB', 'great britain': 'GB', 'england': 'GB',
    'el': 'GR', 'deutschland': 'DE', 'österreich': 'AT', 'italia': 'IT', 'españa': 'ES',
    'european data protection board': 'EU', 'edpb': 'EU', 'edps': 'EU',
}
_NAME_TO_CODE = {v.lower(): k for k, v in COUNTRIES.items()}


def country_code(name):
    if not name:
        return None
    key = re.sub(r'[\(\[].*', '', name).strip().lower()
    if key.upper() in COUNTRIES:
        return key.upper()
    return _NAME_TO_CODE.get(key) or COUNTRY_ALIASES.get(key)


def country_name(name):
    code = country_code(name)
    if code:
        return COUNTRIES[code]
    return name.strip().title() if name else None


# ----- SMALL HELPERS -----
def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def fetch(url, retries=3, timeout=60):
    """GET with a clear User-Agent and simple retry/backoff."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', errors='replace')
        except Exception as e:  # network errors, 5xx
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f'Failed to fetch {url}: {last}')


def parse_date(raw):
    """Returns (decision_date, precision). Never invents a day or a year."""
    date, precision = _parse_date(raw)
    if date and date[:len(date)] > datetime.now().strftime('%Y-%m-%d')[:len(date)]:
        return None, 'unknown'  # a decision can't be in the future: source typo
    return date, precision


def _parse_date(raw):
    if not raw:
        return None, 'unknown'
    s = str(raw).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', s):
        return s, 'day'
    m = re.fullmatch(r'(\d{1,2})[./](\d{1,2})[./](\d{4})', s)
    if m:
        return f'{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}', 'day'
    if re.fullmatch(r'\d{4}-\d{2}', s):
        return s, 'month'
    if re.fullmatch(r'\d{4}', s):
        return s, 'year'
    return None, 'unknown'


def parse_amount(raw):
    """A single clean amount -> int euro. Free text ('200,000 + 150,000', 'Reduced from 24,000 to 22,000')
    -> None: it stays readable in fine_original but is kept out of totals rather than guessed."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw) if raw > 0 else None
    s = re.sub(r"(?i)[\s'\u00a0\u202f€]|eur(o|os)?", '', str(raw))
    m = re.fullmatch(r'(\d{1,3}(?:([.,])\d{3})(?:\2\d{3})*|\d+)(?:[.,]\d{1,2})?', s)
    if not m:
        return None
    n = int(re.sub(r'[.,]', '', m.group(1)))
    return n if n > 0 else None


def clean(s):
    if s is None:
        return None
    s = re.sub(r'\s+', ' ', html.unescape(str(s))).strip()
    return s or None


# ----- DATABASE -----
def connect():
    db = sqlite3.connect(DB_FILE)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    with open(SCHEMA_FILE, encoding='utf-8') as f:
        db.executescript(f.read())
    return db


class Run:
    """Audit-log context manager: every step records what it did, even when it fails."""

    def __init__(self, db, step):
        self.db, self.step = db, step
        self.fetched = self.inserted = self.updated = 0
        self.notes = []

    def __enter__(self):
        cur = self.db.execute('INSERT INTO runs (step, started_at) VALUES (?, ?)', (self.step, now()))
        self.run_id = cur.lastrowid
        self.db.commit()
        return self

    def __exit__(self, exc_type, exc, tb):
        status = 'failed' if exc else 'ok'
        if exc:
            self.notes.append(f'{exc_type.__name__}: {exc}')
            self.db.rollback()
        self.db.execute(
            'UPDATE runs SET finished_at=?, fetched=?, inserted=?, updated=?, status=?, notes=? WHERE run_id=?',
            (now(), self.fetched, self.inserted, self.updated, status, '; '.join(self.notes) or None, self.run_id))
        self.db.commit()
        print(f'  [{self.step}] {status}: fetched {self.fetched}, inserted {self.inserted}, updated {self.updated}'
              + (f' — {"; ".join(self.notes)}' if self.notes else ''))
        return False


UPSERT_FIELDS = ['source', 'country', 'country_code', 'authority', 'decision_date', 'date_precision',
                 'fine_eur', 'fine_original', 'currency', 'controller', 'sector', 'articles_raw',
                 'violation_type', 'outcome', 'summary', 'source_url', 'source_page', 'attribution']


def next_pharos_id(db, decision_date, code):
    """Your ID scheme: YEAR/CC/NNN. 'ND' when the source gives no year (never guess the current year)."""
    year = decision_date[:4] if decision_date else 'ND'
    prefix = f'{year}/{code or "XX"}/'
    # retired IDs (id_history) count too: an old link must never point to a different decision
    row = db.execute("""SELECT MAX(n) FROM (
                            SELECT CAST(substr(pharos_id, ?) AS INTEGER) n FROM cases WHERE pharos_id LIKE ?
                            UNION ALL SELECT CAST(substr(old_id, ?) AS INTEGER) FROM id_history WHERE old_id LIKE ?)""",
                     (len(prefix) + 1, prefix + '%', len(prefix) + 1, prefix + '%')).fetchone()
    return f'{prefix}{(row[0] or 0) + 1:03d}'


def checked_date(db, case_id):
    """(date, precision) Lorenzo found for a decision whose source gives no date (review/currency.xlsx), or None."""
    row = db.execute('SELECT decision_date FROM amount_checks WHERE case_id = ? AND decision_date IS NOT NULL',
                     (case_id,)).fetchone()
    date, precision = parse_date(row[0]) if row else (None, 'unknown')
    return (date, precision) if date else None


def apply_checked_dates(db):
    """A checked date fills in a missing source date; a real source date always wins. An undated decision
    that gets a year also gets a new ID with that year; the old ND/ ID stays in id_history (old links still work)."""
    for r in db.execute("""SELECT c.case_id, c.pharos_id, c.country_code FROM cases c JOIN amount_checks a USING (case_id)
                           WHERE a.decision_date IS NOT NULL AND c.decision_date IS NULL""").fetchall():
        found = checked_date(db, r['case_id'])
        if not found:
            continue
        db.execute('UPDATE cases SET decision_date = ?, date_precision = ?, updated_at = ? WHERE case_id = ?',
                   (*found, now(), r['case_id']))
        if (r['pharos_id'] or '').startswith('ND/'):
            new_id = next_pharos_id(db, found[0], r['country_code'])
            db.execute('INSERT INTO id_history VALUES (?,?,?,?,?)',
                       (r['case_id'], r['pharos_id'], new_id, 'date found in the review (source gives none)', now()))
            db.execute('UPDATE cases SET pharos_id = ? WHERE case_id = ?', (new_id, r['case_id']))
            print(f"    {r['pharos_id']:>12}  ->  {new_id}")


def upsert(db, run, rec):
    """Insert a new case or update a changed one. Never overwrites a value with an empty one."""
    ts = now()
    old = db.execute('SELECT * FROM cases WHERE case_id = ?', (rec['case_id'],)).fetchone()
    if old is not None and not rec.get('decision_date') and checked_date(db, rec['case_id']):
        rec = rec | dict(zip(('decision_date', 'date_precision'), checked_date(db, rec['case_id'])))
    if old is None:
        rec = {k: rec.get(k) for k in UPSERT_FIELDS} | {'case_id': rec['case_id']}
        rec['pharos_id'] = next_pharos_id(db, rec['decision_date'], rec['country_code'])
        rec['first_seen'] = rec['last_seen'] = rec['updated_at'] = ts
        cols = ', '.join(rec)
        db.execute(f'INSERT INTO cases ({cols}) VALUES ({", ".join("?" * len(rec))})', list(rec.values()))
        run.inserted += 1
        return
    # The source is authoritative, so a corrected value (even an empty one) replaces a wrong one.
    # Summaries are the exception: CMS summaries come from a separate step and must survive a list refresh.
    changes = {k: rec.get(k) for k in UPSERT_FIELDS
               if k in rec and rec.get(k) != old[k] and not (k == 'summary' and rec.get(k) in (None, ''))}
    if changes:
        changes['updated_at'] = ts
        run.updated += 1
    changes['last_seen'] = ts
    sets = ', '.join(f'{k} = ?' for k in changes)
    db.execute(f'UPDATE cases SET {sets} WHERE case_id = ?', [*changes.values(), rec['case_id']])


# ----- STEP: MIGRATE (one-off) -----
def step_migrate(db):
    """Imports your February scrape so every existing case keeps its ID."""
    if not os.path.exists(LEGACY_FILE):
        print('  No gdpr_database.json to migrate.')
        return
    with Run(db, 'migrate') as run, open(LEGACY_FILE, encoding='utf-8') as f:
        legacy = json.load(f)
        run.fetched = len(legacy)
        ts = now()
        for r in legacy:
            if not r.get('etid') or db.execute('SELECT 1 FROM cases WHERE case_id=?', (r['etid'],)).fetchone():
                continue
            date, precision = parse_date(r.get('date'))
            code = country_code(r.get('country')) or r.get('country_code')
            db.execute(
                '''INSERT INTO cases (case_id, pharos_id, source, country, country_code, decision_date, date_precision,
                   fine_eur, currency, controller, articles_raw, violation_type, source_url, attribution,
                   first_seen, last_seen, updated_at)
                   VALUES (?, ?, 'cms_tracker', ?, ?, ?, ?, ?, 'EUR', ?, ?, ?, ?, ?, ?, ?, ?)''',
                (r['etid'], r.get('id'), country_name(r.get('country')), code, date, precision,
                 r.get('fine_eur') or None, clean(r.get('controller')), clean(r.get('gdpr_articles')),
                 clean(r.get('violation_type')), r.get('source_url') or None, ATTRIBUTION['cms_tracker'],
                 r.get('date_added') or ts, r.get('date_added') or ts, ts))
            run.inserted += 1
        # IDs built from a guessed year (the old scraper used the current year when the date was unknown)
        guessed = db.execute("""SELECT COUNT(*) FROM cases WHERE date_precision='unknown'
                                AND pharos_id NOT LIKE 'ND/%'""").fetchone()[0]
        if guessed:
            run.notes.append(f'{guessed} IDs carry a guessed year (date unknown) — see README')


# ----- STEP: FIX IDS (one-off, run only before IDs are published) -----
def step_fix_ids(db):
    """Re-issues IDs whose year or country contradicts the case (February scrape: year guessed as 2026
    for undated cases; 'XX' where the country wasn't recognised). Old IDs are kept in id_history."""
    with Run(db, 'fix-ids') as run:
        for r in db.execute('SELECT case_id, pharos_id, decision_date, country_code FROM cases').fetchall():
            year, cc = (r['decision_date'] or '')[:4] or 'ND', r['country_code'] or 'XX'
            parts = (r['pharos_id'] or '').split('/')
            if len(parts) == 3 and parts[0] == year and parts[1] == cc:
                continue
            new_id = next_pharos_id(db, r['decision_date'], r['country_code'])
            reason = 'year' if parts[:1] != [year] else 'country'
            if len(parts) == 3 and parts[0] != year and parts[1] != cc:
                reason = 'year and country'
            db.execute('INSERT INTO id_history VALUES (?,?,?,?,?)',
                       (r['case_id'], r['pharos_id'], new_id, f'{reason} did not match the case', now()))
            db.execute('UPDATE cases SET pharos_id=? WHERE case_id=?', (new_id, r['case_id']))
            run.updated += 1
            print(f"    {r['pharos_id']:>14}  ->  {new_id}")
        db.commit()


# ----- STEP: CMS ENFORCEMENT TRACKER -----
def step_cms(db):
    """The tracker embeds every case as JSON in its homepage: one request, no browser automation."""
    with Run(db, 'cms') as run:
        page = fetch(CMS_URL)
        m = re.search(r'<script[^>]*id="et-cases"[^>]*>(.*?)</script>', page, re.S)
        if not m:
            raise RuntimeError('Case data not found on enforcementtracker.com — the page layout may have changed')
        rows = json.loads(m.group(1))
        run.fetched = len(rows)
        checks = {k: v for k, v in db.execute('SELECT case_id, controller FROM party_checks')}  # Lorenzo's answers win
        for r in rows:
            date, precision = parse_date(r.get('d'))
            name = r.get('C') or r.get('c')
            case_id = f"ETid-{r['e']}"
            upsert(db, run, {
                'case_id': f"ETid-{r['e']}",
                'source': 'cms_tracker',
                'country': country_name(name),
                'country_code': country_code(name),
                'authority': clean(r.get('a')),
                'decision_date': date,
                'date_precision': precision,
                'fine_eur': parse_amount(r.get('f')),
                'currency': 'EUR',
                'controller': shown_name(checks[case_id]) if case_id in checks
                              else cms_name(r.get('p'))[0],  # see CMS NAMES; source text kept in case_parties
                'sector': clean(r.get('s')),
                'articles_raw': clean(r.get('r')),
                'violation_type': clean(r.get('t')),
                'source_url': r.get('u') or None,
                'source_page': f"{CMS_URL}ETid-{r['e']}",
                'attribution': ATTRIBUTION['cms_tracker'],
            })
            if clean(r.get('p')):
                save_cms_party(db, f"ETid-{r['e']}", r.get('p'))
        db.commit()


def step_cms_summaries(db, limit, delay=1.5):
    """Case summaries live on each case page. Fetched slowly, newest first, only when missing."""
    todo = db.execute("""SELECT case_id, source_page FROM cases
                         WHERE source='cms_tracker' AND (summary IS NULL OR summary='')
                         ORDER BY CAST(substr(case_id, 6) AS INTEGER) DESC LIMIT ?""", (limit,)).fetchall()
    with Run(db, 'cms-summaries') as run:
        for i, row in enumerate(todo, 1):
            page = fetch(row['source_page'])
            run.fetched += 1
            m = re.search(r'>Summary</h3>\s*<p[^>]*>(.*?)</p>', page, re.S)
            text = clean(re.sub(r'<[^>]+>', ' ', m.group(1))) if m else None
            if text:
                db.execute('UPDATE cases SET summary=?, updated_at=? WHERE case_id=?', (text, now(), row['case_id']))
                run.updated += 1
            if i % 25 == 0:
                db.commit()
                print(f'    {i}/{len(todo)}')
            time.sleep(delay)
        db.commit()


# ----- STEP: GDPRHUB -----
def _hub(params):
    return json.loads(fetch(GDPRHUB_API + '?' + urllib.parse.urlencode({**params, 'format': 'json'})))


def _template(wikitext):
    m = re.search(r'\{\{DPAdecisionBOX(.*?)\n\}\}', wikitext, re.S | re.I)
    if not m:
        return None
    fields = {}
    for line in m.group(1).split('\n'):
        if line.startswith('|') and '=' in line:
            k, v = line[1:].split('=', 1)
            fields[k.strip()] = v.strip() or None
    return fields


def _wiki_to_text(t):
    t = re.sub(r'\[\[(?:[^\]|]+\|)?([^\]]+)\]\]', r'\1', t)
    t = re.sub(r'\{\{[^}]*\}\}', '', t)
    t = re.sub(r'\[\s*https?://\S+\s+([^\]]*)\]', r'\1', t)
    t = re.sub(r'\[\s*https?://[^\]]*\]', '', t)
    t = re.sub(r"'{2,3}", '', t)
    return clean(t)


def step_gdprhub(db, delay=0.7):
    with Run(db, 'gdprhub') as run:
        titles = []
        for year in range(2018, datetime.now().year + 1):
            cont = None
            while True:
                params = {'action': 'query', 'list': 'categorymembers', 'cmtitle': f'Category:{year}',
                          'cmlimit': 500, 'cmtype': 'page'}
                if cont:
                    params['cmcontinue'] = cont
                j = _hub(params)
                titles += [m['title'] for m in j.get('query', {}).get('categorymembers', [])]
                cont = j.get('continue', {}).get('cmcontinue')
                time.sleep(delay)
                if not cont:
                    break
        titles = list(dict.fromkeys(titles))  # a page can sit in several year categories
        print(f'    {len(titles)} GDPRhub pages to read')

        for i in range(0, len(titles), 50):
            j = _hub({'action': 'query', 'titles': '|'.join(titles[i:i + 50]), 'prop': 'revisions',
                      'rvprop': 'content', 'formatversion': 2})
            for page in j.get('query', {}).get('pages', []):
                text = (page.get('revisions') or [{}])[0].get('content')
                f = _template(text) if text else None
                if not f:
                    continue  # court judgments etc. use a different template
                run.fetched += 1
                currency = (f.get('Currency') or 'EUR').upper()
                currency = CURRENCY_SYMBOLS.get(currency, currency)
                date, precision = parse_date(f.get('Date_Decided'))
                arts = [f[k] for k in (f'GDPR_Article_{n}' for n in range(1, 31)) if f.get(k)]
                body = re.sub(r'\{\{DPAdecisionBOX.*?\n\}\}', '', text, flags=re.S | re.I)
                summary = _wiki_to_text(re.split(r'^==', body, flags=re.M)[0])
                src = next((f[k] for k in (f'Original_Source_Link_{n}' for n in range(1, 6))
                            if (f.get(k) or '').startswith('http')), None)
                names, links = _party_fields(f)
                authority = clean(f.get('DPA_With_Country') or f.get('DPA_Abbrevation'))
                checked = db.execute('SELECT controller FROM party_checks WHERE case_id = ?', (page['title'],)).fetchone()
                controller = shown_name(checked['controller']) if checked else resolve_controller(party_roles(page['title'], authority, names))[0]
                upsert(db, run, {
                    'case_id': page['title'],
                    'source': 'gdprhub',
                    'country': country_name(f.get('Jurisdiction')),
                    'country_code': country_code(f.get('Jurisdiction')),
                    'authority': authority,
                    'decision_date': date,
                    'date_precision': precision,
                    'fine_eur': parse_amount(f.get('Fine')) if currency == 'EUR' else None,
                    'fine_original': f.get('Fine'),
                    'currency': currency if f.get('Fine') else None,
                    'controller': controller,  # see PARTIES: the first party is often the complainant
                    'articles_raw': '; '.join(arts) or None,
                    'violation_type': None,  # GDPRhub's 'Type' is the procedure (Complaint / Own initiative), not the violation
                    'outcome': clean(f.get('Outcome')),
                    'summary': summary if summary and len(summary) > 20 else None,
                    'source_url': src,
                    'source_page': 'https://gdprhub.eu/index.php?title=' + urllib.parse.quote(page['title'].replace(' ', '_')),
                    'attribution': ATTRIBUTION['gdprhub'],
                })
                save_parties(db, page['title'], page['title'], authority, names, links)
            db.commit()
            print(f'    {min(i + 50, len(titles))}/{len(titles)}')
            time.sleep(delay)


# ----- ORGANISATIONS -----
# Level 1, same company: names are compared without capitals, accents, punctuation, notes in brackets and
# differences in how the company form is written ('S.A.' = 'SA'). Different company forms are never merged.
# A name without a company form ('Vodafone') only matches within the same country, and a name that only
# describes a party ('Company', 'Private individual') is never grouped.
# Level 2, same group: organisations.csv, written by hand, because a name cannot prove who owns whom
# (WhatsApp belongs to Meta, LinkedIn to Microsoft). Only groups marked 'ready' reach the site.
_GENERIC_WORDS = set("""
a an the of and for in on at to de del la el los las di da do du des der die das und et
private public individual individuals person persons people natural legal entity entities company companies firm business
undertaking organisation organization controller processor data subject owner owners officer officers police employee employees
employer physician doctor doctors dentist dental pharmacy pharmacist hospital clinic medical health healthcare care centre center
office practice homeowners homeowner housing property community association associations club union federation party political
candidate candidates parliamentary elections election mayor municipality municipal city town council local authority
website websites operator operators online shop shops store stores retailer retail supermarket restaurant restaurants bar bars hotel hotels
cafe gym school schools university teacher student students kindergarten bank banks insurance insurer insurers telecommunications
telecommunication telecom telecoms provider providers service services media newspaper publisher broadcaster agency agencies
attorney lawyer law notary bailiff accountant consultant consultancy sole trader traders self employed entrepreneur
covid 19 test testing betting place places gaming casino job jobcenter jobcentre landlord tenant tenants building
construction real estate agent agents car dealer dealership garage taxi transport transportation logistics energy electricity
gas water utility utilities supplier suppliers debt collection collecting collector collectors marketing advertising call
department ministry government state regional region national federal court church religious foundation charity non profit
nonprofit ngo sports sport fitness security guard guards camera cameras video surveillance cctv app application platform social
network networks commerce ecommerce mail order delivery courier postal post family member members former ex staff manager
director managing head chief citizen citizens resident residents neighbour neighbor neighbours neighbors unknown unnamed anonymous
redacted s
""".split())
_FORMS = {'sa', 'sau', 'sl', 'slu', 'slp', 'spa', 'srl', 'srls', 'sas', 'sarl', 'ltd', 'inc', 'llc', 'gmbh', 'ag', 'kg', 'kgaa',
          'bv', 'nv', 'plc', 'ab', 'as', 'asa', 'aps', 'oy', 'oyj', 'kft', 'zrt', 'nyrt', 'spzoo', 'sro', 'doo', 'dd', 'ehf', 'hf',
          'uc', 'dac', 'se', 'ae', 'oe', 'ike', 'epe', 'sc', 'scs', 'scc', 'sca', 'scrl', 'cv', 'ug', 'ou', 'ad', 'eood', 'ood',
          'sia', 'uab', 'corp', 'lda', 'sgps', 'snc', 'sprl', 'bvba', 'cvba', 'efc', 'sccl', 'aeie', 'smsa'}
_SYN = {'limited': 'ltd', 'incorporated': 'inc', 'corporation': 'corp'}


def _org_tokens(name):
    s = re.sub(r'\([^)]*\)', ' ', name or '')                      # notes in brackets: '(Facebook)', '(ARPAC)'
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode().lower().replace('&', ' and ')
    words, out, run = re.sub(r'[^a-z0-9]+', ' ', s).split(), [], []
    for w in words + ['']:                                          # 's a u' -> 'sau'
        if len(w) == 1:
            run.append(w)
            continue
        if run:
            out.append(''.join(run))
            run = []
        if w:
            out.append(_SYN.get(w, w))
    out = ' '.join(out).replace('sp zoo', 'spzoo').split()
    if len(out) > 2 and out[0] == 'sc':                              # Romanian 'S.C. ... S.A.'
        out = out[1:]
    return out


def org_key(name, country):
    """The same key means the same company. None for a name that only describes the party."""
    t = _org_tokens(name)
    if not t or all(w in _GENERIC_WORDS or w.isdigit() for w in t):
        return None
    if any(w in _FORMS for w in t):
        return ' '.join(t)
    return f'{country or "??"}: ' + ' '.join(t)


def load_same():
    """{key: main key} for companies a person has said are the same, and the set of keys already decided."""
    alias, decided = {}, set()
    if os.path.exists(SAME_FILE):
        with open(SAME_FILE, encoding='utf-8-sig') as f:
            for r in csv.DictReader(f):
                keys = [k for k in (r.get('keys') or '').split(' || ') if k]
                decided.update(keys)
                if (r.get('decision') or '').strip().lower() == 'same' and len(keys) > 1:
                    for k in keys[1:]:
                        alias[k] = keys[0]
    return alias, decided


def load_groups():
    """[(group, compiled pattern, status, note)] from organisations.csv."""
    if not os.path.exists(GROUPS_FILE):
        return []
    with open(GROUPS_FILE, encoding='utf-8-sig') as f:
        return [(r['group'], re.compile(r['pattern'], re.I), r['status'].strip(), r['note'])
                for r in csv.DictReader(f) if (r.get('pattern') or '').strip()]


def org_group(name, groups):
    """The first group whose pattern matches the name (accents removed), or None."""
    plain = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().strip()
    for g, pat, status, note in groups:
        if pat.search(plain):
            return g, status
    return None, None


# ----- REVIEW FILES -----
# The files Lorenzo checks by hand are Excel files, so the status colours survive saving.
# Green = easy, orange = needs judgement, grey = nothing to do. Answer columns have a yellow header.
STATUS_FILL = (('to do (easy)', 'D9EAD3', '1E3A12'), ('to do', 'FCE5CD', '5A2E00'), ('', 'EEEEEE', '777777'))


def review_write(name, fields, rows, answer=(), widths=None, how_to=()):
    """Write review/<name>.xlsx: one row per item, coloured by its 'status', with a 'How to' sheet."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    os.makedirs(REVIEW_DIR, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = name
    ws.append(fields)
    for k, f in enumerate(fields, 1):
        cell = ws.cell(row=1, column=k)
        cell.font = Font(bold=True)
        cell.fill = PatternFill('solid', fgColor='FFE599' if f in answer else 'D9D2C5')
    for r in rows:
        ws.append([r.get(f, '') if r.get(f) is not None else '' for f in fields])
        st = str(r.get('status') or '')
        fill, ink = next((fl, ik) for prefix, fl, ik in STATUS_FILL if st.startswith(prefix))
        for k, f in enumerate(fields, 1):
            cell = ws.cell(row=ws.max_row, column=k)
            cell.alignment = Alignment(vertical='top', wrap_text=f in ('why', 'parties', 'names', 'note', 'gdprhub_summary'))
            if f in answer:
                cell.fill = PatternFill('solid', fgColor='FFFFFF')
                cell.font = Font(bold=True)
            else:
                cell.fill = PatternFill('solid', fgColor=fill)
                cell.font = Font(color=ink)
    for k, f in enumerate(fields, 1):
        ws.column_dimensions[ws.cell(row=1, column=k).column_letter].width = (widths or {}).get(f, 16)
    ws.freeze_panes = 'C2'
    ws.auto_filter.ref = ws.dimensions
    if how_to:
        h = wb.create_sheet('How to')
        for line in how_to:
            h.append([line])
        h.column_dimensions['A'].width = 120
    path = os.path.join(REVIEW_DIR, name + '.xlsx')
    wb.save(path)
    return path


def review_rows(name):
    """The rows of review/<name>.xlsx (or the older .csv), as dicts with text values."""
    xlsx, csv_path = (os.path.join(REVIEW_DIR, name + ext) for ext in ('.xlsx', '.csv'))
    if os.path.exists(xlsx):
        from openpyxl import load_workbook
        ws = load_workbook(xlsx, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = [str(h or '') for h in next(it)]
        return [{h: ('' if v is None else str(v)) for h, v in zip(head, row)} for row in it if any(v is not None for v in row)]
    with open(csv_path, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def review_has_answers(name, cols):
    """True if the review file already holds answers, so a new review run must not overwrite it."""
    if not any(os.path.exists(os.path.join(REVIEW_DIR, name + ext)) for ext in ('.xlsx', '.csv')):
        return False
    return any((r.get(c) or '').strip() for r in review_rows(name) for c in cols)


def step_organisations_review(db):
    """review/organisations.xlsx: companies that may be the same but are kept apart (same country, same name,
    different company form or a missing form), and the groups still marked 'to check'.
    Answer column: 'same' or 'different' for companies, 'ready' or 'no' for groups.
    Then run: python pharos.py organisations-apply"""
    os.makedirs(REVIEW_DIR, exist_ok=True)
    groups = load_groups()
    alias, decided = load_same()
    rows = db.execute('SELECT controller, country_code, COUNT(*) n FROM v_cases WHERE controller IS NOT NULL '
                      'GROUP BY controller, country_code').fetchall()
    by_stem = {}
    for r in rows:
        key = org_key(r['controller'], r['country_code'])
        if not key:
            continue
        key = alias.get(key, key)
        stem = ' '.join(w for w in _org_tokens(r['controller']) if w not in _FORMS)
        by_stem.setdefault((r['country_code'], stem), {}).setdefault(key, []).append((r['controller'], r['n']))
    out = []
    for (cc, stem), keys in sorted(by_stem.items()):
        if len(keys) > 1 and not all(k in decided for k in keys):
            ordered = sorted(keys.items(), key=lambda kv: -sum(n for _, n in kv[1]))   # most decisions first
            out.append({'kind': 'maybe the same company', 'country': cc,
                        'names': ' | '.join(f'{names[0][0]} ({sum(n for _, n in names)})' for _, names in ordered),
                        'answer': '', 'group': '', 'note': '', 'keys': ' || '.join(k for k, _ in ordered)})
    for g, pat, status, note in groups:
        if status == 'to check':
            members = sorted({(r['controller'], r['country_code']) for r in rows if pat.search(
                unicodedata.normalize('NFKD', r['controller']).encode('ascii', 'ignore').decode())})
            out.append({'kind': 'group to check', 'country': '', 'names': ' | '.join(f'{n} [{c}]' for n, c in members),
                        'answer': '', 'group': g, 'note': note, 'keys': ''})
    if review_has_answers('organisations', ['answer']):
        print('  review/organisations has answers not applied yet: run organisations-apply first.')
        return
    for r in out:
        r['status'] = 'to do (easy)' if r['kind'] == 'maybe the same company' else 'to do (your judgement)'
    path = review_write('organisations', ['status', 'kind', 'country', 'names', 'answer', 'group', 'note', 'keys'], out,
                        answer=('answer',), widths={'status': 22, 'kind': 22, 'names': 70, 'answer': 14, 'note': 50},
                        how_to=ORG_HOW_TO)
    print(f'  {len(out)} items to check, written to {path}')


def step_organisations_apply(db):
    """Save the answers from review/organisations.xlsx: companies into same_company.csv, groups into organisations.csv."""
    with Run(db, 'organisations-apply') as run:
        answers = [r for r in review_rows('organisations') if (r.get('answer') or '').strip()]
        run.fetched = len(answers)
        new_same = [r for r in answers if r['kind'] == 'maybe the same company'
                    and r['answer'].strip().lower() in ('same', 'different')]
        if new_same:
            exists = os.path.exists(SAME_FILE)
            with open(SAME_FILE, 'a', newline='', encoding='utf-8') as out:
                w = csv.DictWriter(out, fieldnames=['decision', 'country', 'names', 'keys', 'checked_at'])
                if not exists:
                    w.writeheader()
                for r in new_same:
                    w.writerow({'decision': r['answer'].strip().lower(), 'country': r['country'], 'names': r['names'],
                                'keys': r['keys'], 'checked_at': now()})
                    run.updated += 1
        verdict = {r['group']: r['answer'].strip().lower() for r in answers if r['kind'] == 'group to check'}
        if verdict:
            with open(GROUPS_FILE, encoding='utf-8-sig') as f2:
                rows = list(csv.DictReader(f2))
            for r in rows:
                v = verdict.get(r['group'])
                if v in ('ready', 'no'):
                    r['status'] = v
                    run.updated += 1
            with open(GROUPS_FILE, 'w', newline='', encoding='utf-8') as f2:
                w = csv.DictWriter(f2, fieldnames=['group', 'pattern', 'status', 'note'])
                w.writeheader()
                w.writerows(rows)
    print('  Run "python pharos.py export" to put the answers on the website.')

# ----- CMS NAMES -----
# CMS names the fined party, but sometimes as 'Unknown', as a description with the name in brackets
# ('Restaurant (SANTI 3000, S.L.)'), or with the amount stuck on. The source text is kept in case_parties;
# cases.controller gets the cleaned name. Nothing is rewritten unless the rule below is certain.
_LEGAL_FORM = re.compile(r"""\b(?:s\.?\s?l\.?\s?u?\.?|s\.?\s?a\.?\s?u?\.?|s\.?p\.?a\.?|s\.?r\.?l\.?s?\.?|s\.?a\.?s\.?|gmbh|ag|kg|ltd\.?|limited|
    plc|inc\.?|llc|b\.?v\.?|n\.?v\.?|a\.?s\.?|a/s|ab|oy|oyj|kft\.?|zrt\.?|sp\.?\s?z\s?o\.?\s?o\.?|s\.?r\.?o\.?|a\.?\s?s\.?|d\.?o\.?o\.?|
    e\.?\s?k\.?|sas|sarl|ehf\.?|hf\.?|s\.?c\.?)(?=[\s,.)]|$)""", re.I | re.X)
_CMS_UNKNOWN = re.compile(r'^(?:unknown(?:\s+(?:company|organisation|organization|controller))?|n/?a|not available)$', re.I)
_CMS_NO_NAME = re.compile(r'^(.*?)\s*(?:\(\s*name not (?:available|published|disclosed)[^)]*\)|-\s*no further details published)\s*$', re.I)
_CMS_AMOUNT = re.compile(r'\s*(?:(?:EUR|€)\s*[\d][\d.,\s]*|[\d][\d.,]{3,}\s*(?:EUR|€))\s*$', re.I)


def cms_name(raw):
    """(name for the site, description) from the CMS 'Controller/Processor' text."""
    s = clean(raw)
    if not s:
        return None, None
    s = _CMS_AMOUNT.sub('', s).strip()                     # 'H&M Hennes & Mauritz s.r.l. EUR 50,000'
    if _CMS_UNKNOWN.match(s):
        return None, None
    m = _CMS_NO_NAME.match(s)                              # 'Bank (name not available at the moment)'
    if m:
        return None, m.group(1).strip() or None
    m = re.match(r'^([^()]+?)\s*\(([^()]+)\)$', s)          # 'Restaurant (SANTI 3000, S.L.)'
    if m and _LEGAL_FORM.search(m.group(2)) and not _LEGAL_FORM.search(m.group(1)):
        return m.group(2).strip(), m.group(1).strip()
    return s, None


def step_cms_names(db):
    """Apply cms_name() to the CMS cases already in the database, keeping the source text in case_parties."""
    with Run(db, 'cms-names') as run:
        for c in db.execute("SELECT case_id, controller FROM cases WHERE source = 'cms_tracker'").fetchall():
            run.fetched += 1
            kept = db.execute('SELECT name FROM case_parties WHERE case_id = ? AND position = 1', (c['case_id'],)).fetchone()
            raw = kept['name'] if kept else c['controller']   # first run: what is stored is still the source text
            if raw:
                save_cms_party(db, c['case_id'], raw)
            checked = db.execute('SELECT controller FROM party_checks WHERE case_id = ?', (c['case_id'],)).fetchone()
            name = shown_name(checked['controller']) if checked else cms_name(raw)[0]
            if name != c['controller']:
                db.execute('UPDATE cases SET controller = ?, updated_at = ? WHERE case_id = ?', (name, now(), c['case_id']))
                run.updated += 1
        db.commit()


def save_cms_party(db, case_id, raw):
    db.execute('DELETE FROM case_parties WHERE case_id = ?', (case_id,))
    db.execute('INSERT INTO case_parties (case_id, position, name, link, role, reason) VALUES (?,?,?,?,?,?)',
               (case_id, 1, clean(raw), None, 'respondent', 'CMS lists the party fined'))


# ----- PARTIES: WHO IS THE DECISION ABOUT? -----
# GDPRhub lists up to four parties with no role, often the complainant first. Each party gets a role
# from evidence in the record itself; the controller is named only when that evidence is clear.
# Unclear cases go to a review list (parties-review), and Lorenzo's answers (party_checks) always win.
_LABEL_RESP = re.compile(r'(?:\([^)]*\b|^|\b)(?:controllers?|processors?|defendants?|respondents?|accused|controlled (?:party|entity))\b(?![^(]*\bdata subject)', re.I)
_LABEL_COMP = re.compile(r'\([^)]*\b(?:data subjects?|complainants?|claimants?|plaintiffs?|applicants?|petitioners?)\b|^(?:complainants?|claimants?)\b', re.I)
_COMPLAINANT = re.compile(r"""\b(?:complainants?|data\s+subjects?|claimants?|plaintiffs?|petitioners?|
    represented\s+by|users?|customers?|citizens?|patients?|employees?|workers?|trabajadores|parents?|
    private\s+(?:individual|person)|an?\s+(?:unnamed\s+)?(?:individual|person|citizen))\b""", re.I | re.X)
_NGOS = re.compile(r'\b(?:noyb|la quadrature du net|privacy international|iccl|irish council for civil liberties|bits of freedom|'
                   r'epicenter\.?works|digitalcourage|datenschutzverein|open rights group|panoptykon|homo digitalis|'
                   r'consumer institute|verbraucherzentrale|consumentenbond|facua|ocu\b)', re.I)
_HIDDEN = re.compile(r'^(?:anonymous|anoymous|anonymised|anonymized|unknown|unnamed|n/?a|-+|([A-Z])\1{0,3}|(?:[A-Z]\.){1,4}|[A-Z]\.[A-Z]\.[A-Z])$', re.I)
_HIDDEN_RESP = re.compile(r"^(?:anonymous|anoymous|unknown|unnamed)\b|name not disclosed|\banonymi[sz]ed\b|^data controller\b|^[A-Z]$", re.I)
_AUTHORITY = re.compile(r"""\b(?:data\s+protection\s+(?:authority|inspectorate|commission(?:er)?|agency|ombudsman|board)|
    supervisory\s+authority|datatilsynet|datainspektionen|integritetsskyddsmyndigheten|personvernnemnda|privacy\s+appeals\s+board|
    autoriteit\s+persoonsgegevens|garante\s+per\s+la\s+protezione|commission\s+nationale|information\s+commissioner|
    edpb|european\s+data\s+protection\s+board|andmekaitse|persónuvernd|personuvernd)\b""", re.I | re.X)
_PERSON = re.compile(r'\*\*|^(?:mr|mrs|ms|dr|prof)\.?\s', re.I)
_AUTHORITY_SHORT = {'dpa', 'ico', 'cnil', 'aepd', 'apd', 'gba', 'dsb', 'vdai', 'uodo', 'hdpa', 'ip', 'aki'}


def _words(s):
    s = unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode().lower()
    return re.sub(r'[^a-z0-9 ]+', ' ', s).split()


def _in_title(name, title):
    """Most of the party's distinctive words appear in the GDPRhub page title, which names the case."""
    stop = {'the', 'and', 'ltd', 'limited', 'gmbh', 'inc', 'llc', 'spa', 'srl', 'plc', 'sa', 'ag', 'bv', 'as', 'ab', 'oy', 'sl', 'of', 'de', 'la'}
    w = [x for x in _words(name.split(' (')[0]) if len(x) > 2 and x not in stop]
    t = set(_words(title))
    return bool(w) and sum(x in t for x in w) / len(w) >= 0.6


def party_roles(title, authority, names):
    """[(position, name, role, reason)] for each named party; a name written twice is kept once."""
    out, seen = [], set()
    a = (authority or '').split(' (')[0].strip().lower()
    for pos, p in enumerate(names, 1):
        p = (p or '').strip()
        key = ' '.join(_words(p))
        if not p or key in seen:
            continue
        seen.add(key)
        n = p.split(' (')[0].strip().lower()
        if _LABEL_RESP.search(p) and not _LABEL_COMP.search(p):
            role, why = 'respondent', 'the source labels it as controller, processor or defendant'
        elif _LABEL_COMP.search(p):
            role, why = 'complainant', 'the source labels it as data subject or complainant'
        elif (a and n == a) or n in _AUTHORITY_SHORT or _AUTHORITY.search(p):
            role, why = 'authority', 'it is the regulator or an appeal body'
        elif _NGOS.search(p) or _COMPLAINANT.search(p):
            role, why = 'complainant', 'the name describes who complained'
        elif _HIDDEN.match(p):
            role, why = 'anonymous', 'the name is hidden'
        elif _in_title(p, title):
            role, why = 'respondent', 'the case title names it'
        elif _PERSON.search(p):
            role, why = 'person', 'a private person, role not stated'
        else:
            role, why = 'unknown', None
        out.append((pos, p, role, why))
    return out


def party_name(p):
    """The name without role words the editor added: 'Vs. ', 'Respondent: ', '(controller/respondent)'."""
    p = re.sub(r'^(?:vs\.?|respondent:|controller:|defendant:)\s*', '', p.strip(), flags=re.I)
    p = re.sub(r'\s*\((?:the\s+)?(?:data\s+)?(?:controllers?|processors?|respondents?|defendants?)(?:\s*/\s*[\w ]+)?\)\s*$', '', p, flags=re.I)
    p = re.sub(r'\s+-\s+(?:the\s+)?(?:data\s+)?(?:controller|processor|respondent|defendant)\s*$', '', p, flags=re.I)
    return p.strip() or None


def resolve_controller(roles):
    """(controller or None, status) from party_roles(). status: 'clear', 'review' or 'none'."""
    if not roles:
        return None, 'none'
    resp = [r for r in roles if r[2] == 'respondent']
    open_ = [r for r in roles if r[2] in ('unknown', 'person')]
    if resp and not open_:
        names = [party_name(r[1]) for r in resp]
        if any(n is None or _HIDDEN_RESP.search(n) for n in names):
            return None, 'none'                    # the company's name is hidden in the source
        return ' and '.join(names), 'clear'
    if resp or len(open_) > 1:
        return None, 'review'
    if len(open_) == 1:
        if open_[0][2] == 'person':
            return None, 'review'
        name = party_name(open_[0][1])
        if name is None or _HIDDEN_RESP.search(name):
            return None, 'none'                    # described but not named, e.g. 'Logistics company (anonymized)'
        return name, 'clear'
    return None, 'none'


def save_parties(db, case_id, title, authority, names, links):
    """Store the parties with their roles and return the controller: Lorenzo's check if any, else the rules'."""
    roles = party_roles(title, authority, names)
    db.execute('DELETE FROM case_parties WHERE case_id = ?', (case_id,))
    db.executemany('INSERT INTO case_parties (case_id, position, name, link, role, reason) VALUES (?,?,?,?,?,?)',
                   [(case_id, pos, name, (links[pos - 1] or None) if pos <= len(links) else None, role, why)
                    for pos, name, role, why in roles])
    checked = db.execute('SELECT controller FROM party_checks WHERE case_id = ?', (case_id,)).fetchone()
    return shown_name(checked['controller']) if checked else resolve_controller(roles)[0]


def shown_name(checked):
    """A checked answer as the name to show: 'PERSON: ...' marks a private person, whose name is never shown."""
    return None if (checked or '').upper().startswith('PERSON:') else checked


def _party_fields(f):
    return ([clean(f.get(f'Party_Name_{k}')) for k in range(1, 5)],
            [clean(f.get(f'Party_Link_{k}')) for k in range(1, 5)])


def step_parties(db, delay=1.0):
    """Re-read the party lists of the GDPRhub cases already in the database and set the controller again.
    Changes nothing else, so it can run without a full refresh."""
    with Run(db, 'parties') as run:
        rows = {r['case_id']: r for r in db.execute("SELECT case_id, authority, controller FROM cases WHERE source = 'gdprhub'")}
        ids = list(rows)
        for i in range(0, len(ids), 50):
            j = _hub({'action': 'query', 'titles': '|'.join(ids[i:i + 50]), 'prop': 'revisions',
                      'rvprop': 'content', 'formatversion': 2})
            back = {n['to']: n['from'] for n in j.get('query', {}).get('normalized', [])}
            for page in j.get('query', {}).get('pages', []):
                cid = back.get(page['title'], page['title'])
                text = (page.get('revisions') or [{}])[0].get('content')
                f = _template(text) if text else None
                if cid not in rows or not f:
                    continue
                run.fetched += 1
                names, links = _party_fields(f)
                controller = save_parties(db, cid, cid, rows[cid]['authority'], names, links)
                if controller != rows[cid]['controller']:
                    db.execute('UPDATE cases SET controller = ?, updated_at = ? WHERE case_id = ?', (controller, now(), cid))
                    run.updated += 1
            db.commit()
            time.sleep(delay)


PARTIES_FIELDS = ['status', 'fino_id', 'regulator', 'date', 'fine_eur', 'parties', 'suggestion', 'why', 'also_involved',
                  'controller', 'note', 'gdprhub_summary', 'gdprhub', 'case_id']
PARTIES_WIDTHS = {'status': 24, 'fino_id': 13, 'regulator': 22, 'date': 11, 'fine_eur': 12, 'parties': 50, 'suggestion': 32,
                  'why': 60, 'also_involved': 32, 'controller': 30, 'note': 30, 'gdprhub_summary': 60, 'gdprhub': 20, 'case_id': 20}
PARTIES_HOW_TO = (
    'WHO IS THE DECISION AGAINST?',
    '',
    'Colours: green = easy, orange = needs your judgement, grey = nothing to do (shown so you can see why).',
    'You only type in the two columns with a yellow header: controller and note.',
    '',
    'In the controller column write:',
    '   ok (or yes)             to accept the suggestion (also_involved is then saved as the note)',
    '   a name                  if the suggestion is wrong (copy it from the parties column)',
    '   PERSON: Doctor          for a private person: a short description, never the name',
    '   NONE                    for an unnamed organisation (a school, an anonymised company)',
    '   (nothing)               to skip the row for now',
    '',
    'The controller is the organisation the decision is against: normally the one that pays the fine.',
    'When several organisations were fined: accept the main one with ok; the others stay in the note.',
    '',
    'When you are done: save (keep the .xlsx format), close Excel and tell Claude.',
)
ORG_HOW_TO = (
    'SAME COMPANY? SAME GROUP?',
    '',
    'Colours: green = easy, orange = needs your judgement, grey = done.',
    'You only type in the answer column (yellow header).',
    '',
    '"maybe the same company" rows: write same or different.',
    '"group to check" rows: write ready (show the group on the site) or no (keep it hidden).',
    '',
    'When you are done: save (keep the .xlsx format), close Excel and tell Claude.',
)


def step_parties_review(db):
    """Write review/parties.xlsx: the cases where the rules could not tell who the decision is about.
    Fill in the 'controller' column (or write NONE), then run: python pharos.py parties-apply"""
    os.makedirs(REVIEW_DIR, exist_ok=True)
    checked = {r[0] for r in db.execute('SELECT case_id FROM party_checks')}
    out = []
    for c in db.execute("SELECT case_id, pharos_id, authority, decision_date, fine_eur, source_page FROM cases "
                        "WHERE source = 'gdprhub' ORDER BY decision_date DESC"):
        if c['case_id'] in checked:
            continue
        roles = [(r['position'], r['name'], r['role'], r['reason']) for r in
                 db.execute('SELECT * FROM case_parties WHERE case_id = ? ORDER BY position', (c['case_id'],))]
        if resolve_controller(roles)[1] != 'review':
            continue
        out.append({'case_id': c['case_id'], 'fino_id': c['pharos_id'], 'regulator': c['authority'],
                    'date': c['decision_date'], 'fine_eur': c['fine_eur'],
                    'parties': ' | '.join(f'{name} [{role}]' for _, name, role, _ in roles),
                    'controller': '', 'note': '', 'gdprhub': c['source_page']})
    if review_has_answers('parties', ['controller', 'note']):
        print('  review/parties has answers not applied yet: run parties-apply first.')
        return
    for r in out:
        r['status'] = 'to do (your judgement)'
    path = review_write('parties', PARTIES_FIELDS, out, answer=('controller', 'note'), widths=PARTIES_WIDTHS,
                        how_to=PARTIES_HOW_TO)
    print(f'  {len(out)} cases to check, written to {path}')


def step_parties_apply(db):
    """Read review/parties.xlsx and save every filled-in answer as a check that wins over the rules."""
    with Run(db, 'parties-apply') as run:
        for r in review_rows('parties'):
            answer = (r.get('controller') or '').strip()
            note = (r.get('note') or '').strip()
            if answer.lower() in ('ok', 'yes'):  # accepts the suggestion column, if the file has one
                answer = (r.get('suggestion') or '').strip()
                note = note or (r.get('also_involved') or '').strip()
            if not answer:
                continue
            run.fetched += 1
            # 'PERSON: Doctor' = a private person: the check keeps the description, the case never shows a name
            controller = None if answer.upper() == 'NONE' or answer.upper().startswith('PERSON:') else answer
            db.execute('INSERT INTO party_checks (case_id, controller, note, checked_at) VALUES (?,?,?,?) '
                       'ON CONFLICT(case_id) DO UPDATE SET controller = excluded.controller, note = excluded.note, '
                       'checked_at = excluded.checked_at', (r['case_id'], answer if answer.upper().startswith('PERSON:') else controller, note or None, now()))
            db.execute('UPDATE cases SET controller = ?, updated_at = ? WHERE case_id = ?', (controller, now(), r['case_id']))
            run.updated += 1
        db.commit()


# ----- STEP: LINK THE SAME DECISION ACROSS SOURCES -----
DISTINCTIVE_FINE = 100_000  # at or above this, an exact euro amount in one country and month identifies a decision


def step_link(db):
    """Strict rules only. A linked row is hidden in v_cases, never deleted. Anything ambiguous stays unlinked."""
    with Run(db, 'link') as run:
        linked = {r[0] for r in db.execute("SELECT duplicate_id FROM case_links WHERE status != 'rejected'")}
        primaries = {r[0] for r in db.execute("SELECT primary_id FROM case_links WHERE status != 'rejected'")}
        rejected = {(r[0], r[1]) for r in db.execute("SELECT primary_id, duplicate_id FROM case_links WHERE status = 'rejected'")}

        def add(primary, dup, rule):
            if dup in linked or primary in linked or dup == primary or (primary, dup) in rejected:
                return
            db.execute('INSERT OR IGNORE INTO case_links (primary_id, duplicate_id, rule, created_at) VALUES (?,?,?,?)',
                       (primary, dup, rule, now()))
            linked.add(dup); primaries.add(primary)
            run.inserted += 1

        # Rule 1: identical link to the regulator's publication, used by exactly one case on each side
        for r in db.execute("""SELECT c.case_id AS p, g.case_id AS d FROM cases c JOIN cases g ON g.source_url = c.source_url
                               WHERE c.source='cms_tracker' AND g.source='gdprhub' AND c.source_url IS NOT NULL
                                 AND (SELECT COUNT(*) FROM cases x WHERE x.source='cms_tracker' AND x.source_url=c.source_url) = 1
                                 AND (SELECT COUNT(*) FROM cases y WHERE y.source='gdprhub' AND y.source_url=g.source_url) = 1"""):
            add(r['p'], r['d'], 'same original source URL')

        # Group fined cases by country + month + exact euro fine
        groups = {}
        for r in db.execute("""SELECT case_id, source, country_code, substr(decision_date,1,7) ym, fine_eur FROM cases
                               WHERE fine_eur IS NOT NULL AND date_precision='day' ORDER BY case_id"""):
            groups.setdefault((r['country_code'], r['ym'], r['fine_eur']), {'cms_tracker': [], 'gdprhub': []})[r['source']].append(r['case_id'])

        for (cc, ym, fine), g in groups.items():
            cms, hub = g['cms_tracker'], g['gdprhub']
            # Rule 2: exactly one candidate on each side
            if len(cms) == 1 and len(hub) == 1:
                add(cms[0], hub[0], 'same country, month and exact fine (1:1)')
            # Rule 3: one CMS case and several GDPRhub pages, for a large fine (GDPRhub sometimes has two pages)
            elif len(cms) == 1 and len(hub) > 1 and fine >= DISTINCTIVE_FINE:
                for h in hub:
                    add(cms[0], h, f'same country, month and exact fine of at least EUR {DISTINCTIVE_FINE:,}')
            # Rule 4: duplicate GDPRhub pages with no CMS twin, for a large fine
            elif not cms and len(hub) > 1 and fine >= DISTINCTIVE_FINE:
                for h in hub[1:]:
                    add(hub[0], h, f'duplicate GDPRhub page: same country, month and fine of at least EUR {DISTINCTIVE_FINE:,}')
        db.commit()


# ----- STEP: NORMALISE -----
# The CMS tracker's own 11 sectors are kept as they are; only typos and 'Not assigned' are cleaned
SECTOR_FIXES = {
    'accomodation and hospitality': 'Accommodation and Hospitality',
    'not assigned': None,
    'not available': None,
}
VIOLATIONS = [
    (r'legal basis|lawful basis|unlawful processing', 'Unlawful processing'),
    (r'consent', 'Consent'),
    (r'information obligation|transparency|privacy notice', 'Transparency'),
    (r'data subject.{0,10}rights|right of access|erasure|right to', 'Data subject rights'),
    (r'data breach|breach notification', 'Data breach'),
    (r'technical and organi[sz]ational|information security|security measures', 'Data security'),
    (r'data processing principles|general data processing', 'Data principles'),
    (r'data protection officer|\bdpo\b', 'Data protection officer'),
    (r'cooperat|supervisory authorit', 'Cooperation with the DPA'),
    (r'processor', 'Processor obligations'),
    (r'transfer', 'International transfers'),
]
ARTICLE_CATEGORIES = [
    (range(5, 6), 'Data principles'), (range(6, 7), 'Unlawful processing'), (range(7, 9), 'Consent'),
    (range(9, 11), 'Special categories'), (range(12, 15), 'Transparency'), (range(15, 23), 'Data subject rights'),
    (range(24, 26), 'Accountability'), (range(28, 29), 'Processor obligations'), (range(32, 33), 'Data security'),
    (range(33, 35), 'Data breach'), (range(35, 37), 'DPIA'), (range(37, 40), 'Data protection officer'),
    (range(44, 50), 'International transfers'),
]
OUTCOMES = [
    (r'upheld|violation|partly upheld', 'Violation found'),
    (r'rejected|dismissed|no violation', 'Rejected'),
    (r'annul|overturn|quash', 'Annulled'),
    (r'reprimand|warning|admonish', 'Reprimand'),
    (r'settled|closed|withdrawn|discontinued', 'Closed'),
]


def parse_articles(raw):
    """'Art. 5 (1) a) GDPR, Art. 13 GDPR' / 'Article 6(1)(f) GDPR' -> [('Art. 5(1)(a)', 5), ...]"""
    out = []
    for chunk in re.split(r'[,;]', raw or ''):
        if 'gdpr' not in chunk.lower() and 'dsgvo' not in chunk.lower() and 'rgpd' not in chunk.lower():
            continue  # national law, other EU acts
        m = re.search(r'Art(?:icle)?\.?\s*(\d+)\s*(?:\(\s*(\d+)\s*\))?\s*(?:\(?\s*([a-z])\s*\))?', chunk, re.I)
        if m:
            ref = f'Art. {m.group(1)}' + (f'({m.group(2)})' if m.group(2) else '') + (f'({m.group(3).lower()})' if m.group(3) else '')
            if ref not in [a for a, _ in out]:
                out.append((ref, int(m.group(1))))
    return out


# ----- STEP: FINES IN OTHER CURRENCIES -----
# The European Central Bank publishes one official reference rate per working day since 1999, for every
# currency in the data (also the old Croatian kuna and the Bulgarian lev). A fine in SEK keeps SEK as the
# real figure; the euro value is an estimate for comparison, and the page says so.
ECB_RATES = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip'
CURRENCY_SYMBOLS = {'€': 'EUR', '£': 'GBP', 'N/A': None}


def ecb_rates():
    """{currency: {date: units per euro}} from the ECB's full history file."""
    import io
    import zipfile
    req = urllib.request.Request(ECB_RATES, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        z = zipfile.ZipFile(io.BytesIO(r.read()))
    rows = list(csv.reader(io.TextIOWrapper(z.open(z.namelist()[0]), encoding='utf-8')))
    out = {}
    for row in rows[1:]:
        for cur, v in zip(rows[0][1:], row[1:]):
            if cur.strip() and v.strip() not in ('', 'N/A'):
                out.setdefault(cur.strip(), {})[row[0]] = float(v)
    return out


def read_amount(raw):
    """The number in a fine written in any common format, or None when the format is ambiguous.
    A money amount never has three decimals, so '200.000' and '1.527.855' are thousands, not decimals.
    Ambiguous on purpose: '1,500,00', '10.0000', '20,000 and 30,000' -> None (goes to the currency review)."""
    s = re.sub(r"(?i)[\s'  €£]|\b(eur|gbp|nok|sek|dkk|pln|huf|czk|ron|bgn|isk|hrk|chf)\b", '', str(raw or ''))
    if re.fullmatch(r'\d+(\.\d{1,2})?', s):                     # 1800000.0 / 9686.60
        return float(s)
    if re.fullmatch(r'\d{1,3}(,\d{3})+(\.\d{1,2})?', s):         # 1,800,000 / 9,686.60
        return float(s.replace(',', ''))
    if re.fullmatch(r'\d{1,3}(\.\d{3})+(,\d{1,2})?', s):         # 1.527.855 / 200.000 / 1.200,00
        return float(s.replace('.', '').replace(',', '.'))
    if re.fullmatch(r'\d+,\d{1,2}', s):                          # 9686,60
        return float(s.replace(',', '.'))
    return None


def ecb_rate(rates, currency, date, precision):
    """(units per euro, basis) for a decision date, or (None, the reason there is none)."""
    days = rates.get(currency)
    if not days:
        return None, f'no ECB rate for {currency}'
    if not date or precision == 'unknown':
        return None, 'no date in the source'
    if precision == 'day':
        known = [d for d in days if d <= date]
        best = max(known) if known else None
        if best and (datetime.fromisoformat(date) - datetime.fromisoformat(best)).days <= 7:
            return days[best], f'day {best}'
        return None, f'no ECB rate near {date}'
    period = date[:7] if precision == 'month' else date[:4]
    vals = [v for d, v in days.items() if d.startswith(period)]
    return (sum(vals) / len(vals), f'{precision} {period}') if vals else (None, f'no ECB rate in {period}')


def step_convert(db):
    """Fines in other currencies: an estimate in euro. What cannot be converted safely is left for the review."""
    rates = ecb_rates()
    checks = {r['case_id']: r for r in db.execute('SELECT * FROM amount_checks')}
    with Run(db, 'convert') as run:
        db.execute('DELETE FROM fines_converted')
        for c in db.execute("""SELECT case_id, fine_original, currency, decision_date, date_precision FROM cases
                               WHERE fine_eur IS NULL AND fine_original IS NOT NULL
                               AND COALESCE(currency, '') NOT IN ('', 'EUR')""").fetchall():
            run.fetched += 1
            cur = CURRENCY_SYMBOLS.get(c['currency'], c['currency'])
            chk = checks.get(c['case_id'])
            # a check's amount wins; 0 means 'no single amount'; NULL means 'the source amount is right'
            amount = chk['amount'] if chk and chk['amount'] is not None else read_amount(c['fine_original'])
            date, prec = c['decision_date'], c['date_precision']
            if chk and chk['decision_date']:
                date, prec = parse_date(chk['decision_date'])
            if not amount or not cur:
                continue
            if cur == 'EUR':  # the source wrote '€': a real euro amount, not an estimate
                db.execute('UPDATE cases SET fine_eur = ?, currency = ? WHERE case_id = ?', (round(amount), 'EUR', c['case_id']))
                run.updated += 1
                continue
            rate, basis = ecb_rate(rates, cur, date, prec)
            if rate:
                db.execute('INSERT INTO fines_converted VALUES (?,?,?,?,?,?,?)',
                           (c['case_id'], amount, cur, rate, basis, round(amount / rate), now()))
                run.inserted += 1
        db.commit()


CURRENCY_HOW_TO = (
    'FINES IN OTHER CURRENCIES THAT COULD NOT BE CONVERTED',
    '',
    'Open the source link and read the fine in the decision. Then fill the yellow columns:',
    '   amount   the fine as a plain number in its own currency, e.g. 150000 (NONE if there is no single amount)',
    '   date     only when the source has no date: the decision date, e.g. 2021-05-14, or just the year, e.g. 2021',
    '   note     anything worth remembering, e.g. "two fines: 20,000 and 30,000"',
    '',
    'The euro value is then calculated from the European Central Bank rate of that date.',
    'When you are done: save (keep the .xlsx format), close Excel and tell Claude.',
)


def step_currency_review(db):
    """review/currency.xlsx: non-euro fines the program could not convert safely, with the reason."""
    if review_has_answers('currency', ['amount', 'date', 'note']):
        print('  review/currency has answers not applied yet: run currency-apply first.')
        return
    rates = ecb_rates()
    checked = {r[0] for r in db.execute('SELECT case_id FROM amount_checks')}
    out = []
    for c in db.execute("""SELECT c.* FROM v_cases c LEFT JOIN fines_converted f USING (case_id)
                           WHERE c.fine_eur IS NULL AND c.fine_original IS NOT NULL AND f.case_id IS NULL
                           AND COALESCE(c.currency, '') NOT IN ('', 'EUR') ORDER BY c.pharos_id""").fetchall():
        if c['case_id'] in checked or (c['fine_original'] or '').strip().lower() in ('n/a', 'none', '-', ''):
            continue  # 'n/a': the source records no fine at all, nothing to convert
        cur = CURRENCY_SYMBOLS.get(c['currency'], c['currency'])
        if read_amount(c['fine_original']) is None:
            problem = 'The amount is written in a way the program cannot read safely.'
        elif not cur:
            problem = 'The source gives no currency.'
        else:
            problem = 'No exchange rate: ' + ecb_rate(rates, cur, c['decision_date'], c['date_precision'])[1] + '.'
        out.append({'status': 'to do (your judgement)', 'fino_id': c['pharos_id'], 'regulator': c['authority'],
                    'source_date': c['decision_date'] or '', 'currency': c['currency'], 'as_written': c['fine_original'],
                    'problem': problem, 'amount': '', 'date': '', 'note': '', 'source': c['source_page'] or '',
                    'case_id': c['case_id']})
    path = review_write('currency', ['status', 'fino_id', 'regulator', 'source_date', 'currency', 'as_written', 'problem',
                                     'amount', 'date', 'note', 'source', 'case_id'], out, answer=('amount', 'date', 'note'),
                        widths={'status': 22, 'fino_id': 13, 'regulator': 24, 'as_written': 18, 'problem': 60,
                                'amount': 16, 'date': 13, 'note': 30, 'source': 30}, how_to=CURRENCY_HOW_TO)
    print(f'  {len(out)} fines to check, written to {path}')


def step_currency_apply(db):
    """Save the answers from review/currency.xlsx; they win over what the program reads."""
    with Run(db, 'currency-apply') as run:
        for r in review_rows('currency'):
            amount, date, note = ((r.get(k) or '').strip() for k in ('amount', 'date', 'note'))
            if not (amount or date):
                continue
            run.fetched += 1
            value = None if not amount else 0 if amount.upper() == 'NONE' else read_amount(amount)
            if amount and amount.upper() != 'NONE' and value is None:
                print(f"  {r['fino_id']}: amount '{amount}' is not a plain number, skipped")
                continue
            db.execute('INSERT INTO amount_checks (case_id, amount, decision_date, note, checked_at) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(case_id) DO UPDATE SET amount = excluded.amount, decision_date = excluded.decision_date, '
                       'note = excluded.note, checked_at = excluded.checked_at', (r['case_id'], value, date or None, note or None, now()))
            run.updated += 1
        apply_checked_dates(db)
        db.commit()
    print('  Run "python pharos.py convert" and then "python pharos.py export" to put the answers on the website.')


def step_normalise(db):
    with Run(db, 'normalise') as run:
        db.execute('DELETE FROM case_articles')
        db.execute('DELETE FROM case_categories')
        for r in db.execute('SELECT case_id, sector, articles_raw, violation_type, outcome, source FROM cases').fetchall():
            arts = parse_articles(r['articles_raw'])
            db.executemany('INSERT OR IGNORE INTO case_articles VALUES (?,?,?)', [(r['case_id'], a, n) for a, n in arts])

            cats = {c for p, c in VIOLATIONS if r['violation_type'] and re.search(p, r['violation_type'], re.I)}
            cats |= {c for rng, c in ARTICLE_CATEGORIES for _, n in arts if n in rng}
            db.executemany('INSERT OR IGNORE INTO case_categories VALUES (?,?)', [(r['case_id'], c) for c in cats])

            key = (r['sector'] or '').strip().lower()
            sector_tag = SECTOR_FIXES[key] if key in SECTOR_FIXES else (r['sector'] or None)
            outcome = r['outcome']
            if r['source'] == 'gdprhub' and outcome:
                outcome = next((o for p, o in OUTCOMES if re.search(p, outcome, re.I)), outcome)
            elif r['source'] == 'cms_tracker':
                outcome = 'Fine issued'  # the CMS tracker lists fines only
            db.execute('UPDATE cases SET sector_tag=?, outcome=? WHERE case_id=?', (sector_tag, outcome, r['case_id']))
            run.updated += 1
        db.commit()


# ----- STEP: EXPORT -----
EXPORT_COLUMNS = ['pharos_id', 'case_id', 'source', 'country', 'country_code', 'authority', 'decision_date',
                  'date_precision', 'fine_eur', 'fine_original', 'currency', 'controller', 'sector_tag',
                  'articles', 'categories', 'violation_type', 'best_outcome', 'best_summary', 'source_url',
                  'source_page', 'gdprhub_page', 'attribution']


def step_export(db):
    os.makedirs(EXPORT_DIR, exist_ok=True)
    with Run(db, 'export') as run:
        rows = [dict(r) for r in db.execute(f'SELECT {", ".join(EXPORT_COLUMNS)} FROM v_cases '
                                            'ORDER BY decision_date DESC NULLS LAST, case_id')]
        run.fetched = len(rows)
        # CSV opens straight in Excel (utf-8 with BOM keeps accents intact)
        with open(os.path.join(EXPORT_DIR, 'cases.csv'), 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=EXPORT_COLUMNS)
            w.writeheader()
            w.writerows(rows)
        stats = dict(db.execute('SELECT * FROM v_stats').fetchone())
        stats['total_fines_eur_converted'] = db.execute(  # estimates for fines in other currencies (ECB rate)
            'SELECT COALESCE(SUM(f.fine_eur_est), 0) FROM v_cases v JOIN fines_converted f USING (case_id) WHERE v.fine_eur IS NULL').fetchone()[0]
        meta = {
            'generated_at': now(),
            'licence': 'CC BY-NC-SA 4.0 — https://creativecommons.org/licenses/by-nc-sa/4.0/',
            'sources': list(ATTRIBUTION.values()),
            'stats': stats,
            # when the sources were last read; later steps (fixes, exports) do not make the data newer
            'last_fetch': db.execute("SELECT MAX(finished_at) FROM runs WHERE status = 'ok' "
                                     "AND step IN ('cms', 'gdprhub', 'update')").fetchone()[0],
        }
        with open(os.path.join(EXPORT_DIR, 'cases.json'), 'w', encoding='utf-8') as f:
            json.dump({'meta': meta, 'cases': rows}, f, ensure_ascii=False, separators=(',', ':'))
        with open(os.path.join(EXPORT_DIR, 'stats.json'), 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        run.inserted = len(rows)
        if os.path.isdir(os.path.dirname(SITE_DATA_DIR)):
            named = parties_named(db)
            own = {c for c, in db.execute("SELECT case_id FROM cases WHERE summary IS NOT NULL AND summary != ''")}
            cms_raw = dict(db.execute("SELECT p.case_id, p.name FROM case_parties p JOIN cases c USING (case_id) "
                                      "WHERE c.source = 'cms_tracker' AND p.position = 1"))
            groups = load_groups()
            alias, _ = load_same()
            conv = {r['case_id']: r for r in db.execute('SELECT * FROM fines_converted')}
            describe_parties(db, rows, cms_raw)
            # retired IDs -> current ID, so an old link (#d-ND-GB-001) still opens the decision
            meta = meta | {'moved': dict(db.execute(
                """SELECT h.old_id, c.pharos_id FROM id_history h JOIN cases c USING (case_id)
                   WHERE h.old_id NOT IN (SELECT pharos_id FROM cases WHERE pharos_id IS NOT NULL)"""))}
            for r in rows:
                fx = conv.get(r['case_id'])
                r['fine_amount'] = fx['amount'] if fx else None
                r['fine_eur_est'] = fx['fine_eur_est'] if fx else None
                r['fx'] = f"{fx['rate_basis']}|{fx['rate']:.4f}|{fx['currency']}" if fx else None
                r['org'] = org_key(r['controller'], r['country_code']) if r['controller'] else None
                r['org'] = alias.get(r['org'], r['org'])
                g, status = org_group(r['controller'], groups) if r['org'] else (None, None)
                r['org_group'] = g if status == 'ready' else None
                r['parties_named'] = named.get(r['case_id']) if not r['controller'] and not r['party_kind'] else None
                r['has_own_summary'] = r['case_id'] in own
            export_site(rows, meta)
            run.notes.append(f'website data written to {SITE_DATA_DIR}')


SITE_FIELDS = ['pharos_id', 'case_id', 'source', 'country', 'country_code', 'authority', 'decision_date',
               'date_precision', 'fine_eur', 'fine_original', 'currency', 'controller', 'sector_tag', 'articles',
               'categories', 'violation_type', 'best_outcome', 'source_url', 'source_page', 'gdprhub_page',
               'summary_by', 'parties_named', 'controller_note', 'org', 'org_group',
               'fine_amount', 'fine_eur_est', 'fx', 'party_kind']  # fx = 'day 2026-09-22|10.9012|SEK': basis, units per euro, currency


# ----- WHO AN UNNAMED DECISION IS ABOUT -----
# When no source names the party, the site shows what the source says about it ("a hospital", "a Member of
# Parliament") instead of "not named". party_descriptions.csv holds these, one row per decision:
#   kind = name (the summary names it), description, person (a natural person: never named), opinion (no party)
#   basis = summary / facts (read by the program from GDPRhub's text), read (read by Claude), checked (by Lorenzo)
PERSON_WORDS = re.compile(r'^(?:private individual|private person|natural person|sole trader|individual)s?$', re.I)


def load_party_descriptions():
    if not os.path.exists(PARTY_DESC_FILE):
        return {}
    with open(PARTY_DESC_FILE, encoding='utf-8-sig') as f:
        return {r['case_id']: r for r in csv.DictReader(f)}


def describe_parties(db, rows, cms_raw):
    """Sets controller, controller_note and party_kind ('person', 'opinion' or None) on the export rows."""
    desc = load_party_descriptions()
    persons = {cid: v.split(':', 1)[1].strip() for cid, v in
               db.execute("SELECT case_id, controller FROM party_checks WHERE controller LIKE 'PERSON:%'")}
    for r in rows:
        r['party_kind'] = None
        r['controller_note'] = cms_name(cms_raw[r['case_id']])[1] if r['case_id'] in cms_raw else None
        if r['controller'] and PERSON_WORDS.match(r['controller'].strip()):   # CMS: 'Private individual'
            r['controller_note'], r['controller'], r['party_kind'] = r['controller'].strip().capitalize(), None, 'person'
        if r['controller']:
            continue
        if r['case_id'] in persons:
            r['controller_note'], r['party_kind'] = persons[r['case_id']], 'person'
            continue
        d = desc.get(r['case_id'])
        if not d or r['controller_note']:
            continue
        if d['kind'] == 'name':
            r['controller'] = d['text']
        elif d['kind'] in ('person', 'description'):
            r['controller_note'], r['party_kind'] = d['text'], 'person' if d['kind'] == 'person' else None
        elif d['kind'] == 'opinion':
            r['party_kind'] = 'opinion'


UNNAMED_HOW_TO = (
    'DECISIONS WHERE NO SOURCE SAYS WHO THE DECISION IS ABOUT',
    '',
    'Optional: these show "Not named in the source" on the site, which is true. Answer only the ones you want to.',
    'Open the source link, then fill the two yellow columns:',
    '   kind   name          the organisation is named in the decision (text = its name)',
    '          description   only described, e.g. a hospital (text = Hospital)',
    '          person        a natural person (text = what they are, e.g. Doctor; never the name)',
    '          opinion       a general opinion or guidance: nobody was the target (text can stay empty)',
    '   text   as above',
    '',
    'When you are done: save (keep the .xlsx format), close Excel and tell Claude.',
)


def step_unnamed_review(db):
    """review/unnamed.xlsx: decisions the site can only show as 'Not named in the source'."""
    if review_has_answers('unnamed', ['kind', 'text']):
        print('  review/unnamed has answers not applied yet: run unnamed-apply first.')
        return
    rows = [dict(r) for r in db.execute('SELECT * FROM v_cases ORDER BY pharos_id')]
    cms_raw = dict(db.execute("SELECT p.case_id, p.name FROM case_parties p JOIN cases c USING (case_id) "
                              "WHERE c.source = 'cms_tracker' AND p.position = 1"))
    named = parties_named(db)
    describe_parties(db, rows, cms_raw)
    out = []
    for r in rows:
        if r['controller'] or r['controller_note'] or r['party_kind'] or named.get(r['case_id']):
            continue
        first = re.split(r'(?<=[.!?])\s', (r['best_summary'] or '').strip())[0][:300]
        out.append({'status': 'optional', 'fino_id': r['pharos_id'], 'regulator': r['authority'],
                    'date': r['decision_date'] or '', 'summary': first or '(no summary in the source)',
                    'kind': '', 'text': '', 'source': r['source_url'] or r['source_page'] or '', 'case_id': r['case_id']})
    path = review_write('unnamed', ['status', 'fino_id', 'regulator', 'date', 'summary', 'kind', 'text', 'source', 'case_id'],
                        out, answer=('kind', 'text'), how_to=UNNAMED_HOW_TO,
                        widths={'status': 12, 'fino_id': 13, 'regulator': 24, 'summary': 80, 'kind': 14, 'text': 30, 'source': 30})
    print(f'  {len(out)} decisions, written to {path}')


def step_unnamed_apply(db):
    """Save the answers from review/unnamed.xlsx into party_descriptions.csv (basis: checked)."""
    desc = load_party_descriptions()
    with Run(db, 'unnamed-apply') as run:
        for r in review_rows('unnamed'):
            kind, text = (r.get('kind') or '').strip().lower(), (r.get('text') or '').strip()
            if kind not in ('name', 'description', 'person', 'opinion') or (kind != 'opinion' and not text):
                continue
            desc[r['case_id']] = {'fino_id': r['fino_id'], 'kind': kind, 'text': text or 'General opinion',
                                  'basis': 'checked', 'case_id': r['case_id']}
            run.updated += 1
        with open(PARTY_DESC_FILE, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=['fino_id', 'kind', 'text', 'basis', 'case_id'])
            w.writeheader()
            w.writerows(sorted(desc.values(), key=lambda d: d['fino_id']))
    print('  Run "python pharos.py export" to put the answers on the website.')


def summary_by(r):
    """Who wrote the summary the site shows: the case's own source, or GDPRhub through a linked page."""
    if not r['best_summary']:
        return None
    return r['source'] if r['has_own_summary'] else 'gdprhub'


def parties_named(db):
    """For decisions whose controller is not clear: the parties the source names, minus complainants,
    regulators and hidden names, so the site can show them without saying who is who."""
    out = {}
    for cid, in db.execute("SELECT case_id FROM cases WHERE source = 'gdprhub' AND controller IS NULL"):
        roles = [(r['position'], r['name'], r['role'], r['reason']) for r in
                 db.execute('SELECT * FROM case_parties WHERE case_id = ? ORDER BY position', (cid,))]
        if resolve_controller(roles)[1] == 'review':
            out[cid] = '; '.join(party_name(r[1]) or r[1] for r in roles if r[2] in ('respondent', 'unknown', 'person'))
    return out


def export_site(rows, meta):
    """cases.json: every case without its summary, as compact arrays.
    summaries/N.json: the summaries of rows N*250 ... N*250+249, in the same order."""
    shard_dir = os.path.join(SITE_DATA_DIR, 'summaries')
    os.makedirs(shard_dir, exist_ok=True)
    # build everything before touching the old files, so a failure leaves the site as it was
    groups = {g: note for g, _, status, note in load_groups() if status == 'ready'}
    site = {'meta': meta | {'fields': SITE_FIELDS, 'summary_shard': SUMMARY_SHARD, 'groups': groups},
            'rows': [[summary_by(r) if k == 'summary_by' else r[k] for k in SITE_FIELDS] for r in rows]}
    for old in os.listdir(shard_dir):  # shard count can shrink
        os.remove(os.path.join(shard_dir, old))
    with open(os.path.join(SITE_DATA_DIR, 'cases.json'), 'w', encoding='utf-8') as f:
        json.dump(site, f, ensure_ascii=False, separators=(',', ':'))
    with open(os.path.join(SITE_DATA_DIR, 'stats.json'), 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)  # small file for the homepage
    for i in range(0, len(rows), SUMMARY_SHARD):
        with open(os.path.join(shard_dir, f'{i // SUMMARY_SHARD}.json'), 'w', encoding='utf-8') as f:
            json.dump([r['best_summary'] for r in rows[i:i + SUMMARY_SHARD]], f, ensure_ascii=False, separators=(',', ':'))


# ----- STATS -----
def step_stats(db):
    s = db.execute('SELECT * FROM v_stats').fetchone()
    print(f"\n  Decisions: {s['cases']:,}  |  Countries: {s['countries']}  |  Total fines: €{s['total_fines_eur']:,}")
    print(f"  Latest decision: {s['latest_decision']}  |  Last successful run: {s['last_run']}")
    for r in db.execute('SELECT source, COUNT(*) n, SUM(summary IS NOT NULL) s FROM cases GROUP BY source'):
        print(f"  {r['source']:12} {r['n']:6,} rows, {r['s']:6,} with a summary")
    print(f"  Cross-source links: {db.execute('SELECT COUNT(*) FROM case_links').fetchone()[0]}")
    print('\n  Most-enforced GDPR articles:')
    for r in db.execute('SELECT * FROM v_articles_enforced LIMIT 8'):
        print(f"    Art. {r['gdpr_article']:<4} {r['cases']:5,} cases   €{(r['total_fines_eur'] or 0):>15,}")
    print('\n  Last runs:')
    for r in db.execute('SELECT step, finished_at, status, fetched, inserted, updated FROM runs ORDER BY run_id DESC LIMIT 6'):
        print(f"    {r['finished_at']}  {r['step']:14} {r['status'] or 'running':7} "
              f"fetched {r['fetched']}, inserted {r['inserted']}, updated {r['updated']}")


# ----- MAIN -----
def main():
    p = argparse.ArgumentParser(description='GDPR enforcement database pipeline')
    p.add_argument('step', choices=['migrate', 'fix-ids', 'cms', 'cms-summaries', 'gdprhub', 'parties',
                                    'parties-review', 'parties-apply', 'cms-names', 'organisations-review', 'organisations-apply', 'link', 'normalise', 'convert',
                                    'currency-review', 'currency-apply', 'unnamed-review', 'unnamed-apply', 'export', 'stats', 'update'])
    p.add_argument('--limit', type=int, default=50, help='cms-summaries: how many case pages to fetch')
    a = p.parse_args()
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')  # € and accents in the Windows console
    db = connect()
    try:
        if a.step == 'migrate':
            step_migrate(db)
        elif a.step == 'fix-ids':
            step_fix_ids(db)
        elif a.step == 'cms':
            step_cms(db)
        elif a.step == 'cms-summaries':
            step_cms_summaries(db, a.limit)
        elif a.step == 'gdprhub':
            step_gdprhub(db)
        elif a.step == 'parties':
            step_parties(db)
        elif a.step == 'parties-review':
            step_parties_review(db)
        elif a.step == 'parties-apply':
            step_parties_apply(db)
        elif a.step == 'cms-names':
            step_cms_names(db)
        elif a.step == 'organisations-review':
            step_organisations_review(db)
        elif a.step == 'organisations-apply':
            step_organisations_apply(db)
        elif a.step == 'link':
            step_link(db)
        elif a.step == 'normalise':
            step_normalise(db)
        elif a.step == 'convert':
            step_convert(db)
        elif a.step == 'currency-review':
            step_currency_review(db)
        elif a.step == 'currency-apply':
            step_currency_apply(db)
        elif a.step == 'unnamed-review':
            step_unnamed_review(db)
        elif a.step == 'unnamed-apply':
            step_unnamed_apply(db)
        elif a.step == 'export':
            step_export(db)
        elif a.step == 'stats':
            step_stats(db)
        elif a.step == 'update':
            for step in (step_cms, step_gdprhub, step_link, step_normalise, step_convert, step_export):
                step(db)
            step_stats(db)
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
