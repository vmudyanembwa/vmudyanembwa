import {
  ACTIVITY_LEVELS, GOAL_ADJUSTMENTS, MEALS,
  calorieTarget, macroTargets, tdee,
  dateKey, parseDateKey, shiftDateKey,
  entryNutrients, sumEntries, history, searchFoods,
} from './calc.js';
import { FOODS } from './foods.js';
import { load, save, normalize } from './storage.js';

const $ = (sel) => document.querySelector(sel);

/** Small element builder; children may be strings or nodes. Text is never parsed as HTML. */
function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  node.append(...children);
  return node;
}

const fmt = (n) => Math.round(n).toLocaleString();
const fmt1 = (n) => (Math.round(n * 10) / 10).toLocaleString();

let state = load();
let currentDay = dateKey(new Date());

function persist() {
  if (!save(state)) toast('Could not save — storage may be full or disabled');
}

function entriesFor(day) {
  return state.log[day] ?? [];
}

// ---------- Rendering ----------

function render() {
  renderDate();
  renderSummary();
  renderMeals();
  renderWeek();
}

function renderDate() {
  const today = dateKey(new Date());
  const labels = { [today]: 'Today', [shiftDateKey(today, -1)]: 'Yesterday', [shiftDateKey(today, 1)]: 'Tomorrow' };
  const long = parseDateKey(currentDay).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
  $('#date-title').textContent = labels[currentDay] ? `${labels[currentDay]} · ${long}` : long;
  $('#date-input').value = currentDay;
}

function renderSummary() {
  const totals = sumEntries(entriesFor(currentDay));
  const { goals } = state;
  const remaining = goals.calories - totals.calories;
  const over = remaining < 0;

  $('#cal-goal').textContent = fmt(goals.calories);
  $('#cal-eaten').textContent = fmt(totals.calories);
  $('#cal-remaining').textContent = fmt(Math.abs(remaining));
  $('#cal-remaining-label').textContent = over ? 'kcal over' : 'kcal left';

  const ring = $('#ring-fill');
  const circumference = 2 * Math.PI * 52;
  const pct = Math.min(1, goals.calories > 0 ? totals.calories / goals.calories : 0);
  ring.style.strokeDasharray = circumference;
  ring.style.strokeDashoffset = circumference * (1 - pct);
  ring.classList.toggle('over', over);

  const macros = $('#macros');
  macros.replaceChildren(
    ...[
      ['Protein', 'protein', '--protein'],
      ['Carbs', 'carbs', '--carbs'],
      ['Fat', 'fat', '--fat'],
    ].map(([label, key, color]) => {
      const goal = goals[key];
      const width = goal > 0 ? Math.min(100, (totals[key] / goal) * 100) : 0;
      const fill = el('i');
      fill.style.width = `${width}%`;
      fill.style.background = `var(${color})`;
      return el('div', { class: 'macro' },
        el('div', { class: 'label' }, el('span', {}, label), el('span', {}, `${fmt(totals[key])} / ${fmt(goal)} g`)),
        el('div', { class: 'bar' }, fill),
      );
    }),
  );
}

function renderMeals() {
  const entries = entriesFor(currentDay);
  $('#meals').replaceChildren(
    ...MEALS.map((meal) => {
      const items = entries.filter((e) => e.meal === meal);
      const kcal = sumEntries(items).calories;
      const list = items.length
        ? el('ul', { class: 'entries' }, ...items.map(entryRow))
        : el('p', { class: 'empty' }, 'Nothing logged yet.');
      return el('section', { class: 'card meal', 'aria-label': meal },
        el('div', { class: 'meal-head' },
          el('h2', {}, meal),
          el('span', { class: 'kcal' }, `${fmt(kcal)} kcal`),
          el('button', { class: 'btn small', type: 'button', onclick: () => openAdd(meal) }, '+ Add'),
        ),
        list,
      );
    }),
  );
}

