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

const plural = (n, word) => `${n.toLocaleString()} ${word}${n === 1 ? '' : 's'}`;

// Stable colour per source title for the little book covers.
function coverColour(title) {
  let h = 0;
  for (const ch of title) h = (h * 31 + ch.codePointAt(0)) % 360;
  return `hsl(${h} 35% 42%)`;
}

// ---------- Shared highlight pieces ----------

function sourceLine(h) {
  return el('div', { class: 'source-line' },
    el('a', { href: `#/source/${h.source_id}` }, h.title),
    h.author ? ` — ${h.author}` : '',
    h.location ? el('span', {}, ` · loc. ${h.location}`) : '',
  );
}

function tagList(tags) {
  if (!tags.length) return null;
  return el('div', { class: 'tags' }, tags.map((t) => el('a', { class: 'tag', href: `#/search?tag=${encodeURIComponent(t)}` }, `#${t}`)));
}

function editHighlight(h, onSaved) {
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
  $('#edit-note').focus();
}

async function toggleFavourite(h) {
  return api(`highlights/${h.id}`, { method: 'PATCH', body: { favorite: !h.favorite } });
}

/** Highlight card used in library, source and search views. Re-renders itself in place. */
function highlightCard(h, { showSource = true } = {}) {
  const card = el('article', { class: `card hl${h.discarded ? ' discarded' : ''}` });
  const render = () => {
    card.className = `card hl${h.discarded ? ' discarded' : ''}`;
    fill(card, 
      el('blockquote', { class: 'quote' }, h.text),
      showSource ? sourceLine(h) : (h.location ? el('div', { class: 'source-line' }, `Location ${h.location}`) : null),
      h.note ? el('div', { class: 'note' }, h.note) : null,
      tagList(h.tags),
      el('div', { class: 'hl-actions' },
        el('button', {
          class: 'btn link', type: 'button', 'aria-pressed': String(h.favorite),
          onclick: async () => { try { Object.assign(h, await toggleFavourite(h)); render(); } catch (e) { toast(e.message); } },
        }, h.favorite ? '★ Favourite' : '☆ Favourite'),
        el('button', { class: 'btn link', type: 'button', onclick: () => editHighlight(h, (u) => { Object.assign(h, u); render(); }) },
          h.note || h.tags.length ? 'Edit note & tags' : 'Add note & tags'),
        h.discarded ? el('button', {
          class: 'btn link', type: 'button',
          onclick: async () => { Object.assign(h, await api(`highlights/${h.id}`, { method: 'PATCH', body: { discarded: false } })); render(); toast('Back in your reviews'); },
        }, 'Restore to review') : null,
        el('button', {
          class: 'btn link danger', type: 'button',
          onclick: async () => {
            if (!confirm('Delete this highlight permanently?')) return;
            try { await api(`highlights/${h.id}`, { method: 'DELETE' }); card.remove(); toast('Deleted'); } catch (e) { toast(e.message); }
          },
        }, 'Delete'),
      ),
    );
  };
  render();
  return card;
}

function emptyState(text, action) {
  return el('div', { class: 'empty card' }, el('p', {}, text), action);
}

// ---------- Review ----------

const ACTIONS = [
  { key: 'soon', label: 'Soon', hint: 'days', shortcut: '1' },
  { key: 'later', label: 'Later', hint: 'weeks', shortcut: '2', primary: true },
  { key: 'someday', label: 'Someday', hint: 'months', shortcut: '3' },
];

let reviewKeys = null;

