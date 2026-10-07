/* Built from fines.html by pipeline/build_seo.mjs. Do not edit here. */
const byId = id => document.getElementById(id);
/* Laws menu: opens on click, closes on Escape, an outside click, or after choosing */
const lawsBtn = byId('nav-gdpr'), lawsMenu = byId('laws-menu');
function setLaws(open) { lawsBtn.setAttribute('aria-expanded', String(open)); lawsMenu.hidden = !open; }
lawsBtn.addEventListener('click', e => { e.stopPropagation(); setLaws(lawsMenu.hidden); if (!lawsMenu.hidden) lawsMenu.querySelector('a').focus(); });
lawsMenu.addEventListener('click', e => { if (e.target.closest('a')) setLaws(false); });
document.addEventListener('click', e => { if (!e.target.closest('#laws')) setLaws(false); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !lawsMenu.hidden) { setLaws(false); lawsBtn.focus(); } });

/* Sticky header: remember its height (for links to headings and the side contents) and show a soft edge once scrolled */
(() => {
  const head = document.querySelector('header.top');
  if (!head) return;
  const height = () => document.documentElement.style.setProperty('--head-h', head.offsetHeight + 'px');
  height();
  if (window.ResizeObserver) new ResizeObserver(height).observe(head);
  const edge = () => head.classList.toggle('scrolled', window.scrollY > 4);
  edge();
  addEventListener('scroll', edge, { passive: true });
})();

/* Light / dark button, as on the homepage */
const root = document.documentElement, modeBtn = byId('mode');
function setMode(v, save) {
  if (v === 'light') root.dataset.mode = 'light'; else delete root.dataset.mode;
  const label = v === 'light' ? 'Switch to dark mode' : 'Switch to light mode';
  modeBtn.setAttribute('aria-label', label); modeBtn.title = label;
  if (save) { try { localStorage.setItem('mode', v); } catch (e) {} }
}
modeBtn.addEventListener('click', () => {
    const next = root.dataset.mode === 'light' ? 'dark' : 'light';
    const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (!document.startViewTransition) { setMode(next, true); return; }
    if (!calm) root.dataset.switch = next;
    const t = document.startViewTransition(() => setMode(next, true));
    t.finished.finally(() => { delete root.dataset.switch; });
    if (calm) return;
    const b = modeBtn.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
    const far = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    const dot = `circle(0px at ${x}px ${y}px)`, all = `circle(${far}px at ${x}px ${y}px)`;
    t.ready.then(() => next === 'light'
      ? root.animate({ clipPath: [dot, all] }, { duration: 700, easing: 'cubic-bezier(.25,.7,.25,1)', pseudoElement: '::view-transition-new(root)' })
      : root.animate({ clipPath: [all, dot] }, { duration: 560, easing: 'cubic-bezier(.5,0,.75,.4)', pseudoElement: '::view-transition-old(root)' }));
  });
let saved = null;
try { saved = localStorage.getItem('mode'); } catch (e) {}
setMode(saved, false);

/* Copy citation (decision pages): the date of access is today's */
(() => {
  const b = byId('copy-cite'); if (!b || !b.dataset.cite) return;
  const M = ['January','February','March','April','May','June','July','August','September','October','November','December'];
  b.addEventListener('click', () => {
    const d = new Date(), t = `${b.dataset.cite}, accessed ${d.getDate()} ${M[d.getMonth()]} ${d.getFullYear()}.`;
    const done = m => { byId('copied').textContent = m; };
    try { navigator.clipboard.writeText(t).then(() => done('Copied.'), () => done(t)); } catch (e) { done(t); }
  });
})();

/* Recitals (GDPR and AI Act pages): "Go to recital", and links straight to #rec-12 or #ai-rec-12 open the preamble */
(() => {
  const pre = byId('preamble'); if (!pre) return;
  const p = (pre.querySelector('.rec') || { id: 'rec-1' }).id.replace(/\d+$/, ''), max = +(byId('goto-rec') || {}).max;
  const re = new RegExp('^#' + p + '(\\d+)$');
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
