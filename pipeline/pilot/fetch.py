"""Pilot, step 1: download the original of each chosen decision and turn it into plain text.

  python fetch.py            reads pick.json, writes <slug>/source.(pdf|html), <slug>/text.txt and fetch_report.json

Nothing is guessed: a page that is not the decision itself (a press release, an annual report) is recorded as such
and the links on it that look like the decision are listed, so the next step can follow them.
"""
import json, os, re, sys, html, urllib.request, urllib.error, ssl

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from pharos import USER_AGENT

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept-Language': 'en,*;q=0.5'})
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read(), r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get('Content-Type', '') if e.headers else '', b'', url
    except urllib.error.URLError as e:
        if not isinstance(e.reason, ssl.SSLError): return 0, str(e)[:120], b'', url
        # some regulators' certificates are incomplete; the text is public, so retry without the chain check
        with urllib.request.urlopen(req, timeout=60, context=ssl._create_unverified_context()) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read(), r.geturl()
    except ssl.SSLError:
        # some regulators' certificates are incomplete; the text is public, so retry without the chain check
        with urllib.request.urlopen(req, timeout=60, context=ssl._create_unverified_context()) as r:
            return r.status, r.headers.get('Content-Type', ''), r.read(), r.geturl()
    except Exception as e:
        return 0, str(e)[:120], b'', url

def pdf_text(data):
    import pymupdf
    doc = pymupdf.open(stream=data, filetype='pdf')
    pages = [p.get_text() for p in doc]
    return '\n'.join(pages), len(pages)

def html_text(data):
    s = data.decode('utf-8', errors='replace')
    s = re.sub(r'(?is)<(script|style|nav|header|footer|noscript)\b.*?</\1>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</(p|div|li|h\d|tr)>', '\n', s)
    s = html.unescape(re.sub(r'<[^>]+>', ' ', s))
    s = re.sub(r'[ \t\xa0]+', ' ', s)
    return re.sub(r'\n\s*\n+', '\n\n', s).strip()

def doc_links(data, base):
    """Links on a page that may lead to the decision itself (PDFs, 'decision'/'Bescheid'/'besluit' ...)."""
    s = data.decode('utf-8', errors='replace')
    out = []
    for href, label in re.findall(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', s, re.S):
        label = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', label))).strip()
        if re.search(r'\.pdf\b|decision|d[ée]cision|besluit|beslut|bescheid|decisi|rozhodnut|decyzj|hat[aá]roz|odluk|απόφαση', href + ' ' + label, re.I):
            out.append([urllib.parse.urljoin(base, html.unescape(href)), label[:120]])
    return out[:40]

import urllib.parse
pick = json.load(open(os.path.join(HERE, 'pick.json'), encoding='utf-8'))
old = {r['pharos_id']: r for r in json.load(open(os.path.join(HERE, 'fetch_report.json'), encoding='utf-8'))} if os.path.exists(os.path.join(HERE, 'fetch_report.json')) else {}
report = []
for pid, cc, date, fine, controller, source, url in pick:
    if old.get(pid, {}).get('http') == 200 and '--all' not in sys.argv:
        report.append(old[pid]); continue
    slug = pid.replace('/', '-')
    d = os.path.join(HERE, slug); os.makedirs(d, exist_ok=True)
    status, ctype, data, final = get(url)
    row = {'pharos_id': pid, 'country': cc, 'url': url, 'final_url': final, 'http': status, 'type': ctype.split(';')[0]}
    if status == 200 and data:
        is_pdf = data[:5] == b'%PDF-' or 'pdf' in ctype
        open(os.path.join(d, 'source.pdf' if is_pdf else 'source.html'), 'wb').write(data)
        if is_pdf:
            text, n = pdf_text(data); row['pages'] = n
        else:
            text = html_text(data); row['links'] = doc_links(data, final)
        open(os.path.join(d, 'text.txt'), 'w', encoding='utf-8').write(text)
        row['chars'] = len(text)
        row['scanned'] = bool(is_pdf and len(text.strip()) < 300 * max(1, row.get('pages', 1)) / 3)
    report.append(row)
    print(f"{pid:13} {cc} http={status} {row['type'][:24]:24} chars={row.get('chars', 0):>8} pages={row.get('pages', '-')}{' SCANNED?' if row.get('scanned') else ''}")
json.dump(report, open(os.path.join(HERE, 'fetch_report.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
