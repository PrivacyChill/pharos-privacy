"""The AI Act (Regulation (EU) 2024/1689) as shown on the site: articles, chapters, annexes and the
preamble (citations and 180 recitals). Writes aiact/aiact.json; see README.md.

- Articles and annexes come from the consolidated text (CELEX 02024R1689-20260727), which includes the
  Digital Omnibus on AI (Regulation (EU) 2026/1744). There is no English corrigendum.
- Recitals are not in the consolidated text, so they come from the Official Journal text (32024R1689).
- Every article, annex and recital is checked word for word against the source; the script stops if a word is lost.
"""
import re, json, html, sys
import xml.etree.ElementTree as ET

D = 'aiact/'
NS = '{http://www.w3.org/1999/xhtml}'
SUP_A, SUP_B = '\x01', '\x02'      # a real superscript (10 to the power 25), kept as <sup>

def load(f):
    raw = open(D + f, encoding='utf-8').read()
    # drop footnote call-outs like "(<a href="#E0001" ...><span class="superscript">1</span></a>)"
    raw = re.sub(r'\s*\(<a href="#E\d+"[^>]*>\s*<span class="superscript">\*?\d+</span>\s*</a>\)', '', raw)
    # Official Journal style: "\xa0<a ...>(<span class="oj-super oj-note-tag">1</span>)</a>"
    raw = re.sub(r'\s*<a id="ntc[^"]*"[^>]*>\s*\(<span class="oj-super oj-note-tag">\d+</span>\)</a>', '', raw)
    return ET.fromstring(raw.encode('utf-8'))

def cls(e): return e.get('class') or ''
def tag(e): return e.tag.replace(NS, '')

def text(e):
    """Inline text of an element, skipping amendment markers."""
    out = []
    def walk(x):
        if tag(x) == 'p' and cls(x) == 'modref': return
        sup = tag(x) == 'span' and cls(x) == 'superscript'
        if sup: out.append(SUP_A)
        if x.text: out.append(x.text)
        for c in x:
            walk(c)
            if c.tail: out.append(c.tail)
        if sup: out.append(SUP_B)
    walk(e)
    s = ''.join(out).replace('\xa0', ' ')
    s = re.sub(r'[►▼◄]\s*(?:[A-Z]\d*)?', '', s)
    s = re.sub(r'\s*' + SUP_A + r'\s*', SUP_A, s); s = re.sub(r'\s*' + SUP_B, SUP_B, s)
    return re.sub(r'\s+', ' ', s).strip()

ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII', 'XIII', 'XIV']
ART_KEYS = set()          # filled from the source before any link is made
OWN = {'on': True}        # off inside Articles 102 to 110: they amend other acts, so their article numbers are those acts'
AMENDING = {str(n) for n in range(102, 111)}
# typing slips in the official titles, removed on the site (the words are unchanged)
TITLE_FIX = {'1': {"Subject matter'": 'Subject matter'}}

def esc(s):
    s = html.escape(s, quote=False)
    return re.sub(SUP_A + '(.*?)' + SUP_B, r'<sup>\1</sup>', s)

# what may sit between an article reference and "of Regulation ..." in a reference to another act
QUAL = r'(?:,?\s*(?:(?:first|second|third|fourth|fifth|sixth|last)\s+)?(?:subparagraph|sentence|indent)|,?\s*points?\s+\(?[a-z0-9]+\)?(?:\([a-z0-9]+\))*(?:\s*(?:,|and|or|to)\s*\(?[a-z0-9]+\)?(?:\([a-z0-9]+\))*)*|\s*(?:,|and|or|to)?\s*\(\d+\)(?:\([a-z]+\))*)*'
OTHER_ACT = re.compile(r'(?:' + QUAL + r'\s*(?:,|or|and)?\s*Articles?\s+[\da-z()]+(?:\s*(?:,|or|and|to)\s*[\da-z()]+)*)*' + QUAL + r',?\s*(?:of (?!this Regulation)|TFEU|TEU|thereof)')

