"""Which Italian decisions has the Garante taken down after losing in court?

The Garante replaces a decision it lost in court with one sentence ("Il provvedimento n. ... è stato rimosso
dal sito web ... a seguito di sentenza definitiva sfavorevole al Garante"). This visits the page of every
Italian case in Fino once (1.5 s apart; DELAY=4 to go slower) and lists the ones carrying that notice, or any other removal notice,
in review/italy-removed.xlsx. Nothing on the website changes; Lorenzo decides how to show them.

    python pilot/garante_removed.py          (resumes where it stopped: pages already read are cached)
"""
import html
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import pharos  # noqa: E402

CACHE = os.path.join(HERE, 'garante', 'status.json')  # docweb number -> what the page says (gitignored folder)
URL = 'https://www.garanteprivacy.it/web/guest/home/docweb/-/docweb-display/docweb/{}'
# 'Il provvedimento n. 577 del ... è stato rimosso ... a seguito di sentenza ...': numbers have dots ('n. 577',
# 'Milano n. 123'), so the notice is taken whole and cut where the page's script code starts
NOTICE = re.compile(r"(Il provvedimento.{0,160}?(?:è|e'|é) stat[oa] rimoss[oa].{0,400})", re.I)
NOTICE_END = re.compile(r'\s(?:function|var|Condividi|//)\s')


def read(n):
    req = urllib.request.Request(URL.format(n), headers={'User-Agent': 'Mozilla/5.0 (Fino research; one request per page)'})
    try:
        raw = urllib.request.urlopen(req, timeout=40).read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return {'state': f'http {e.code}'}
    except Exception as e:  # network trouble: not cached, tried again on the next run
        return {'state': 'error', 'error': str(e)[:120]}
    text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', raw)))
    m = NOTICE.search(text)
    if m:
        return {'state': 'removed', 'notice': NOTICE_END.split(m.group(1))[0].strip()}
    if f'[doc. web n. {n}]' in text or 'IL GARANTE PER LA PROTEZIONE' in text.upper():
        return {'state': 'online'}
    return {'state': 'unclear', 'start': text[:200]}


def main():
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    cache = json.load(open(CACHE, encoding='utf-8')) if os.path.exists(CACHE) else {}
    db = sqlite3.connect(pharos.DB_FILE)
    db.row_factory = sqlite3.Row
    cases = {}
    for r in db.execute("SELECT * FROM v_cases WHERE country_code = 'IT' AND source_url LIKE '%docweb%'"):
        m = re.search(r'docweb/(\d+)', r['source_url'])
        if m:
            cases.setdefault(m.group(1), []).append(r)
    todo = [n for n in cases if n not in cache or cache[n]['state'] in ('error', 'unclear')]
    print(f'{len(cases)} Garante documents, {len(todo)} to read')
    for i, n in enumerate(todo, 1):
        cache[n] = read(n)
        if i % 25 == 0 or cache[n]['state'] != 'online':
            print(f'  {i}/{len(todo)} {n}: {cache[n]["state"]}', flush=True)
            json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
        time.sleep(float(os.environ.get('DELAY', 1.5)))
    json.dump(cache, open(CACHE, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)

    from collections import Counter
    print(Counter(v['state'] for v in cache.values()))
    rows = []
    for n, rs in sorted(cases.items()):
        st = cache.get(n, {})
        if st.get('state') == 'online':
            continue
        for r in rs:
            rows.append({'fino_id': r['pharos_id'], 'case_id': r['case_id'], 'date': r['decision_date'],
                         'fine_eur': r['fine_eur'], 'controller': r['controller'] or '', 'source': r['source'],
                         'garante_page': URL.format(n), 'what_the_page_says': st.get('state', 'not read'),
                         'notice': st.get('notice') or st.get('start') or st.get('error') or '', 'show_as': '', 'note': ''})
    fields = list(rows[0]) if rows else ['fino_id']
    path = pharos.review_write('italy-removed', fields, rows, answer=('show_as', 'note'),
                               how_to=['Decisions the Garante took down, or whose page could not be read.',
                                       "show_as: 'annulled' (a court annulled it), 'keep' (show as now), or your own words.",
                                       'Nothing changes on the website until we decide together how to show them.'])
    print(f'{len(rows)} rows written to {path}')


if __name__ == '__main__':
    main()
