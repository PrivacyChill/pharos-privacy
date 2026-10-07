"""The inventory: every published decision we know of, before anyone reads it.

One row per decision per publisher (reference, country, date, outcome, link), collected slowly from lists that
may be reused: regulators' own lists, the EDPB register, CMS and GDPRhub (already in gdpr.db). Never copied from
another database whose maker has not allowed it (The DPO: its public counts only, see sources.md).
A list or link we cannot open is flagged in review/blocked.xlsx for Lorenzo, never worked around.

    python inventory.py edpb          the EDPB register of final one-stop-shop decisions (~1,600, ~10 minutes)
    python inventory.py gdprhub       GDPRhub's court judgments (with the decision they rule on) and appeal fields
    python inventory.py cnil          the CNIL's sanction-type decisions from the French open data
    python inventory.py report        what the inventory holds, per country, next to Fino
    python inventory.py blocked       write review/blocked.xlsx
"""
import glob
import html
import json
import os
import re
import sqlite3
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request

import pharos

UA = 'Mozilla/5.0 (Fino, a free GDPR decisions database; slow, one page at a time)'
BACKOFF = (60, 300, 900)  # seconds to wait after a refusal before trying again; then the page is flagged

SCHEMA = """
CREATE TABLE IF NOT EXISTS inventory (
    publisher     TEXT NOT NULL,            -- who lists it: 'edpb', 'cnil', 'gdprhub-court', ...
    ref           TEXT NOT NULL,            -- the publisher's own number for the decision
    country_code  TEXT,
    authority     TEXT,
    decision_date TEXT,
    party         TEXT,                     -- as the list names it; often empty (the EDPB redacts)
    outcome       TEXT,
    articles      TEXT,
    url           TEXT,                     -- the decision itself, when the list links it
    page          TEXT,                     -- the list page it was found on
    extra         TEXT,                     -- anything else the list gives, as JSON
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    PRIMARY KEY (publisher, ref)
);
CREATE TABLE IF NOT EXISTS blocked (
    url        TEXT PRIMARY KEY,
    publisher  TEXT,
    problem    TEXT,
    first_seen TEXT NOT NULL,
    last_tried TEXT NOT NULL
);
"""


def connect():
    db = pharos.connect() if hasattr(pharos, 'connect') else sqlite3.connect(pharos.DB_FILE)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def get(db, url, publisher, delay):
    """One page, politely: wait `delay` first; after a refusal wait 1, 5, 15 minutes; then flag it and go on."""
    for wait in (0,) + BACKOFF:
        if wait:
            print(f'    refused ({problem}), waiting {wait // 60} min', flush=True)
        time.sleep(delay + wait)
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60).read()
            db.execute('DELETE FROM blocked WHERE url = ?', (url,))
            return raw.decode('utf-8', 'replace')
        except urllib.error.HTTPError as e:
            problem = f'HTTP {e.code}'
            if e.code == 404:
                break
        except Exception as e:
            problem = str(e)[:100]
    db.execute('INSERT INTO blocked (url, publisher, problem, first_seen, last_tried) VALUES (?,?,?,?,?) '
               'ON CONFLICT(url) DO UPDATE SET problem = excluded.problem, last_tried = excluded.last_tried',
               (url, publisher, problem, pharos.now(), pharos.now()))
    db.commit()
    return None


def save(db, row):
    ts = pharos.now()
    row = {k: row.get(k) for k in ('publisher', 'ref', 'country_code', 'authority', 'decision_date', 'party',
                                   'outcome', 'articles', 'url', 'page', 'extra')}
    db.execute(f"""INSERT INTO inventory ({', '.join(row)}, first_seen, last_seen) VALUES ({', '.join('?' * len(row))}, ?, ?)
                   ON CONFLICT(publisher, ref) DO UPDATE SET {', '.join(f'{k} = excluded.{k}' for k in row if k not in ('publisher', 'ref'))},
                   last_seen = excluded.last_seen""", (*row.values(), ts, ts))


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


