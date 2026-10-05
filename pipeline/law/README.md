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
