# Margin

A self-hosted take on [Readwise](https://readwise.io/): collect your reading highlights in one place, and a short daily review brings them back with spaced repetition so you actually remember what you read.

Pure Python standard library (`sqlite3` + `http.server`) with a vanilla-JS frontend. **Nothing to install** beyond Python 3.10+. (PDF.js is bundled for reading PDFs.)

## Features

- **Import** Kindle `My Clippings.txt`, Readwise-style CSV exports, or Markdown/plain text (drag and drop, CLI, or paste). Re-importing is safe: duplicates are skipped, so you can re-import your Kindle file whenever you like.
  - Kindle notes are attached to their highlight, bookmarks are ignored, and when you extended a highlight only the longest version is kept.
- **Apple Books (Mac)**: one click (or `python3 -m margin import-apple-books`) pulls in your Books highlights and notes. Run it again whenever you like; only new highlights are added.
- **Reader for EPUB books**: add DRM-free EPUBs and read them in Margin, with a contents panel, adjustable text size and font, Auto/Light/Sepia/Dark themes (Auto follows your Mac; the sun/moon button flips light and dark in one tap), and it remembers where you were. **Select text to highlight it** (optionally with a note); highlights are marked in the book and go straight into your library and Daily Review, under the same book as any Kindle/Apple Books highlights. Try it with `sample/Meditations (sample).epub`.
- **PDF books** open in the same reader: real pages exactly as printed, with text you can select and highlight into your library, page turning, bookmarks, the PDF's own outline plus "Go to page", resume, full screen, and light/sepia/dark themes. Try `sample/Meditations (sample).pdf`.
- **Daily review**: a fixed set of highlights each day (default 10). For each one, choose when to see it again: **Soon** (days), **Later** (weeks), **Someday** (months), or **Discard**. Intervals grow each time you answer, favourites come back twice as often, and there's a daily streak. Keyboard: `1` `2` `3`, `f` favourite, `n` note, `d` discard.
- **Library** of books and articles, each with its own highlights.
- **Notes, tags and favourites** on any highlight.
- **Search** across highlight text, notes, titles and authors, and filter by tag or favourites.
- **Export** to Markdown (one file per source, Obsidian-friendly, zipped) or CSV. The CSV can be imported again here or into Readwise.
- **Daily email digest** (`python -m margin digest`) for cron or Task Scheduler.
- Light and dark mode, works on phones.

## Run it

**On a Mac, the easy way:** in Finder, open the `margin` folder and double-click **Start Margin.command**. It starts Margin and opens it in your browser; keep its Terminal window open while you use Margin. (First time only: if macOS says it can't verify the developer, right-click the file → **Open** → **Open**.) For Margin to be available without this, use `install-mac` below.

From Terminal:

```sh
cd margin
python3 -m margin serve            # http://127.0.0.1:8080
```

Data is stored in `~/.margin/margin.db`. Use `--db path` or `MARGIN_DB` to put it somewhere else (for example, a synced folder).

Try it with the sample files:

```sh
python3 -m margin import "sample/My Clippings.txt"
python3 -m margin import sample/articles.csv
python3 -m margin import sample/notes.md
```

## CLI

| Command | What it does |
| --- | --- |
| `serve [--host H] [--port P]` | Run the web app (binds to localhost by default) |
| `import FILE [--format kindle\|readwise_csv\|markdown] [--title T] [--author A]` | Import a file; format is guessed if omitted |
| `import-apple-books` | Import highlights and notes from Apple Books on this Mac |
| `import-books PATH...` (or `import-epub`) | Add EPUB and PDF books to the Reader: files, unpacked EPUB folders, or folders to search |
| `export OUT.csv` / `export OUT.zip` | Export everything as CSV or zipped Markdown |
| `digest [--dry-run]` | Email today's review (`--dry-run` prints it instead) |
| `setup-email` | Set up the daily email (password goes in the Keychain) |
| `daily` | Morning job: Apple Books import, then email |
| `install-mac [--time HH:MM] [--port P]` / `uninstall-mac` | Run in the background on macOS |

### Apple Books

Margin reads Books' own databases in `~/Library/Containers/com.apple.iBooksX/Data/Documents/` and copies them first, so Books' data is never modified. Highlights from deleted annotations are skipped, and books are matched by title and author.

If macOS blocks access, open **System Settings → Privacy & Security → Full Disk Access**, turn it on for **Terminal**, then restart Terminal.

### Reader (EPUB and PDF books)

Add books from the **Reader** page (button, or drop files anywhere on it), the **Add highlights → EPUB book** tile, or the command line:

```sh
python3 -m margin import-books ~/Books               # every .epub and .pdf in a folder (searched recursively)
python3 -m margin import-epub "~/Downloads/Some Book.epub"
```

Books are copied to `~/.margin/books/`; adding the same file twice does nothing. Removing a book from the shelf keeps its highlights.

- **Turning pages:** by default books are laid out as pages (two side by side on a wide screen) that you flip with `←`/`→` or Space, by clicking the left/right edge, swiping on a trackpad or touchscreen, or with the arrows at the bottom. Prefer one long column? Switch **Layout** to **Scroll** under **T**.
- **Full screen:** the full-screen button (or `F`) hides everything but the book; the toolbar tucks away and comes back when you move the mouse or tap the middle of the page. `Esc` or `F` exits. It stays full screen as you move between chapters.
- **Bookmarks & pages left:** tap the bookmark icon (or press `B`) to bookmark a page; a ribbon marks it. The list icon opens **Contents / Bookmarks / Highlights** for the book. The bottom bar shows pages left in the chapter and an estimate for the whole book (worked out from each chapter's length and how much text fits on your page, so it adjusts to text size and window).
- **While reading:** select text → **Highlight** or **Note** (or press `H`). Click a highlight to add a note or tags, or remove it. The list icon opens the contents, **T** opens text, layout and theme settings.
- **From your library:** highlights made in the reader have an **Open in book** link that jumps back to the exact passage.
- **PDFs:** pages are drawn by [PDF.js](https://mozilla.github.io/pdf.js/) (Mozilla, Apache-2.0), copied into `margin/static/vendor/pdfjs/` so it works offline — Margin's only third-party code, loaded only when a PDF is opened. The first time a PDF is opened (or right after you upload it) the browser reads its title, page count, outline and first page, and saves them as the book's details and cover. Each PDF page is treated like an EPUB chapter, so highlights and bookmarks are stored per page (shown as "p. 12"). Password-protected PDFs need the password removed first; scanned PDFs without a text layer can be read but not highlighted.
- **DRM:** books bought from stores (Kindle, Apple Books, Kobo, etc.) are usually DRM-protected and can't be opened outside their own app; Margin says so instead of failing silently. Most free and indie EPUBs (e.g. Project Gutenberg, Standard Ebooks) work.
- **Apple Books:** EPUBs you added to Apple Books yourself are stored as unpacked folders, which `import-epub` understands. They're usually under `~/Library/Mobile Documents/iCloud~com~apple~iBooks/Documents` (iCloud) — Terminal needs Full Disk Access to read there.
- **Safety:** EPUB pages are web pages and can contain scripts. Margin never shows a book's HTML directly: chapters are reduced on the server to plain text, headings, lists, tables, images and links, then rebuilt in the page with DOM APIs. Book images are served with a sandboxing Content-Security-Policy, so nothing in a book can run code.

### Run in the background on a Mac

```sh
python3 -m margin install-mac            # morning job at 06:00; use --time 07:30 to change it
```

This sets up two macOS LaunchAgents (`~/Library/LaunchAgents/com.margin.*.plist`):

- **com.margin.server** starts Margin at login and restarts it if it stops, so http://127.0.0.1:8080 is always available without Terminal.
- **com.margin.daily** runs `python3 -m margin daily` every morning. It imports new Apple Books highlights, then sends the email (if set up). If the Mac is asleep at that time, it runs on wake.

It's safe to re-run (for example, to change the time). Logs are in `~/.margin/logs/`. To stop and remove both jobs: `python3 -m margin uninstall-mac`.

**Full Disk Access for Python:** background jobs don't inherit Terminal's access, so Python needs its own for the Apple Books import. `install-mac` prints the exact path to add under System Settings → Privacy & Security → Full Disk Access.

While the background server is running, don't also start `python3 -m margin serve` by hand: the port will already be in use.

### Daily email

```sh
python3 -m margin setup-email
```

This asks for your SMTP server, login and recipient, saves them to `~/.margin/email.env`, stores the password in the **macOS Keychain** (never in a file), and offers to send a test email. With `install-mac` the email goes out each morning automatically; to send one manually, run `python3 -m margin digest`.

- Gmail: `smtp.gmail.com`, port 587, with an [app password](https://myaccount.google.com/apppasswords) (needs 2-step verification).
- Microsoft 365: `smtp.office365.com`, port 587. Your admin may need to enable "Authenticated SMTP" for your mailbox.

Environment variables override the file: `MARGIN_SMTP_HOST`, `MARGIN_SMTP_PORT`, `MARGIN_SMTP_USER`, `MARGIN_SMTP_PASSWORD`, `MARGIN_EMAIL_FROM`, `MARGIN_EMAIL_TO`, `MARGIN_APP_URL`.

The email shows the same highlights as that day's in-app review, so answering them in the app keeps both in step.

## How the review schedule works

Each highlight has an interval in days. When you answer, the new interval is `max(floor, current × multiplier)`, capped at a year:

| Answer | Multiplier | Minimum |
| --- | --- | --- |
| Soon | ×1.5 | 3 days |
| Later | ×2.5 | 14 days |
| Someday | ×5 | 60 days |

Favourites get half the interval. Each day's set is picked from highlights that are due, mixed across sources. If not enough are due, it is topped up with the ones you haven't seen for the longest. The set is saved, so reloading the page doesn't reshuffle it. Tweak the numbers in `margin/review.py`.

## Test

```sh
cd margin
python3 -m unittest discover -s tests
```

The tests cover the importers (using the files in `sample/`), scheduling, the database, exports, the email builder and the HTTP API.

## Notes

- There's no login: it's meant to run on your own machine. If you expose it on a network, put it behind something that adds authentication.
- Layout: `margin/db.py` (SQLite store), `importers.py`, `review.py`, `export.py`, `digest.py`, `epub.py` (EPUB parsing and sanitising), `server.py` (JSON API + static files), `__main__.py` (CLI), `static/` (frontend). Tests build their EPUBs with `tests/epub_fixtures.py`; `tests/make_sample_book.py` regenerates the sample book.