# ----- THE EDPB REGISTER -----
EDPB = 'https://www.edpb.europa.eu/registers/register-of-final-one-stop-shop-decisions_en?page={}'


def edpb(db, delay=3.0):
    """Each entry: EDPB number, date, lead authority, the decision PDF, articles, concerned authorities, topics,
    outcome. No party names (the register redacts them); those come from reading the PDF."""
    page, total = 0, None
    while True:
        url = EDPB.format(page)
        t = get(db, url, 'edpb', delay)
        if t is None:
            page += 1
            continue
        if total is None:
            m = re.search(r'(\d[\d,]*) items', text(t))
            total = int(m.group(1).replace(',', '')) if m else None
            print(f'  EDPB register: {total} decisions', flush=True)
        blocks = t.split('<div  class="foss-decision-teaser">')[1:]
        if not blocks:
            break
        for b in blocks:
            field = lambda name: (re.search(rf'class="foss-decision[-\w]*__{name}[^"]*"[^>]*>(.*?)</(?:div|dd|ul)>', b, re.S) or [None, ''])[1]
            ref = text(field('id'))
            date = re.search(r'<time datetime="(\d{4}-\d\d-\d\d)', b)
            lead = re.search(r'teaser__lead-sa.*?member-country-token__code">\s*([\w/-]+)', b, re.S)
            pdfs = re.findall(r'href="(/system/files/[^"]+)"', b)
            value = lambda name: (re.search(rf'teaser__{name}-value[^"]*"[^>]*>(.*?)(?=<div class="foss-decision-teaser__[\w-]+-label|</details>)', b, re.S) or [None, ''])[1]
            labels = {'main legal reference': value('main-legel-ref'), 'outcome': value('outcome')}
            # the lead authority's flag uses the same classes, so concerned authorities are read after their label only
            csa = re.findall(r'member-country-token__code">\s*([\w/-]+)', b.split('concerned-sa-label', 1)[-1].split('main-legel-ref-label')[0]) if 'concerned-sa-label' in b else []
            topics = [text(x) for x in re.findall(r'relevant-topics-list-item-link[^>]*>(.*?)</a>', b, re.S)]
            arts = re.findall(r'Article (\d+)', text(labels.get('main legal reference', '')))
            cc = (lead.group(1) if lead else '').upper()
            save(db, {'publisher': 'edpb', 'ref': ref, 'country_code': cc.split('/')[0] or None,
                      'authority': cc or None, 'decision_date': date.group(1) if date else None,
                      'outcome': text(labels.get('outcome', '')) or None, 'articles': ', '.join(dict.fromkeys(arts)) or None,
                      'url': 'https://www.edpb.europa.eu' + pdfs[0] if pdfs else None, 'page': url,
                      'extra': json.dumps({'concerned': sorted({c.upper() for c in csa}), 'topics': topics,
                                           'files': ['https://www.edpb.europa.eu' + p for p in dict.fromkeys(pdfs)]})})
        db.commit()
        print(f'  page {page + 1}: {db.execute("SELECT COUNT(*) FROM inventory WHERE publisher = ?", ("edpb",)).fetchone()[0]} so far', flush=True)
        page += 1


