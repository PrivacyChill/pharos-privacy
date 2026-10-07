# Where the decisions are: one row per publisher

First survey 7 October 2026. Goal: an inventory of **every** published decision, built from the regulators' own
lists (never by copying another database: CMS, GDPRhub and The DPO are used only to measure what we miss).
"Fino" and "The DPO" are decision counts on 7 Oct 2026; The DPO's are from its public statistics page.

Status: **ok** = the list answered (7 Oct); **blocked** = refuses automated visits; **find** = list not found yet.

| Code | Publisher | Where the decisions are listed | Status | Fino | The DPO | Notes |
|---|---|---|---|---|---|---|
| AT | Datenschutzbehörde | RIS open-data API (data.bka.gv.at), not the website | ok via API | 145 | 300 | Website has a security check; the API worked in the pilot |
| BE | APD/GBA + Market Court | gegevensbeschermingsautoriteit.be search, type "decision" (1,257 results) | ok | 269 | 449 | Market Court rulings are published there too; page 1 of a decision carries the appeal note |
| BG | CPDP | cpdp.bg | find | 37 | 149 | |
| CY | Commissioner | dataprotection.gov.cy | ok (home page) | 69 | 253 | Decision list still to locate |
| CZ | UOOU | uoou.gov.cz/media/rozhodnuti/... and "poskytnuté informace" PDFs | find | | | No single list found yet |
| DE | BfDI + 16 Länder | Mostly annual reports; fines (OWiG) are rarely published | find | 236 | 142 | Structural gap for everyone. Lower Saxony publishes a fines list (lfd.niedersachsen.de); FragDenStaat FOI answers |
| DK | Datatilsynet | datatilsynet.dk/afgoerelser/afgoerelser (by year, filters) | ok | 116 | 441 | "A selection" of decisions |
| EE | AKI | aki.ee PDFs (ettekirjutus, vaideotsus) | find | | | Many are public-information cases, not GDPR |
| EU | EDPB register of final one-stop-shop decisions | edpb.europa.eu/registers/register-of-final-one-stop-shop-decisions_en (~145 pages) | ok | | | Official summaries of every cross-border decision; reuse allowed with credit. Excludes some DE Länder, LT, NL |
| FI | Tietosuojavaltuutettu | finlex.fi/fi/viranomaiset/tsv/ | ok | 102 | 235 | Finlex has an open-data API |
| FR | CNIL | **data.gouv.fr "Les délibérations de la CNIL"** (open data, DILA) | ok | 128 | 664 | Solves the Légifrance block; Licence Ouverte allows commercial reuse |
| GR | HDPA | dpa.gr/el/enimerwtiko/prakseisArxis | ok | 198 | 393 | |
| HR | AZOP | azop.hr/rjesenja/ | ok | 67 | 246 | |
| HU | NAIH | naih.hu/hatarozatok-vegzesek | ok | 124 | 342 | |
| IE | DPC | dataprotection.ie/en/dpc-guidance/decisions | find (URL moved) | 62 | | Large decisions are scanned PDFs |
| IS | Persónuvernd | personuvernd.is/urlausnir/ | ok | 123 | 144 | |
| IT | Garante | garanteprivacy.it docweb (+ newsletters) | ok, rate-limited | 766 | 602 | Blocks after ~700 quick requests; go slower |
| LI | Datenschutzstelle | datenschutzstelle.li | ok (home page) | | | |
| LT | VDAI | vdai.lrv.lt/lt/sprendimai/ (2026, 2025 and 'iki 2025' pages, found by Lorenzo) | ok via a browser session (Cloudflare) | 36 | 443 | Since 2025 every complaint decision is in a table (party, subject, result, PDF): 387 for 2025-26; before 2025 only 45 'more significant decisions', often summaries. Annual reports: vdai.lrv.lt/lt/administracine-informacija/veiklos-ataskaitos/ |
| LU | CNPD | cnpd.public.lu/fr/decisions-sanctions.html | ok | | | |
| LV | DVI | dvi.gov.lv/lv/lemumi | ok | 22 | 250 | |
| MT | IDPC | idpc.org.mt/decisions/ | ok | 23 | 258 | |
| NL | AP | autoriteitpersoonsgegevens.nl (URL moved) | find | 57 | | Must publish all GDPR sanctions by law since 1 Sept 2026 |
| NO | Datatilsynet + Personvernnemnda (appeals board) | datatilsynet.no "Sentrale avgjørelser"; personvernnemnda.no | ok / find | 117 | | The appeals board is an appeal source |
| PL | UODO | orzeczenia.uodo.gov.pl (searchable database) | ok | 151 | 300 | |
| PT | CNPD | cnpd.pt/decisoes/deliberacoes/ | ok | | | |
| RO | ANSPDCP | dataprotection.ro news | ok | 320 | 312 | Mostly press releases, not decisions |
| SE | IMY | imy.se/tillsyner/ | ok | 92 | | |
| SI | IP | ip-rs.si | find | 104 | 141 | |
| SK | UOOU | dataprotection.gov.sk | find | | | |
| ES | AEPD | aepd.es/informes-y-resoluciones/resoluciones | ok | 1,294 | 786 | Publishes everything, including dismissed complaints: needs a scope rule |

**Appeals and courts (for "what happened next"):** Belgian Market Court (on the APD site), Norwegian
Personvernnemnda, Spanish Audiencia Nacional (CENDOJ), French Conseil d'État (Judilibre / Légifrance API),
Dutch courts (rechtspraak.nl open data), Austrian BVwG (RIS), Polish administrative courts (CBOSA), CJEU (EUR-Lex).
Italian courts publish little: the Garante's removal notices are the main signal (`pilot/garante_removed.py`).

## Machine-readable routes and the daily watch (7 October 2026)

`watch.py` checks 41 channels every morning at 07:30 (Windows task "Fino Watch"); the channels are in
`watch_channels.csv`. What exists, regulator by regulator:

- **APIs and open data:** Austria (RIS open-data API, 1,891 DSB documents, no key needed), France (DILA CNIL
  dumps, a new file each working day), Finland (Finlex open-data REST API), Spain (AEPD open-data portal on
  datos.gob.es, still to explore), EU (EDPB register; EUR-Lex/CELLAR for the CJEU), GDPRhub (MediaWiki API),
  UK (ICO spreadsheet of monetary penalties since 2010; no feed). Poland's orzeczenia.uodo.gov.pl is a public
  database built as a script app: its data route is still to find.
- **RSS feeds (mostly news, not every decision):** EDPB (news, publications), Greece (**a decisions-only feed**),
  Czech Republic, Finland, Germany (BfDI), Italy, Netherlands, Romania, Sweden, Liechtenstein; Bulgaria and Cyprus
  refuse automated visits.
- **Sitemaps listing decision pages:** Denmark, Netherlands, ICO, Ireland, Croatia, Czech Republic, Latvia, Malta.
  Spain, Italy, Finland, Romania: the sitemap does not list the decisions.
- **List pages watched directly:** EDPB register, Belgium, Hungary, Luxembourg, Portugal, Spain, Sweden, Norway,
  France (CNIL sanctions page, incl. the simplified procedure), Poland (news).
- **To fix (pattern or address):** Finland (Finlex page), Slovakia, Slovenia, Iceland, Portugal, Italy (decision list).
- **Refused:** Estonia, Lithuania, Bulgaria and Cyprus feeds (in review/blocked.xlsx).
