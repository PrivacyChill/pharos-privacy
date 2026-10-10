// Makes Fino findable: one real page per decision and per GDPR article, plus the sitemap, robots.txt,
// llms.txt, search-engine tags on the main pages, and the live figures on the homepage.
//
//   node pipeline/build_seo.mjs          build everything into site/ (pharos.py export runs this)
//   node pipeline/build_seo.mjs --ping   after the push is live: tell Bing, Yandex, Seznam and others
//                                        (IndexNow) which pages are new or changed
//
// The fines page shows decisions behind a # link (fines.html#d-2024-fr-001). Search engines ignore
// everything after the #, so to them the 5,000 decisions were one page. Here the page's own drawing code
// (renderDecision, renderArticle, renderGdpr in fines.html) runs once per decision and the result is saved
// as a plain HTML page, so the static pages always look and read exactly like the live view.

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

const BASE = process.env.FINO_BASE || 'https://finoprivacy.com';   // change once, when the domain changes
const ROOT = path.resolve(import.meta.dirname, '..');
const SITE = path.join(ROOT, 'site');
const LASTMOD_FILE = path.join(ROOT, 'pipeline', 'seo_lastmod.json');
const PENDING_FILE = path.join(ROOT, 'pipeline', 'seo_pending.json');
const read = p => fs.readFileSync(path.join(SITE, p), 'utf8');
const write = (p, s) => { fs.mkdirSync(path.dirname(path.join(SITE, p)), { recursive: true }); fs.writeFileSync(path.join(SITE, p), s); };
const hash = s => crypto.createHash('sha1').update(s).digest('hex').slice(0, 12);
const attr = s => String(s ?? '').replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const unesc = s => s.replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
const text = html => unesc(html.replace(/<[^>]+>/g, '').replace(/&rsquo;/g, '’').replace(/&middot;/g, '·').replace(/&nbsp;/g, ' ')).replace(/\s+/g, ' ').trim();
const cut = (s, n = 158) => s.length <= n ? s : s.slice(0, s.lastIndexOf(' ', n - 1)).replace(/[,;:.\s]+$/, '') + '…';
const TODAY = new Date().toISOString().slice(0, 10);

if (process.argv.includes('--ping')) { await ping(); process.exit(0); }

