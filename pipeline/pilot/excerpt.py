"""Cut a decision down to the parts that carry the facts, for a cheaper reader.

    python excerpt.py <case folder> [budget in characters, default 20000]

Writes <folder>/excerpt.txt: the opening (parties, date, any note about later events), the passages about the
fine (amounts, Article 83, the words for 'fine' in each language) and the operative part at the end ('for these
reasons ...'), in the order they appear, each marked with its position. Short decisions are kept whole. Every
excerpt is cut from the text itself, so quotes copied from it are still found by verify.py.
"""
import glob
import os
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
HEAD, TAIL = 3500, 2500

# where the operative part starts, in the languages of the EU regulators
OPERATIVE = re.compile(r"""PER\s+QUESTI\s+MOTIVI|TUTTO\s+CI[OÒ]\s+PREMESSO|\bDISPONE\b|\bINGIUNGE\b|\bRESUELVE\b|\bACUERDA\b|
    PAR\s+CES\s+MOTIFS|\bD[EÉ]CIDE\b|\bSpruch\b|\bSPRUCH\b|ergeht\s+folgende|\bBESLUIT\b|\bDICTUM\b|\bbeslist\b|
    FOR\s+THESE\s+REASONS|\bDECISION\b|\bORDERS?\b|ΓΙΑ\s+ΤΟΥΣ\s+ΛΟΓΟΥΣ\s+ΑΥΤΟΥΣ|\bpostanawia\b|\borzeka\b|
    Z\s+tych\s+wzgl[eę]d[oó]w|\bbeslutar\b|\bBeslut\b|n\s?u\s?s\s?p\s?r\s?e\s?n\s?d\s?[žz]\s?i\s?a|\bdelibera\b|
    Pelo\s+exposto|\bDECIDE\b|\bdispune\b|\bRJE[ŠS]ENJE\b|\bv[ýy]rok\b|\bP[äa][äa]t[öo]s\b|\bAfg[øo]relse\b|\bhat\s+entschieden\b""",
                       re.X)
STRONG = re.compile(r"""PER\s+QUESTI\s+MOTIVI|TUTTO\s+CI[OÒ]\s+PREMESSO|PAR\s+CES\s+MOTIFS|FOR\s+THESE\s+REASONS|
    ΓΙΑ\s+ΤΟΥΣ\s+ΛΟΓΟΥΣ\s+ΑΥΤΟΥΣ|Z\s+tych\s+wzgl[eę]d[oó]w|Pelo\s+exposto|\bRESUELVE\b|ergeht\s+folgende|
    om\s+deze\s+redenen|OM\s+DEZE\s+REDENEN|AUS\s+DIESEN\s+GR[ÜU]NDEN|Aus\s+diesen\s+Gr[üu]nden""", re.X)
FINE = re.compile(r"""\d[\d .,'’]{2,}\s?(?:€|EUR|euro|eur|zł|PLN|SEK|DKK|NOK|kr\b|HUF|Ft\b|lei\b|RON|Kč|CZK|ISK|BGN|лв)|
    (?:€|EUR)\s?\d[\d .,]{2,}|\b83\s?(?:\(|Abs|ust|par|lid|stk|odst|st\.|straipsn)|
    Geldbu[ßs]e|Geldstrafe|sanzione\s+amministrativa|sanci[oó]n|amende|boete|bauda|πρόστιμο|\bkar[aęy]\s+pieni|
    sanktionsavgift|bøde|sakko|bírság|amend[aă]|coima|globa\b|pokut""", re.X | re.I)


def clean(t):
    t = re.sub(r'[ \t\xa0]+', ' ', t)
    t = re.sub(r'\n\s*\n+', '\n', t)
    return re.sub(r'(?<=\S) ?\n(?=\S)', ' ', t)


def merge(spans):
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def excerpt(t, budget):
    if len(t) <= budget * 1.25:
        return t, 'full'
    spans = [[0, HEAD], [len(t) - TAIL, len(t)]]
    # the operative part: the first strong heading ('for these reasons') in the second half, else the last weaker
    # word ('decides'); then what follows it
    strong = [m.start() for m in STRONG.finditer(t) if m.start() > len(t) * 0.4]
    ops = strong[:1] or [m.start() for m in OPERATIVE.finditer(t) if m.start() > len(t) * 0.4][-1:]
    if ops:
        spans.append([ops[0] - 300, min(len(t), ops[0] + 9000)])
    used = sum(e - s for s, e in merge(spans))
    # the fine: windows round the densest places with amounts and Article 83, best first, until the budget is spent
    hits = [m.start() for m in FINE.finditer(t)]
    windows = sorted(((sum(1 for h in hits if p - 700 <= h <= p + 1100), p) for p in hits), reverse=True)
    for _, p in windows:
        if used >= budget:
            break
        cand = merge(spans + [[max(0, p - 700), min(len(t), p + 1100)]])
        size = sum(e - s for s, e in cand)
        if size > used and size <= budget + 1500:
            spans, used = cand, size
    if used < budget:                    # what is left goes to the opening (Austria puts its ruling first)
        spans = merge(spans + [[0, HEAD + budget - used]])
        used = sum(e - s for s, e in spans)
    parts = merge(spans)
    return '\n'.join(f'[... from character {s} of {len(t)} ...]\n{t[s:e]}' for s, e in parts), \
        f"excerpt: {len(parts)} parts, {used} of {len(t)} characters (opening, fine passages, operative part, end)"


if __name__ == '__main__':
    folder, budget = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    files = [f for f in sorted(glob.glob(os.path.join(folder, '*.txt'))) if not f.endswith(('_c.txt', 'excerpt.txt'))]
    out, notes = [], []
    for f in files:
        text, how = excerpt(clean(open(f, encoding='utf-8').read()), budget // max(1, len(files)))
        out.append(f'===== {os.path.basename(f)} ({how})\n{text}')
        notes.append(f'{os.path.basename(f)}: {how}')
    open(os.path.join(folder, 'excerpt.txt'), 'w', encoding='utf-8').write('\n\n'.join(out))
    print(folder, '|', ' | '.join(notes))
