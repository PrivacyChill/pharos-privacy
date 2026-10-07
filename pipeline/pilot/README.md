# Pilot: reading the original decisions (7 October 2026)

Twenty decisions, picked at random across 20 countries, fine sizes and outcomes (`pick.json`), read by
Claude Opus 5.5 in one session on Lorenzo's subscription. Purpose: find out whether Fino can build its own
database from the regulators' original decisions, automatically, and what it takes.

The results (`<case>/analysis.json`) are the **reference answers** for testing a cheaper model later: the
same 20 cases, the same format, and the differences show what the cheaper model gets wrong.

## Steps

1. `python fetch.py` downloads each decision's source link and turns it into text (`<case>/text.txt`;
   PDFs with PyMuPDF). Pages that are not the decision itself list the links that may lead to it.
2. Reading: `python peek.py <case> <head> <tail> "<keywords>"` shows the opening, the passages around the
   key words (fine, Article 83(2), aggravating, mitigating, the operative part) and the end. Long decisions
   were read this way, not page by page; `read_mode` in each analysis says what was read.
3. `python verify.py` checks every quote in every analysis against the original text, word for word
   (spaces, line breaks and typographic quotes normalised). A missed quote means the fact is not shown.

## The format (one `analysis.json` per decision)

`doc_kind`, `language`, `read_mode`, `authority`, `reference`, `decision_date`, `controller`, `trigger`,
`summary_en` (our own words), `infringements`, `outcome`, `fine` (plus `fine_per_infringement`,
`fine_calculation`, `before_reductions_eur` where the source gives them), `guidelines_used`,
`factors_aggravating` and `factors_mitigating` (each with the Article 83(2) letter), `why_no_fine`,
`turnover`, `orders`, `people_affected`, `data_categories`, `status` (appeal, removed, final),
`procedure_length`, `checks_against_fino`, `notes`. Every fact carries a `quote` from the original.
Facts read from page images carry `"verified": false` instead.

## What we found

**Getting the originals (20 links)**

| Result | Cases |
|---|---|
| Full decision downloaded | 12 (ES x3, IT x2, BE, GR, NO, HU, SE, AT, IE) |
| Only a press release exists or is linked | 4 (DE/049, RO, HR, NL) |
| Only an annual report mentions it | 1 (DE/011, Saxony) |
| Removed by the regulator after losing in court | 1 (IT/011) |
| Blocked (Légifrance, 403) | 1 (FR) |
| Server error | 1 (PL) |

Fixes needed for an automatic run: retry with an incomplete certificate chain (Spain); follow the link from a
press page to the PDF (Greece, Ireland); the RIS open-data API for Austria (stored links are temporary
search sessions); the Légifrance API (PISTE) for France; OCR or page images for scanned PDFs (Ireland).

**Accuracy**: 273 of 273 quotes found word for word. Opus made no quoting error that the checker caught.
The checker cannot judge whether a fact is *interpreted* correctly; that is what Lorenzo's sample check is for.

**What the originals add that Fino does not have**

- A fine Fino shows as current was **annulled by a court** (2023/IT/011, EUR 50,000).
- **Appeals pending** (Belgium 2026/BE/004 at the Market Court; Uber announced an objection).
- **Exact amounts**: Fastweb EUR 4,501,868, not the rounded EUR 4,500,000.
- **How the fine was calculated**: turnover, the cap, the percentage (Italy 5% of the maximum; Belgium 0.025% of
  turnover for a public body), one fine per infringement (Sweden, Ireland, Belgium).
- **Spanish reductions**: EUR 250,000 proposed, EUR 150,000 paid after two 20% reductions.
- **Why there was no fine**: public bodies cannot be fined in Spain (reprimand only); Italy chose a reprimand
  for cooperation and no previous infringements.
- **The Article 83(2) factors**, in each regulator's own words, including unusual ones (Hungary lowered the
  fine because the regulator itself was late; Belgium cited austerity).
- **A missing party name** (Austria: Bildungsdirektion Oberösterreich).

**Cost** (Opus, this session): the 20 cases took roughly 600,000 characters of reading (about 170,000 tokens)
plus the writing. Long cases (LaLiga 139 pages, Belgium 105 pages) were read in part; for those the core
facts are reliable but some details (LaLiga's final turnover figure) were not reached.

## Lessons for the skill

1. One fresh reader per decision, the same format, quotes always.
2. Read in this order: operative part, fine section, opening; then the rest only if a field is still empty.
3. Mark the kind of document: decision, press release, annual report, removed. Press releases give the
   outline but rarely the factors.
4. Private persons are never named (complainants, employees, the nurse in Spain).
5. Everything uncertain (scanned pages, a press release covering several fines, an unclear match) goes to
   review, never into Fino directly.
6. Test a cheaper model on these same 20 cases and compare field by field before it runs on its own.

## Cheaper models tested (7 October 2026)

The reading procedure is now a skill (`C:\Users\angel\AI\Skills\fino-reader\SKILL.md`). Six of the 20 cases
(IT, ES, BE 105 pages, GR, AT, NL press release) were read blind by Sonnet and by Haiku (texts copied to
`_test/`, no reference answers in reach) and scored with `python compare.py sonnet|haiku`.

| | Sonnet | Haiku |
|---|---|---|
| Fine, date, controller, articles, outcome | same as Opus in every case | same as Opus in every case |
| Quotes found word for word | 139 / 139 | 112 / 112 |
| Article 83(2) factors (aggravating / mitigating) | 22 / 15 (Opus 14 / 11) | 8 / 10 |
| Status (appeal etc.) | right in every case | thinner: 4 of 6 differ or missing |
| Tokens per decision (agent run) | about 85,000 | about 77,000 |

Sonnet matched Opus and found one infringement Opus missed (Fastweb, Articles 33-34 breach notification).
"Differences" in the scoring were reference gaps (Spain: no date in the text for either reader).
**Choice:** Sonnet for the full reading; Haiku is good enough for the official check of the core facts
(date, amount, party, outcome) only.

## Lean reader test (7 October 2026)

`excerpt.py` cuts each decision to about 20,000 characters (opening, fine passages, operative part, end; short
texts whole). Sonnet read only the excerpts of the 6 test cases (`analysis_lean.json`, `compare.py lean`).

- Quotes: 93 of 93 found word for word.
- Core fields equal to Sonnet on the full text: fine 4/4, organisation 6/6, outcome 6/6, document kind 6/6, date 4/4
  where the document has one (the Spanish decision carries no date; the Dutch text is a press release), status
  consistent (the two "differs" are wording).
- Thinner on long decisions: Italy and Belgium lost most per-article findings, orders and Article 83(2) factors.
- Cost: 56-67k tokens per decision as an agent run (about 64k on average, against about 80k for the full text).
  About 50k of that is the agent's fixed overhead, not the decision. A direct request with only the excerpt and
  the instructions would be about 10k tokens.