/* ---------- 1. Take the fines page apart ---------- */
const FINES = read('fines.html');
const between = (s, a, b) => { const i = s.indexOf(a); const j = s.indexOf(b, i + a.length); if (i < 0 || j < 0) throw new Error('fines.html changed: ' + a); return s.slice(i, j); };
const CSS = between(FINES, '<style>', '</style>').slice('<style>'.length).replace(/url\("fonts\//g, 'url("/fonts/');
const CSP = FINES.match(/<meta http-equiv="Content-Security-Policy"[^>]*>/)[0];
const MODE = FINES.match(/<script>try \{ if \(localStorage[^<]*<\/script>/)[0];
const HEADER = between(FINES, '<header class="top">', '</header>') + '</header>';
const FOOTER = between(FINES, '<footer>', '</footer>') + '</footer>';
const SHELL_JS = between(FINES, '/* Laws menu', '\n</script>');
let BOOT = between(FINES, 'function boot(DATA) {', '/* Load the data, then start.');
const HOOK = "window.addEventListener('hashchange', route);";
if (!BOOT.includes(HOOK)) throw new Error('fines.html changed: router hook');
BOOT = BOOT.replace(HOOK, 'globalThis.__FINO = { CASES, BY_SLUG, GDPR, CHAPTER, party, fineInfo, when, authority, slug, citation, artStats, bigEur, num, route };\n' + HOOK);

/* Shared files: one stylesheet and one script, cached once by the browser for every static page */
const CSS_V = hash(CSS);
const JS = `const byId = id => document.getElementById(id);
${SHELL_JS}

/* Copy citation (decision pages): the date of access is today's */
(() => {
  const b = byId('copy-cite'); if (!b || !b.dataset.cite) return;
  const M = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  b.addEventListener('click', () => {
    const d = new Date(), t = \`\${b.dataset.cite}, accessed \${d.getDate()} \${M[d.getMonth()]} \${d.getFullYear()}.\`;
    const done = m => { byId('copied').textContent = m; };
    try { navigator.clipboard.writeText(t).then(() => done('Copied.'), () => done(t)); } catch (e) { done(t); }
  });
})();

/* Recitals (GDPR and AI Act pages): "Go to recital", and links straight to #rec-12 or #ai-rec-12 open the preamble */
(() => {
  const pre = byId('preamble'); if (!pre) return;
  const p = (pre.querySelector('.rec') || { id: 'rec-1' }).id.replace(/\\d+$/, ''), max = +(byId('goto-rec') || {}).max;
  const re = new RegExp('^#' + p + '(\\\\d+)$');
  const open = n => { const el = byId(p + n); if (!el) return; pre.open = true; el.scrollIntoView({ block: 'start' }); };
  const m = location.hash.match(re); if (m) open(+m[1]);
  addEventListener('hashchange', () => { const k = location.hash.match(re); if (k) open(+k[1]); });
  const f = byId('goto-form'); if (!f) return;
  f.addEventListener('submit', e => {
    e.preventDefault();
    const n = +byId('goto-rec').value;
    if (!(n >= 1 && n <= max)) { byId('goto-msg').textContent = 'Choose a number from 1 to ' + max + '.'; return; }
    byId('goto-msg').textContent = '';
    history.replaceState(null, '', '#' + p + n); open(n);
  });
})();
`;
const JS_V = hash(JS);
write('assets/fino.css', '/* Built from fines.html by pipeline/build_seo.mjs. Do not edit here. */\n' + CSS);
write('assets/fino.js', '/* Built from fines.html by pipeline/build_seo.mjs. Do not edit here. */\n' + JS);

/* ---------- 2. Run the page's own code on the data ---------- */
const cases = JSON.parse(read('data/cases.json'));
const gdpr = JSON.parse(read('data/reg-2016-679.json'));
const aiact = JSON.parse(read('data/reg-2024-1689.json'));
let lawLinks = null; try { lawLinks = JSON.parse(read('data/law-links.json')); } catch (e) {}
/* what the GDPR / AI Act boxes say about one article, so a page's date moves when its box changes */
const linksOf = (kind, k) => !lawLinks ? '' : JSON.stringify(kind === 'gdpr'
  ? [lawLinks.cites[k], lawLinks.cite_recs[k], lawLinks.pairs.filter(p => p.gdpr === +k)]
  : lawLinks.pairs.filter(p => p.kind === kind && p.ai === k));
let ex = null; try { ex = JSON.parse(read('data/explainers.json')); } catch (e) {}
const shards = Math.ceil(cases.rows.length / cases.meta.summary_shard);
const clean = s => (s && s.replace(/<!--[\s\S]*?-->/g, '').replace(/\s+/g, ' ').trim()) || null;
const sum = Array.from({ length: shards }, (_, i) => JSON.parse(read(`data/summaries/${i}.json`))).flat().map(clean);
const by = cases.meta.fields.indexOf('summary_by');
const DATA = { meta: cases.meta, fields: cases.meta.fields, rows: cases.rows, sum, gdpr, aiact, ex, links: lawLinks, cred: cases.rows.map(r => r[by] === 'cms_tracker' ? 'c' : 'g') };

/* a pretend browser: just enough for the drawing code; #main keeps what it is given */
const el = () => ({ innerHTML: '', textContent: '', value: '', style: {}, dataset: {}, hidden: false,
  classList: { toggle() {}, add() {}, remove() {}, contains: () => false },
  addEventListener() {}, setAttribute() {}, removeAttribute() {}, focus() {}, scrollIntoView() {},
  querySelector: () => el(), querySelectorAll: () => [], closest: () => null });
const els = {};
const fakeDoc = { getElementById: id => (els[id] ||= el()), querySelector: () => el(), querySelectorAll: () => [],
  documentElement: el(), body: el(), addEventListener() {}, title: '' };
const loc = { hash: '' };
const fakeWin = { addEventListener() {}, scrollTo() {}, scrollY: 0 };
const first = cases.rows[0][cases.meta.fields.indexOf('pharos_id')].replace(/\//g, '-');
loc.hash = '#d-' + first;
new Function('document', 'window', 'location', 'history', 'navigator', 'matchMedia', 'addEventListener', 'scrollTo', 'innerWidth', 'innerHeight',
  BOOT + '\nboot(arguments[10]);')(fakeDoc, fakeWin, loc, { replaceState() {} }, {}, () => ({ matches: false }), () => {}, () => {}, 1200, 800, DATA);
const F = globalThis.__FINO;
const show = h => { loc.hash = '#' + h; F.route(); return els.main.innerHTML; };

/* ---------- 3. Page shell ---------- */
function links(html, { inPageRecitals = false } = {}) {
  return html
    .replace(/href="#d-([^"]+)"/g, 'href="/decisions/$1.html"')
    .replace(/href="#x-([^"]+)"/g, 'href="/fines.html#x-$1"')
    .replace(/href="#art-(\d+)"/g, 'href="/gdpr/article-$1.html"')
    .replace(/href="#rec-(\d+)"/g, inPageRecitals ? 'href="#rec-$1"' : 'href="/gdpr/#rec-$1"')
    .replace(/href="#gdpr"/g, 'href="/gdpr/"')
    .replace(/href="#ai-art-(\d+a?)"/g, 'href="/ai-act/article-$1.html"')
    .replace(/href="#ai-annex-([IVX]+)"/g, (_, r) => `href="/ai-act/annex-${r.toLowerCase()}.html"`)
    .replace(/href="#ai-rec-(\d+)"/g, inPageRecitals ? 'href="#ai-rec-$1"' : 'href="/ai-act/#ai-rec-$1"')
    .replace(/href="#ai-act"/g, 'href="/ai-act/"')
    .replace(/href="#fines" data-(art|org|group)="([^"]*)"/g, (_, k, v) => `href="/fines.html#fines-${k}-${encodeURIComponent(unesc(v))}"`)
    .replace(/href="#fines"/g, 'href="/fines.html"')
    .replace(/href="#style"/g, 'href="/fines.html#style"')
    .replace(/href="index\.html"/g, 'href="/"')
    .replace(/href="(?![a-z]+:|\/|#)([^"]+)"/gi, 'href="/$1"');
}
const header = on => links(HEADER)
  .replace('id="nav-fines"', on === 'fines' ? 'id="nav-fines" class="on"' : 'id="nav-fines"')
  .replace('id="nav-gdpr"', on === 'gdpr' ? 'id="nav-gdpr" class="on"' : 'id="nav-gdpr"')
  .replace(`href="/${on}">`, `href="/${on}" class="on" aria-current="page">`);
const OG_IMAGE = `${BASE}/og/fino.png`;
const LOGO = `${BASE}/og/fino-logo.png`;
const ORG = { '@type': 'Organization', '@id': `${BASE}/#org`, name: 'Fino', url: `${BASE}/`, logo: LOGO };
const crumbs = list => ({ '@type': 'BreadcrumbList', itemListElement: list.map(([name, url], i) => ({ '@type': 'ListItem', position: i + 1, name, ...(url ? { item: url } : {}) })) });
const ld = obj => `<script type="application/ld+json">${JSON.stringify({ '@context': 'https://schema.org', ...obj }).replace(/</g, '\\u003c')}</script>`;

function headTags({ title, desc, url, type = 'website', image = OG_IMAGE }) {
  return `<title>${attr(title)}</title>
<meta name="description" content="${attr(desc)}">
<link rel="canonical" href="${url}">
<meta property="og:type" content="${type}">
<meta property="og:site_name" content="Fino">
<meta property="og:title" content="${attr(title.replace(/ · Fino$/, ''))}">
<meta property="og:description" content="${attr(desc)}">
<meta property="og:url" content="${url}">
<meta property="og:image" content="${image}">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Fino, the mascot of the site, next to the words: GDPR fines and decisions, in plain English">
<meta property="og:locale" content="en_GB">
<meta name="twitter:card" content="summary_large_image">`;
}
function page({ title, desc, url, on, main, json, css = '', js = '', type }) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
${CSP}
<meta name="referrer" content="strict-origin-when-cross-origin">
<meta name="viewport" content="width=device-width, initial-scale=1">
${headTags({ title, desc, url, type })}
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="preload" href="/fonts/fraunces-300-800-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="/assets/fino.css?v=${CSS_V}">
${MODE}
${ld(json)}${css ? `
<style>${css}</style>` : ''}
</head>
<body>

${header(on)}

<main id="main">${main}</main>

${links(FOOTER)}

<script src="/assets/fino.js?v=${JS_V}" defer></script>${js ? `
<script>${js}</script>` : ''}
</body>
</html>
`;
}

/* lastmod only moves when what a page says changes, so search engines can trust it */
let lastmod = {}; try { lastmod = JSON.parse(fs.readFileSync(LASTMOD_FILE, 'utf8')); } catch (e) {}
const seen = new Set(), changed = [];
function stamp(url, content) {
  const h = hash(content), old = lastmod[url];
  if (!old || old[0] !== h) { lastmod[url] = [h, TODAY]; changed.push(url); }
  seen.add(url);
  return lastmod[url][1];
}
function clearHtml(dir) {
  const d = path.join(SITE, dir);
  if (fs.existsSync(d)) for (const f of fs.readdirSync(d)) if (f.endsWith('.html')) fs.unlinkSync(path.join(d, f));
}

/* ---------- 4. Decision pages ---------- */
clearHtml('decisions');
const sitemap = [];
for (const c of F.CASES) {
  const s = F.slug(c.pharos_id), url = `${BASE}/decisions/${s}.html`;
  const p = F.party(c), f = F.fineInfo(c), d = F.when(c.decision_date, c.date_precision);
  /* several parties named but not yet checked: only the first goes in the title, so no unchecked name is headlined */
  const who = p.person ? `${p.name} (private person)` : p.unchecked ? `${c.parties_named.split('; ')[0]} and others` : p.name;
  const where = [c.country, c.year].filter(Boolean).join(', ');
  const what = (f.none ? (/^(Rejected|Violation found|Reprimand)/.test(c.best_outcome || '') ? `GDPR decision: ${c.best_outcome.toLowerCase()}` : 'GDPR decision') : `GDPR fine of ${f.label}`)
    + (f.status ? `, ${f.status.toLowerCase()}` : '');
  const title = `${who}: ${what}${where ? ` (${where})` : ''} · Fino`;
  const arts = c.arts.length ? ` Articles ${c.arts.slice(0, 4).join(', ')} GDPR.` : '';
  const desc = c.summary ? cut(c.summary)
    : cut(`${F.authority(c.authority)} decision${d.main !== 'Date not published' ? ` of ${d.main}` : ''} about ${who}: ${f.label}.${arts} Facts, sources and similar decisions.`);
  let main = show('d-' + s);
  main = main.replace(/<svg class="strip"[\s\S]*?<\/svg>\s*<p class="legend">Each dot is one fine[^<]*<\/p>/,
    `<p class="legend"><a href="#d-${s}" data-chart>See this fine among all the fines in ${attr(c.country)} &rarr;</a></p>`);
  const cite = F.citation(c).replace(/, accessed [^,]*\.$/, '');
  main = links(main).replace('href="/decisions/' + s + '.html" data-chart', `href="/fines.html#d-${s}"`)
    .replace('id="copy-cite"', `id="copy-cite" data-cite="${attr(cite)}"`);
  const mod = stamp(url, JSON.stringify([cases.rows[c.idx], c.summary]));
  const about = c.controller && c.party_kind !== 'person' ? { about: { '@type': 'Organization', name: c.controller } } : {};
  const json = { '@graph': [
    { '@type': 'WebPage', '@id': url, url, name: title.replace(/ · Fino$/, ''), description: desc, inLanguage: 'en', dateModified: mod,
      isPartOf: { '@id': `${BASE}/fines.html#dataset` }, publisher: { '@id': `${BASE}/#org` }, ...about,
      mentions: { '@type': 'GovernmentOrganization', name: F.authority(c.authority) },
      ...(c.source_url ? { citation: c.source_url } : {}) },
    crumbs([['Fino', `${BASE}/`], ['GDPR fines', `${BASE}/fines.html`], [p.person ? 'Private person' : who]]),
    ORG ] };
  write(`decisions/${s}.html`, page({ title, desc, url, on: 'fines', main, json }));
  sitemap.push([url, mod]);
}
/* old case numbers (an undated case that later got its year) forward to the new page */
for (const [old, cur] of Object.entries(cases.meta.moved || {})) {
  const o = F.slug(old), n = F.slug(cur);
  if (o === n || F.BY_SLUG.get(o)?.pharos_id === old || !F.BY_SLUG.has(n) || F.BY_SLUG.get(n).pharos_id !== cur) continue;
  write(`decisions/${o}.html`, `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved · Fino</title><meta name="robots" content="noindex"><link rel="canonical" href="${BASE}/decisions/${n}.html"><meta http-equiv="refresh" content="0; url=/decisions/${n}.html"></head><body><p>This decision now has the number ${cur}: <a href="/decisions/${n}.html">open it</a>.</p></body></html>\n`);
}

