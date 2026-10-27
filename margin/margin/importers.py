"""Parsers that turn exported highlight files into plain dicts.

Each parser returns a list of dicts with keys:
    title, author, text, note, location, highlighted_at, tags (list[str]), url
Unknown values are empty strings / empty lists. Parsers never touch the database.
"""

import csv
import io
import re

from .db import COLORS

SUPPORTED = ("kindle", "readwise_csv", "markdown")


def _blank(**kw):
    item = {"title": "", "author": "", "text": "", "note": "", "location": "",
            "highlighted_at": "", "tags": [], "url": ""}
    item.update(kw)
    return item


# ---------- Kindle "My Clippings.txt" ----------

_KINDLE_SEP = "=========="
_TITLE_AUTHOR = re.compile(r"^(?P<title>.*?)\s*\((?P<author>[^()]*)\)\s*$")
_LOCATION = re.compile(r"Location\s+([\d\-–]+)", re.I)
_PAGE = re.compile(r"page\s+([\d\-–]+)", re.I)
_ADDED = re.compile(r"Added on\s+(.*)$")


def parse_kindle(content: str) -> list[dict]:
    """Parse Kindle's My Clippings.txt. Notes are attached to the highlight at the same location."""
    content = content.lstrip("﻿")
    highlights, notes = [], []
    for block in content.split(_KINDLE_SEP):
        lines = [line.strip().lstrip("﻿") for line in block.strip().splitlines()]
        if len(lines) < 3:
            continue
        header, meta, body = lines[0], lines[1], "\n".join(lines[2:]).strip()
        if not body:
            continue
        m = _TITLE_AUTHOR.match(header)
        title, author = (m["title"], m["author"]) if m else (header, "")
        author = ", ".join(a.strip() for a in author.split(";") if a.strip())  # Kindle joins co-authors with ';'
        loc = _LOCATION.search(meta) or _PAGE.search(meta)  # PDFs/some devices only have pages
        added = _ADDED.search(meta)
        item = _blank(title=title.strip(), author=author.strip(), text=body,
                      location=loc.group(1) if loc else "",
                      highlighted_at=added.group(1).strip() if added else "")
        kind = meta.lower()
        if "your note" in kind:
            notes.append(item)
        elif "your bookmark" in kind:
            continue
        else:
            highlights.append(item)

    # Kindle stores a note as its own clipping; its location is the end of the highlight range.
    for note in notes:
        target = next(
            (h for h in reversed(highlights)
             if h["title"] == note["title"] and note["location"]
             and h["location"].split("-")[-1] == note["location"]),
            None,
        )
        if target:
            target["note"] = (target["note"] + "\n" + note["text"]).strip()
    return _dedupe_kindle(highlights)


def _dedupe_kindle(items: list[dict]) -> list[dict]:
    """Kindle keeps old versions when you extend a highlight; keep the longest per start location."""
    best: dict[tuple, dict] = {}
    order = []
    for h in items:
        start = h["location"].split("-")[0] if h["location"] else None
        key = (h["title"], start) if start else (h["title"], h["text"])
        if key not in best:
            order.append(key)
            best[key] = h
        elif len(h["text"]) > len(best[key]["text"]):
            h["note"] = h["note"] or best[key]["note"]
            best[key] = h
    return [best[k] for k in order]


# ---------- Readwise-style CSV ----------

def parse_readwise_csv(content: str) -> list[dict]:
    """Parse the CSV format Readwise exports (and accepts for import).

    Columns: Highlight, Book Title, Book Author, Note, Location, Highlighted at, Tags, URL, Color.
    Column names are matched case-insensitively; only Highlight is required.
    """
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    if not reader.fieldnames:
        return []
    cols = {name.strip().lower(): name for name in reader.fieldnames}
    if "highlight" not in cols:
        raise ValueError("CSV needs a 'Highlight' column")

    def get(row, *names):
        for n in names:
            if n in cols and row.get(cols[n]):
                return row[cols[n]].strip()
        return ""

    out = []
    for row in reader:
        text = get(row, "highlight")
        if not text:
            continue
        tags = [t.strip() for t in re.split(r"[,;]", get(row, "tags", "document tags")) if t.strip()]
        out.append(_blank(
            text=text,
            title=get(row, "book title", "title"),
            author=get(row, "book author", "author"),
            note=get(row, "note"),
            location=get(row, "location"),
            highlighted_at=get(row, "highlighted at", "date"),
            tags=tags,
            url=get(row, "url", "source url"),
            # Readwise has colours we don't (e.g. orange); those become the default.
            color=get(row, "color").lower() if get(row, "color").lower() in COLORS else "",
        ))
    return out


# ---------- Markdown / plain text ----------

def parse_markdown(content: str, title: str = "", author: str = "") -> list[dict]:
    """Paste-friendly format.

    Optional '# Title' and 'Author: name' lines set the source. Highlights are separated by
    blank lines; a leading '> ' or '- ' is stripped. A line starting 'Note:' attaches to the
    previous highlight and '#tag' words in it become tags.
    """
    out: list[dict] = []
    paragraphs = re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip())
    for para in paragraphs:
        # Header lines ('# Title', 'Author: x') may sit together at the top of a paragraph.
        lines = para.strip().splitlines()
        while lines and (lines[0].startswith("# ") or lines[0].lower().startswith("author:")):
            header = lines.pop(0)
            if header.startswith("# "):
                title = header[2:].strip()
            else:
                author = header.split(":", 1)[1].strip()
        para = "\n".join(lines).strip()
        if not para:
            continue
        if para.lower().startswith("note:") and out:
            note = para.split(":", 1)[1].strip()
            out[-1]["tags"] += re.findall(r"(?<!\w)#([\w-]+)", note)
            out[-1]["note"] = re.sub(r"(?<!\w)#[\w-]+", "", note).strip()
            continue
        lines = [re.sub(r"^(> ?|- )", "", line) for line in para.splitlines()]
        out.append(_blank(text="\n".join(lines).strip()))
    for item in out:
        item["title"], item["author"] = title, author
    return out


def parse(fmt: str, content: str, **kw) -> list[dict]:
    if fmt == "kindle":
        return parse_kindle(content)
    if fmt == "readwise_csv":
        return parse_readwise_csv(content)
    if fmt == "markdown":
        return parse_markdown(content, **kw)
    raise ValueError(f"unsupported format {fmt!r}; use one of {', '.join(SUPPORTED)}")


def guess_format(filename: str, content: str) -> str:
    name = filename.lower()
    if name.endswith(".csv"):
        return "readwise_csv"
    if "my clippings" in name or _KINDLE_SEP in content:
        return "kindle"
    return "markdown"
