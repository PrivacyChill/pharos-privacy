"""Preamble of the GDPR (citations + 173 recitals) from the Official Journal text,
with the 2018 corrigendum to recital 71 applied. Adds it to gdpr.json."""
import re, json, html, io, contextlib
import xml.etree.ElementTree as ET

with contextlib.redirect_stdout(io.StringIO()):
    import gdpr_parse  # reuse its text() and link() helpers
from gdpr_parse import link, esc

raw = open('gdpr_cellar.html', encoding='utf-8').read()
# drop footnote call-outs like "\xa0<a ...>(<span class="oj-super oj-note-tag">1</span>)</a>"
raw = re.sub(r'\s*<a id="ntc[^"]*"[^>]*>\s*\(<span class="oj-super oj-note-tag">\d+</span>\)</a>', '', raw)
root = ET.fromstring(raw.encode('utf-8'))
NS = '{http://www.w3.org/1999/xhtml}'

def text(e):
    s = ''.join(e.itertext()).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s).strip()

pbl = next(e for e in root.iter() if e.get('id') == 'pbl_1')
opening, citations, closing, recitals = None, [], None, {}
for x in pbl:
    i = x.get('id') or ''
    if x.tag == NS + 'p':
        s = text(x)
        if s.startswith('THE EUROPEAN PARLIAMENT'): opening = s
        elif s.startswith('HAVE ADOPTED'): closing = s
        elif s == 'Whereas:': pass
    elif i.startswith('cit_'):
        citations.append(text(x))
    elif i.startswith('rct_'):
        n = int(i[4:])
        cells = [td for td in x.iter(NS + 'td')]
        paras = [text(p) for p in cells[1].iter(NS + 'p')]
        recitals[n] = [p for p in paras if p]

# Corrigendum OJ L 127, 23.5.2018, p. 2: recital 71, fifth and sixth sentences
corr = open('corr_32016R0679R%2802%29.html', encoding='utf-8').read()
cs = re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', corr))).replace('\xa0', ' ')
m = re.search(r'recital 71, fifth and sixth sentences: for: ‘\(71\) … (.*?)’, read: ‘\(71\) … (.*?)’\.', cs)
old, new = m.group(1).strip(), m.group(2).strip()
r71 = ' '.join(recitals[71])
assert old in r71, 'recital 71 text not found'
recitals[71] = [r71.replace(old, new)]

assert len(recitals) == 173 and all(recitals[n] for n in range(1, 174))
pre = {
    'opening': opening, 'citations': citations, 'closing': closing,
    'recitals': {n: ''.join(f'<p>{link(p)}</p>' for p in ps) for n, ps in recitals.items()},
    'corrected': [71],
}
# check: no words lost
for n, ps in recitals.items():
    a = re.findall(r'\w+', ' '.join(ps)); b = re.findall(r'\w+', html.unescape(re.sub(r'<[^>]+>', ' ', pre['recitals'][n])))
    assert a == b, n
g = json.load(open('gdpr.json', encoding='utf-8'))
g['preamble'] = pre
json.dump(g, open('gdpr.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(len(citations), 'citations,', len(recitals), 'recitals; links:', sum(v.count('xref') for v in pre['recitals'].values()))
print(opening, '|', closing)
print(pre['recitals'][71][-700:].encode('ascii', 'backslashreplace').decode())
