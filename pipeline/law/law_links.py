"""Links between the GDPR and the AI Act, for the "In the AI Act" / "In the GDPR" boxes on the article pages.
Writes site/data/law-links.json.

Two kinds of link:
- cites: the AI Act itself names a GDPR article ("Article 35 of Regulation (EU) 2016/679"). Read from the
  AI Act text, so nothing is judged by hand.
- pairs: the two laws deal with the same topic without saying so (GDPR Article 22 and AI Act Article 86).
  These are a judgement call, so they live in pairs.csv and only those Lorenzo answered 'yes' go on the site.

  python law_links.py            build site/data/law-links.json (pairs answered 'yes' only)
  python law_links.py --preview  the same, but also with the pairs still waiting for an answer (for a preview)
  python law_links.py review     write review/law-pairs.xlsx for Lorenzo
  python law_links.py apply      read his answers from review/law-pairs.xlsx into pairs.csv
"""
import csv, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PAIRS = os.path.join(HERE, 'pairs.csv')
FIELDS = ['gdpr', 'ai', 'topic', 'why', 'suggestion', 'answer']
sys.path.insert(0, os.path.dirname(HERE))


def load(name):
    return json.load(open(os.path.join(ROOT, 'site', 'data', name), encoding='utf-8'))


def read_pairs():
    with open(PAIRS, encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def write_pairs(rows):
    with open(PAIRS, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def ai_key(k):
    """'86' is an article, 'Annex III' an annex."""
    return ('ai-annex', k.split()[1]) if k.startswith('Annex') else ('ai-art', k)


def build(preview=False):
    ai, gdpr = load('reg-2024-1689.json'), load('reg-2016-679.json')
    cites, cite_recs = {}, {}
    places = [('ai-art', k, ai['arts'][k]['t'], ai['arts'][k]['h']) for k in ai['order']]
    places += [('ai-annex', r, ai['annexes'][r]['t'], ai['annexes'][r]['h']) for r in ai['annex_order']]
    for kind, k, title, h in places:
        for n in sorted({int(x) for x in re.findall(r'href="#art-(\d+)"', h)}):
            cites.setdefault(n, []).append([kind, k, title])
    for r, h in ai['preamble']['recitals'].items():
        for n in sorted({int(x) for x in re.findall(r'href="#art-(\d+)"', h)}):
            cite_recs.setdefault(n, []).append(int(r))
    pairs = []
    for p in read_pairs():
        a = p['answer'].strip().lower()
        if a == 'yes' or (preview and not a and p['suggestion'] != 'no'):
            n, (kind, k) = int(p['gdpr']), ai_key(p['ai'])
            assert str(n) in gdpr['arts'], p
            title = ai['arts'][k]['t'] if kind == 'ai-art' else ai['annexes'][k]['t']
            pairs.append({'gdpr': n, 'kind': kind, 'ai': k, 'ai_t': title, 'topic': p['topic']})
    out = {'cites': cites, 'cite_recs': cite_recs, 'pairs': pairs}
    path = os.path.join(ROOT, 'site', 'data', 'law-links.json')
    json.dump(out, open(path, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print(f'law-links.json: {len(cites)} GDPR articles named in the AI Act, {len(pairs)} topic pairs'
          f'{" (preview: with unanswered ones)" if preview else ""}.')


def review():
    import pharos
    ai, gdpr = load('reg-2024-1689.json'), load('reg-2016-679.json')
    rows = []
    for p in read_pairs():
        kind, k = ai_key(p['ai'])
        rows.append({'status': 'nothing to do: answered' if p['answer'] else ('to do (easy)' if p['suggestion'] == 'yes' else 'to do'),
                     'gdpr': f"Article {p['gdpr']} · {gdpr['arts'][p['gdpr']]['t']}",
                     'ai_act': f"{'Article ' + k if kind == 'ai-art' else 'Annex ' + k} · {(ai['arts'] if kind == 'ai-art' else ai['annexes'])[k]['t']}",
                     'topic': p['topic'], 'why': p['why'], 'suggestion': p['suggestion'], 'answer': p['answer']})
    if pharos.review_has_answers('law-pairs', ['answer']):
        sys.exit('review/law-pairs.xlsx already has answers: run "python law_links.py apply" first.')
    path = pharos.review_write('law-pairs', ['status', 'gdpr', 'ai_act', 'topic', 'why', 'suggestion', 'answer'], rows,
        answer=('answer',), widths={'status': 18, 'gdpr': 42, 'ai_act': 46, 'topic': 26, 'why': 70, 'suggestion': 11, 'answer': 10},
        how_to=['GDPR and AI Act: same topic, shown side by side on Fino',
                '',
                'Each row suggests that a GDPR article and an AI Act article deal with the same topic. If you say yes,',
                'the GDPR article page gets a link to the AI Act article ("In the AI Act") and the other way round ("In the GDPR"),',
                'with the topic as the label. The AI Act article page then also shows how many fines cite the GDPR article.',
                '',
                'In the answer column write: yes (show it), no (never show it), or nothing (decide later).',
                'You can also change the topic text: it is the label people will see.',
                'Green rows: Claude is fairly sure. Orange rows: a closer call, your judgement.',
                '',
                'Links where the AI Act itself names a GDPR article are not in this list: they are facts and are always shown.',
                'Save (keep .xlsx), close Excel, tell Claude.'])
    print('wrote', path)


def apply():
    import pharos
    answers = {(r['gdpr'], r['ai_act']): r for r in pharos.review_rows('law-pairs')}
    rows = read_pairs()
    ai, gdpr = load('reg-2024-1689.json'), load('reg-2016-679.json')
    n = 0
    for p in rows:
        kind, k = ai_key(p['ai'])
        key = (f"Article {p['gdpr']} · {gdpr['arts'][p['gdpr']]['t']}",
               f"{'Article ' + k if kind == 'ai-art' else 'Annex ' + k} · {(ai['arts'] if kind == 'ai-art' else ai['annexes'])[k]['t']}")
        r = answers.get(key)
        if not r: continue
        a = r['answer'].strip().lower()
        if a in ('yes', 'ok', 'y'): a = 'yes'
        elif a in ('no', 'n'): a = 'no'
        elif a: sys.exit(f'Unclear answer "{r["answer"]}" for {key}: write yes or no.')
        if a != p['answer'] or r['topic'] != p['topic']:
            p['answer'], p['topic'] = a, r['topic'] or p['topic']; n += 1
    write_pairs(rows)
    print(n, 'answers saved in pairs.csv')
    build()


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    {'review': review, 'apply': apply}.get(cmd, lambda: build(preview='--preview' in sys.argv))()