function entryRow(entry) {
  const n = entryNutrients(entry);
  const servings = entry.servings === 1 ? entry.serving : `${fmt1(entry.servings)} × ${entry.serving}`;
  return el('li', {},
    el('div', {},
      el('span', { class: 'name' }, entry.name),
      el('span', { class: 'meta' }, `${servings} · P ${fmt1(n.protein)} · C ${fmt1(n.carbs)} · F ${fmt1(n.fat)}`),
    ),
    el('span', { class: 'cal' }, fmt(n.calories)),
    el('button', {
      class: 'btn danger-text', type: 'button', 'aria-label': `Remove ${entry.name}`,
      onclick: () => removeEntry(entry.id),
    }, '✕'),
  );
}

function renderWeek() {
  const days = history(state.log, currentDay, 7);
  const goal = state.goals.calories;
  const max = Math.max(goal * 1.2, ...days.map((d) => d.calories), 1);
  const chartHeight = 130; // px available to bars (container minus labels)

  const chart = $('#week-chart');
  const line = el('div', { class: 'goal-line', title: `Goal ${fmt(goal)} kcal` });
  line.style.bottom = `${(goal / max) * chartHeight + 22}px`;

  chart.replaceChildren(
    line,
    ...days.map((d) => {
      const bar = el('i', { title: `${fmt(d.calories)} kcal` });
      bar.style.height = `${(d.calories / max) * chartHeight}px`;
      if (d.calories > goal) bar.classList.add('over');
      const label = parseDateKey(d.date).toLocaleDateString(undefined, { weekday: 'narrow' });
      return el('div', { class: `col${d.date === currentDay ? ' current' : ''}` }, bar, el('small', {}, label));
    }),
  );

  const logged = days.filter((d) => d.calories > 0);
  $('#week-avg').textContent = logged.length
    ? `Average on ${logged.length} logged day${logged.length > 1 ? 's' : ''}: ${fmt(logged.reduce((s, d) => s + d.calories, 0) / logged.length)} kcal`
    : 'No entries in this week yet.';
}

// ---------- Mutations ----------

function addEntry(meal, food, servings) {
  const entry = {
    id: crypto.randomUUID?.() ?? `${Date.now()}-${Math.random()}`,
    meal,
    name: food.name,
    serving: food.serving,
    servings,
    calories: food.calories,
    protein: food.protein,
    carbs: food.carbs,
    fat: food.fat,
  };
  state.log[currentDay] = [...entriesFor(currentDay), entry];
  persist();
  render();
  toast(`Added ${food.name}`);
}

function removeEntry(id) {
  const remaining = entriesFor(currentDay).filter((e) => e.id !== id);
  if (remaining.length) state.log[currentDay] = remaining;
  else delete state.log[currentDay];
  persist();
  render();
}

// ---------- Add food dialog ----------

const addDialog = $('#add-dialog');
let addMeal = 'breakfast';
let addTab = 'search';
let selectedFood = null;

function allFoods() {
  return [...state.customFoods.map((f) => ({ ...f, custom: true })), ...FOODS];
}