# ----- GDPRHUB: COURT JUDGMENTS AND APPEAL FIELDS (CC BY-NC-SA, like the decisions Fino already imports) -----
def gdprhub(db, delay=1.5):
    """Court pages (COURTdecisionBOX) as 'gdprhub-court', with the decision they rule on (Appeal_From_*);
    and, for regulator pages, their Appeal_To_* fields when filled, as 'gdprhub-appeal'."""
    titles = []
    for year in range(2018, int(pharos.now()[:4]) + 1):
        cont = None
        while True:
            params = {'action': 'query', 'list': 'categorymembers', 'cmtitle': f'Category:{year}', 'cmlimit': 500, 'cmtype': 'page'}
            if cont:
                params['cmcontinue'] = cont
            j = pharos._hub(params)
            titles += [m['title'] for m in j.get('query', {}).get('categorymembers', [])]
            cont = j.get('continue', {}).get('cmcontinue')
            time.sleep(delay)
            if not cont:
                break
    titles = list(dict.fromkeys(titles))
    print(f'  GDPRhub: {len(titles)} pages', flush=True)
    courts = appeals = 0
    for i in range(0, len(titles), 50):
        j = pharos._hub({'action': 'query', 'titles': '|'.join(titles[i:i + 50]), 'prop': 'revisions',
                         'rvprop': 'content', 'formatversion': 2})
        for p in j.get('query', {}).get('pages', []):
            t = (p.get('revisions') or [{}])[0].get('content') or ''
            f = {k: v.strip() for k, v in re.findall(r'^\|([A-Za-z_0-9]+)=(.*)$', t, re.M)}
            page = 'https://gdprhub.eu/index.php?title=' + urllib.parse.quote(p['title'].replace(' ', '_'))
            appeal = {k: f[k] for k in f if k.startswith('Appeal_') and f[k] and f[k] != 'Unknown'}
            date, _ = pharos.parse_date(f.get('Date_Decided'))
            src = next((f[k] for k in (f'Original_Source_Link_{n}' for n in range(1, 6)) if f.get(k, '').startswith('http')), None)
            if 'COURTdecisionBOX' in t:
                body = re.sub(r'\{\{COURTdecisionBOX.*?\n\}\}', '', t, flags=re.S)
                save(db, {'publisher': 'gdprhub-court', 'ref': p['title'], 'country_code': pharos.country_code(f.get('Jurisdiction')),
                          'authority': f.get('Court_With_Country') or f.get('Court_English_Name'), 'decision_date': date,
                          'party': f.get('Party_Name_1') or None, 'url': src, 'page': page,
                          'articles': ', '.join(re.findall(r'Article (\d+)', ' '.join(f.get(f'GDPR_Article_{n}', '') for n in range(1, 31)))) or None,
                          'extra': json.dumps({'ecli': f.get('ECLI'), 'appeal': appeal,
                                               'gist': (pharos._wiki_to_text(re.split(r'^==', body, flags=re.M)[0]) or '')[:400]}, ensure_ascii=False)})
                courts += 1
            elif 'DPAdecisionBOX' in t and appeal:
                save(db, {'publisher': 'gdprhub-appeal', 'ref': p['title'], 'country_code': pharos.country_code(f.get('Jurisdiction')),
                          'authority': f.get('DPA_With_Country'), 'decision_date': date, 'url': src, 'page': page,
                          'outcome': appeal.get('Appeal_To_Status'), 'extra': json.dumps({'appeal': appeal}, ensure_ascii=False)})
                appeals += 1
        db.commit()
        time.sleep(delay)
    print(f'  {courts} court judgments, {appeals} regulator decisions with appeal fields', flush=True)


# ----- CNIL (FRANCE): THE DILA OPEN DATA, LICENCE OUVERTE (reuse allowed, commercial included, with credit) -----
CNIL_DIR = 'https://echanges.dila.gouv.fr/OPENDATA/CNIL/'
CNIL_CACHE = os.path.join(pharos.HERE, 'cache', 'cnil')
CNIL_KINDS = re.compile(r'sanction|avertissement|mise en demeure|cl.ture|rappel|injonction|amende', re.I)  # decisions, not opinions


