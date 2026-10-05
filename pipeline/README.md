# GDPR Enforcement Database

A SQL database (SQLite, one file: `gdpr.db`) of GDPR enforcement decisions from EU/EEA
data protection authorities. It powers Pharos Privacy.

| Source | What it adds | Licence |
|---|---|---|
| enforcementtracker.com, provided by CMS | Every published fine: authority, date, amount, organisation, sector, articles, violation type, link to the regulator | CC BY-NC-SA 4.0 |
| GDPRhub (noyb) | Decisions with and without fines, outcome, appeal status, short English summary | CC BY-NC-SA 4.0 |

Both licences require **attribution, non-commercial use, and the same licence** for anything built on them.
Every row carries its own `attribution` line.

## Running it

Python 3 only — nothing to install.

```
python pharos.py update            # refresh everything: CMS + GDPRhub + link + normalise + export
python pharos.py stats             # overview, most-enforced articles, last runs
```

Individual steps:

| Command | What it does |
|---|---|
| `migrate` | One-off. Imported `gdpr_database.json` (Feb 2026) so every case kept its ID. Already done. |
| `cms` | Downloads all Enforcement Tracker cases. The site embeds them in one page, so this is a single request. |
| `cms-summaries --limit N` | Fetches the summary from each case page, 1.5 s apart, newest first, only where missing. |
| `gdprhub` | Reads GDPRhub decision pages through its public API. |
| `link` | Marks the same decision appearing in both sources (see below). |
| `normalise` | Splits articles into `case_articles`, assigns violation categories, cleans sectors and outcomes. |
| `export` | Writes `exports/cases.csv` (opens in Excel), `exports/cases.json` and `exports/stats.json`. |

## Tables

| Table / view | Contents |
|---|---|
| `cases` | One row per decision per source. |
| `case_articles` | One row per GDPR article per case (`Art. 5(1)(a)`, plus the article number for grouping). |
| `case_categories` | One row per violation category per case. |
| `case_links` | Pairs of rows that are the same decision in two sources, with the rule that linked them. |
| `runs` | Audit log: every step, when it ran, what it fetched/inserted/updated, and whether it failed. |
| `v_cases` | **Use this one.** Each real decision once, with the best available summary and outcome. |
| `v_stats` | Totals. |
| `v_articles_enforced` | Cases and fines per GDPR article. |

## Useful queries

Open `gdpr.db` in [DB Browser for SQLite](https://sqlitebrowser.org/) (free) or any SQL tool.

```sql
-- Which obligations get enforced most?
SELECT * FROM v_articles_enforced LIMIT 10;

-- Fines against employers for monitoring or employee data, largest first
SELECT pharos_id, country, decision_date, controller, fine_eur, violation_type
FROM v_cases WHERE sector_tag = 'Employment' AND fine_eur IS NOT NULL
ORDER BY fine_eur DESC LIMIT 20;

-- Security failures (Art. 32) in health care, by country
SELECT country, COUNT(*) AS cases, SUM(fine_eur) AS total_eur
FROM v_cases v JOIN case_articles a ON a.case_id = v.case_id
WHERE a.article_n = 32 AND v.sector_tag = 'Health Care'
GROUP BY country ORDER BY cases DESC;

-- Typical fine for a transparency failure (Art. 13/14) in each country
SELECT country, COUNT(*) AS cases, CAST(AVG(fine_eur) AS INT) AS avg_eur, MAX(fine_eur) AS max_eur
FROM v_cases v JOIN case_articles a ON a.case_id = v.case_id
WHERE a.article_n IN (13, 14) AND fine_eur IS NOT NULL
GROUP BY country HAVING cases >= 5 ORDER BY avg_eur DESC;
```

## IDs

`pharos_id` follows your scheme: `YEAR/COUNTRY/NUMBER`, e.g. `2026/HR/001`. An ID is assigned
once and never changes — safe to cite.

- New cases continue the numbering (`2026/IT/107` → `2026/IT/108`).
- When the source gives no date, new IDs use `ND` instead of a year (e.g. `ND/DE/001`).
- **23 IDs from the February scrape carry a guessed year.** The old scraper used the current
  year (2026) when a case had no date. Find them with
  `SELECT pharos_id FROM cases WHERE date_precision = 'unknown';`.
  Nothing is published yet, so this is the moment to decide whether to re-number them.

## De-duplication: linking, not deleting

The same decision can appear in both CMS and GDPRhub, and GDPRhub occasionally has two pages
for one decision. Rows are never deleted. `case_links` records each pair with the rule that
matched it, and `v_cases` shows the decision once (CMS row first, with GDPRhub's summary and
outcome added). The rules are strict; anything ambiguous stays unlinked:

1. Same link to the regulator's original publication, when that link belongs to a single case
   on each side. Newsletters and annual reports cover many decisions, so shared links don't count.
2. Same country, same month, exactly the same euro fine, and exactly one candidate on each side.
3. One CMS case and several GDPRhub pages with the same country, month and exact fine, when the
   fine is at least EUR 100,000 (an amount that precise and that large identifies one decision).
4. Several GDPRhub pages and no CMS case, same country, month and exact fine of at least EUR 100,000.

Small fines (EUR 1,000, 5,000 ...) repeat too often to link on amount alone, so they're only
linked by rules 1 and 2.

To overrule a link: `UPDATE case_links SET status = 'rejected' WHERE duplicate_id = '...';`
Rejected pairs are never re-linked by later runs.

## Things to know

- **Dates** are stored as precisely as the source gives them (`2024-03-05`, `2019-11`, or `2021`),
  with `date_precision` saying which. No day or year is ever invented.
- **Fines** in `fine_eur` are euro only, and only when the source gives one clean amount.
  Fines in other currencies, and free text such as "200,000 + 150,000" or "Reduced from 24,000 to
  22,000", stay readable in `fine_original` but are left out of totals rather than guessed.
- **Dates in the future** (source typos) are stored as unknown.
- **CMS summaries:** before running `cms-summaries` for all ~3,300 cases, email
  info@enforcementtracker.com. Their licence allows reuse, but they also reserve text-and-data-mining
  rights under §44b UrhG, and a full run fetches every case page. The single-request `cms` step is
  unaffected.
- **OneDrive:** don't run the pipeline while OneDrive is mid-sync on `gdpr.db`, and keep the
  database closed in other tools while it runs.

## Old files

`scraper_enforcementtracker.py`, `database.py` and the GDPRhub test scripts are superseded by
`pharos.py` and kept for reference. The old scraper read the visible table in a browser, so
authority, sector and summary came out empty for every case. `scraper_edpb.py` (EDPB Article 60
register) is not yet part of the pipeline.