/** Foods logged most recently, de-duplicated, shown before the user types. */
function recentFoods(limit = 6) {
  const seen = new Set();
  const out = [];
  const days = Object.keys(state.log).sort().reverse();
  for (const day of days) {
    for (const e of [...state.log[day]].reverse()) {
      const key = `${e.name}|${e.serving}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(e);
      if (out.length >= limit) return out;
    }
  }
  return out;
}

function openAdd(meal) {
  addMeal = meal;
  selectedFood = null;
  $('#add-meal-name').textContent = meal;
  $('#add-form').reset();
  $('#servings').value = 1;
  $('#add-error').textContent = '';
  setTab('search');
  renderResults();
  addDialog.showModal();
  $('#food-search').focus();
}

function setTab(tab) {
  addTab = tab;
  for (const t of document.querySelectorAll('.tab')) t.setAttribute('aria-selected', String(t.dataset.tab === tab));
  for (const p of document.querySelectorAll('[data-panel]')) p.hidden = p.dataset.panel !== tab;
  renderSelected();
}

function renderResults() {
  const q = $('#food-search').value;
  const foods = q.trim() ? searchFoods(allFoods(), q, 12) : recentFoods();
  const list = $('#search-results');
  if (!foods.length) {
    list.replaceChildren(el('li', { class: 'muted' }, q.trim() ? 'No match — try the Custom tab.' : 'Start typing to search.'));
    return;
  }
  list.replaceChildren(
    ...(!q.trim() ? [el('li', { class: 'muted' }, 'Recent')] : []),
    ...foods.map((f) =>
      el('li', {},
        el('button', { type: 'button', onclick: () => { selectedFood = f; renderSelected(); $('#servings').focus(); } },
          el('span', {}, f.name, ...(f.custom ? [el('span', { class: 'tag' }, 'mine')] : []), el('br'), el('small', {}, f.serving)),
          el('small', {}, `${fmt(f.calories)} kcal`),
        ),
      ),
    ),
  );
}

function renderSelected() {
  const box = $('#selected-food');
  if (addTab !== 'search' || !selectedFood) {
    box.hidden = true;
    return;
  }
  const s = Number($('#servings').value) || 0;
  const n = entryNutrients({ ...selectedFood, servings: s });
  box.hidden = false;
  box.replaceChildren(
    el('strong', {}, selectedFood.name), ` — ${selectedFood.serving}`, el('br'),
    `${fmt1(s)} serving${s === 1 ? '' : 's'}: ${fmt(n.calories)} kcal · P ${fmt1(n.protein)} g · C ${fmt1(n.carbs)} g · F ${fmt1(n.fat)} g`,
  );
}

function readCustomFood() {
  const name = $('#c-name').value.trim();
  const calories = Number($('#c-cal').value);
  if (!name) return { error: 'Give the food a name.' };
  if ($('#c-cal').value === '' || !Number.isFinite(calories) || calories < 0) return { error: 'Enter calories per serving.' };
  return {
    food: {
      name,
      serving: $('#c-serving').value.trim() || '1 serving',
      calories,
      protein: Number($('#c-protein').value) || 0,
      carbs: Number($('#c-carbs').value) || 0,
      fat: Number($('#c-fat').value) || 0,
    },
  };
}

addDialog.addEventListener('close', () => {
  if (addDialog.returnValue !== 'add') return;
  // Validation already ran in the submit handler; this just applies it.
  const pending = addDialog.pending;
  addDialog.pending = null;
  if (pending) addEntry(addMeal, pending.food, pending.servings);
});

$('#add-form').addEventListener('submit', (ev) => {
  if (ev.submitter?.value !== 'add') return;
  const servings = Number($('#servings').value);
  const fail = (msg) => { ev.preventDefault(); $('#add-error').textContent = msg; };

  if (!Number.isFinite(servings) || servings <= 0) return fail('Servings must be more than 0.');

  let food = selectedFood;
  if (addTab === 'custom') {
    const { food: custom, error } = readCustomFood();
    if (error) return fail(error);
    food = custom;
    if ($('#c-save').checked) {
      state.customFoods = [custom, ...state.customFoods.filter((f) => f.name.toLowerCase() !== custom.name.toLowerCase())];
    }
  } else if (!food) {
    return fail('Pick a food from the list, or use the Custom tab.');
  }
  const { custom: _ignored, ...clean } = food;
  addDialog.pending = { food: clean, servings };
});

$('#food-search').addEventListener('input', () => { selectedFood = null; renderSelected(); renderResults(); });
$('#food-search').addEventListener('keydown', (ev) => {
  // Enter in the search box picks the first result instead of submitting an empty selection.
  if (ev.key === 'Enter' && !selectedFood) {
    ev.preventDefault();
    $('#search-results button')?.click();
  }
});
$('#servings').addEventListener('input', renderSelected);
for (const t of document.querySelectorAll('.tab')) t.addEventListener('click', () => setTab(t.dataset.tab));

// ---------- Settings dialog ----------

const settingsDialog = $('#settings-dialog');

$('#p-activity').append(...Object.entries(ACTIVITY_LEVELS).map(([v, { label }]) => el('option', { value: v }, label)));
$('#p-goal').append(...Object.entries(GOAL_ADJUSTMENTS).map(([v, { label }]) => el('option', { value: v }, label)));

function readProfile() {
  const profile = {
    sex: $('#p-sex').value,
    age: Number($('#p-age').value),
    heightCm: Number($('#p-height').value),
    weightKg: Number($('#p-weight').value),
    activity: $('#p-activity').value,
    goal: $('#p-goal').value,
  };
  const valid = profile.age >= 13 && profile.heightCm >= 100 && profile.weightKg >= 30;
  return valid ? profile : null;
}

function openSettings() {
  const p = state.profile ?? { sex: 'female', age: '', heightCm: '', weightKg: '', activity: 'light', goal: 'maintain' };
  $('#p-sex').value = p.sex;
  $('#p-age').value = p.age;
  $('#p-height').value = p.heightCm;
  $('#p-weight').value = p.weightKg;
  $('#p-activity').value = p.activity;
  $('#p-goal').value = p.goal;
  $('#g-cal').value = state.goals.calories;
  $('#g-protein').value = state.goals.protein;
  $('#g-carbs').value = state.goals.carbs;
  $('#g-fat').value = state.goals.fat;
  $('#calc-result').textContent = '';
  settingsDialog.showModal();
}

$('#calc-btn').addEventListener('click', () => {
  const profile = readProfile();
  if (!profile) {
    $('#calc-result').textContent = 'Fill in age, height and weight first.';
    return;
  }
  const kcal = calorieTarget(profile);
  const m = macroTargets(kcal);
  $('#g-cal').value = kcal;
  $('#g-protein').value = m.protein;
  $('#g-carbs').value = m.carbs;
  $('#g-fat').value = m.fat;
  $('#calc-result').textContent = `Maintenance ≈ ${fmt(tdee(profile))} kcal/day. Target set to ${fmt(kcal)} kcal. Adjust below if you like, then Save.`;
});

settingsDialog.addEventListener('close', () => {
  if (settingsDialog.returnValue !== 'save') return;
  state.profile = readProfile() ?? state.profile;
  state.goals = {
    calories: Number($('#g-cal').value),
    protein: Number($('#g-protein').value),
    carbs: Number($('#g-carbs').value),
    fat: Number($('#g-fat').value),
  };
  persist();
  render();
  toast('Goals saved');
});

$('#settings-btn').addEventListener('click', openSettings);

// ---------- Date navigation ----------

$('#prev-day').addEventListener('click', () => { currentDay = shiftDateKey(currentDay, -1); render(); });
$('#next-day').addEventListener('click', () => { currentDay = shiftDateKey(currentDay, 1); render(); });
$('#date-input').addEventListener('change', (ev) => {
  if (ev.target.value) { currentDay = ev.target.value; render(); }
});

// ---------- Export / import ----------

$('#export-btn').addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(state, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  el('a', { href: url, download: `calorie-tracker-${dateKey(new Date())}.json` }).click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

$('#import-input').addEventListener('change', async (ev) => {
  const file = ev.target.files?.[0];
  ev.target.value = '';
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (!confirm('Replace all current data with this backup?')) return;
    state = normalize(data);
    persist();
    render();
    toast('Backup imported');
  } catch {
    toast('That file is not a valid backup');
  }
});

// ---------- Toast ----------

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
}

render();
if (!state.profile && !Object.keys(state.log).length) openSettings();
