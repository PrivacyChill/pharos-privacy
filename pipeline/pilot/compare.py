"""Compare a tested model's readings with the Opus reference answers, field by field.

    python compare.py sonnet       reads _test/<case>/analysis_sonnet.json against <case>/analysis.json

Fields scored: fine amount, decision date, controller, infringed articles, outcome, status, document kind, and
how many of the model's quotes are found word for word in the original. 'same' / 'differs' / 'missing'
(the model left it out) / 'extra' (only the model has it). Differences are for a person to judge: the reference
can be wrong too.
"""
import glob, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.argv += [] if len(sys.argv) > 1 else ['sonnet']
NAME = sys.argv[1]
exec(open(os.path.join(HERE, 'verify.py'), encoding='utf-8').read().split('total = found = 0')[0])  # norm(), quotes()


def val(x, key='value'):
    return x.get(key) if isinstance(x, dict) else x


def date(a):
    m = re.search(r'\d{4}-\d\d-\d\d', str(val(a.get('decision_date')) or ''))
    return m.group(0) if m else None


def amount(a):
    f = a.get('fine')
    return f.get('amount_eur') if isinstance(f, dict) else None


def arts(a):
    return {re.match(r'\s*(\d+)', str(i.get('article', ''))).group(1) for i in a.get('infringements') or []
            if re.match(r'\s*(\d+)', str(i.get('article', '')))}


def name(a):
    v = str(val(a.get('controller')) or '').lower()
    return re.sub(r'[^a-z0-9äöüéèàòùìßøåæ ]', '', v)


def words(s):
    return {w for w in s.split() if len(w) > 3}


def cmp(ref, got, close=lambda r, g: r == g):
    if ref in (None, '', set(), []) and got in (None, '', set(), []):
        return '-'
    if got in (None, '', set(), []):
        return 'missing'
    if ref in (None, '', set(), []):
        return 'extra'
    return 'same' if close(ref, got) else 'differs'


rows, tally = [], {}
for f in sorted(glob.glob(os.path.join(HERE, '_test', '*', f'analysis_{NAME}.json'))):
    case = os.path.basename(os.path.dirname(f))
    got = json.load(open(f, encoding='utf-8'))
    ref = json.load(open(os.path.join(HERE, case, 'analysis.json'), encoding='utf-8'))
    src = norm(' '.join(open(t, encoding='utf-8').read() for t in glob.glob(os.path.join(os.path.dirname(f), '*.txt'))))
    q = list(quotes(got))
    ok = sum(1 for _, s in q if norm(s) in src)
    r = {
        'case': case,
        'fine': cmp(amount(ref), amount(got)),
        'date': cmp(date(ref), date(got)),
        'controller': cmp(name(ref), name(got), lambda r, g: bool(words(r) & words(g)) or r in g or g in r),
        'articles': cmp(arts(ref), arts(got), lambda r, g: len(r & g) >= max(1, len(r) // 2)),
        'outcome': cmp(set(ref.get('outcome') or []), set(got.get('outcome') or []),
                       lambda r, g: bool({x.split()[0].lower() for x in r} & {x.split()[0].lower() for x in g})),
        'status': cmp(str(val(ref.get('status')) or ''), str(val(got.get('status')) or ''),
                      lambda r, g: r.split()[0].lower() in g.lower() or g.split()[0].lower() in r.lower()),
        'doc_kind': cmp(str(ref.get('doc_kind', '')), str(got.get('doc_kind', '')),
                        lambda r, g: r.split()[0].lower() == g.split()[0].lower()),
        'quotes': f'{ok}/{len(q)}',
        'detail': f"fine {amount(ref)} vs {amount(got)}; date {date(ref)} vs {date(got)}; "
                  f"arts {sorted(arts(ref))} vs {sorted(arts(got))}; ctrl '{name(ref)[:40]}' vs '{name(got)[:40]}'",
    }
    rows.append(r)
    for k, v in r.items():
        if k not in ('case', 'quotes', 'detail'):
            tally.setdefault(k, {}).setdefault(v, 0)
            tally[k][v] += 1

for r in rows:
    print(f"{r['case']:12} " + '  '.join(f"{k}={r[k]}" for k in ('fine', 'date', 'controller', 'articles', 'outcome', 'status', 'doc_kind', 'quotes')))
    print(f"{'':12} {r['detail']}")
print('\nby field:', json.dumps(tally))
