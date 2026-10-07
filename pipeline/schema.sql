-- ============================================
-- GDPR ENFORCEMENT DATABASE — SQL SCHEMA (SQLite)
-- File: schema.sql
--
-- One row per decision per source. Nothing is ever deleted to "de-duplicate":
-- when the same decision appears in two sources, the pair is recorded in
-- case_links, and the v_cases view shows each real-world decision once.
-- ============================================

PRAGMA foreign_keys = ON;

-- ----- CASES -----
CREATE TABLE IF NOT EXISTS cases (
    case_id         TEXT PRIMARY KEY,          -- 'ETid-1679' (CMS) or GDPRhub page title
    pharos_id       TEXT UNIQUE,               -- your ID, e.g. '2026/HR/001'; assigned once, never changed
    source          TEXT NOT NULL CHECK (source IN ('cms_tracker', 'gdprhub')),

    country         TEXT,                      -- 'Croatia'
    country_code    TEXT,                      -- 'HR'
    authority       TEXT,
    decision_date   TEXT,                      -- 'YYYY-MM-DD', 'YYYY-MM' or 'YYYY' — as precise as the source
    date_precision  TEXT CHECK (date_precision IN ('day', 'month', 'year', 'unknown')),

    fine_eur        INTEGER,                   -- euro only; NULL when no fine or not in euro
    fine_original   TEXT,                      -- amount as the source wrote it
    currency        TEXT,

    controller      TEXT,                      -- organisation fined / investigated
    sector          TEXT,                      -- source's own sector label
    sector_tag      TEXT,                      -- CMS's 11 sectors, typo-fixed; NULL when unassigned
    articles_raw    TEXT,                      -- e.g. 'Art. 6 (1) GDPR, Art. 5 (1) d) GDPR'
    violation_type  TEXT,
    outcome         TEXT,                      -- normalised: Fine issued / Violation found / Rejected / ...
    summary         TEXT,                      -- the source's own summary text

    source_url      TEXT,                      -- regulator's original publication
    source_page     TEXT,                      -- page on the tracker / GDPRhub
    attribution     TEXT NOT NULL,             -- licence credit line required by CC BY-NC-SA

    first_seen      TEXT NOT NULL,             -- when the pipeline first stored it
    last_seen       TEXT NOT NULL,             -- last time it was present at the source
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_cases_country ON cases (country_code);
CREATE INDEX IF NOT EXISTS idx_cases_date    ON cases (decision_date);
CREATE INDEX IF NOT EXISTS idx_cases_fine    ON cases (fine_eur);
CREATE INDEX IF NOT EXISTS idx_cases_sector  ON cases (sector_tag);
CREATE INDEX IF NOT EXISTS idx_cases_source  ON cases (source);

-- ----- ARTICLES (one row per article per case) -----
CREATE TABLE IF NOT EXISTS case_articles (
    case_id   TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    article   TEXT NOT NULL,                   -- 'Art. 5(1)(a)'
    article_n INTEGER NOT NULL,                -- 5 — for "all Article 5 cases"
    PRIMARY KEY (case_id, article)
);
CREATE INDEX IF NOT EXISTS idx_articles_n ON case_articles (article_n);

-- ----- VIOLATION CATEGORIES (one row per category per case) -----
CREATE TABLE IF NOT EXISTS case_categories (
    case_id  TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    category TEXT NOT NULL,                    -- 'Data security', 'Consent', ...
    PRIMARY KEY (case_id, category)
);

-- ----- LINKS (the same decision recorded twice: across sources, or twice in GDPRhub) -----
CREATE TABLE IF NOT EXISTS case_links (
    primary_id   TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,  -- kept (CMS when there is one)
    duplicate_id TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,  -- hidden in v_cases
    rule         TEXT NOT NULL,                -- why they were linked
    status       TEXT NOT NULL DEFAULT 'auto' CHECK (status IN ('auto', 'confirmed', 'rejected')),
    created_at   TEXT NOT NULL,
    PRIMARY KEY (primary_id, duplicate_id)
);

-- ----- PARTIES (GDPRhub lists up to four parties per decision, without saying who is who) -----
-- Every party is kept as the source wrote it. 'role' is what the rules in pharos.py could tell
-- from the record itself; cases.controller is filled only when that evidence is clear.
CREATE TABLE IF NOT EXISTS case_parties (
    case_id   TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,                -- 1-4, as listed by the source
    name      TEXT NOT NULL,
    link      TEXT,
    role      TEXT NOT NULL CHECK (role IN ('respondent', 'complainant', 'authority', 'anonymous', 'person', 'unknown')),
    reason    TEXT,                            -- why the rules gave this role
    PRIMARY KEY (case_id, position)
);

-- Lorenzo's own check of who a decision is about. It always wins over the rules.
CREATE TABLE IF NOT EXISTS party_checks (
    case_id     TEXT PRIMARY KEY REFERENCES cases (case_id) ON DELETE CASCADE,
    controller  TEXT,                          -- NULL = no organisation can be named
    note        TEXT,
    checked_at  TEXT NOT NULL
);

-- ----- ID HISTORY (IDs are stable; any correction is recorded here) -----
CREATE TABLE IF NOT EXISTS id_history (
    case_id    TEXT NOT NULL REFERENCES cases (case_id) ON DELETE CASCADE,
    old_id     TEXT NOT NULL,
    new_id     TEXT NOT NULL,
    reason     TEXT NOT NULL,
    changed_at TEXT NOT NULL
);

-- ----- AUDIT LOG (every pipeline run) -----
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    step        TEXT NOT NULL,                 -- 'migrate', 'cms', 'gdprhub', 'link', 'normalise', 'export'
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    fetched     INTEGER DEFAULT 0,
    inserted    INTEGER DEFAULT 0,
    updated     INTEGER DEFAULT 0,
    status      TEXT,                          -- 'ok' / 'failed'
    notes       TEXT
);