async function renderReview() {
  const data = await api('review');
  const { items, stats } = data;

  if (!stats.highlights) {
    fill(view, 
      el('div', { class: 'page-head' }, el('h1', {}, 'Daily review')),
      emptyState('Your library is empty. Import highlights from Kindle, a CSV, or paste some in, and they will start resurfacing here every day.',
        el('a', { class: 'btn primary', href: '#/import' }, 'Add highlights')),
    );
    return;
  }

  let index = items.findIndex((h) => !h.review_action);
  const show = () => {
    if (index === -1 || index >= items.length) return showDone();
    const h = items[index];
    const doneCount = items.filter((x) => x.review_action).length;
    const progressFill = el('i');
    progressFill.style.width = `${(doneCount / items.length) * 100}%`;

    const answer = async (action) => {
      try {
        Object.assign(h, await api(`review/${h.id}`, { method: 'POST', body: { action } }), { review_action: action });
        if (action === 'discard') toast('Discarded — it won’t come up in reviews again');
        index = items.findIndex((x) => !x.review_action);
        show();
      } catch (e) { toast(e.message); }
    };
    const fav = async () => {
      try { Object.assign(h, await toggleFavourite(h)); show(); } catch (e) { toast(e.message); }
    };
    const note = () => editHighlight(h, (u) => { Object.assign(h, u); show(); });

    reviewKeys = (ev) => {
      if (ev.target.closest('input, textarea, dialog') || ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const a = ACTIONS.find((x) => x.shortcut === ev.key);
      if (a) answer(a.key);
      else if (ev.key === 'f') fav();
      else if (ev.key === 'n') { ev.preventDefault(); note(); }
      else if (ev.key === 'd') answer('discard');
    };

    fill(view, 
      el('div', { class: 'page-head' },
        el('h1', {}, 'Daily review'),
        el('span', { class: 'muted small' }, stats.streak ? `🔥 ${plural(stats.streak, 'day')} streak` : ''),
      ),
      el('div', { class: 'review-progress' },
        el('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': items.length, 'aria-valuenow': doneCount }, progressFill),
        el('span', { class: 'small muted' }, `${index + 1} of ${items.length}`),
      ),
      el('article', { class: 'card review-card' },
        el('blockquote', { class: 'quote' }, h.text),
        sourceLine(h),
        h.note ? el('div', { class: 'note' }, h.note) : null,
        tagList(h.tags),
      ),
      el('p', { class: 'small muted', style: 'margin:16px 0 0' }, 'When should this come back?'),
      el('div', { class: 'review-buttons' },
        ACTIONS.map((a) => el('button', { class: `btn${a.primary ? ' primary' : ''}`, type: 'button', onclick: () => answer(a.key) },
          a.label, el('small', {}, `in ${a.hint} `, el('kbd', {}, a.shortcut))))),
      el('div', { class: 'review-extra' },
        el('div', { class: 'row' },
          el('button', { class: 'btn link', type: 'button', 'aria-pressed': String(h.favorite), onclick: fav },
            h.favorite ? '★ Favourite' : '☆ Favourite', ' ', el('kbd', {}, 'f')),
          el('button', { class: 'btn link', type: 'button', onclick: note }, h.note ? 'Edit note' : 'Add note', ' ', el('kbd', {}, 'n')),
        ),
        el('button', { class: 'btn link danger', type: 'button', onclick: () => answer('discard') }, 'Discard ', el('kbd', {}, 'd')),
      ),
    );
  };

  const showDone = async () => {
    reviewKeys = null;
    const s = await api('stats');
    fill(view, 
      el('div', { class: 'card done' },
        el('div', { style: 'font-size:2.5rem' }, '✓'),
        el('p', { class: 'big' }, 'You’re done for today'),
        el('p', { class: 'muted' }, `You reviewed ${plural(items.length, 'highlight')}. New ones will be waiting tomorrow.`),
        el('div', { class: 'stats' },
          stat(s.streak, 'day streak'), stat(s.highlights, 'highlights'), stat(s.sources, 'sources'), stat(s.favorites, 'favourites')),
        el('div', { class: 'row', style: 'justify-content:center' },
          el('a', { class: 'btn', href: '#/library' }, 'Browse library'),
          el('a', { class: 'btn', href: '#/search?favorite=1' }, 'See favourites')),
      ),
      reviewSettings(s),
    );
  };

  show();
}

function stat(value, label) {
  return el('div', { class: 'stat' }, el('strong', {}, Number(value).toLocaleString()), el('span', {}, label));
}

function reviewSettings(stats) {
  const input = el('input', { type: 'number', min: 1, max: 100, value: stats.daily_count, style: 'width:90px' });
  return el('form', {
    class: 'card row', style: 'margin-top:16px;justify-content:space-between',
    onsubmit: async (ev) => {
      ev.preventDefault();
      try { await api('settings', { method: 'PUT', body: { daily_count: Number(input.value) } }); toast('Saved — applies from tomorrow’s review'); } catch (e) { toast(e.message); }
    },
  },
  el('label', { class: 'row' }, 'Highlights per daily review', input),
  el('button', { class: 'btn' }, 'Save'));
}

document.addEventListener('keydown', (ev) => reviewKeys?.(ev));

// ---------- Library ----------

async function renderLibrary() {
  const sources = await api('sources');
  fill(view, 
    el('div', { class: 'page-head' },
      el('h1', {}, 'Library'),
      el('span', { class: 'muted small' }, plural(sources.length, 'source'))),
    sources.length
      ? el('div', { class: 'sources' }, sources.map((s) => {
        const cover = el('div', { class: 'cover', 'aria-hidden': 'true' }, (s.title.match(/\p{L}|\p{N}/u) || ['?'])[0].toUpperCase());
        cover.style.background = coverColour(s.title);
        return el('a', { class: 'card source-card', href: `#/source/${s.id}` },
          cover,
          el('div', {}, el('h3', {}, s.title), s.author ? el('p', {}, s.author) : null, el('p', {}, plural(s.highlight_count, 'highlight'))));
      }))
      : emptyState('No sources yet.', el('a', { class: 'btn primary', href: '#/import' }, 'Add highlights')),
  );
}

