import re, json, html
import xml.etree.ElementTree as ET
raw=open('gdpr_cons.html',encoding='utf-8').read()
# drop footnote call-outs like "\xa0(<a href="#E0001" ...><span>1</span></a>)"
raw=re.sub(r'\s*\(<a href="#E\d+"[^>]*>\s*<span class="superscript">\d+</span>\s*</a>\)','',raw)
root=ET.fromstring(raw.encode('utf-8'))
NS='{http://www.w3.org/1999/xhtml}'
def cls(e): return e.get('class') or ''
def tag(e): return e.tag.replace(NS,'')
def text(e):
    """Inline text of an element, skipping correction markers."""
    out=[]
    def walk(x):
        if tag(x)=='p' and cls(x)=='modref': return
        if x.text: out.append(x.text)
        for c in x:
            walk(c)
            if c.tail: out.append(c.tail)
    walk(e)
    s=''.join(out).replace('\xa0',' ')
    s=re.sub(r'[►▼◄]\s*[A-Z]\d*','',s)
    return re.sub(r'\s+',' ',s).strip()
def esc(s): return html.escape(s, quote=False)
def link(s):
    s=esc(s)
    # "Article 89(1)", "Articles 13 and 14" -> in-site links on the article number
    def rep(m):
        word, rest = m.group(1), m.group(2)
        after = m.string[m.end():m.end()+120]
        # a reference to another act ("Article 25(6) of Directive 95/46/EC") is left as plain text
        # also when chained: "Article 25(6) or Article 26(4) of Directive 95/46/EC"
        if re.match(r'(?:\s*(?:,|or|and)?\s*Articles?\s+[\d()a-z]+(?:\s*(?:,|or|and)\s*[\d()a-z]+)*)*\s*of (?!this Regulation)', after): return m.group(0)
        rest=re.sub(r'(?<!\()\b(\d{1,2})\b(?!\))', lambda n: f'<a class="xref" href="#art-{n.group(1)}">{n.group(1)}</a>' if 1<=int(n.group(1))<=99 else n.group(1), rest)
        return word+rest
    s=re.sub(r'\b(Articles? )((?:\d{1,2}\b(?:\(\d+\))*(?:\([a-z]+\))*(?:, | and | or | to )?)+)', rep, s)
    return s
def block(e):
    """Turn one structural element into minimal HTML."""
    t=tag(e); c=cls(e)
    if t=='p' and c in ('modref','title-article-norm','arrow'): return ''
    if t=='p': 
        s=text(e); return f'<p>{link(s)}</p>' if s else ''
    if t=='div' and 'grid-list' in c and 'grid-container' in c:
        cols=[x for x in e if tag(x)=='div']
        lab=text(cols[0]) if cols else ''
        body=''.join(block(x) for x in cols[1]) if len(cols)>1 else ''
        return f'<div class="pt"><span class="pl">{esc(lab)}</span><div>{body}</div></div>'
    if t=='div' and c=='norm':
        kids=list(e)
        if kids and tag(kids[0])=='span' and cls(kids[0])=='no-parag':
            num=text(kids[0]); body=''.join(block(x) for x in kids[1:])
            return f'<div class="par"><span class="pn">{esc(num)}</span><div>{body}</div></div>'
        return ''.join(block(x) for x in kids)
    if t=='div' and c=='eli-title': return ''
    if t in ('div','span'):
        structural=[x for x in e if tag(x) in ('div','p','table')]
        if not structural:
            s=text(e); return f'<p>{link(s)}</p>' if s else ''
        lead=f'<p>{link(e.text.strip())}</p>' if (e.text or '').strip() else ''
        return lead+''.join(block(x) for x in e)
    if t=='table':
        return ''.join(block(x) for x in e.iter() if tag(x)=='p')
    return ''
arts={}; chapters=[]
def visit(e, ch, sec):
    for x in e:
        i=x.get('id') or ''
        if re.fullmatch(r'cpt_[IVX]+', i):
            heads=[text(p) for p in x if tag(p)=='p' and cls(p).startswith('title-division')]
            ch={'id':i,'num':heads[0].replace('CHAPTER','Chapter') if heads else i,'title':heads[1] if len(heads)>1 else '','arts':[]}
            chapters.append(ch); visit(x, ch, None); continue
        if re.fullmatch(r'cpt_[IVX]+\.sct_\d+', i):
            heads=[text(p) for p in x if tag(p)=='p' and cls(p).startswith('title-division')]
            visit(x, ch, ' — '.join(heads)); continue
        m=re.fullmatch(r'art_(\d+)', i)
        if m:
            n=int(m.group(1))
            title=''.join(text(t) for t in x.iter() if cls(t)=='stitle-article-norm')
            body=''.join(block(k) for k in x if not (tag(k)=='div' and cls(k)=='eli-title'))
            arts[n]={'t':title,'ch':ch['id'] if ch else None,'sec':sec,'h':body}
            if ch: ch['arts'].append(n)
            continue
        if tag(x) in ('div','body'): visit(x, ch, sec)
visit(root.find(NS+'body'), None, None)
print(len(arts), len(chapters), [ (c['num'],c['title'],len(c['arts'])) for c in chapters][:11])
json.dump({'arts':arts,'chapters':chapters},open('gdpr.json','w',encoding='utf-8'),ensure_ascii=False)
for n in (5,83):
    print(n, arts[n]['t'], arts[n]['sec']); print(arts[n]['h'][:1200].encode('ascii','backslashreplace').decode()); print()
print(len(json.dumps(arts)))
print('empty', sum(a['h'].count('<div></div>') for a in arts.values()), [n for n,a in arts.items() if '<div></div>' in a['h']])
print('sections', sorted({a['sec'] for a in arts.values() if a['sec']})[:12])
print(arts[83]['h'][:400])
print(arts[13]['h'][-600:])

# check: every word of each article's source text survives the conversion
bad=[]
for x in root.iter():
    m=re.fullmatch(r'art_(\d+)', x.get('id') or '')
    if not m: continue
    n=int(m.group(1))
    src=re.sub(r'^Article \d+\s*','',text(x)); src=src.replace(arts[n]['t'],'',1)
    got=html.unescape(re.sub(r'<[^>]+>',' ',arts[n]['h']))
    a=re.findall(r'\w+',src); b=re.findall(r'\w+',got)
    if a!=b: bad.append((n,len(a),len(b)))
print('mismatched articles:', bad)
