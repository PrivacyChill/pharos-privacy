"""Show the parts of a decision that carry the facts: the opening, passages around key words, the end."""
import re, sys, glob
slug, head, tail = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
keys = sys.argv[4] if len(sys.argv) > 4 else ''
files = [f for f in sorted(glob.glob(slug + '/*.txt')) if not f.endswith('_c.txt')]
f = next((x for x in files if 'decision' in x), files[0]) if len(sys.argv) < 6 else slug + '/' + sys.argv[5]
t = open(f, encoding='utf-8').read()
t = re.sub(r'[ \t\xa0]+', ' ', t); t = re.sub(r'\n\s*\n+', '\n', t); t = re.sub(r'(?<=\S) ?\n(?=\S)', ' ', t)
print(f, len(t), 'chars\n----- HEAD'); print(t[:head])
if keys:
    spans = []
    for m in re.finditer(keys, t, re.I):
        s, e = max(head, m.start() - 600), min(len(t) - tail, m.end() + 900)
        if s < e and (not spans or s > spans[-1][1]): spans.append([s, e])
        elif spans and s <= spans[-1][1]: spans[-1][1] = max(spans[-1][1], e)
    for s, e in spans[:8]: print(f'----- @{s}'); print(t[s:e])
print('----- TAIL'); print(t[-tail:])
