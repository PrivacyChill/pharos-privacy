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
    row = db.execute("SELECT MAX(CAST(substr(pharos_id, ?) AS INTEGER)) FROM cases WHERE pharos_id LIKE ?",
                     (len(prefix) + 1, prefix + '%')).fetchone()
    return f'{prefix}{(row[0] or 0) + 1:03d}'


def upsert(db, run, rec):
    """Insert a new case or update a changed one. Never overwrites a value with an empty one."""
    ts = now()
    old = db.execute('SELECT * FROM cases WHERE case_id = ?', (rec['case_id'],)).fetchone()
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
        for r in rows:
            date, precision = parse_date(r.get('d'))
            name = r.get('C') or r.get('c')
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
                'controller': clean(r.get('p')),
                'sector': clean(r.get('s')),
                'articles_raw': clean(r.get('r')),
                'violation_type': clean(r.get('t')),
                'source_url': r.get('u') or None,
                'source_page': f"{CMS_URL}ETid-{r['e']}",
                'attribution': ATTRIBUTION['cms_tracker'],
            })
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
                date, precision = parse_date(f.get('Date_Decided'))
                arts = [f[k] for k in (f'GDPR_Article_{n}' for n in range(1, 31)) if f.get(k)]
                body = re.sub(r'\{\{DPAdecisionBOX.*?\n\}\}', '', text, flags=re.S | re.I)
                summary = _wiki_to_text(re.split(r'^==', body, flags=re.M)[0])
                src = next((f[k] for k in (f'Original_Source_Link_{n}' for n in range(1, 6))
                            if (f.get(k) or '').startswith('http')), None)
                names, links = _party_fields(f)
                authority = clean(f.get('DPA_With_Country') or f.get('DPA_Abbrevation'))
                checked = db.execute('SELECT controller FROM party_checks WHERE case_id = ?', (page['title'],)).fetchone()
                controller = checked['controller'] if checked else resolve_controller(party_roles(page['title'], authority, names))[0]
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
    return checked['controller'] if checked else resolve_controller(roles)[0]


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


def step_parties_review(db):
    """Write review/parties.csv: the cases where the rules could not tell who the decision is about.
    Fill in the 'controller' column (or write NONE), then run: python pharos.py parties-apply"""
    os.makedirs(REVIEW_DIR, exist_ok=True)
    path = os.path.join(REVIEW_DIR, 'parties.csv')
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
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['case_id', 'fino_id', 'regulator', 'date', 'fine_eur', 'parties',
                                          'controller', 'note', 'gdprhub'])
        w.writeheader()
        w.writerows(out)
    print(f'  {len(out)} cases to check, written to {path}')


def step_parties_apply(db):
    """Read review/parties.csv and save every filled-in answer as a check that wins over the rules."""
    path = os.path.join(REVIEW_DIR, 'parties.csv')
    with Run(db, 'parties-apply') as run, open(path, encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            answer = (r.get('controller') or '').strip()
            if not answer:
                continue
            run.fetched += 1
            controller = None if answer.upper() == 'NONE' else answer
            db.execute('INSERT INTO party_checks (case_id, controller, note, checked_at) VALUES (?,?,?,?) '
                       'ON CONFLICT(case_id) DO UPDATE SET controller = excluded.controller, note = excluded.note, '
                       'checked_at = excluded.checked_at', (r['case_id'], controller, (r.get('note') or '').strip() or None, now()))
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
            for r in rows:
                r['parties_named'] = named.get(r['case_id'])
                r['has_own_summary'] = r['case_id'] in own
            export_site(rows, meta)
            run.notes.append(f'website data written to {SITE_DATA_DIR}')


SITE_FIELDS = ['pharos_id', 'case_id', 'source', 'country', 'country_code', 'authority', 'decision_date',
               'date_precision', 'fine_eur', 'fine_original', 'currency', 'controller', 'sector_tag', 'articles',
               'categories', 'violation_type', 'best_outcome', 'source_url', 'source_page', 'gdprhub_page',
               'summary_by', 'parties_named']


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
    site = {'meta': meta | {'fields': SITE_FIELDS, 'summary_shard': SUMMARY_SHARD},
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
                                    'parties-review', 'parties-apply', 'link', 'normalise', 'export', 'stats', 'update'])
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
        elif a.step == 'link':
            step_link(db)
        elif a.step == 'normalise':
            step_normalise(db)
        elif a.step == 'export':
            step_export(db)
        elif a.step == 'stats':
            step_stats(db)
        elif a.step == 'update':
            for step in (step_cms, step_gdprhub, step_link, step_normalise, step_export):
                step(db)
            step_stats(db)
    finally:
        db.close()


if __name__ == '__main__':
    sys.exit(main())
