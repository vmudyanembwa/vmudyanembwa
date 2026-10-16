# Calorie Tracker

A lightweight calorie and macro tracker that runs entirely in the browser. No account, no server, no build step — your data stays in `localStorage` on your device.

## Features

- **Daily log by meal** — breakfast, lunch, dinner and snacks, with per-entry protein/carbs/fat.
- **Food search** — ~50 built-in common foods (including local staples like pap and boerewors), with your recent foods shown first.
- **Custom foods** — enter your own food once and it is saved to "my foods" for next time.
- **Servings** — log fractional servings (e.g. 1.5 × a cup of rice).
- **Goal calculator** — estimates your maintenance calories (Mifflin–St Jeor × activity level) and sets a target to lose, maintain or gain, with a 30/40/30 protein/carbs/fat split. Targets can be edited by hand.
- **Progress at a glance** — calorie ring, macro bars, and a 7-day chart against your goal.
- **Any day** — move back and forward through days or pick a date.
- **Backup** — export/import all data as JSON.
- Works on phones and desktops, light and dark mode.

## Run it

ES modules need to be served over HTTP (opening `index.html` directly from disk won't load the scripts):

```sh
npm start          # or: python3 -m http.server 8000
```

Then open <http://localhost:8000>. It also works as-is on any static host (e.g. GitHub Pages).

## Test

```sh
npm test
```

Unit tests cover the calculation, date and search logic in `js/calc.js` and data loading in `js/storage.js`.

## Layout

| Path | Purpose |
| --- | --- |
| `index.html` | Page markup and dialogs |
| `css/styles.css` | Styles (light/dark theme tokens) |
| `js/app.js` | UI rendering and event handling |
| `js/calc.js` | Pure functions: BMR/TDEE, targets, totals, dates, search |
| `js/foods.js` | Built-in food reference (values per serving, approximate) |
| `js/storage.js` | `localStorage` load/save and import normalisation |
| `tests/` | `node:test` unit tests |

Nutrition values are approximate and for general guidance only.
