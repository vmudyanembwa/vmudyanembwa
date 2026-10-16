# Margin

A self-hosted take on [Readwise](https://readwise.io/): collect your reading highlights in one place, and a short daily review brings them back with spaced repetition so you actually remember what you read.

Pure Python standard library (`sqlite3` + `http.server`) with a vanilla-JS frontend. **Nothing to install** beyond Python 3.10+.

## Features

- **Import** Kindle `My Clippings.txt`, Readwise-style CSV exports, or Markdown/plain text (drag and drop, CLI, or paste). Re-importing is safe: duplicates are skipped, so you can re-import your Kindle file whenever you like.
  - Kindle notes are attached to their highlight, bookmarks are ignored, and when you extended a highlight only the longest version is kept.
- **Apple Books (Mac)**: one click (or `python3 -m margin import-apple-books`) pulls in your Books highlights and notes. Run it again whenever you like; only new highlights are added.
- **Daily review**: a fixed set of highlights each day (default 10). For each one, choose when to see it again: **Soon** (days), **Later** (weeks), **Someday** (months), or **Discard**. Intervals grow each time you answer, favourites come back twice as often, and there's a daily streak. Keyboard: `1` `2` `3`, `f` favourite, `n` note, `d` discard.
- **Library** of books and articles, each with its own highlights.
- **Notes, tags and favourites** on any highlight.
- **Search** across highlight text, notes, titles and authors, and filter by tag or favourites.
- **Export** to Markdown (one file per source, Obsidian-friendly, zipped) or CSV. The CSV can be imported again here or into Readwise.
- **Daily email digest** (`python -m margin digest`) for cron or Task Scheduler.
- Light and dark mode, works on phones.

## Run it

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
| `export OUT.csv` / `export OUT.zip` | Export everything as CSV or zipped Markdown |
| `digest [--dry-run]` | Email today's review (`--dry-run` prints it instead) |
| `setup-email` | Set up the daily email (password goes in the Keychain) |
| `daily` | Morning job: Apple Books import, then email |
| `install-mac [--time HH:MM] [--port P]` / `uninstall-mac` | Run in the background on macOS |

### Apple Books

Margin reads Books' own databases in `~/Library/Containers/com.apple.iBooksX/Data/Documents/` and copies them first, so Books' data is never modified. Highlights from deleted annotations are skipped, and books are matched by title and author.

If macOS blocks access, open **System Settings → Privacy & Security → Full Disk Access**, turn it on for **Terminal**, then restart Terminal.

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
- Layout: `margin/db.py` (SQLite store), `importers.py`, `review.py`, `export.py`, `digest.py`, `server.py` (JSON API + static files), `__main__.py` (CLI), `static/` (frontend).
