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