def link(s):
    """'Article 6(1)', 'Articles 102 to 109', 'Annex III' become links on this site.
    A reference to another act ('Article 10 of Regulation (EU) 2016/679', 'Article 16 TFEU') stays plain text."""
    s = esc(s)
    if not OWN['on']: return s
    def art(m):
        word, rest = m.group(1), m.group(2)
        if OTHER_ACT.match(m.string, m.end()): return m.group(0)
        rest = re.sub(r'(?<![(\d])\b(\d{1,3}a?)\b(?!\))',
                      lambda n: f'<a class="xref" href="#ai-art-{n.group(1)}">{n.group(1)}</a>' if n.group(1) in ART_KEYS else n.group(1), rest)
        return word + rest
    s = re.sub(r'\b(Articles? )((?:\d{1,3}a?\b(?: ?\(\d+[a-z]?\))*(?:\([a-z]+\))*(?:,? and (?=\d)|, (?=\d)| or (?=\d)| to (?=\d))?)+)', art, s)
    def anx(m):
        word, rest = m.group(1), m.group(2)
        if re.match(r',?\s*(?:to|of) (?:Regulation|Directive|Decision|Council|Commission|the Treaty|that )', m.string[m.end():]): return m.group(0)
        rest = re.sub(r'\b([IVX]+)\b', lambda n: f'<a class="xref" href="#ai-annex-{n.group(1)}">{n.group(1)}</a>' if n.group(1) in ROMAN else n.group(1), rest)
        return word + rest
    s = re.sub(r'\b(Annex(?:es)? )((?:[IVX]+\b(?:,? and (?=[IVX]+\b)|, (?=[IVX]+\b)| or (?=[IVX]+\b)| to (?=[IVX]+\b))?)+)', anx, s)
    return s

def block(e):
    """One structural element as minimal HTML (the same shapes as the GDPR on the site)."""
    t = tag(e); c = cls(e)
    if t == 'p' and c in ('modref', 'title-article-norm', 'arrow', 'footnote'): return ''
    if t == 'p' and c.startswith('title-gr-seq'):
        s = text(e); return f'<p class="sh">{link(s)}</p>' if s else ''
    if t == 'p':
        s = text(e); return f'<p>{link(s)}</p>' if s else ''
    if t == 'hr' or t == 'br': return ''
    if t == 'div' and 'grid-list' in c and 'grid-container' in c:
        cols = [x for x in e if tag(x) == 'div']
        lab = text(cols[0]) if cols else ''
        body = ''.join(block(x) for x in cols[1]) if len(cols) > 1 else ''
        if not list(cols[1]) and text(cols[1]): body = f'<p>{link(text(cols[1]))}</p>'
        return f'<div class="pt"><span class="pl">{esc(lab)}</span><div>{body}</div></div>'
    if t == 'div' and c == 'norm':
        kids = list(e)
        if kids and tag(kids[0]) == 'span' and cls(kids[0]) == 'no-parag':
            num = text(kids[0]); body = ''.join(block(x) for x in kids[1:])
            return f'<div class="par"><span class="pn">{esc(num)}</span><div>{body}</div></div>'
        return ''.join(block(x) for x in kids)
    if t == 'div' and c == 'eli-title': return ''
    if t == 'table':
        rows = []
        for tr in e.iter(NS + 'tr'):
            cells = [' '.join(text(p) for p in td.iter() if tag(p) == 'p' and text(p)) for td in tr.iter(NS + 'td')]
            rows.append(cells)
        head, body = rows[0], rows[1:]
        th = ''.join(f'<th scope="col">{esc(x)}</th>' for x in head)
        tb = ''.join('<tr>' + ''.join(f'<td>{link(x)}</td>' for x in r) + '</tr>' for r in body)
        return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{tb}</tbody></table></div>'
    if t in ('div', 'span'):
        structural = [x for x in e if tag(x) in ('div', 'p', 'table')]
        if not structural:
            s = text(e); return f'<p>{link(s)}</p>' if s else ''
        lead = f'<p>{link(e.text.strip())}</p>' if (e.text or '').strip() else ''
        return lead + ''.join(block(x) + (f'<p>{link(x.tail.strip())}</p>' if (x.tail or '').strip() else '') for x in e)
    return ''

