# About me

- **Name:** Victor (GitHub: `vmudyanembwa`). Call me Victor.
- **Location:** South Africa. Default to SA context: ZAR (R), metric units, SAST (UTC+2), British/SA spelling, local examples where relevant.
- **Work:** Automation and integration: connecting business systems, automating workflows, and removing manual steps.
- **Main language:** Python. Prefer Python for scripts, automation and tooling unless the project already uses something else.

# How to work with me

- **Be concise.** Get to the point and skip filler, long preambles and recaps of what I just said.
- **Explain the why, briefly.** When you make a non-obvious choice, add a sentence or two on the reasoning so I learn the approach. Don't write essays.
- **Just build it.** For routine work, pick sensible defaults and get on with it instead of asking lots of questions. Tell me what you assumed.
- **Ask before big changes.** Check with me first before anything large or hard to undo: deleting or overwriting work, changing architecture, adding major dependencies, touching production systems or credentials, or anything that costs money.
- **Verify your work.** Run the code and tests before saying something works. If something failed or was skipped, say so plainly.

# Coding preferences

- Write clear, practical code over clever code. Keep dependencies minimal.
- For automation: make scripts safe to re-run, log what they do, handle failures clearly, and keep secrets in environment variables (never in code).
- Include a short README with how to run and test anything new.

# This repository

- `margin/`: Margin, a self-hosted Readwise-style highlights app. Python stdlib only (sqlite3 + http.server) with a vanilla-JS frontend in `margin/margin/static/`. Run with `cd margin && python3 -m margin serve`; test with `python3 -m unittest discover -s tests`. Keep it dependency-free — the one agreed exception is PDF.js, vendored in `margin/margin/static/vendor/pdfjs/` (see its README for version and how to update); ask before adding anything else. The EPUB reader never renders book HTML directly: `epub.py` sanitises chapters into a node tree that the frontend rebuilds with DOM APIs — keep it that way.
- `index.html`, `css/`, `js/`: browser-based calorie tracker. Plain HTML/CSS/JS with no build step; data is saved in `localStorage`.
- Run it with `npm start` (serves on http://localhost:8000). Run the tests with `npm test` (`node:test`).
- Keep pure logic in `js/calc.js` so it stays unit-testable. Build UI elements with `textContent`/DOM APIs rather than `innerHTML`.
