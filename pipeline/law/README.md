# Law texts for the website

`site/data/reg-2016-679.json` holds the GDPR as shown on the site: 99 articles, the chapters and
the preamble (citations and 173 recitals). It is built from the EU Publications Office,
which serves the same documents as EUR-Lex (eur-lex.europa.eu itself blocks scripts).

## Rebuild

Run from this folder:

```
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o gdpr_cons.html "http://publications.europa.eu/resource/celex/02016R0679-20160504"
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o gdpr_cellar.html "http://publications.europa.eu/resource/celex/32016R0679"
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o "corr_32016R0679R%2802%29.html" "http://publications.europa.eu/resource/celex/32016R0679R%2802%29"
python gdpr_parse.py
python recitals_parse.py
python -c "import json; json.dump(json.load(open('gdpr.json', encoding='utf-8')), open('../../site/data/reg-2016-679.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))"
```

- **Articles** come from the consolidated text (CELEX 02016R0679-20160504), which already
  includes the 2018 corrigendum.
- **Recitals** are not in the consolidated text, so they come from the Official Journal text
  (CELEX 32016R0679). The one English correction to a recital (recital 71, corrigendum
  OJ L 127, 23.5.2018, p. 2) is applied by `recitals_parse.py`, which stops if the text to
  correct is not found.
- Both scripts check that every word of the source survives the conversion, and stop if not.
- References such as "Article 6(1)" become links to the article on the site. A reference to
  another act ("Article 29 of Directive 95/46/EC") is left as plain text.

The downloaded `.html` files and the intermediate `gdpr.json` here are not committed.

# The AI Act

`site/data/reg-2024-1689.json` holds the AI Act (Regulation (EU) 2024/1689) as shown on the site:
116 articles (113 plus 4a, 60a and 75a), 14 annexes, the chapters and the preamble (citations and
180 recitals). The site loads it only when an AI Act page is opened.

## Rebuild

Run from this folder (the downloads go in `aiact/`, which is not committed):

```
mkdir -p aiact
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o aiact/aiact_cons.html "http://publications.europa.eu/resource/celex/02024R1689-20260727"
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o aiact/aiact_oj.html "http://publications.europa.eu/resource/celex/32024R1689"
curl -sL -H "Accept: application/xhtml+xml" -H "Accept-Language: eng" -o aiact/amend_2026_1744.html "http://publications.europa.eu/resource/celex/32026R1744"
python aiact_parse.py
python -c "import json; json.dump(json.load(open('aiact/aiact.json', encoding='utf-8')), open('../../site/data/reg-2024-1689.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))"
node ../build_seo.mjs
```

- **Articles and annexes** come from the consolidated text of 27 July 2026, which includes the Digital
  Omnibus on AI (Regulation (EU) 2026/1744, OJ 24.7.2026). When EUR-Lex publishes a newer consolidated
  version, change the date in the first URL and in `AI_CONS` (site/fines.html and build_seo.mjs).
  To find versions and corrigenda: query the SPARQL endpoint for CELEX numbers containing `2024R1689`.
- **Recitals** come from the Official Journal text (32024R1689). The four corrigenda so far
  (R(01) to R(04), 2025-2026) exist only in other languages, so the English text is unchanged.
- **Changed in 2026**: the parser follows the ▼M1 / ▼B markers of the consolidated text. Its list
  (42 places) was checked against the 43 points of Article 1 of Regulation 2026/1744 (points 2 and 3
  both touch Article 2).
- The parser checks that every word of the source survives, and stops if not.
- References become links: "Article 6(1)", "Articles 102 to 109", "Annex III". A reference to another
  act ("Article 10 of Regulation (EU) 2016/679", "Articles 5, 6, and 7 of Regulation (EU) No 1025/2012",
  "Article 16 TFEU") stays plain text. Articles 102 to 110 amend other acts, so nothing in them is linked.
- The official text has a typing slip in the title of Article 1 ("Subject matter`" in the Official
  Journal, "Subject matter'" in the consolidated text). It has no meaning, so the parser removes it
  (`TITLE_FIX` in aiact_parse.py). The words of the law are not changed.