HEAD_CASE = {'AI': 'AI', 'EU': 'EU', 'II': 'II'}
def sentence(s):
    """'PROHIBITED AI PRACTICES' -> 'Prohibited AI practices' (headings are all capitals in the source)."""
    if s != s.upper(): return s
    w = s.lower().split(' ')
    w = [HEAD_CASE.get(x.upper(), x) for x in w]
    w[0] = w[0][0].upper() + w[0][1:]
    return ' '.join(w)

def words(s): return re.findall(r'\w+', s.replace(SUP_A, ' ').replace(SUP_B, ' '))
def html_words(h): return re.findall(r'\w+', html.unescape(re.sub(r'<[^>]+>', ' ', h)))

# ---------- articles, chapters, annexes from the consolidated text ----------
cons = load('aiact_cons.html')
for x in cons.iter():
    m = re.fullmatch(r'art_(\d+a?)', x.get('id') or '')
    if m: ART_KEYS.add(m.group(1))

# which parts the 2026 amendment wrote: a ▼M1 marker opens amended text, ▼B goes back to the 2024 text
state = {'m': 'B'}
amended = set()
arts, order, chapters, annexes = {}, [], [], {}

def marks(x, key):
    """Walk an article or annex in document order; note it if any of its text sits under a ▼M1 marker."""
    for e in x.iter():
        if tag(e) == 'p' and cls(e) == 'modref':
            state['m'] = 'M1' if 'M1' in text_raw(e) else 'B'
        elif state['m'] == 'M1' and key and (e.text or '').strip():
            amended.add(key)
        for s in ([e.text or ''] + [c.tail or '' for c in e]):
            if '►M1' in s: amended.add(key)
def text_raw(e): return ''.join(e.itertext())

def visit(e, ch, sec):
    for x in e:
        i = x.get('id') or ''
        if re.fullmatch(r'cpt_[IVX]+', i):
            heads = [text(p) for p in x if tag(p) == 'p' and cls(p).startswith('title-division')]
            ch = {'id': i, 'num': 'Chapter ' + i[4:], 'title': sentence(heads[1]) if len(heads) > 1 else '', 'arts': []}
            chapters.append(ch); visit(x, ch, None); continue
        if re.fullmatch(r'cpt_[IVX]+\.sct_\d+', i):
            heads = [text(p) for p in x if tag(p) == 'p' and cls(p).startswith('title-division')]
            visit(x, ch, ' — '.join([heads[0].title()] + heads[1:])); continue
        m = re.fullmatch(r'art_(\d+a?)', i)
        if m:
            k = m.group(1)
            marks(x, k)
            title = ''.join(text(t) for t in x.iter() if cls(t) == 'stitle-article-norm')
            title = TITLE_FIX.get(k, {}).get(title, title)
            OWN['on'] = k not in AMENDING
            body = ''.join(block(c) for c in x if not (tag(c) == 'div' and cls(c) == 'eli-title'))
            OWN['on'] = True
            arts[k] = {'t': title, 'ch': ch['id'] if ch else None, 'sec': sec, 'h': body}
            order.append(k)
            if ch: ch['arts'].append(k)
            continue
        m = re.fullmatch(r'anx_([IVX]+)', i)
        if m:
            k = m.group(1)
            marks(x, 'annex-' + k)
            kids = list(x)
            ti = next(j for j, c in enumerate(kids) if cls(c) == 'title-annex-1')
            nxt = kids[ti + 1]
            if cls(nxt) == 'title-annex-2': title, rest = text(nxt), kids[ti + 2:]
            else: title, rest = text(nxt), kids[ti + 2:]          # Annex XIV: its title is a plain paragraph
            annexes[k] = {'t': title, 'h': ''.join(block(c) for c in rest)}
            continue
        if tag(x) == 'p' and cls(x) == 'modref':
            state['m'] = 'M1' if 'M1' in text_raw(x) else 'B'
        if tag(x) in ('div', 'body'): visit(x, ch, sec)

visit(cons.find(NS + 'body'), None, None)

