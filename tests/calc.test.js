import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  bmr, tdee, calorieTarget, macroTargets, entryNutrients, sumEntries,
  dateKey, shiftDateKey, history, searchFoods,
} from '../js/calc.js';
import { normalize } from '../js/storage.js';
import { FOODS } from '../js/foods.js';

const male = { sex: 'male', age: 30, heightCm: 180, weightKg: 80, activity: 'moderate', goal: 'maintain' };

test('bmr uses Mifflin-St Jeor', () => {
  assert.equal(bmr(male), 1780);
  assert.equal(bmr({ ...male, sex: 'female' }), 1614);
});

test('tdee applies activity factor', () => {
  assert.equal(tdee(male), 1780 * 1.55);
});

test('calorieTarget adjusts for goal and respects floor', () => {
  assert.equal(calorieTarget(male), Math.round(1780 * 1.55));
  assert.equal(calorieTarget({ ...male, goal: 'lose' }), Math.round(1780 * 1.55) - 500);
  const small = { sex: 'female', age: 70, heightCm: 150, weightKg: 45, activity: 'sedentary', goal: 'lose' };
  assert.equal(calorieTarget(small), 1200);
});

test('macroTargets splits 30/40/30', () => {
  assert.deepEqual(macroTargets(2000), { protein: 150, carbs: 200, fat: 67 });
});

test('entries scale by servings and sum', () => {
  const a = { calories: 100, protein: 10, carbs: 5, fat: 2, servings: 2 };
  const b = { calories: 50, protein: 1, carbs: 10, fat: 0, servings: 0.5 };
  assert.deepEqual(entryNutrients(a), { calories: 200, protein: 20, carbs: 10, fat: 4 });
  assert.deepEqual(sumEntries([a, b]), { calories: 225, protein: 20.5, carbs: 15, fat: 4 });
  assert.deepEqual(sumEntries([]), { calories: 0, protein: 0, carbs: 0, fat: 0 });
});

test('date keys are local and shift across month boundaries', () => {
  assert.equal(dateKey(new Date(2026, 0, 5)), '2026-01-05');
  assert.equal(shiftDateKey('2026-03-01', -1), '2026-02-28');
  assert.equal(shiftDateKey('2026-12-31', 1), '2027-01-01');
});

test('history returns oldest-first totals including empty days', () => {
  const log = { '2026-10-16': [{ calories: 500, protein: 0, carbs: 0, fat: 0, servings: 1 }] };
  const h = history(log, '2026-10-16', 3);
  assert.deepEqual(h, [
    { date: '2026-10-14', calories: 0 },
    { date: '2026-10-15', calories: 0 },
    { date: '2026-10-16', calories: 500 },
  ]);
});

test('searchFoods is case-insensitive and ranks prefix matches first', () => {
  const res = searchFoods(FOODS, 'RICE');
  assert.ok(res.length >= 2);
  assert.ok(res.every((f) => f.name.toLowerCase().includes('rice')));
  assert.deepEqual(searchFoods(FOODS, '   '), []);
  const ranked = searchFoods([{ name: 'Brown egg' }, { name: 'Egg' }], 'egg');
  assert.equal(ranked[0].name, 'Egg');
});

test('built-in foods have complete numeric data', () => {
  for (const f of FOODS) {
    for (const k of ['calories', 'protein', 'carbs', 'fat']) assert.equal(typeof f[k], 'number', `${f.name}.${k}`);
    assert.ok(f.name && f.serving);
  }
});

test('normalize fills missing fields and rejects junk', () => {
  const n = normalize({ goals: { calories: 1800 } });
  assert.equal(n.goals.calories, 1800);
  assert.equal(n.goals.protein, 150);
  assert.deepEqual(n.log, {});
  assert.deepEqual(normalize('nope').customFoods, []);
});