/* a decision merged into its twin from the other source forwards to it, if its page was ever published */
let merged = {}; try { merged = JSON.parse(fs.readFileSync(path.join(ROOT, 'pipeline', 'seo_merged.json'), 'utf8')); } catch (e) {}
for (const [old, cur] of Object.entries(merged)) {
  const o = F.slug(old), n = F.slug(cur), ou = `${BASE}/decisions/${o}.html`;
  if (!(ou in lastmod) || o === n || F.BY_SLUG.get(o)?.pharos_id === old || !F.BY_SLUG.has(n) || F.BY_SLUG.get(n).pharos_id !== cur) continue;
  write(`decisions/${o}.html`, `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved · Fino</title><meta name="robots" content="noindex"><link rel="canonical" href="${BASE}/decisions/${n}.html"><meta http-equiv="refresh" content="0; url=/decisions/${n}.html"></head><body><p>Two sources published this decision; Fino now shows it once, as ${cur}: <a href="/decisions/${n}.html">open it</a>.</p></body></html>
`);
  seen.add(ou);  // remembered, so the forward survives later builds
}

/* ---------- 5. The GDPR: one page per article, and the whole text with the recitals ---------- */
clearHtml('gdpr');
const ELI = 'https://eur-lex.europa.eu/eli/reg/2016/679/oj';
const GDPR_LAW = { '@type': 'Legislation', '@id': `${BASE}/gdpr/#law`, name: 'General Data Protection Regulation (GDPR)', alternateName: 'Regulation (EU) 2016/679',
  legislationIdentifier: 'CELEX:32016R0679', legislationType: 'Regulation', legislationJurisdiction: 'EU', legislationDate: '2016-04-27', sameAs: ELI };
