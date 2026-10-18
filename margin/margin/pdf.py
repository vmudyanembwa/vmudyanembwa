"""PDF books for the reader.

Python's standard library can't read PDFs, so the server only stores them. The browser renders pages
with PDF.js (vendored in static/vendor/pdfjs) and, the first time a PDF is opened, reports back its
page count, title, outline and a cover image, which are saved with the book.

In the reader a PDF page plays the role an EPUB chapter does: highlights, bookmarks and reading
progress are all stored against the page index.
"""

import re
from pathlib import Path

from .epub import content_hash

MAX_COVER_BYTES = 3 * 1024 * 1024


class PdfError(Exception):
    pass


def is_pdf(data: bytes) -> bool:
    # The header may follow a few junk bytes; the spec allows it within the first 1 KB.
    return b"%PDF-" in data[:1024]


def title_from_filename(filename: str) -> str:
    stem = Path(filename or "").stem
    stem = re.sub(r"[_]+", " ", stem).strip()
    return stem or "Untitled PDF"


def add_to_library(store, data: bytes, books_dir: Path, filename: str = "") -> tuple[dict, bool]:
    """Copy a PDF into the books folder and register it. Same file twice → same book."""
    if not is_pdf(data):
        raise PdfError("not a PDF file")
    sha1 = content_hash(data)
    books_dir = Path(books_dir)
    books_dir.mkdir(parents=True, exist_ok=True)
    file = f"{sha1}.pdf"
    target = books_dir / file
    if not target.exists():
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)
    info = {"kind": "pdf", "title": title_from_filename(filename), "author": "", "language": "",
            "chapters": 0, "cover": "", "toc": [], "chapter_titles": [], "chapter_chars": None}
    doc_id, created = store.add_document(sha1, file, info)
    return store.get_document(doc_id), created


def cover_type(data: bytes) -> str:
    """Only PNG and JPEG covers are accepted (the browser renders page 1 to PNG)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return ""


def clean_meta(body: dict) -> dict:
    """Validate what the browser reports about a PDF before storing it."""
    try:
        pages = int(body.get("pages", 0))
    except (TypeError, ValueError):
        raise PdfError("pages must be a number")
    if not 1 <= pages <= 100_000:
        raise PdfError("pages out of range")
    text = lambda v, n: re.sub(r"\s+", " ", str(v or "")).strip()[:n]  # noqa: E731
    toc = []
    for entry in (body.get("toc") or [])[:5000]:
        if not isinstance(entry, dict):
            continue
        try:
            page, level = int(entry.get("chapter")), int(entry.get("level", 0))
        except (TypeError, ValueError):
            continue
        title = text(entry.get("title"), 300)
        if title and 0 <= page < pages:
            toc.append({"title": title, "chapter": page, "anchor": "", "level": max(0, min(level, 6))})
    # Each page's section name = the last outline entry at or before it.
    titles, current, by_page = [], "", {}
    for entry in toc:
        by_page.setdefault(entry["chapter"], entry["title"])
    for i in range(pages):
        current = by_page.get(i, current)
        titles.append(current)
    return {"title": text(body.get("title"), 300), "author": text(body.get("author"), 300),
            "chapters": pages, "toc": toc, "chapter_titles": titles}
