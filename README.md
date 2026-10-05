# Pharos

A free, searchable collection of GDPR enforcement decisions from across Europe: https://pharosprivacy.com

A personal, non-commercial project by Lorenzo A. (CIPP/E). Not legal advice.

## How it works

- `pipeline/pharos.py` builds `pipeline/gdpr.db` (SQLite) from two public sources, then exports a compact copy to `site/data/`. Standard-library Python only. See [pipeline/README.md](pipeline/README.md).
- `site/` is a static website. Searching happens in the visitor's browser; nothing is sent anywhere.
- A scheduled job refreshes the data every week and publishes it through GitHub Pages.

Data rules: nothing is guessed. Dates keep the precision of the source, fines are recorded only when the source gives one clear euro amount, and duplicates across sources are linked, never deleted. Every run is logged in the `runs` table.

## Data licence

The case data comes from [enforcementtracker.com, provided by CMS](https://www.enforcementtracker.com/) and [GDPRhub](https://gdprhub.eu/) (noyb). Both are licensed under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), and so is the data in this repository (`pipeline/gdpr.db`, `site/data/`): you may reuse it for non-commercial purposes, with credit to the sources, under the same licence.