# ---------- checks: every word of the source survives ----------
bad = []
for x in cons.iter():
    i = x.get('id') or ''
    m = re.fullmatch(r'art_(\d+a?)', i)
    if m:
        k = m.group(1)
        src = re.sub(r'^Article \d+a?\s*', '', text(x)).replace(arts[k]['t'], '', 1)
        got = arts[k]['h']
    elif re.fullmatch(r'anx_([IVX]+)', i):
        k = 'annex-' + i[4:]
        src = re.sub(r'^ANNEX [IVX]+\s*', '', text(x)).replace(annexes[i[4:]]['t'], '', 1)
        got = annexes[i[4:]]['h']
    else: continue
    if words(src) != html_words(got): bad.append(k)
assert not bad, f'words lost in {bad}'
assert len([k for k in arts if k.isdigit()]) == 113 and len(annexes) == 14, (len(arts), len(annexes))

# ---------- preamble from the Official Journal text ----------
oj = load('aiact_oj.html')
def ojtext(e):
    s = ''.join(e.itertext()).replace('\xa0', ' ')
    return re.sub(r'\s+', ' ', s).strip()
pbl = next(e for e in oj.iter() if e.get('id') == 'pbl_1')
opening, citations, closing, recitals = None, [], None, {}
for x in pbl:
    i = x.get('id') or ''
    if tag(x) == 'p':
        s = ojtext(x)
        if s.startswith('THE EUROPEAN PARLIAMENT'): opening = s
        elif s.startswith('HAVE ADOPTED'): closing = s
    elif i.startswith('cit_'):
        citations.append(ojtext(x))
    elif i.startswith('rct_'):
        n = int(i[4:])
        cells = [td for td in x.iter(NS + 'td')]
        recitals[n] = [p for p in (ojtext(p) for p in cells[1].iter(NS + 'p')) if p]
assert len(recitals) == 180 and all(recitals[n] for n in range(1, 181)), len(recitals)
rec_h = {n: ''.join(f'<p>{link(p)}</p>' for p in ps) for n, ps in recitals.items()}
for n, ps in recitals.items():
    assert words(' '.join(ps)) == html_words(rec_h[n]), f'recital {n}'

# ---------- titles: the 2026 text against the 2024 text, for articles the amendment did not touch ----------
oj_titles = {}
for x in oj.iter():
    m = re.fullmatch(r'art_(\d+a?)', x.get('id') or '')
    if m:
        t = [ojtext(p) for p in x.iter() if 'oj-sti-art' in cls(p)]
        oj_titles[m.group(1)] = (t[0] if t else '').replace('Subject matter`', 'Subject matter')
diff = [(k, oj_titles.get(k), arts[k]['t']) for k in order if k not in amended and oj_titles.get(k) != arts[k]['t']]

out = {
    'arts': arts, 'order': order, 'chapters': chapters,
    'annexes': annexes, 'annex_order': [r for r in ROMAN if r in annexes],
    'amended': sorted(amended, key=lambda k: (k.startswith('annex'), ROMAN.index(k[6:]) if k.startswith('annex') else int(re.sub(r'\D', '', k)), k)),
    'preamble': {'opening': opening, 'citations': citations, 'closing': closing, 'recitals': rec_h},
}
json.dump(out, open(D + 'aiact.json', 'w', encoding='utf-8'), ensure_ascii=False)

print(len(arts), 'articles,', len(annexes), 'annexes,', len(chapters), 'chapters,', len(recitals), 'recitals,', len(citations), 'citations')
print('changed by 2026/1744:', len(out['amended']), out['amended'])
print('titles that differ from 2024 in untouched articles:', diff)
print('links:', sum(a['h'].count('xref') for a in arts.values()), 'in articles,', sum(a['h'].count('xref') for a in annexes.values()), 'in annexes,', sum(v.count('xref') for v in rec_h.values()), 'in recitals')
print([(c['num'], c['title'], len(c['arts'])) for c in chapters])
print({k: a['sec'] for k, a in arts.items() if a['sec']}.get('6'))
print('sup:', [k for k, a in arts.items() if '<sup>' in a['h']])