def cnil(db):
    """The full dump plus every daily update since, unpacked into cache/cnil; one row per sanction-type text
    decided since the GDPR applied (25 May 2018). The title names the organisation."""
    os.makedirs(CNIL_CACHE, exist_ok=True)
    listing = urllib.request.urlopen(CNIL_DIR, timeout=60).read().decode()
    files = sorted(set(re.findall(r'href="((?:Freemium_cnil_global|CNIL)_[\d-]+\.tar\.gz)"', listing)))
    full = [f for f in files if f.startswith('Freemium')][-1]
    for f in [full] + [f for f in files if f.startswith('CNIL_') and f[5:13] >= full[20:28]]:
        path = os.path.join(CNIL_CACHE, f)
        if not os.path.exists(path):
            time.sleep(1)
            urllib.request.urlretrieve(CNIL_DIR + f, path)
            with tarfile.open(path) as tar:
                tar.extractall(CNIL_CACHE, filter='data')
    n = 0
    for f in glob.glob(os.path.join(CNIL_CACHE, 'cnil', '**', 'CNILTEXT*.xml'), recursive=True):
        t = open(f, encoding='utf-8').read(6000)
        g = lambda tag: (re.search(rf'<{tag}>(.*?)</{tag}>', t, re.S) or [None, None])[1]
        kind, date = g('NATURE_DELIB') or '', g('DATE_TEXTE') or ''
        if not CNIL_KINDS.search(kind) or date < '2018-05-25':
            continue
        save(db, {'publisher': 'cnil', 'ref': g('ID'), 'country_code': 'FR', 'authority': 'CNIL', 'decision_date': date,
                  'party': text(g('TITREFULL') or '')[:300] or None, 'outcome': kind,
                  'url': 'https://www.legifrance.gouv.fr/cnil/id/' + g('ID'), 'page': CNIL_DIR,
                  'extra': json.dumps({'numero': g('NUMERO'), 'title': g('TITRE')}, ensure_ascii=False)})
        n += 1
    db.commit()
    print(f'  CNIL: {n} sanction-type texts since 25 May 2018', flush=True)


def report(db):
    fino = dict(db.execute('SELECT country_code, COUNT(*) FROM v_cases GROUP BY 1'))
    print(f"{'':4}{'Fino':>6}  inventory by publisher")
    rows = db.execute('SELECT country_code, publisher, COUNT(*) n FROM inventory GROUP BY 1, 2 ORDER BY 1').fetchall()
    for cc in sorted({r['country_code'] or '?' for r in rows} | {k or '?' for k in fino}):
        parts = ', '.join(f"{r['publisher']} {r['n']}" for r in rows if (r['country_code'] or '?') == cc)
        print(f'{cc:4}{fino.get(cc, 0):>6}  {parts}')


def blocked(db):
    rows = [dict(r) | {'what_you_found': '', 'note': ''} for r in db.execute('SELECT * FROM blocked ORDER BY publisher, url')]
    rows += [{'url': u, 'publisher': p, 'problem': why, 'first_seen': '7 Oct 2026', 'last_tried': '7 Oct 2026',
              'what_you_found': '', 'note': ''} for p, u, why in KNOWN_BLOCKED
             if not any(r['url'] == u for r in rows)]
    path = pharos.review_write('blocked', list(rows[0]), rows, answer=('what_you_found', 'note'),
                               how_to=['Lists and decisions Fino could not open automatically, even slowly.',
                                       'If you can open one in your browser, write what you see (or a better link) in what_you_found.',
                                       'Nothing here is worked around without your OK.'])
    print(f'  {len(rows)} rows written to {path}')


# found by hand on 7 Oct 2026 (sources.md, the pilot)
KNOWN_BLOCKED = [
    ('vdai', 'https://vdai.lrv.lt/', 'Cloudflare check: refuses automated visits (Lithuania, 443 decisions at The DPO, 36 in Fino)'),
    ('dsb', 'https://www.ris.bka.gv.at/Dsk/', 'Security check on the website; the RIS open-data API works instead'),
    ('cnil', 'https://www.legifrance.gouv.fr/cnil/', 'HTTP 403 for automated visits; the CNIL open data on data.gouv.fr is the route'),
    ('uodo', 'https://uodo.gov.pl/decyzje/', 'HTTP 500 twice in the pilot (2 decisions)'),
    ('garante', 'https://www.garanteprivacy.it/', 'Stops answering after ~700 quick requests; works again at 4 seconds a page'),
]

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'report'
    db = connect()
    {'edpb': edpb, 'gdprhub': gdprhub, 'cnil': cnil, 'report': report, 'blocked': blocked}[cmd](db)
