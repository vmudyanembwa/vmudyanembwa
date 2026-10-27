"""Read highlights and notes from Apple Books on macOS.

Apple Books keeps annotations and its library in two SQLite databases inside its sandbox container.
We copy them to a temporary folder and read the copies, so Books' own data is never opened for
writing or locked while Books is running.
"""

import logging
import os
import shutil
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

CONTAINER = Path.home() / "Library/Containers/com.apple.iBooksX/Data/Documents"
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)  # Core Data timestamps count from here
# ZANNOTATIONSTYLE: the colour picked in Books (0 is "underline").
STYLE_COLORS = {0: "underline", 1: "green", 2: "blue", 3: "yellow", 4: "pink", 5: "purple"}


class AppleBooksError(Exception):
    pass


def find_databases(root: Path | None = None) -> tuple[Path, Path]:
    """Locate the annotation and library databases. File names carry a version suffix, hence the glob."""
    root = root or CONTAINER
    # List folders explicitly: Path.glob() silently returns nothing when macOS denies access,
    # which would make a permission problem look like missing data.
    found = {}
    for folder, prefix in (("AEAnnotation", "AEAnnotation"), ("BKLibrary", "BKLibrary")):
        try:
            names = os.listdir(root / folder)
        except PermissionError:
            raise AppleBooksError(_PERMISSION_HELP)
        except FileNotFoundError:
            names = []
        matches = sorted(n for n in names if n.startswith(prefix) and n.endswith(".sqlite"))
        if not matches:
            raise AppleBooksError(
                "Couldn't find Apple Books data. Open Books on this Mac at least once, and make sure "
                "Terminal has Full Disk Access (System Settings → Privacy & Security → Full Disk Access). "
                f"Looked in: {root / folder}")
        found[folder] = root / folder / matches[-1]
    return found["AEAnnotation"], found["BKLibrary"]


_PERMISSION_HELP = (
    "macOS blocked access to Apple Books' data. Open System Settings → Privacy & Security → "
    "Full Disk Access, turn it on for Terminal (or the app running Margin), then restart Terminal "
    "and try again.")


def _copy_db(src: Path, dest_dir: Path) -> Path:
    """Copy a SQLite file plus its -wal/-shm companions so recent, uncheckpointed changes are included."""
    for suffix in ("", "-wal", "-shm"):
        part = Path(f"{src}{suffix}")
        if part.exists():
            try:
                shutil.copy2(part, dest_dir / part.name)
            except PermissionError:
                raise AppleBooksError(_PERMISSION_HELP)
    return dest_dir / src.name


def _timestamp(value) -> str:
    if value is None:
        return ""
    try:
        return (APPLE_EPOCH + timedelta(seconds=float(value))).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError):
        return ""


def read_highlights(annotation_db: Path | None = None, library_db: Path | None = None) -> list[dict]:
    """Return highlights in the same dict format as the file importers, in reading order per book."""
    if annotation_db is None or library_db is None:
        found_ann, found_lib = find_databases()
        annotation_db = annotation_db or found_ann
        library_db = library_db or found_lib

    with tempfile.TemporaryDirectory(prefix="margin-books-") as tmp:
        tmp = Path(tmp)
        ann_copy = _copy_db(Path(annotation_db), tmp)
        lib_copy = _copy_db(Path(library_db), tmp)
        try:
            conn = sqlite3.connect(ann_copy)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("ATTACH DATABASE ? AS lib", (str(lib_copy),))
                rows = conn.execute("""
                    SELECT a.ZANNOTATIONSELECTEDTEXT AS text,
                           a.ZANNOTATIONNOTE AS note,
                           a.ZANNOTATIONCREATIONDATE AS created,
                           a.ZANNOTATIONSTYLE AS style,
                           b.ZTITLE AS title,
                           b.ZAUTHOR AS author
                    FROM ZAEANNOTATION a
                    LEFT JOIN lib.ZBKLIBRARYASSET b ON b.ZASSETID = a.ZANNOTATIONASSETID
                    WHERE a.ZANNOTATIONDELETED = 0
                      AND a.ZANNOTATIONSELECTEDTEXT IS NOT NULL
                      AND TRIM(a.ZANNOTATIONSELECTEDTEXT) != ''
                    ORDER BY b.ZTITLE, a.ZANNOTATIONASSETID, a.ZPLLOCATIONRANGESTART, a.ZANNOTATIONCREATIONDATE
                """).fetchall()
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            raise AppleBooksError(f"Couldn't read Apple Books data ({e}). A Books update may have changed its format.")

    items = [{
        "title": (r["title"] or "Unknown book").strip(),
        "author": (r["author"] or "").strip(),
        "text": r["text"].strip(),
        "note": (r["note"] or "").strip(),
        "location": "",
        "highlighted_at": _timestamp(r["created"]),
        "tags": [],
        "url": "",
        "color": STYLE_COLORS.get(r["style"], "yellow"),
    } for r in rows]
    log.info("apple books: found %d highlights in %d books", len(items), len({i["title"] for i in items}))
    return items