async function renderSource(id) {
  const s = await api(`sources/${id}`);
  fill(view, 
    el('p', { style: 'margin:0 0 8px' }, el('a', { href: '#/library', class: 'small muted' }, '← Library')),
    el('div', { class: 'page-head' },
      el('div', {},
        el('h1', { style: 'font-family:var(--serif)' }, s.title),
        el('p', { class: 'muted', style: 'margin:4px 0 0' },
          [s.author, plural(s.highlights.length, 'highlight')].filter(Boolean).join(' · '),
          s.url ? el('span', {}, ' · ', el('a', { href: s.url, target: '_blank', rel: 'noopener noreferrer' }, 'Open original')) : null),
      ),
    ),
    el('div', { class: 'stack' }, s.highlights.map((h) => highlightCard(h, { showSource: false }))),
  );
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

  const input = el('input', { type: 'search', value: q, placeholder: 'Search text, notes, titles, authors…', 'aria-label': 'Search' });
  let timer;
  input.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => update({ q: input.value.trim() }), 300); });

  fill(view, 
    el('div', { class: 'page-head' }, el('h1', {}, 'Search'), el('span', { class: 'muted small' }, plural(results.length, 'result'))),
    el('div', { class: 'filters' }, input,
      el('button', { class: 'chip', type: 'button', 'aria-pressed': String(favourite), onclick: () => update({ favorite: favourite ? '' : '1' }) }, '★ Favourites')),
    tags.length ? el('div', { class: 'chip-row', 'aria-label': 'Filter by tag' },
      tags.map((t) => el('button', {
        class: 'chip', type: 'button', 'aria-pressed': String(t.name === tag),
        onclick: () => update({ tag: t.name === tag ? '' : t.name }),
      }, `#${t.name} `, el('span', { class: 'muted' }, t.count)))) : null,
    results.length
      ? el('div', { class: 'stack' }, results.map((h) => highlightCard(h)))
      : el('p', { class: 'empty' }, q || tag || favourite ? 'Nothing matches.' : 'Your library is empty.'),
  );
  if (document.activeElement === document.body || params.has('q')) {
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  }
}

// ---------- Import / add ----------