-- ----- VIEWS -----

-- Each real-world decision once. The primary row is kept; a linked duplicate adds its summary/outcome
-- (and its fine) when the primary has none. Correlated subqueries (not joins) so several duplicates never multiply rows.
DROP VIEW IF EXISTS v_cases;
CREATE VIEW v_cases AS
SELECT
    c.case_id, c.pharos_id, c.source, c.country, c.country_code, c.authority, c.decision_date, c.date_precision,
    -- the primary's fine; when its source gives no amount at all, the linked page's (2021/IT/017, Enel)
    COALESCE(c.fine_eur, CASE WHEN COALESCE(c.fine_original, '') = '' THEN
             (SELECT g.fine_eur FROM case_links l JOIN cases g ON g.case_id = l.duplicate_id
              WHERE l.primary_id = c.case_id AND l.status != 'rejected' AND g.fine_eur IS NOT NULL
              ORDER BY g.case_id LIMIT 1) END)                              AS fine_eur,
    c.fine_original, c.currency, c.controller, c.sector, c.sector_tag, c.articles_raw, c.violation_type,
    c.outcome, c.summary, c.source_url, c.source_page, c.attribution, c.first_seen, c.last_seen, c.updated_at,
    COALESCE(NULLIF(c.summary, ''),
             (SELECT g.summary FROM case_links l JOIN cases g ON g.case_id = l.duplicate_id
              WHERE l.primary_id = c.case_id AND l.status != 'rejected' AND g.summary IS NOT NULL
              ORDER BY g.case_id LIMIT 1))                                   AS best_summary,
    COALESCE(c.outcome,
             (SELECT g.outcome FROM case_links l JOIN cases g ON g.case_id = l.duplicate_id
              WHERE l.primary_id = c.case_id AND l.status != 'rejected' AND g.outcome IS NOT NULL
              ORDER BY g.case_id LIMIT 1))                                   AS best_outcome,
    (SELECT g.source_page FROM case_links l JOIN cases g ON g.case_id = l.duplicate_id
     WHERE l.primary_id = c.case_id AND l.status != 'rejected' AND g.source = 'gdprhub'
     ORDER BY g.case_id LIMIT 1)                                             AS gdprhub_page,
    (SELECT group_concat(article, ', ') FROM case_articles a WHERE a.case_id = c.case_id) AS articles,
    (SELECT group_concat(category, ', ') FROM case_categories k WHERE k.case_id = c.case_id) AS categories
FROM cases c
WHERE c.case_id NOT IN (SELECT duplicate_id FROM case_links WHERE status != 'rejected');

DROP VIEW IF EXISTS v_stats;
CREATE VIEW v_stats AS
SELECT
    COUNT(*)                         AS cases,
    COUNT(DISTINCT country_code)     AS countries,
    COALESCE(SUM(fine_eur), 0)       AS total_fines_eur,
    MAX(decision_date)               AS latest_decision,
    (SELECT MAX(finished_at) FROM runs WHERE status = 'ok') AS last_run
FROM v_cases;

-- Which obligations regulators enforce most — the core compliance question.
-- Articles 51-84 (supervisory authorities, cooperation, remedies, penalties) are left out:
-- they describe what regulators and courts do, not what organisations must do.
-- A fine is counted in full under every article the decision cites.
DROP VIEW IF EXISTS v_articles_enforced;
CREATE VIEW v_articles_enforced AS
SELECT
    a.article_n                      AS gdpr_article,
    COUNT(DISTINCT v.case_id)        AS cases,
    SUM(v.fine_eur)                  AS total_fines_eur,
    CAST(AVG(v.fine_eur) AS INTEGER) AS avg_fine_eur
FROM v_cases v
JOIN case_articles a ON a.case_id = v.case_id
WHERE a.article_n NOT BETWEEN 51 AND 84
GROUP BY a.article_n
ORDER BY cases DESC;

-- ----- FINES IN OTHER CURRENCIES -----
-- An estimate in euro, from the European Central Bank's reference rate on the decision date
-- (or the month/year average when the source gives only that). The source amount stays the real figure.
CREATE TABLE IF NOT EXISTS fines_converted (
    case_id       TEXT PRIMARY KEY REFERENCES cases(case_id) ON DELETE CASCADE,
    amount        REAL NOT NULL,             -- the fine in its own currency, as read from fine_original
    currency      TEXT NOT NULL,             -- 'SEK'
    rate          REAL NOT NULL,             -- units of that currency per 1 euro
    rate_basis    TEXT NOT NULL,             -- 'day 2026-09-22', 'month 2024-03' or 'year 2021'
    fine_eur_est  INTEGER NOT NULL,          -- amount / rate, rounded to the euro
    converted_at  TEXT NOT NULL
);

-- Lorenzo's answers from review/currency.xlsx: they win over what the program reads
CREATE TABLE IF NOT EXISTS amount_checks (
    case_id       TEXT PRIMARY KEY REFERENCES cases(case_id) ON DELETE CASCADE,
    amount        REAL,                      -- the right amount; 0 = no single amount; NULL = the source amount is right
    decision_date TEXT,                      -- 'YYYY', 'YYYY-MM' or 'YYYY-MM-DD' when the source has none
    note          TEXT,
    checked_at    TEXT NOT NULL
);
