// Margin frontend: hash-routed single page talking to the JSON API.

const $ = (sel, root = document) => root.querySelector(sel);
const view = $('#view');

/** Element builder. Strings become text nodes, so user content is never parsed as HTML. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  node.append(...children.flat().filter((c) => c != null && c !== false));
  return node;
}

/** replaceChildren that skips null/false, matching el(), so optional parts never render as "null". */
function fill(node, ...children) {
  node.replaceChildren(...children.flat().filter((c) => c != null && c !== false));
}

// ---------- Icons (24×24 stroke paths, drawn with DOM APIs) ----------

const ICONS = {
  review: ['M17 2l4 4-4 4', 'M3 11v-1a4 4 0 0 1 4-4h14', 'M7 22l-4-4 4-4', 'M21 13v1a4 4 0 0 1-4 4H3'],
  library: ['M4 19.5A2.5 2.5 0 0 1 6.5 17H20', 'M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z'],
  search: ['M21 21l-4.35-4.35', 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16z'],
  plus: ['M12 5v14', 'M5 12h14'],
  star: ['M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z'],
  note: ['M12 20h9', 'M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z'],
  tag: ['M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z', 'M7 7h.01'],
  trash: ['M3 6h18', 'M8 6V4h8v2', 'M19 6l-1 14H6L5 6'],
  discard: ['M18 6L6 18', 'M6 6l12 12'],
  restore: ['M3 12a9 9 0 1 0 3-6.7L3 8', 'M3 3v5h5'],
  trophy: ['M8 21h8', 'M12 17v4', 'M7 4h10v5a5 5 0 0 1-10 0V4z', 'M17 5h3v2a3 3 0 0 1-3 3', 'M7 5H4v2a3 3 0 0 0 3 3'],
  upload: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M17 8l-5-5-5 5', 'M12 3v12'],
  download: ['M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4', 'M7 10l5 5 5-5', 'M12 15V3'],
  kindle: ['M6 2h12a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z', 'M10 18h4'],
  books: ['M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z', 'M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z'],
  table: ['M3 3h18v18H3z', 'M3 9h18', 'M3 15h18', 'M9 3v18'],
  paste: ['M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2', 'M9 2h6v4H9z'],
  external: ['M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6', 'M15 3h6v6', 'M10 14L21 3'],
  back: ['M15 18l-6-6 6-6'],
  flame: ['M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.07-2.14-.22-4.05 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.15.43-2.29 1-3a2.5 2.5 0 0 0 2.5 2.5z'],
};

function icon(name, { filled = false, label } = {}) {
  const NS = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', `icon${filled ? ' filled' : ''}`);
  if (label) svg.setAttribute('aria-label', label);
  else svg.setAttribute('aria-hidden', 'true');
  for (const d of ICONS[name]) {
    const path = document.createElementNS(NS, 'path');
    path.setAttribute('d', d);
    svg.append(path);
  }
  return svg;
}

// ---------- Helpers ----------

async function api(path, { method = 'GET', body } = {}) {
  const res = await fetch(`/api/${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2600);
}

const plural = (n, word) => `${Number(n).toLocaleString()} ${word}${n === 1 ? '' : 's'}`;
const sourceKind = (s) => (s.url ? 'article' : 'book');

function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso.length === 10 ? `${iso}T00:00` : iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-ZA', { day: 'numeric', month: 'short', year: 'numeric' });
}

// Stable colour per source title for the generated covers.
function coverColour(title) {
  let h = 0;
  for (const ch of title) h = (h * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${h} 42% 40%)`;
}

function cover(source, size = 'sm') {
  const kind = sourceKind(source);
  // Small covers show an initial (or a link icon for articles); the large one shows the title.
  const initial = (source.title.match(/\p{L}|\p{N}/u) || ['?'])[0].toUpperCase();
  const face = size === 'lg' ? el('span', {}, source.title) : (kind === 'article' ? icon('external') : el('span', {}, initial));
  const c = el('div', { class: `cover ${size} ${kind}`, 'aria-hidden': 'true' }, face);
  c.style.background = coverColour(source.title);
  return c;
}

function sourceChip(h) {
  return el('a', { class: 'source-chip', href: `#/source/${h.source_id}` },
    cover(h, 'xs'),
    el('div', { class: 'who' }, el('strong', {}, h.title), h.author ? el('span', {}, h.author) : null));
}

function tagList(tags) {
  if (!tags.length) return null;
  return el('div', { class: 'tags' }, tags.map((t) => el('a', { class: 'tag', href: `#/search?tag=${encodeURIComponent(t)}` }, t)));
}

function noteBlock(note) {
  return note ? el('div', { class: 'note' }, el('b', {}, 'Note: '), note) : null;
}

function editHighlight(h, onSaved, focus = 'note') {
  const dialog = $('#edit-dialog');
  $('#edit-quote').textContent = h.text;
  $('#edit-note').value = h.note;
  $('#edit-tags').value = h.tags.join(', ');
  dialog.returnValue = '';
  dialog.onclose = async () => {
    if (dialog.returnValue !== 'save') return;
    try {
      const updated = await api(`highlights/${h.id}`, {
        method: 'PATCH',
        body: { note: $('#edit-note').value, tags: $('#edit-tags').value.split(',') },
      });
      onSaved(updated);
      toast('Saved');
    } catch (e) { toast(e.message); }
  };
  dialog.showModal();
  $(focus === 'tags' ? '#edit-tags' : '#edit-note').focus();
}

const toggleFavourite = (h) => api(`highlights/${h.id}`, { method: 'PATCH', body: { favorite: !h.favorite } });

function actionButton(iconName, label, onclick, extra = {}) {
  return el('button', { class: `btn ghost ${extra.class || ''}`, type: 'button', onclick, title: extra.title || label, 'aria-pressed': extra.pressed },
    icon(iconName, { filled: extra.filled }), el('span', {}, label), extra.kbd ? el('kbd', {}, extra.kbd) : null);
}

/** One highlight in a list (library, book page, search). Re-renders itself in place. */
function highlightItem(h, { showSource = true } = {}) {
  const item = el('li');
  const render = () => {
    item.className = `hl${h.discarded ? ' discarded' : ''}`;
    const update = async (body, message) => {
      try { Object.assign(h, await api(`highlights/${h.id}`, { method: 'PATCH', body })); render(); if (message) toast(message); } catch (e) { toast(e.message); }
    };
    fill(item,
      showSource ? sourceChip(h) : null,
      h.favorite ? el('span', { class: 'fav-flag', title: 'Favourite' }, icon('star', { filled: true, label: 'Favourite' })) : null,
      el('blockquote', { class: 'quote' }, h.text),
      noteBlock(h.note),
      tagList(h.tags),
      el('div', { class: 'meta' },
        h.location ? el('span', {}, `Location ${h.location}`) : null,
        h.highlighted_at ? el('span', {}, h.highlighted_at) : null,
        h.discarded ? el('span', {}, 'Discarded from review') : null),
      el('div', { class: 'hl-actions' },
        actionButton('star', h.favorite ? 'Favourited' : 'Favourite', () => update({ favorite: !h.favorite }), { pressed: String(h.favorite), filled: h.favorite }),
        actionButton('note', h.note ? 'Edit note' : 'Note', () => editHighlight(h, (u) => { Object.assign(h, u); render(); })),
        actionButton('tag', 'Tag', () => editHighlight(h, (u) => { Object.assign(h, u); render(); }, 'tags')),
        h.discarded ? actionButton('restore', 'Restore', () => update({ discarded: false }, 'Back in your reviews')) : null,
        actionButton('trash', 'Delete', async () => {
          if (!confirm('Delete this highlight permanently?')) return;
          try { await api(`highlights/${h.id}`, { method: 'DELETE' }); item.remove(); toast('Deleted'); } catch (e) { toast(e.message); }
        }, { class: 'danger' }),
      ),
    );
  };
  render();
  return item;
}

function emptyState(iconName, title, text, action) {
  return el('div', { class: 'card empty' },
    el('div', { class: 'tile-icon' }, icon(iconName)), el('h2', {}, title), el('p', {}, text), action);
}

// ---------- Navigation ----------

const NAV = [
  { key: 'review', label: 'Daily Review', icon: 'review' },
  { key: 'library', label: 'Library', icon: 'library' },
  { key: 'search', label: 'Search', icon: 'search' },
];
let reviewRemaining = null;

function renderNav(page) {
  const current = page === 'source' ? 'library' : page;
  fill($('#nav'), NAV.map((n) => el('a', { href: `#/${n.key}`, 'aria-current': n.key === current ? 'page' : null },
    icon(n.icon), el('span', { class: 'label' }, n.label),
    n.key === 'review' && reviewRemaining ? el('span', { class: 'badge', title: `${reviewRemaining} left today` }, reviewRemaining) : null)));
  const add = $('#add-btn');
  fill(add, icon('plus'), el('span', { class: 'label' }, 'Add highlights'));
  if (page === 'import') add.setAttribute('aria-current', 'page'); else add.removeAttribute('aria-current');
}

function setRemaining(items) {
  reviewRemaining = items.filter((h) => !h.review_action).length;
  renderNav(currentPage);
}

// ---------- Daily review ----------

const FREQUENCIES = [
  { key: 'soon', label: 'Soon', hint: 'in a few days', shortcut: '1' },
  { key: 'later', label: 'Later', hint: 'in a few weeks', shortcut: '2', primary: true },
  { key: 'someday', label: 'Someday', hint: 'in a few months', shortcut: '3' },
];

let reviewKeys = null;
document.addEventListener('keydown', (ev) => reviewKeys?.(ev));

async function renderReview() {
  const { items, stats } = await api('review');
  setRemaining(items);

  if (!stats.highlights) {
    fill(view, el('div', { class: 'narrow' },
      emptyState('books', 'Welcome to Margin',
        'Bring in your highlights from Kindle, Apple Books or a file. Each day, Margin resurfaces a handful so you remember what you read.',
        el('a', { class: 'btn primary', href: '#/import' }, icon('plus'), 'Add your first highlights'))));
    return;
  }

  let index = items.findIndex((h) => !h.review_action);

  const show = () => {
    if (index === -1) return showDone();
    const h = items[index];
    const doneCount = items.filter((x) => x.review_action).length;
    const bar = el('i');
    bar.style.width = `${((doneCount + 1) / items.length) * 100}%`;

    const answer = async (action) => {
      try {
        Object.assign(h, await api(`review/${h.id}`, { method: 'POST', body: { action } }), { review_action: action });
        if (action === 'discard') toast('Discarded — it won’t come up in reviews again');
        setRemaining(items);
        index = items.findIndex((x) => !x.review_action);
        show();
      } catch (e) { toast(e.message); }
    };
    const fav = async () => { try { Object.assign(h, await toggleFavourite(h)); show(); toast(h.favorite ? 'Added to favourites' : 'Removed from favourites'); } catch (e) { toast(e.message); } };
    const edit = (focus) => editHighlight(h, (u) => { Object.assign(h, u); show(); }, focus);

    reviewKeys = (ev) => {
      if (ev.target.closest('input, textarea, dialog') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const f = FREQUENCIES.find((x) => x.shortcut === ev.key);
      if (f) answer(f.key);
      else if (ev.key === 'f') fav();
      else if (ev.key === 'n') { ev.preventDefault(); edit('note'); }
      else if (ev.key === 't') { ev.preventDefault(); edit('tags'); }
      else if (ev.key === 'd') answer('discard');
    };

    fill(view, el('div', { class: 'narrow' },
      el('div', { class: 'review-top' },
        el('h1', {}, 'Daily Review'),
        el('div', { class: 'progress', role: 'progressbar', 'aria-label': 'Review progress', 'aria-valuemin': 0, 'aria-valuemax': items.length, 'aria-valuenow': doneCount }, bar),
        el('span', { class: 'count' }, `${index + 1} of ${items.length}`)),
      el('article', { class: 'card review-card' },
        el('div', { class: 'card-body' },
          sourceChip(h),
          el('blockquote', { class: 'quote' }, h.text),
          noteBlock(h.note),
          tagList(h.tags)),
        el('div', { class: 'review-toolbar' },
          actionButton('star', h.favorite ? 'Favourited' : 'Favourite', fav, { pressed: String(h.favorite), filled: h.favorite }),
          actionButton('note', h.note ? 'Edit note' : 'Note', () => edit('note')),
          actionButton('tag', 'Tag', () => edit('tags')),
          el('span', { class: 'spacer' }),
          actionButton('discard', 'Discard', () => answer('discard'), { class: 'danger', title: 'Never show this in reviews again' })),
        el('div', { class: 'review-feedback' },
          el('p', {}, 'When would you like to see this again?'),
          el('div', { class: 'freq' }, FREQUENCIES.map((f) => el('button', {
            class: `btn${f.primary ? ' primary' : ''}`, type: 'button', onclick: () => answer(f.key),
          }, f.label, el('small', {}, f.hint)))))),
      el('p', { class: 'shortcuts' }, 'Shortcuts',
        el('kbd', {}, '1'), el('kbd', {}, '2'), el('kbd', {}, '3'), ' when',
        el('kbd', {}, 'F'), ' favourite', el('kbd', {}, 'N'), ' note', el('kbd', {}, 'T'), ' tag', el('kbd', {}, 'D'), ' discard'),
    ));
  };

  const showDone = async () => {
    reviewKeys = null;
    const s = await api('stats');
    const count = el('input', { type: 'number', min: 1, max: 100, value: s.daily_count, 'aria-label': 'Highlights per Daily Review' });
    fill(view, el('div', { class: 'narrow stack' },
      el('div', { class: 'card done' },
        el('div', { class: 'trophy' }, icon('trophy')),
        el('h1', {}, 'You’ve completed your Daily Review!'),
        el('p', {}, s.streak > 1 ? `That’s ${s.streak} days in a row. See you tomorrow.` : 'Come back tomorrow for a fresh set of highlights.'),
        el('div', { class: 'stats' },
          stat(s.streak, 'day streak'), stat(s.highlights, 'highlights'), stat(s.sources, 'books & articles'), stat(s.favorites, 'favourites')),
        el('div', { class: 'row', style: 'justify-content:center' },
          el('a', { class: 'btn', href: '#/library' }, icon('library'), 'Browse library'),
          el('a', { class: 'btn', href: '#/search?favorite=1' }, icon('star'), 'Favourites'))),
      el('form', {
        class: 'card settings-row',
        onsubmit: async (ev) => {
          ev.preventDefault();
          try { await api('settings', { method: 'PUT', body: { daily_count: Number(count.value) } }); toast('Saved — applies from tomorrow’s review'); } catch (e) { toast(e.message); }
        },
      },
      el('div', {}, el('h3', {}, 'Highlights per Daily Review'), el('span', { class: 'small muted' }, 'How many highlights to resurface each day')),
      el('div', { class: 'row' }, count, el('button', { class: 'btn' }, 'Save'))),
    ));
  };

  show();
}

function stat(value, label) {
  return el('div', { class: 'stat' }, el('strong', {}, Number(value).toLocaleString()), el('span', {}, label));
}

// ---------- Library ----------

async function renderLibrary(params) {
  const sources = await api('sources');
  const kind = params.get('kind') || 'all';
  if (!sources.length) {
    fill(view, el('div', { class: 'page-head' }, el('h1', {}, 'Library')),
      emptyState('library', 'Your library is empty', 'Import highlights and every book and article will appear here.',
        el('a', { class: 'btn primary', href: '#/import' }, icon('plus'), 'Add highlights')));
    return;
  }
  const counts = { all: sources.length, book: 0, article: 0 };
  for (const s of sources) counts[sourceKind(s)] += 1;
  const shown = kind === 'all' ? sources : sources.filter((s) => sourceKind(s) === kind);
  const total = sources.reduce((n, s) => n + s.highlight_count, 0);

  fill(view,
    el('div', { class: 'page-head' },
      el('div', {}, el('h1', {}, 'Library'), el('p', {}, `${plural(total, 'highlight')} from ${plural(sources.length, 'source')}`))),
    el('div', { class: 'tabs', role: 'group', 'aria-label': 'Filter by type' },
      [['all', 'All'], ['book', 'Books'], ['article', 'Articles']].map(([key, label]) => el('button', {
        class: 'tab', type: 'button', 'aria-pressed': String(kind === key),
        onclick: () => { location.hash = key === 'all' ? '#/library' : `#/library?kind=${key}`; },
      }, label, el('span', { class: 'n' }, counts[key])))),
    el('div', { class: 'card lib-table' },
      el('div', { class: 'lib-row head', 'aria-hidden': 'true' },
        el('span'), el('span', {}, 'Title'), el('span', { class: 'hide-sm' }, 'Author'), el('span', {}, 'Highlights'), el('span', { class: 'hide-sm' }, 'Last added')),
      shown.length ? shown.map((s) => el('a', { class: 'lib-row', href: `#/source/${s.id}` },
        cover(s, 'sm'),
        el('div', { style: 'min-width:0' }, el('div', { class: 'title' }, s.title), el('div', { class: 'sub' }, sourceKind(s) === 'article' ? 'Article' : 'Book')),
        el('span', { class: 'sub hide-sm' }, s.author || '—'),
        el('span', { class: 'num' }, s.highlight_count),
        el('span', { class: 'num hide-sm' }, formatDate(s.last_added)),
      )) : el('p', { class: 'empty muted' }, 'Nothing here yet.')),
  );
}

async function renderSource(id) {
  const s = await api(`sources/${id}`);
  const favs = s.highlights.filter((h) => h.favorite).length;
  fill(view, el('div', { class: 'narrow' },
    el('a', { href: '#/library', class: 'back' }, icon('back'), 'Library'),
    el('div', { class: 'book-head' },
      cover(s, 'lg'),
      el('div', {},
        el('h1', {}, s.title),
        s.author ? el('div', { class: 'by' }, s.author) : null,
        el('div', { class: 'facts' },
          el('span', {}, plural(s.highlights.length, 'highlight')),
          favs ? el('span', {}, `${favs} favourite${favs === 1 ? '' : 's'}`) : null,
          s.url ? el('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer', class: 'row', style: 'gap:4px' }, 'Open original', icon('external')) : null))),
    el('ul', { class: 'card hl-list' }, s.highlights.map((h) => highlightItem(h, { showSource: false }))),
  ));
}

// ---------- Search ----------

async function renderSearch(params) {
  const q = params.get('q') || '';
  const tag = params.get('tag') || '';
  const favourite = params.get('favorite') === '1';
  const [results, tags] = await Promise.all([
    api(`highlights?${new URLSearchParams({ q, tag, favorite: favourite ? '1' : '' })}`),
    api('tags'),
  ]);

  const update = (changes) => {
    const next = new URLSearchParams({ q, tag, favorite: favourite ? '1' : '', ...changes });
    for (const [k, v] of [...next]) if (!v) next.delete(k);
    location.hash = `#/search${next.toString() ? `?${next}` : ''}`;
  };

  const input = el('input', { type: 'search', value: q, placeholder: 'Search highlights, notes, titles and authors', 'aria-label': 'Search' });
  let timer;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => update({ q: input.value.trim() }), 300); });

  const filtered = q || tag || favourite;
  fill(view, el('div', { class: 'narrow' },
    el('div', { class: 'page-head' }, el('h1', {}, 'Search')),
    el('div', { class: 'search-box' }, icon('search'), input),
    el('div', { class: 'filter-row', role: 'group', 'aria-label': 'Filters' },
      el('button', { class: 'tab', type: 'button', 'aria-pressed': String(favourite), onclick: () => update({ favorite: favourite ? '' : '1' }) }, '★ Favourites'),
      tags.map((t) => el('button', {
        class: 'tab', type: 'button', 'aria-pressed': String(t.name === tag),
        onclick: () => update({ tag: t.name === tag ? '' : t.name }),
      }, `#${t.name}`, el('span', { class: 'n' }, t.count)))),
    el('p', { class: 'small muted', style: 'margin:0 0 12px' }, filtered ? plural(results.length, 'result') : `${plural(results.length, 'highlight')}, newest first`),
    results.length
      ? el('ul', { class: 'card hl-list' }, results.map((h) => highlightItem(h)))
      : el('div', { class: 'card empty' }, el('p', {}, filtered ? 'Nothing matches that search.' : 'Your library is empty.')),
  ));
  if (params.has('q') || document.activeElement === document.body) {
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

// ---------- Add highlights ----------

const SOURCES = [
  { key: 'kindle', label: 'Kindle', desc: 'My Clippings.txt from your Kindle', icon: 'kindle', colour: '#232f3e' },
  { key: 'apple-books', label: 'Apple Books', desc: 'Highlights and notes on this Mac', icon: 'books', colour: '#f28c28' },
  { key: 'csv', label: 'CSV / Readwise', desc: 'A Readwise export or any CSV', icon: 'table', colour: '#1f9d55' },
  { key: 'paste', label: 'Paste text', desc: 'Several highlights at once', icon: 'paste', colour: '#7c4dff' },
  { key: 'manual', label: 'Add manually', desc: 'Type in a single highlight', icon: 'note', colour: '#2f6fed' },
  { key: 'export', label: 'Export', desc: 'Markdown or CSV download', icon: 'download', colour: '#5f6b7a' },
];

function statusLine() {
  const p = el('p', { class: 'status', role: 'status' });
  p.set = (text, kind = '') => { p.textContent = text; p.className = `status ${kind}`; };
  return p;
}

async function importContent(status, content, filename, extra = {}) {
  status.set('Importing…');
  try {
    const r = await api('import', { method: 'POST', body: { content, filename, ...extra } });
    status.set(`Found ${plural(r.found, 'highlight')}: ${r.added} new, ${r.skipped} already in your library.`, 'ok');
    toast(`${plural(r.added, 'new highlight')} imported`);
  } catch (e) { status.set(e.message, 'err'); }
}

function filePanel(title, intro, accept, steps) {
  const status = statusLine();
  const input = el('input', { type: 'file', accept, multiple: true });
  const handle = async (files) => { for (const f of files) await importContent(status, await f.text(), f.name); };
  input.addEventListener('change', () => { handle([...input.files]); input.value = ''; });
  const drop = el('label', { class: 'dropzone' }, input, icon('upload'),
    el('div', {}, el('strong', {}, 'Drop a file here'), ' or click to choose'));
  for (const evName of ['dragenter', 'dragover']) drop.addEventListener(evName, (ev) => { ev.preventDefault(); drop.classList.add('over'); });
  for (const evName of ['dragleave', 'drop']) drop.addEventListener(evName, () => drop.classList.remove('over'));
  drop.addEventListener('drop', (ev) => { ev.preventDefault(); handle([...ev.dataTransfer.files]); });
  return [el('h2', {}, title), el('p', {}, intro), drop, status, el('ol', { class: 'steps' }, steps.map((s) => el('li', {}, s)))];
}

const PANELS = {
  kindle: () => filePanel('Import from Kindle', 'Re-import any time — only new highlights are added.', '.txt,text/plain', [
    'Connect your Kindle to your Mac with a USB cable.',
    ['Open the Kindle drive and find ', el('code', {}, 'documents/My Clippings.txt'), '.'],
    'Drag that file onto the box above.',
  ]),

  'apple-books': () => {
    const status = statusLine();
    const button = el('button', {
      class: 'btn primary', type: 'button',
      onclick: async () => {
        button.disabled = true;
        status.set('Reading Apple Books…');
        try {
          const r = await api('import/apple-books', { method: 'POST' });
          status.set(`Found ${plural(r.found, 'highlight')}: ${r.added} new, ${r.skipped} already in your library.`, 'ok');
          toast(`${plural(r.added, 'new highlight')} from Apple Books`);
        } catch (e) { status.set(e.message, 'err'); }
        button.disabled = false;
      },
    }, icon('books'), 'Import from Apple Books');
    return [el('h2', {}, 'Import from Apple Books'),
      el('p', {}, 'Pulls in highlights and notes from Books on this Mac. Run it again any time — only new highlights are added.'),
      button, status,
      el('ol', { class: 'steps' },
        el('li', {}, 'If macOS blocks access, open System Settings → Privacy & Security → Full Disk Access and switch on Terminal (or Python, if Margin runs in the background).'),
        el('li', {}, 'With the background schedule installed, this also runs automatically every morning.'))];
  },

  csv: () => filePanel('Import a CSV', 'Works with Readwise exports and any spreadsheet saved as CSV.', '.csv,text/csv', [
    ['Needs a ', el('code', {}, 'Highlight'), ' column. ', el('code', {}, 'Book Title'), ', ', el('code', {}, 'Book Author'), ', ', el('code', {}, 'Note'), ', ', el('code', {}, 'Tags'), ' and ', el('code', {}, 'URL'), ' are used when present.'],
    'In Readwise: Export → CSV, then drop the file above.',
  ]),

  paste: () => {
    const status = statusLine();
    const form = el('form', { class: 'stack' },
      el('div', { class: 'grid2' },
        el('label', { class: 'field' }, el('span', {}, 'Title'), el('input', { name: 'title', required: true, placeholder: 'Book or article title' })),
        el('label', { class: 'field' }, el('span', {}, 'Author'), el('input', { name: 'author' }))),
      el('label', { class: 'field' }, el('span', {}, 'Highlights ', el('small', {}, '— one per paragraph; a line starting “Note:” attaches to the one above, and #words in it become tags')),
        el('textarea', { name: 'content', rows: 8, required: true })),
      el('div', { class: 'row end' }, el('button', { class: 'btn primary' }, 'Import highlights')), status);
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const f = form.elements;
      await importContent(status, f.content.value, '', { format: 'markdown', title: f.title.value, author: f.author.value });
      f.content.value = '';
    });
    return [el('h2', {}, 'Paste highlights'), el('p', {}, 'Handy for PDFs, web pages and notes from anywhere.'), form];
  },

  manual: () => {
    const titles = el('datalist', { id: 'source-titles' });
    api('sources').then((s) => fill(titles, s.map((x) => el('option', { value: x.title })))).catch(() => {});
    const form = el('form', { class: 'stack' },
      el('label', { class: 'field' }, el('span', {}, 'Highlight'), el('textarea', { name: 'text', rows: 4, required: true })),
      el('div', { class: 'grid2' },
        el('label', { class: 'field' }, el('span', {}, 'Title'), el('input', { name: 'title', list: 'source-titles', autocomplete: 'off', placeholder: 'Book or article' })),
        el('label', { class: 'field' }, el('span', {}, 'Author'), el('input', { name: 'author' }))),
      el('div', { class: 'grid2' },
        el('label', { class: 'field' }, el('span', {}, 'URL ', el('small', {}, 'optional')), el('input', { name: 'url', type: 'url' })),
        el('label', { class: 'field' }, el('span', {}, 'Tags ', el('small', {}, 'comma separated')), el('input', { name: 'tags' }))),
      el('label', { class: 'field' }, el('span', {}, 'Note ', el('small', {}, 'optional')), el('textarea', { name: 'note', rows: 2 })),
      titles,
      el('div', { class: 'row end' }, el('button', { class: 'btn primary' }, 'Save highlight')));
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      try {
        await api('highlights', { method: 'POST', body: Object.fromEntries(new FormData(form)) });
        toast('Highlight saved');
        form.elements.text.value = '';
        form.elements.note.value = '';
        form.elements.text.focus();
      } catch (e) { toast(e.message); }
    });
    return [el('h2', {}, 'Add a highlight'), el('p', {}, 'For a quote from a paper book, a podcast or a conversation.'), form];
  },

  export: () => [
    el('h2', {}, 'Export your highlights'),
    el('p', {}, 'Your highlights are yours. Take them anywhere.'),
    el('div', { class: 'row' },
      el('a', { class: 'btn', href: '/api/export/markdown' }, icon('download'), 'Markdown (.zip)'),
      el('a', { class: 'btn', href: '/api/export/csv' }, icon('download'), 'CSV')),
    el('ol', { class: 'steps' },
      el('li', {}, 'Markdown: one file per book or article — drop the folder into Obsidian or any notes app.'),
      el('li', {}, 'CSV: opens in Excel or Numbers, and can be imported back into Margin or Readwise.')),
  ],
};