const artNums = Object.keys(gdpr.arts).map(Number).sort((a, b) => a - b);
for (const n of artNums) {
  const a = gdpr.arts[n], st = F.artStats(n), url = `${BASE}/gdpr/article-${n}.html`;
  const title = `Article ${n} GDPR: ${a.t} · Fino`;
  const desc = cut(`Full text of Article ${n} GDPR (${a.t})${st.list.length ? `, cited in ${F.num(st.list.length)} decisions${st.fined.length ? ` with ${F.bigEur(st.total)} in fines` : ''}. See the biggest fines` : '. With a link to the official text on EUR-Lex'}.`);
  const main = links(show('art-' + n));
  const mod = stamp(url, a.h + st.list.length + linksOf('gdpr', n));
  const json = { '@graph': [
    { '@type': 'Legislation', '@id': url, url, name: `Article ${n} GDPR: ${a.t}`, legislationIdentifier: `Regulation (EU) 2016/679, Article ${n}`,
      legislationType: 'Article', legislationJurisdiction: 'EU', legislationLegalForce: 'InForce', inLanguage: 'en', isPartOf: GDPR_LAW,
      sameAs: `https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02016R0679-20160504#art_${n}`, dateModified: mod },
    crumbs([['Fino', `${BASE}/`], ['GDPR', `${BASE}/gdpr/`], [`Article ${n}`]]), ORG ] };
  write(`gdpr/article-${n}.html`, page({ title, desc, url, on: 'gdpr', main, json }));
  sitemap.push([url, mod]);
}
{
  const url = `${BASE}/gdpr/`, main = links(show('gdpr'), { inPageRecitals: true });
  const title = 'The GDPR: full text, all 99 articles and 173 recitals, with the fines · Fino';
  const desc = 'The General Data Protection Regulation in the official English text: all 99 articles and 173 recitals, each article linked to the GDPR fines and decisions that cite it.';
  const mod = stamp(url, main);
  write('gdpr/index.html', page({ title, desc, url, on: 'gdpr', main,
    json: { '@graph': [{ ...GDPR_LAW, url, inLanguage: 'en', legislationLegalForce: 'InForce', dateModified: mod }, crumbs([['Fino', `${BASE}/`], ['GDPR']]), ORG] } }));
  sitemap.unshift([url, mod]);
}

