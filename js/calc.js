// Pure calculation helpers. No DOM or storage access so they can be unit tested in Node.

export const ACTIVITY_LEVELS = {
  sedentary: { label: 'Sedentary (little or no exercise)', factor: 1.2 },
  light: { label: 'Light (1–3 days/week)', factor: 1.375 },
  moderate: { label: 'Moderate (3–5 days/week)', factor: 1.55 },
  active: { label: 'Active (6–7 days/week)', factor: 1.725 },
  athlete: { label: 'Very active (hard training / physical job)', factor: 1.9 },
};

export const GOAL_ADJUSTMENTS = {
  lose: { label: 'Lose ~0.5 kg/week', delta: -500 },
  maintain: { label: 'Maintain weight', delta: 0 },
  gain: { label: 'Gain ~0.25 kg/week', delta: 250 },
};

export const MEALS = ['breakfast', 'lunch', 'dinner', 'snacks'];

/** Mifflin–St Jeor basal metabolic rate in kcal/day. */
export function bmr({ sex, weightKg, heightCm, age }) {
  const base = 10 * weightKg + 6.25 * heightCm - 5 * age;
  return sex === 'female' ? base - 161 : base + 5;
}

/** Total daily energy expenditure in kcal/day. */
export function tdee(profile) {
  const level = ACTIVITY_LEVELS[profile.activity] ?? ACTIVITY_LEVELS.sedentary;
  return bmr(profile) * level.factor;
}

/** Recommended daily calorie target, never below a safe floor. */
export function calorieTarget(profile) {
  const delta = GOAL_ADJUSTMENTS[profile.goal]?.delta ?? 0;
  const floor = profile.sex === 'female' ? 1200 : 1500;
  return Math.max(floor, Math.round(tdee(profile) + delta));
}

/** Default macro split (30% protein, 40% carbs, 30% fat) in grams. */
export function macroTargets(calories) {
  return {
    protein: Math.round((calories * 0.3) / 4),
    carbs: Math.round((calories * 0.4) / 4),
    fat: Math.round((calories * 0.3) / 9),
  };
}

/** Nutrients for one log entry: food values are per serving, scaled by servings. */
export function entryNutrients(entry) {
  const s = Number(entry.servings) || 0;
  return {
    calories: entry.calories * s,
    protein: entry.protein * s,
    carbs: entry.carbs * s,
    fat: entry.fat * s,
  };
}

export function sumEntries(entries) {
  return entries.reduce(
    (acc, e) => {
      const n = entryNutrients(e);
      acc.calories += n.calories;
      acc.protein += n.protein;
      acc.carbs += n.carbs;
      acc.fat += n.fat;
      return acc;
    },
    { calories: 0, protein: 0, carbs: 0, fat: 0 },
  );
}

/** Local-time YYYY-MM-DD key for a Date. */
export function dateKey(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

export function parseDateKey(key) {
  const [y, m, d] = key.split('-').map(Number);
  return new Date(y, m - 1, d);
}

export function shiftDateKey(key, days) {
  const d = parseDateKey(key);
  d.setDate(d.getDate() + days);
  return dateKey(d);
}

/** Calorie totals for the `days` days ending on `endKey` (oldest first). */
export function history(log, endKey, days = 7) {
  const out = [];
  for (let i = days - 1; i >= 0; i--) {
    const key = shiftDateKey(endKey, -i);
    out.push({ date: key, calories: sumEntries(log[key] ?? []).calories });
  }
  return out;
}

/** Case-insensitive search across food names; prefix matches rank first. */
export function searchFoods(foods, query, limit = 8) {
  const q = query.trim().toLowerCase();
  if (!q) return [];
  return foods
    .map((f) => ({ f, i: f.name.toLowerCase().indexOf(q) }))
    .filter(({ i }) => i !== -1)
    .sort((a, b) => a.i - b.i || a.f.name.localeCompare(b.f.name))
    .slice(0, limit)
    .map(({ f }) => f);
}