function renderImport(sub) {
  const active = PANELS[sub] ? sub : 'kindle';
  fill(view,
    el('div', { class: 'page-head' },
      el('div', {}, el('h1', {}, 'Add highlights'), el('p', {}, 'Bring your reading together in one place.'))),
    el('div', { class: 'tiles' }, SOURCES.map((s) => {
      const badge = el('div', { class: 'tile-icon' }, icon(s.icon));
      badge.style.background = s.colour;
      return el('button', {
        class: 'tile', type: 'button', 'aria-pressed': String(s.key === active),
        onclick: () => { location.hash = `#/import/${s.key}`; },
      }, badge, el('strong', {}, s.label), el('span', {}, s.desc));
    })),
    el('section', { class: 'card pad panel' }, PANELS[active]()),
  );
}

// ---------- Router ----------

let currentPage = 'review';

async function route() {
  reviewKeys = null;
  const [path, query = ''] = location.hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean);
  currentPage = ['library', 'source', 'search', 'import'].includes(parts[0]) ? parts[0] : 'review';
  renderNav(currentPage);
  const params = new URLSearchParams(query);
  try {
    if (currentPage === 'library') await renderLibrary(params);
    else if (currentPage === 'source' && parts[1]) await renderSource(parts[1]);
    else if (currentPage === 'search') await renderSearch(params);
    else if (currentPage === 'import') renderImport(parts[1]);
    else await renderReview();
  } catch (e) {
    fill(view, el('div', { class: 'narrow' }, emptyState('discard', 'Something went wrong', e.message,
      el('a', { class: 'btn', href: '#/review' }, 'Back to Daily Review'))));
  }
}

window.addEventListener('hashchange', () => {
  const searching = location.hash.startsWith('#/search') && document.activeElement?.type === 'search';
  route().then(() => { if (!searching) window.scrollTo(0, 0); });
});

// Show today's remaining count in the nav even when starting on another page.
if (location.hash && !location.hash.startsWith('#/review')) {
  api('review').then(({ items }) => setRemaining(items)).catch(() => {});
}
route();