/* ---------- 5b. The AI Act: one page per article and annex, and the whole text with the recitals ---------- */
clearHtml('ai-act');
const AI_LAW = { '@type': 'Legislation', '@id': `${BASE}/ai-act/#law`, name: 'Artificial Intelligence Act (AI Act)', alternateName: 'Regulation (EU) 2024/1689',
  legislationIdentifier: 'CELEX:32024R1689', legislationType: 'Regulation', legislationJurisdiction: 'EU', legislationDate: '2024-06-13',
  sameAs: 'https://eur-lex.europa.eu/eli/reg/2024/1689/oj' };
const AI_CONS = 'https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02024R1689-20260727';
const aiChanged = k => aiact.amended.includes(k) ? (/a$/.test(k) || k === 'annex-XIV' ? ' Added in July 2026 by the Digital Omnibus on AI.' : ' Includes the July 2026 changes (Digital Omnibus on AI).') : '';
for (const k of aiact.order) {
  const a = aiact.arts[k], url = `${BASE}/ai-act/article-${k}.html`;
  const title = `Article ${k} AI Act: ${a.t} · Fino`;
  const desc = cut(`Full text of Article ${k} of the EU AI Act (${a.t}), in the official English text.${aiChanged(k)} With a link to the original on EUR-Lex.`);
  const main = links(show('ai-art-' + k));
  const mod = stamp(url, a.h + linksOf('ai-art', k));
  const json = { '@graph': [
    { '@type': 'Legislation', '@id': url, url, name: `Article ${k} AI Act: ${a.t}`, legislationIdentifier: `Regulation (EU) 2024/1689, Article ${k}`,
      legislationType: 'Article', legislationJurisdiction: 'EU', legislationLegalForce: 'InForce', inLanguage: 'en', isPartOf: AI_LAW,
      sameAs: `${AI_CONS}#art_${k}`, dateModified: mod },
    crumbs([['Fino', `${BASE}/`], ['AI Act', `${BASE}/ai-act/`], [`Article ${k}`]]), ORG ] };
  write(`ai-act/article-${k}.html`, page({ title, desc, url, on: 'gdpr', main, json }));
  sitemap.push([url, mod]);
}
for (const r of aiact.annex_order) {
  const a = aiact.annexes[r], url = `${BASE}/ai-act/annex-${r.toLowerCase()}.html`;
  const title = `Annex ${r} AI Act: ${a.t} · Fino`;
  const desc = cut(`Full text of Annex ${r} of the EU AI Act (${a.t}), in the official English text.${aiChanged('annex-' + r)} With a link to the original on EUR-Lex.`);
  const main = links(show('ai-annex-' + r));
  const mod = stamp(url, a.h + linksOf('ai-annex', r));
  const json = { '@graph': [
    { '@type': 'Legislation', '@id': url, url, name: `Annex ${r} AI Act: ${a.t}`, legislationIdentifier: `Regulation (EU) 2024/1689, Annex ${r}`,
      legislationJurisdiction: 'EU', legislationLegalForce: 'InForce', inLanguage: 'en', isPartOf: AI_LAW, sameAs: `${AI_CONS}#anx_${r}`, dateModified: mod },
    crumbs([['Fino', `${BASE}/`], ['AI Act', `${BASE}/ai-act/`], [`Annex ${r}`]]), ORG ] };
  write(`ai-act/annex-${r.toLowerCase()}.html`, page({ title, desc, url, on: 'gdpr', main, json }));
  sitemap.push([url, mod]);
}
{
  const url = `${BASE}/ai-act/`, main = links(show('ai-act'), { inPageRecitals: true });
  const nArts = aiact.order.length, nAnx = aiact.annex_order.length, nRec = Object.keys(aiact.preamble.recitals).length;
  const title = `The EU AI Act: full text, all ${nArts} articles, ${nAnx} annexes and ${nRec} recitals, as amended in 2026 · Fino`;
  const desc = `The Artificial Intelligence Act (Regulation (EU) 2024/1689) in the official English text, including the July 2026 changes: all ${nArts} articles, ${nAnx} annexes and ${nRec} recitals, each linked to EUR-Lex.`;
  const mod = stamp(url, main);
  write('ai-act/index.html', page({ title, desc, url, on: 'gdpr', main,
    json: { '@graph': [{ ...AI_LAW, url, inLanguage: 'en', legislationLegalForce: 'InForce', dateModified: mod }, crumbs([['Fino', `${BASE}/`], ['AI Act']]), ORG] } }));
  sitemap.unshift([url, mod]);
}

