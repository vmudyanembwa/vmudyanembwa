// Persistence in localStorage. Everything lives under one key so export/import is trivial.

const KEY = 'calorie-tracker:v1';

export function defaultState() {
  return {
    profile: null, // { sex, age, heightCm, weightKg, activity, goal }
    goals: { calories: 2000, protein: 150, carbs: 200, fat: 67 },
    customFoods: [],
    log: {}, // { 'YYYY-MM-DD': [{ id, meal, name, serving, servings, calories, protein, carbs, fat }] }
  };
}

export function load() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return defaultState();
    return normalize(JSON.parse(raw));
  } catch {
    return defaultState();
  }
}

export function save(state) {
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
    return true;
  } catch {
    return false;
  }
}

/** Fill in any missing fields so older or hand-edited data still loads. */
export function normalize(data) {
  const base = defaultState();
  if (!data || typeof data !== 'object') return base;
  return {
    profile: data.profile ?? null,
    goals: { ...base.goals, ...(data.goals ?? {}) },
    customFoods: Array.isArray(data.customFoods) ? data.customFoods : [],
    log: data.log && typeof data.log === 'object' ? data.log : {},
  };
}
