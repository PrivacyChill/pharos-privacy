"""Pilot, step 3: check every quote in <slug>/analysis.json against the original text, word for word.

  python verify.py      prints, per decision, how many quotes were found in the source, and lists the misses

A quote is found when it appears in the source after spaces, line breaks and hyphenated line ends are normalised.
Nothing is fixed here: a miss means the fact is not shown until it is checked.
"""
import json, os, re, glob

HERE = os.path.dirname(os.path.abspath(__file__))

def norm(s):
    s = s.replace('­', '').replace('\xa0', ' ')
    s = re.sub(r'-\s*\n\s*', '', s)                          # "Daten-\nschutz" -> "Datenschutz"
    s = re.sub(r'[‘’‚′`´]', "'", s)
    s = re.sub(r'[“”„«»]', '"', s)
    s = re.sub(r'[‐-―]', '-', s)
    return re.sub(r'\s+', ' ', s).strip().lower()

def quotes(x, path=''):
    """Every {"quote": ...} anywhere in the analysis, with where it sits."""
    if isinstance(x, dict):
        if isinstance(x.get('quote'), str) and x['quote'].strip():
            yield path, x['quote']
        for k, v in x.items():
            if k != 'quote': yield from quotes(v, f'{path}.{k}')
    elif isinstance(x, list):
        for i, v in enumerate(x): yield from quotes(v, f'{path}[{i}]')

total = found = 0
rows = []
for f in sorted(glob.glob(os.path.join(HERE, '*', 'analysis.json'))):
    d = os.path.dirname(f)
    src = ' '.join(open(t, encoding='utf-8').read() for t in sorted(glob.glob(os.path.join(d, '*.txt'))))
    S = norm(src)
    a = json.load(open(f, encoding='utf-8'))
    q = list(quotes(a))
    miss = [(p, s) for p, s in q if norm(s) not in S]
    total += len(q); found += len(q) - len(miss)
    rows.append((os.path.basename(d), len(q), len(miss)))
    print(f'{os.path.basename(d):14} quotes {len(q):3}  not found {len(miss)}')
    for p, s in miss: print(f'      MISS {p}: {s[:110]}')
print(f'\n{found} of {total} quotes found word for word ({100 * found / max(total, 1):.1f}%).')