/* ---------- 6. Main pages: search tags in their head, figures on the homepage ---------- */
const S = cases.meta.stats;
const fmt = n => Number(n).toLocaleString('en-GB');
const lf = (cases.meta.last_fetch || cases.meta.generated_at).slice(0, 10);
const updated = new Date(lf + 'T12:00:00Z').toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
const total = `€${((S.total_fines_eur + (S.total_fines_eur_converted || 0)) / 1e9).toFixed(2)} billion`;
const dates = F.CASES.map(c => c.decision_date).filter(Boolean).sort();
const DATASET = { '@type': 'Dataset', '@id': `${BASE}/fines.html#dataset`, name: 'Fino: GDPR fines and decisions',
  description: `${fmt(S.cases)} GDPR fines and decisions by data protection authorities in ${S.countries} countries, from ${dates[0].slice(0, 4)} to today, with the regulator, the date, the fine in euro (other currencies converted at the ECB rate and marked as estimates), the GDPR articles involved, a short summary and links to the original decision. Built from the CMS Enforcement Tracker and GDPRhub (noyb), with duplicates merged.`,
  url: `${BASE}/fines.html`, license: 'https://creativecommons.org/licenses/by-nc-sa/4.0/', isAccessibleForFree: true,
  creator: { '@id': `${BASE}/#org` }, publisher: { '@id': `${BASE}/#org` },
  isBasedOn: ['https://www.enforcementtracker.com/', 'https://gdprhub.eu/'],
  keywords: ['GDPR fines', 'GDPR enforcement', 'data protection authority decisions', 'privacy fines Europe', 'DPA decisions'],
  temporalCoverage: `${dates[0]}/${dates[dates.length - 1]}`, spatialCoverage: 'Europe', dateModified: lf,
  variableMeasured: ['Fine amount in euro', 'Decision date', 'Country', 'Data protection authority', 'GDPR articles', 'Outcome', 'Sector'],
  distribution: [{ '@type': 'DataDownload', encodingFormat: 'application/json', contentUrl: `${BASE}/data/cases.json` }] };