function renderImport() {
  const status = el('p', { class: 'small', role: 'status' });

  const importContent = async (content, filename, extra = {}) => {
    status.textContent = 'Importing…';
    try {
      const r = await api('import', { method: 'POST', body: { content, filename, ...extra } });
      status.textContent = `Found ${plural(r.found, 'highlight')}: ${r.added} added, ${r.skipped} already in your library.`;
      toast(`${plural(r.added, 'new highlight')} imported`);
    } catch (e) { status.textContent = e.message; }
  };

  const fileInput = el('input', { type: 'file', accept: '.txt,.csv,.md,text/plain,text/csv,text/markdown', multiple: true });
  const handleFiles = async (files) => {
    for (const f of files) await importContent(await f.text(), f.name);
  };
  fileInput.addEventListener('change', () => { handleFiles([...fileInput.files]); fileInput.value = ''; });

  const drop = el('label', { class: 'dropzone' }, fileInput,
    el('strong', {}, 'Drop a file here or click to choose'),
    el('p', { class: 'small muted', style: 'margin:4px 0 0' }, 'Kindle My Clippings.txt · Readwise-style CSV · Markdown / text'));
  for (const evName of ['dragenter', 'dragover']) drop.addEventListener(evName, (ev) => { ev.preventDefault(); drop.classList.add('over'); });
  for (const evName of ['dragleave', 'drop']) drop.addEventListener(evName, () => drop.classList.remove('over'));
  drop.addEventListener('drop', (ev) => { ev.preventDefault(); handleFiles([...ev.dataTransfer.files]); });

  // Manual single highlight
  const manual = el('form', { class: 'card stack' },
    el('h2', {}, 'Add one highlight'),
    el('label', { class: 'field' }, el('span', {}, 'Highlight'), el('textarea', { name: 'text', rows: 4, required: true })),
    el('div', { class: 'grid2' },
      el('label', { class: 'field' }, el('span', {}, 'Title (book, article…)'), el('input', { name: 'title', list: 'source-titles', autocomplete: 'off' })),
      el('label', { class: 'field' }, el('span', {}, 'Author'), el('input', { name: 'author' }))),
    el('div', { class: 'grid2' },
      el('label', { class: 'field' }, el('span', {}, 'URL (optional)'), el('input', { name: 'url', type: 'url' })),
      el('label', { class: 'field' }, el('span', {}, 'Tags (comma separated)'), el('input', { name: 'tags' }))),
    el('label', { class: 'field' }, el('span', {}, 'Note (optional)'), el('textarea', { name: 'note', rows: 2 })),
    el('datalist', { id: 'source-titles' }),
    el('div', { class: 'row end' }, el('button', { class: 'btn primary' }, 'Save highlight')),
  );
  manual.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(manual));
    try {
      await api('highlights', { method: 'POST', body });
      toast('Highlight saved');
      manual.elements.text.value = '';
      manual.elements.note.value = '';
      manual.elements.text.focus();
    } catch (e) { toast(e.message); }
  });
  api('sources').then((sources) => {
    $('#source-titles', manual)?.replaceChildren(...sources.map((s) => el('option', { value: s.title })));
  }).catch(() => {});

  // Paste many
  const paste = el('form', { class: 'card stack' },
    el('h2', {}, 'Paste several'),
    el('p', { class: 'small muted', style: 'margin:0' }, 'One highlight per paragraph. A line starting with “Note:” attaches to the highlight above it; #words in a note become tags.'),
    el('div', { class: 'grid2' },
      el('label', { class: 'field' }, el('span', {}, 'Title'), el('input', { name: 'title', required: true })),
      el('label', { class: 'field' }, el('span', {}, 'Author'), el('input', { name: 'author' }))),
    el('label', { class: 'field' }, el('span', {}, 'Highlights'), el('textarea', { name: 'content', rows: 6, required: true })),
    el('div', { class: 'row end' }, el('button', { class: 'btn primary' }, 'Import')),
  );
  paste.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const f = paste.elements;
    await importContent(f.content.value, '', { format: 'markdown', title: f.title.value, author: f.author.value });
    f.content.value = '';
  });

  fill(view, 
    el('div', { class: 'page-head' }, el('h1', {}, 'Add highlights')),
    el('div', { class: 'stack' },
      el('section', { class: 'card stack' },
        el('h2', {}, 'Import a file'),
        drop, status,
        el('details', {},
          el('summary', { class: 'small' }, 'Where do I find these files?'),
          el('ul', { class: 'how' },
            el('li', {}, 'Kindle: plug it in by USB and copy ', el('code', {}, 'documents/My Clippings.txt'), '. Re-importing later only adds new highlights.'),
            el('li', {}, 'Readwise: Export → CSV. Any CSV with a ', el('code', {}, 'Highlight'), ' column works; ', el('code', {}, 'Book Title'), ', ', el('code', {}, 'Book Author'), ', ', el('code', {}, 'Note'), ' and ', el('code', {}, 'Tags'), ' are used if present.'),
            el('li', {}, 'Markdown/text: one highlight per paragraph. Optional first lines ', el('code', {}, '# Title'), ' and ', el('code', {}, 'Author: Name'), '.'),
          )),
      ),
      manual,
      paste,
      el('section', { class: 'card stack' },
        el('h2', {}, 'Export'),
        el('p', { class: 'small muted', style: 'margin:0' }, 'Take your highlights anywhere: one Markdown file per source (works with Obsidian), or a CSV that this app and Readwise can import.'),
        el('div', { class: 'row' },
          el('a', { class: 'btn', href: '/api/export/markdown' }, 'Markdown (.zip)'),
          el('a', { class: 'btn', href: '/api/export/csv' }, 'CSV'))),
    ),
  );
}

// ---------- Router ----------

async function route() {
  reviewKeys = null;
  const [path, query = ''] = location.hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean);
  const page = parts[0] || 'review';
  for (const a of document.querySelectorAll('[data-nav]')) {
    if (a.dataset.nav === (page === 'source' ? 'library' : page)) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  }
  try {
    if (page === 'library') await renderLibrary();
    else if (page === 'source' && parts[1]) await renderSource(parts[1]);
    else if (page === 'search') await renderSearch(new URLSearchParams(query));
    else if (page === 'import') renderImport();
    else await renderReview();
  } catch (e) {
    fill(view, emptyState(`Something went wrong: ${e.message}`, el('a', { class: 'btn', href: '#/review' }, 'Back to review')));
  }
}

window.addEventListener('hashchange', () => {
  const searching = location.hash.startsWith('#/search') && document.activeElement?.type === 'search';
  route().then(() => { if (!searching) window.scrollTo(0, 0); });
});
route();
