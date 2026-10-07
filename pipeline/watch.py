"""The daily watch: what did the regulators publish since yesterday?

Each channel in watch_channels.csv is one way to see a regulator's new publications: an RSS feed, a sitemap
(only the pages matching 'match'), a list page (the links matching 'match') or an API. Every item seen is
remembered in gdpr.db (watch_seen); a run reports only what is new, plus decision pages whose 'last changed'
date moved (the Belgian authority adds appeal notes to page 1 that way). The first run of a channel only
records what is there (no flood of old items).

    python watch.py            check every channel, slowly; new items go to review/watch.xlsx
    python watch.py DK EDPB    only these regulators
    python watch.py status     how each channel did on its last run

Runs every morning at 07:30 as the Windows task 'Fino Watch' (pythonw, output in watch.log).

A channel that refuses even after the slow retries is flagged in the 'blocked' table (review/blocked.xlsx).
"""
import csv
import html
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pharos

CHANNELS = os.path.join(pharos.HERE, 'watch_channels.csv')
UA = 'Mozilla/5.0 (Fino, a free GDPR decisions database; one daily visit)'
DELAY = 3  # seconds between requests to the same site
LOOSE = ssl._create_unverified_context()  # some regulators serve an incomplete certificate chain (AEPD)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch_seen (
    channel    TEXT NOT NULL,
    url        TEXT NOT NULL,
    title      TEXT,
    lastmod    TEXT,       -- the sitemap's or feed's date for the item, when it gives one
    first_seen TEXT NOT NULL,
    changed_at TEXT,       -- when lastmod last moved after we first saw it
    baseline   INTEGER NOT NULL DEFAULT 0,  -- 1 = already there on the channel's first run
    PRIMARY KEY (channel, url)
);
CREATE TABLE IF NOT EXISTS watch_runs (
    channel  TEXT NOT NULL,
    run_at   TEXT NOT NULL,
    items    INTEGER,
    new      INTEGER,
    changed  INTEGER,
    status   TEXT
);
"""


def get(url):
    """Politely: wait, then try; after a refusal wait 1 and 5 minutes. Returns text, or None."""
    for wait in (0, 60, 300):
        time.sleep(DELAY + wait)
        for ctx in (None, LOOSE):
            try:
                r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60, context=ctx)
                return r.read(60_000_000).decode('utf-8', 'replace')
            except urllib.error.HTTPError as e:
                if e.code in (403, 404, 410):
                    return None
                break
            except Exception as e:
                if 'CERTIFICATE' in str(e) and ctx is None:
                    continue
                break
    return None


def text(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


# ----- one reader per kind of channel: each returns [(url, title, lastmod)] -----
def read_rss(ch):
    t = get(ch['url'])
    if t is None:
        return None
    items = []
    for b in re.findall(r'<item\b.*?</item>|<entry\b.*?</entry>', t, re.S):
        link = re.search(r'<link>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</link>|<link[^>]+href="([^"]+)"', b, re.S)
        title = re.search(r'<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>', b, re.S)
        date = re.search(r'<(?:pubDate|updated|published|dc:date)>(.*?)</', b, re.S)
        if link:
            items.append((html.unescape((link.group(1) or link.group(2)).strip()), text(title.group(1)) if title else None,
                          date.group(1).strip() if date else None))
    return items


def read_sitemap(ch, url=None, depth=0):
    t = get(url or ch['url'])
    if t is None:
        return None if depth == 0 else []
    if '<sitemapindex' in t and depth < 2:
        out = []
        for child in re.findall(r'<loc>\s*(.*?)\s*</loc>', t):
            out += read_sitemap(ch, html.unescape(child), depth + 1) or []
        return out
    out = []
    for b in re.findall(r'<url>.*?</url>', t, re.S):
        loc = re.search(r'<loc>\s*(.*?)\s*</loc>', b, re.S)
        mod = re.search(r'<lastmod>\s*(.*?)\s*</lastmod>', b, re.S)
        if loc:
            out.append((html.unescape(loc.group(1)), None, mod.group(1) if mod else None))
    return out


def read_page(ch):
    t = get(ch['url'])
    if t is None:
        return None
    out = []
    for m in re.finditer(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', t, re.S | re.I):
        out.append((urllib.parse.urljoin(ch['url'], html.unescape(m.group(1))), text(m.group(2))[:200] or None, None))
    return out


def read_ris(ch):
    """Austria: the RIS open-data API, newest 100 decisions of the DSB."""
    t = get(ch['url'])
    if t is None:
        return None
    docs = json.loads(t)['OgdSearchResult']['OgdDocumentResults'].get('OgdDocumentReference') or []
    out = []
    for d in docs if isinstance(docs, list) else [docs]:
        m = d['Data']['Metadaten']
        gz = (m['Judikatur'].get('Geschaeftszahl') or {}).get('item')
        out.append((m['Allgemein'].get('DokumentUrl'), f"{gz if isinstance(gz, str) else ', '.join(gz or [])} ({m['Judikatur'].get('Entscheidungsdatum')})",
                    m['Allgemein'].get('Geaendert')))
    return out


def read_dila(ch):
    """France: the DILA folder of CNIL open data; each new daily file is an item."""
    t = get(ch['url'])
    if t is None:
        return None
    return [(urllib.parse.urljoin(ch['url'], f), f, None) for f in sorted(set(re.findall(r'href="(CNIL_[\d-]+\.tar\.gz)"', t)))]


def read_mediawiki(ch):
    """GDPRhub: pages created recently, through the same API route as the import (pharos._hub)."""
    time.sleep(DELAY)
    j = pharos._hub({'action': 'query', 'list': 'recentchanges', 'rctype': 'new', 'rcnamespace': 0, 'rclimit': 500})
    return [('https://gdprhub.eu/index.php?title=' + urllib.parse.quote(c['title'].replace(' ', '_')), c['title'], c['timestamp'])
            for c in j.get('query', {}).get('recentchanges', [])]


READERS = {'rss': read_rss, 'sitemap': read_sitemap, 'page': read_page, 'ris': read_ris, 'dila': read_dila,
           'mediawiki': read_mediawiki}


def channels():
    with open(CHANNELS, encoding='utf-8') as f:
        return [r for r in csv.DictReader(f) if r['code'] and not r['code'].startswith('#')]


def run(db, only=()):
    now = pharos.now()
    found = []
    for ch in channels():
        if only and ch['code'] not in only:
            continue
        name = f"{ch['code']}:{ch['kind']}:{ch['url']}"
        try:
            items = READERS[ch['kind']](ch)
        except Exception as e:  # a page that changed shape: flagged like a refusal, the run goes on
            print(f"  {ch['code']:5} {ch['kind']:9} error: {str(e)[:80]}", flush=True)
            items = None
        if items is None:
            db.execute('INSERT INTO watch_runs VALUES (?,?,?,?,?,?)', (name, now, None, None, None, 'refused'))
            db.execute("INSERT INTO blocked (url, publisher, problem, first_seen, last_tried) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(url) DO UPDATE SET problem = excluded.problem, last_tried = excluded.last_tried",
                       (ch['url'], ch['code'], f"watch: no answer ({ch['kind']})", now, now))
            db.commit()
            print(f"  {ch['code']:5} {ch['kind']:9} refused", flush=True)
            continue
        if ch['match']:
            pat = re.compile(ch['match'], re.I)
            items = [i for i in items if pat.search(urllib.parse.unquote(i[0]))]
        items = list({i[0]: i for i in items}.values())
        first = not db.execute('SELECT 1 FROM watch_seen WHERE channel = ? LIMIT 1', (name,)).fetchone()
        seen = {r[0]: r[1] for r in db.execute('SELECT url, lastmod FROM watch_seen WHERE channel = ?', (name,))}
        new = changed = 0
        for url, title, mod in items:
            if url not in seen:
                db.execute('INSERT INTO watch_seen (channel, url, title, lastmod, first_seen, baseline) VALUES (?,?,?,?,?,?)',
                           (name, url, title, mod, now, int(first)))
                if not first:
                    new += 1
                    found.append({'found': now[:10], 'regulator': ch['code'], 'what': 'new', 'title': title or '',
                                  'url': url, 'channel': ch['kind'], 'note': ch['note']})
            elif mod and seen[url] and mod[:10] != seen[url][:10]:
                db.execute('UPDATE watch_seen SET lastmod = ?, changed_at = ? WHERE channel = ? AND url = ?', (mod, now, name, url))
                if ch.get('changes') == 'yes':
                    changed += 1
                    found.append({'found': now[:10], 'regulator': ch['code'], 'what': 'changed', 'title': title or '',
                                  'url': url, 'channel': ch['kind'], 'note': ch['note']})
        db.execute('INSERT INTO watch_runs VALUES (?,?,?,?,?,?)', (name, now, len(items), new, changed, 'baseline' if first else 'ok'))
        db.commit()
        print(f"  {ch['code']:5} {ch['kind']:9} {len(items):6} items, " + ('first run: recorded' if first else f'{new} new, {changed} changed'), flush=True)
    if found:
        old = pharos.review_rows('watch') if os.path.exists(os.path.join(pharos.REVIEW_DIR, 'watch.xlsx')) else []
        rows = found + [r for r in old if r.get('url')][:2000]
        pharos.review_write('watch', list(found[0]), rows, how_to=[
            'What the regulators published, newest first, as the daily watch found it.',
            "'new' = a page or feed item not seen before; 'changed' = a decision page whose date moved (appeal notes, corrections).",
            'Nothing to fill in: new decisions go to the inventory and the reading queue.'])
    print(f'  {len(found)} new or changed items' + (' -> review/watch.xlsx' if found else ''))


def status(db):
    for r in db.execute("""SELECT channel, run_at, items, new, changed, status FROM watch_runs w
                           WHERE run_at = (SELECT MAX(run_at) FROM watch_runs x WHERE x.channel = w.channel) ORDER BY channel"""):
        print(f"  {r[5]:8} {str(r[2] or ''):>6} items {str(r[3] or 0):>4} new  {r[0][:110]}")


if __name__ == '__main__':
    if sys.stdout is None:  # started by the daily task with pythonw (no console): write to watch.log
        sys.stdout = sys.stderr = open(os.path.join(pharos.HERE, 'watch.log'), 'a', encoding='utf-8')
        print(f'--- {pharos.now()}')
    db = sqlite3.connect(pharos.DB_FILE)
    db.executescript(SCHEMA)
    import inventory  # the 'blocked' table lives there
    db.executescript(inventory.SCHEMA)
    args = sys.argv[1:]
    status(db) if args == ['status'] else run(db, set(args))
