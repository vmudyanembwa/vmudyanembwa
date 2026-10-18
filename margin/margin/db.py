"""SQLite storage for sources, highlights, tags and the daily review."""

import hashlib
import json
import logging
import random
import re
import sqlite3
import threading
from datetime import date, datetime

from . import review

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (title, author)
);
CREATE TABLE IF NOT EXISTS highlights (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    highlighted_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    fingerprint TEXT NOT NULL UNIQUE,
    favorite INTEGER NOT NULL DEFAULT 0,
    discarded INTEGER NOT NULL DEFAULT 0,
    interval_days REAL NOT NULL DEFAULT 0,
    due TEXT NOT NULL,
    review_count INTEGER NOT NULL DEFAULT 0,
    last_reviewed TEXT
);
CREATE INDEX IF NOT EXISTS highlights_source ON highlights(source_id);
CREATE INDEX IF NOT EXISTS highlights_due ON highlights(due);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE IF NOT EXISTS highlight_tags (
    highlight_id INTEGER NOT NULL REFERENCES highlights(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (highlight_id, tag_id)
);
CREATE TABLE IF NOT EXISTS review_items (
    day TEXT NOT NULL,
    highlight_id INTEGER NOT NULL REFERENCES highlights(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    action TEXT,
    PRIMARY KEY (day, highlight_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT '',
    file TEXT NOT NULL,
    sha1 TEXT NOT NULL UNIQUE,
    info TEXT NOT NULL,
    progress_chapter INTEGER NOT NULL DEFAULT 0,
    progress_pos REAL NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    last_opened TEXT
);
"""

SCHEMA += """
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chapter INTEGER NOT NULL,
    position REAL NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS bookmarks_doc ON bookmarks(doc_id, chapter);
"""

# Columns added after the first release; created on startup if an older database lacks them.
MIGRATIONS = {
    "highlights": [
        ("doc_id", "INTEGER REFERENCES documents(id) ON DELETE SET NULL"),
        ("chapter", "INTEGER"),
    ],
}

DEFAULT_SETTINGS = {"daily_count": "10"}


def fingerprint(text: str, title: str) -> str:
    """Same highlight text in the same source = duplicate, ignoring case and whitespace."""
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()  # noqa: E731
    return hashlib.sha1(f"{norm(title)}|{norm(text)}".encode()).hexdigest()


def clean_tags(tags) -> list[str]:
    seen, out = set(), []
    for t in tags or []:
        t = t.strip().lstrip("#").strip()
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out


class Store:
    def __init__(self, path: str = ":memory:", today=date.today):
        self._today = today  # injectable for tests
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        with self.conn:
            for table, columns in MIGRATIONS.items():
                existing = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                for name, decl in columns:
                    if name not in existing:
                        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                        log.info("database: added %s.%s", table, name)

    def today(self) -> date:
        return self._today()

    def close(self):
        self.conn.close()

    # ---------- settings ----------

    def get_setting(self, key: str) -> str:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS[key]

    def set_setting(self, key: str, value: str):
        if key not in DEFAULT_SETTINGS:
            raise ValueError(f"unknown setting {key!r}")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))

    # ---------- writing ----------

    def _source_id(self, title: str, author: str, url: str = "") -> int:
        title = title.strip() or "Untitled"
        author = author.strip()
        row = self.conn.execute(
            "SELECT id FROM sources WHERE title = ? AND author = ?", (title, author)).fetchone()
        if row:
            if url:
                self.conn.execute("UPDATE sources SET url = ? WHERE id = ? AND url = ''", (url, row["id"]))
            return row["id"]
        cur = self.conn.execute(
            "INSERT INTO sources(title, author, url, created_at) VALUES (?, ?, ?, ?)",
            (title, author, url, _now()))
        return cur.lastrowid

    def _set_tags(self, highlight_id: int, tags):
        self.conn.execute("DELETE FROM highlight_tags WHERE highlight_id = ?", (highlight_id,))
        for name in clean_tags(tags):
            self.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
            tag_id = self.conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
            self.conn.execute("INSERT OR IGNORE INTO highlight_tags VALUES (?, ?)", (highlight_id, tag_id))
        self.conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM highlight_tags)")

    def _insert(self, item: dict) -> int | None:
        """Insert one highlight; returns its id, or None if it's empty or already present."""
        text = (item.get("text") or "").strip()
        if not text:
            return None
        title = (item.get("title") or "").strip() or "Untitled"
        fp = fingerprint(text, title)
        if self.conn.execute("SELECT 1 FROM highlights WHERE fingerprint = ?", (fp,)).fetchone():
            return None
        source_id = self._source_id(title, item.get("author") or "", item.get("url") or "")
        cur = self.conn.execute(
            "INSERT INTO highlights(source_id, text, note, location, highlighted_at, created_at,"
            " fingerprint, due, doc_id, chapter) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, text, (item.get("note") or "").strip(), item.get("location") or "",
             item.get("highlighted_at") or "", _now(), fp, self.today().isoformat(),
             item.get("doc_id"), item.get("chapter")))
        if item.get("tags"):
            self._set_tags(cur.lastrowid, item["tags"])
        return cur.lastrowid

    def add_highlights(self, items: list[dict]) -> dict:
        """Insert parsed highlights. Duplicates (by fingerprint) are skipped, so re-imports are safe."""
        added = skipped = 0
        with self._lock, self.conn:
            for item in items:
                if self._insert(item) is None:
                    skipped += 1
                else:
                    added += 1
        log.info("import: %d added, %d skipped", added, skipped)
        return {"added": added, "skipped": skipped}

    def add_highlight(self, item: dict) -> dict | None:
        """Insert a single highlight and return it, or None if it was a duplicate."""
        with self._lock, self.conn:
            new_id = self._insert(item)
        return self.get_highlight(new_id) if new_id else None

    def update_highlight(self, highlight_id: int, fields: dict) -> dict | None:
        allowed = {"note": str, "favorite": bool, "discarded": bool, "text": str}
        with self._lock, self.conn:
            if not self.conn.execute("SELECT 1 FROM highlights WHERE id = ?", (highlight_id,)).fetchone():
                return None
            for key, cast in allowed.items():
                if key in fields:
                    value = cast(fields[key])
                    if key == "text" and not value.strip():
                        raise ValueError("highlight text cannot be empty")
                    self.conn.execute(f"UPDATE highlights SET {key} = ? WHERE id = ?",
                                      (int(value) if cast is bool else value.strip(), highlight_id))
            if "tags" in fields:
                self._set_tags(highlight_id, fields["tags"])
        return self.get_highlight(highlight_id)

    def delete_highlight(self, highlight_id: int) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute("DELETE FROM highlights WHERE id = ?", (highlight_id,))
            self.conn.execute("DELETE FROM sources WHERE id NOT IN (SELECT source_id FROM highlights)")
            self.conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM highlight_tags)")
        return cur.rowcount > 0

    # ---------- reading ----------

    _TAG_LIST = """COALESCE((SELECT group_concat(t.name, '\x1f') FROM highlight_tags ht
                    JOIN tags t ON t.id = ht.tag_id WHERE ht.highlight_id = h.id), '') AS tag_list"""
    _HIGHLIGHT_SELECT = f"""
        SELECT h.*, s.title, s.author, s.url, {_TAG_LIST}
        FROM highlights h JOIN sources s ON s.id = h.source_id
    """

    @staticmethod
    def _highlight(row) -> dict:
        d = dict(row)
        d["tags"] = sorted(filter(None, d.pop("tag_list").split("\x1f")), key=str.lower)
        d["favorite"] = bool(d["favorite"])
        d["discarded"] = bool(d["discarded"])
        d.pop("fingerprint", None)
        return d

    def get_highlight(self, highlight_id: int) -> dict | None:
        row = self.conn.execute(self._HIGHLIGHT_SELECT + " WHERE h.id = ?", (highlight_id,)).fetchone()
        return self._highlight(row) if row else None

    def search(self, q: str = "", tag: str = "", source_id: int | None = None,
               favorite: bool = False, include_discarded: bool = True, limit: int = 200) -> list[dict]:
        where, args = [], []
        for word in q.split():
            where.append("(h.text LIKE ? OR h.note LIKE ? OR s.title LIKE ? OR s.author LIKE ?)")
            args += [f"%{word}%"] * 4
        if tag:
            where.append("h.id IN (SELECT ht.highlight_id FROM highlight_tags ht JOIN tags t"
                         " ON t.id = ht.tag_id WHERE t.name = ?)")
            args.append(tag)
        if source_id is not None:
            where.append("h.source_id = ?")
            args.append(source_id)
        if favorite:
            where.append("h.favorite = 1")
        if not include_discarded:
            where.append("h.discarded = 0")
        sql = self._HIGHLIGHT_SELECT + (" WHERE " + " AND ".join(where) if where else "")
        order = " ORDER BY h.id" if source_id is not None else " ORDER BY h.id DESC"
        rows = self.conn.execute(sql + order + " LIMIT ?", (*args, limit)).fetchall()
        return [self._highlight(r) for r in rows]

    def sources(self) -> list[dict]:
        rows = self.conn.execute("""
            SELECT s.*, COUNT(h.id) AS highlight_count, MAX(h.created_at) AS last_added
            FROM sources s JOIN highlights h ON h.source_id = s.id
            GROUP BY s.id ORDER BY last_added DESC, s.title
        """).fetchall()
        return [dict(r) for r in rows]

    def get_source(self, source_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "highlights": self.search(source_id=source_id, limit=10_000)}

    def tags(self) -> list[dict]:
        rows = self.conn.execute("""
            SELECT t.name, COUNT(*) AS count FROM tags t JOIN highlight_tags ht ON ht.tag_id = t.id
            GROUP BY t.id ORDER BY count DESC, t.name COLLATE NOCASE
        """).fetchall()
        return [dict(r) for r in rows]

    # ---------- reader documents (EPUBs) ----------

    def add_document(self, sha1: str, file: str, info: dict) -> tuple[int, bool]:
        """Register a book. Returns (id, created); the same file added twice returns the existing id."""
        with self._lock, self.conn:
            row = self.conn.execute("SELECT id FROM documents WHERE sha1 = ?", (sha1,)).fetchone()
            if row:
                return row["id"], False
            cur = self.conn.execute(
                "INSERT INTO documents(title, author, file, sha1, info, added_at) VALUES (?, ?, ?, ?, ?, ?)",
                (info["title"], info["author"], file, sha1, json.dumps(info), _now()))
            return cur.lastrowid, True

    @staticmethod
    def _document(row) -> dict:
        d = dict(row)
        info = json.loads(d.pop("info"))
        d.pop("sha1", None)
        chapters = max(1, info["chapters"])
        d["progress"] = round(min(1.0, (d["progress_chapter"] + d["progress_pos"]) / chapters), 4)
        d["chapter_chars"] = info.get("chapter_chars")  # None for books added before page estimates
        d.update(chapters=info["chapters"], cover=info["cover"], toc=info["toc"],
                 chapter_titles=info["chapter_titles"], language=info.get("language", ""))
        return d

    def documents(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM documents ORDER BY COALESCE(last_opened, added_at) DESC, id DESC").fetchall()
        out = []
        for r in rows:
            d = self._document(r)
            for key in ("toc", "chapter_titles"):
                d.pop(key)
            out.append(d)
        return out

    def get_document(self, doc_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            return None
        d = self._document(row)
        src = self.conn.execute("SELECT id FROM sources WHERE title = ? AND author = ?",
                                (d["title"], d["author"])).fetchone()
        d["source_id"] = src["id"] if src else None
        d["highlight_count"] = self.conn.execute(
            "SELECT COUNT(*) FROM highlights WHERE doc_id = ?", (doc_id,)).fetchone()[0]
        return d

    def set_progress(self, doc_id: int, chapter: int, position: float) -> bool:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE documents SET progress_chapter = ?, progress_pos = ?, last_opened = ? WHERE id = ?",
                (int(chapter), min(1.0, max(0.0, float(position))), _now(), doc_id))
        return cur.rowcount > 0

    def delete_document(self, doc_id: int) -> str | None:
        """Remove a book from the reader (its highlights stay). Returns the file name to delete."""
        with self._lock, self.conn:
            row = self.conn.execute("SELECT file FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if not row:
                return None
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        return row["file"]

    def set_document_info(self, doc_id: int, key: str, value):
        """Fill in a metadata field computed after import (e.g. chapter lengths for older books)."""
        with self._lock, self.conn:
            row = self.conn.execute("SELECT info FROM documents WHERE id = ?", (doc_id,)).fetchone()
            if row:
                info = json.loads(row["info"])
                info[key] = value
                self.conn.execute("UPDATE documents SET info = ? WHERE id = ?", (json.dumps(info), doc_id))

    def bookmarks(self, doc_id: int) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM bookmarks WHERE doc_id = ? ORDER BY chapter, position",
                                 (doc_id,)).fetchall()
        return [dict(r) for r in rows]

    def add_bookmark(self, doc_id: int, chapter: int, position: float, label: str) -> dict:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO bookmarks(doc_id, chapter, position, label, created_at) VALUES (?, ?, ?, ?, ?)",
                (doc_id, int(chapter), min(1.0, max(0.0, float(position))), label.strip()[:200], _now()))
        return dict(self.conn.execute("SELECT * FROM bookmarks WHERE id = ?", (cur.lastrowid,)).fetchone())

    def delete_bookmark(self, bookmark_id: int) -> bool:
        with self._lock, self.conn:
            return self.conn.execute("DELETE FROM bookmarks WHERE id = ?", (bookmark_id,)).rowcount > 0

    def document_highlights(self, doc_id: int) -> list[dict]:
        rows = self.conn.execute(self._HIGHLIGHT_SELECT + " WHERE h.doc_id = ? ORDER BY h.chapter, h.id",
                                 (doc_id,)).fetchall()
        return [self._highlight(r) for r in rows]

    def chapter_highlights(self, doc_id: int, chapter: int) -> list[dict]:
        rows = self.conn.execute(self._HIGHLIGHT_SELECT + " WHERE h.doc_id = ? AND h.chapter = ? ORDER BY h.id",
                                 (doc_id, chapter)).fetchall()
        return [self._highlight(r) for r in rows]

    def stats(self) -> dict:
        c = self.conn.execute
        today = self.today().isoformat()
        return {
            "highlights": c("SELECT COUNT(*) FROM highlights").fetchone()[0],
            "sources": c("SELECT COUNT(*) FROM sources").fetchone()[0],
            "favorites": c("SELECT COUNT(*) FROM highlights WHERE favorite = 1").fetchone()[0],
            "due": c("SELECT COUNT(*) FROM highlights WHERE discarded = 0 AND due <= ?", (today,)).fetchone()[0],
            "streak": self.streak(),
            "daily_count": int(self.get_setting("daily_count")),
        }

    # ---------- daily review ----------

    def daily_review(self) -> list[dict]:
        """Today's review set. Chosen once per day and stored, so reloading shows the same items."""
        day = self.today().isoformat()
        with self._lock, self.conn:
            exists = self.conn.execute("SELECT 1 FROM review_items WHERE day = ?", (day,)).fetchone()
            if not exists:
                self._build_review(day)
        rows = self.conn.execute(f"""
            SELECT h.*, s.title, s.author, s.url, r.action AS review_action, {self._TAG_LIST}
            FROM review_items r
            JOIN highlights h ON h.id = r.highlight_id
            JOIN sources s ON s.id = h.source_id
            WHERE r.day = ? ORDER BY r.position
        """, (day,)).fetchall()
        return [self._highlight(r) for r in rows]

    def _build_review(self, day: str):
        count = int(self.get_setting("daily_count"))
        rng = random.Random(day)  # stable per day
        due = [r["id"] for r in self.conn.execute(
            "SELECT id FROM highlights WHERE discarded = 0 AND due <= ? ORDER BY due, id", (day,))]
        # Mix older and newer due items rather than strictly oldest-first.
        picked = rng.sample(due, min(count, len(due)))
        if len(picked) < count:
            # Not enough due: top up with the highlights we haven't seen for the longest.
            filler = [r["id"] for r in self.conn.execute(
                "SELECT id FROM highlights WHERE discarded = 0 AND due > ? "
                "ORDER BY COALESCE(last_reviewed, '0000'), id LIMIT ?", (day, count - len(picked)))]
            picked += filler
        # Avoid showing two highlights from the same source back to back where possible.
        sources = dict(self.conn.execute(
            f"SELECT id, source_id FROM highlights WHERE id IN ({','.join('?' * len(picked))})", picked
        ).fetchall()) if picked else {}
        ordered = _spread(picked, sources)
        self.conn.executemany("INSERT INTO review_items(day, highlight_id, position) VALUES (?, ?, ?)",
                              [(day, hid, i) for i, hid in enumerate(ordered)])

    def review_action(self, highlight_id: int, action: str) -> dict | None:
        """Record feedback: soon / later / someday reschedule; discard removes it from future reviews."""
        today = self.today()
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT interval_days, favorite FROM highlights WHERE id = ?", (highlight_id,)).fetchone()
            if not row:
                return None
            if action == "discard":
                self.conn.execute("UPDATE highlights SET discarded = 1 WHERE id = ?", (highlight_id,))
            else:
                interval = review.next_interval(row["interval_days"], action, bool(row["favorite"]))
                self.conn.execute(
                    "UPDATE highlights SET interval_days = ?, due = ?, review_count = review_count + 1,"
                    " last_reviewed = ? WHERE id = ?",
                    (interval, review.next_due(today, interval).isoformat(), today.isoformat(), highlight_id))
            self.conn.execute("UPDATE review_items SET action = ? WHERE day = ? AND highlight_id = ?",
                              (action, today.isoformat(), highlight_id))
        return self.get_highlight(highlight_id)

    def streak(self) -> int:
        """Consecutive days (ending today, or yesterday if today isn't done yet) with a finished review."""
        rows = self.conn.execute("""
            SELECT day FROM review_items GROUP BY day
            HAVING COUNT(*) > 0 AND SUM(action IS NULL) = 0 ORDER BY day DESC
        """).fetchall()
        done = {r["day"] for r in rows}
        day = self.today()
        if day.isoformat() not in done:
            day = date.fromordinal(day.toordinal() - 1)
        streak = 0
        while day.isoformat() in done:
            streak += 1
            day = date.fromordinal(day.toordinal() - 1)
        return streak


def _spread(ids: list[int], source_of: dict[int, int]) -> list[int]:
    """Reorder so consecutive items come from different sources when possible (greedy)."""
    remaining = list(ids)
    out: list[int] = []
    while remaining:
        prev = source_of.get(out[-1]) if out else None
        pick = next((i for i in remaining if source_of.get(i) != prev), remaining[0])
        remaining.remove(pick)
        out.append(pick)
    return out


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