const exList = ex?.explainers || [];
const tops = [
  ['index.html', `${BASE}/`, p => ({ '@graph': [
    { '@type': 'WebSite', '@id': `${BASE}/#website`, name: 'Fino', url: `${BASE}/`, inLanguage: 'en', description: p.desc, publisher: { '@id': `${BASE}/#org` } },
    { ...ORG, founder: { '@type': 'Person', name: 'Lorenzo A.' }, description: 'Free database of GDPR fines and decisions, the GDPR text linked to the fines, and plain-English privacy explainers.' }] })],
  ['fines.html', `${BASE}/fines.html`, p => ({ '@graph': [DATASET, { '@type': 'WebPage', '@id': `${BASE}/fines.html`, url: `${BASE}/fines.html`, name: p.title, description: p.desc, mainEntity: { '@id': DATASET['@id'] } }, ORG] })],
  ['explainers.html', `${BASE}/explainers.html`, p => ({ '@graph': [{ '@type': 'CollectionPage', url: `${BASE}/explainers.html`, name: p.title, description: p.desc,
    hasPart: exList.map(e => ({ '@type': 'Article', url: `${BASE}/${e.slug}.html`, headline: e.question })) }, ORG] })],
  ...exList.map(e => [`${e.slug}.html`, `${BASE}/${e.slug}.html`, p => ({ '@graph': [
    { '@type': 'Article', '@id': `${BASE}/${e.slug}.html`, headline: p.title, alternativeHeadline: e.question, description: p.desc, url: `${BASE}/${e.slug}.html`,
      image: `${BASE}/cards/${e.slug}.png`, inLanguage: 'en', dateModified: p.mod, author: { '@type': 'Person', name: 'Lorenzo A.' }, publisher: { '@id': `${BASE}/#org` },
      about: e.arts.map(n => ({ '@type': 'Legislation', name: `Article ${n} GDPR`, url: `${BASE}/gdpr/article-${n}.html` })),
      citation: e.decisions.map(id => `${BASE}/decisions/${F.slug(id)}.html`) },
    crumbs([['Fino', `${BASE}/`], ['Explainers', `${BASE}/explainers.html`], [p.title]]), ORG] }), 'article'])
];
for (const [file, url, json, type] of tops) {
  if (!fs.existsSync(path.join(SITE, file))) continue;
  let t = read(file).replace(/\n?<!-- seo -->[\s\S]*?<!-- \/seo -->/, '');
  if (file === 'index.html') {
    const fig = { cases: fmt(S.cases), countries: String(S.countries), total, updated };
    t = t.replace(/(<(b|span) data-n="(\w+)">)[^<]*(<\/\2>)/g, (m, open, tag, k, close) => fig[k] ? open + fig[k] + close : m);
  }
  if (file === 'fines.html') t = t.replace(/(<meta name="description" content="Search )[\d,]+( GDPR fines and decisions from )\d+( countries)/, `$1${fmt(S.cases)}$2${S.countries}$3`);
  const title = text(t.match(/<title>([\s\S]*?)<\/title>/)[1]);
  const desc = unesc(t.match(/<meta name="description" content="([^"]*)"/)[1]);
  const mod = stamp(url, t);
  const tags = headTags({ title, desc, url, type: type || 'website' }).split('\n')
    .filter(l => !l.startsWith('<title>') && !l.startsWith('<meta name="description"'))
    .filter(l => !(l.startsWith('<link rel="canonical"') && /<link rel="canonical"/.test(t)));
  const block = `<!-- seo -->\n${tags.join('\n')}\n${ld(json({ title: title.replace(/ · Fino$/, ''), desc, mod }))}\n<!-- /seo -->`;
  t = t.replace('</head>', block + '\n</head>');
  write(file, t);
  sitemap.unshift([url, mod]);
}

/* ---------- 6b. Hand-written pages (About, Privacy, Tools) from pipeline/pages, in the same shell ----------
   Each file starts with <!--page {"file": ..., "title": ..., "desc": ..., "schema": {...}} -->; <style> goes in the head,
   <script> at the end; {{cases}}, {{countries}}, {{total}} and {{updated}} become today's figures. */
const PAGES = path.join(ROOT, 'pipeline', 'pages');
const fig = { cases: fmt(S.cases), countries: String(S.countries), total, updated };
for (const f of fs.existsSync(PAGES) ? fs.readdirSync(PAGES).filter(f => f.endsWith('.html')) : []) {
  let src = fs.readFileSync(path.join(PAGES, f), 'utf8');
  const meta = JSON.parse(src.match(/^<!--page (\{[\s\S]*?\}) -->/)[1]);
  src = src.replace(/^<!--page[\s\S]*?-->\s*/, '').replace(/\{\{(\w+)\}\}/g, (m, k) => fig[k] ?? m);
  const css = [], js = [];
  src = src.replace(/<style>([\s\S]*?)<\/style>\s*/g, (_, c) => { css.push(c); return ''; })
           .replace(/<script>([\s\S]*?)<\/script>\s*/g, (_, c) => { js.push(c); return ''; });
  const url = `${BASE}/${meta.file}`, main = links(src);
  const mod = stamp(url, src);
  const json = { '@graph': [{ '@id': url, url, name: meta.title.replace(/ · Fino$/, ''), description: meta.desc, inLanguage: 'en',
    dateModified: mod, publisher: { '@id': `${BASE}/#org` }, ...meta.schema },
    crumbs([['Fino', `${BASE}/`], [meta.crumb || meta.title.replace(/ · Fino$/, '')]]), ORG] };
  write(meta.file, page({ title: meta.title, desc: meta.desc, url, on: meta.file, main, json, css: css.join('\n'), js: js.join('\n') }));
  sitemap.unshift([url, mod]);
}

/* ---------- 7. sitemap.xml, robots.txt, llms.txt ---------- */
for (const u of Object.keys(lastmod)) if (!seen.has(u)) delete lastmod[u];
fs.writeFileSync(LASTMOD_FILE, JSON.stringify(lastmod));
const order = u => u === `${BASE}/` ? 0 : /\/(fines|explainers)\.html$/.test(u) ? 1 : /\/(gdpr|ai-act)\/$/.test(u) ? 2 : /\/decisions\//.test(u) ? 5 : /\/(gdpr|ai-act)\//.test(u) ? 4 : 3;
sitemap.sort((a, b) => order(a[0]) - order(b[0]));
write('sitemap.xml', `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
${sitemap.map(([u, m]) => `<url><loc>${u}</loc><lastmod>${m}</lastmod></url>`).join('\n')}
</urlset>
`);
write('robots.txt', `# Fino: everything here may be read by search engines and by AI assistants.
User-agent: *
Allow: /

Sitemap: ${BASE}/sitemap.xml
`);
const topArts = artNums.map(n => [n, F.artStats(n).list.length]).sort((a, b) => b[1] - a[1]).slice(0, 15).map(x => x[0]).sort((a, b) => a - b);
write('llms.txt', `# Fino

> Fino is a free, independent website about EU privacy law. It has a searchable database of ${fmt(S.cases)} GDPR fines and decisions from data protection authorities in ${S.countries} countries (${total} in fines), the full GDPR text with every article linked to the decisions that cite it, the full EU AI Act as amended in 2026, and plain-English explainers that answer common compliance questions with the rules and real decisions. Made by Lorenzo A., CIPP/E. Not legal advice.

Facts about the data:

- Sources: the CMS Enforcement Tracker and GDPRhub (noyb), both CC BY-NC-SA 4.0, with duplicates merged. Every decision links to the regulator's original.
- Each decision has a permanent page at ${BASE}/decisions/<case number>.html, for example ${BASE}/decisions/${F.slug(F.CASES[0].pharos_id)}.html. The case number (e.g. ${F.CASES[0].pharos_id}) is Fino's own: year / country code / number.
- Fines in other currencies are converted to euro at the European Central Bank rate of the decision date and marked as estimates (≈).
- Private individuals are never named; they are described (for example "Doctor").
- The data was last read from the sources on ${updated}.

## Explainers

${exList.map(e => `- [${e.question}](${BASE}/${e.slug}.html): the rules (GDPR Articles ${e.arts.join(', ')}) and ${e.decisions.length} real decisions`).join('\n')}
- [All explainers](${BASE}/explainers.html)

## GDPR fines and decisions

- [Search all decisions](${BASE}/fines.html): filter by country, year, sector, article, outcome and fine size
- [Sitemap with every decision page](${BASE}/sitemap.xml)

## The GDPR

- [The full text, 99 articles and 173 recitals](${BASE}/gdpr/)
${topArts.map(n => `- [Article ${n}: ${gdpr.arts[n].t}](${BASE}/gdpr/article-${n}.html): ${fmt(F.artStats(n).list.length)} decisions cite it`).join('\n')}

## The AI Act

- [The full text as amended in July 2026: ${aiact.order.length} articles, ${aiact.annex_order.length} annexes and ${Object.keys(aiact.preamble.recitals).length} recitals](${BASE}/ai-act/)
- [Article 5: ${aiact.arts['5'].t}](${BASE}/ai-act/article-5.html)
- [Article 6: ${aiact.arts['6'].t}](${BASE}/ai-act/article-6.html)
- [Article 50: ${aiact.arts['50'].t}](${BASE}/ai-act/article-50.html)
- [Annex III: ${aiact.annexes.III.t}](${BASE}/ai-act/annex-iii.html)

## About

- [Who made Fino and how it works](${BASE}/About.html): sources, duplicates, who the decision is against, private people, currency conversion, case numbers, the review process and known limits
- [Privacy](${BASE}/Privacy.html)
- [AI Act triage](${BASE}/Tool.html): three questions, the likely AI Act category and what to do next

## Optional

- [All decisions as JSON](${BASE}/data/cases.json): one row per decision (field names in meta.fields); summaries are in ${BASE}/data/summaries/0.json and onwards, 250 per file, in the same order. CC BY-NC-SA 4.0.
`);

/* IndexNow: the key file proves the site is ours; the changed pages wait for --ping after the push */
let key = fs.readdirSync(SITE).find(f => /^[0-9a-f]{32}\.txt$/.test(f))?.slice(0, 32);
if (!key) { key = crypto.randomBytes(16).toString('hex'); write(`${key}.txt`, key); }
let pending = []; try { pending = JSON.parse(fs.readFileSync(PENDING_FILE, 'utf8')); } catch (e) {}
fs.writeFileSync(PENDING_FILE, JSON.stringify([...new Set([...pending, ...changed])].filter(u => seen.has(u))));

console.log(`SEO: ${F.CASES.length} decision pages, ${artNums.length + 1} GDPR pages, ${aiact.order.length + aiact.annex_order.length + 1} AI Act pages, ${sitemap.length} URLs in the sitemap, ${changed.length} new or changed.`);

async function ping() {
  const key = fs.readdirSync(SITE).find(f => /^[0-9a-f]{32}\.txt$/.test(f))?.slice(0, 32);
  let urls = []; try { urls = JSON.parse(fs.readFileSync(PENDING_FILE, 'utf8')); } catch (e) {}
  if (!key || !urls.length) { console.log('IndexNow: nothing to send.'); return; }
  const host = new URL(BASE).host;
  for (let i = 0; i < urls.length; i += 10000) {
    const r = await fetch('https://api.indexnow.org/indexnow', { method: 'POST', headers: { 'Content-Type': 'application/json; charset=utf-8' },
      body: JSON.stringify({ host, key, keyLocation: `${BASE}/${key}.txt`, urlList: urls.slice(i, i + 10000) }) });
    console.log(`IndexNow: sent ${Math.min(10000, urls.length - i)} pages, answer ${r.status} ${r.statusText}`);
    if (!r.ok && r.status !== 202) return;
  }
  fs.writeFileSync(PENDING_FILE, '[]');
}
