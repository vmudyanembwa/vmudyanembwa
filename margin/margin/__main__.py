"""Command line entry point: python -m margin <command>"""

import argparse
import logging
import os
import sys
from pathlib import Path

from . import apple_books, digest, export, importers
from .db import Store

DEFAULT_DB = os.environ.get("MARGIN_DB", str(Path.home() / ".margin" / "margin.db"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="margin", description="Keep, review and resurface your reading highlights.")
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the web app")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)

    p = sub.add_parser("import", help="import highlights from a file (safe to re-run; duplicates are skipped)")
    p.add_argument("file", type=Path)
    p.add_argument("--format", choices=importers.SUPPORTED, help="default: guessed from the file")
    p.add_argument("--title", default="", help="source title for markdown/text files")
    p.add_argument("--author", default="")

    p = sub.add_parser("import-apple-books", help="import highlights and notes from Apple Books (macOS)")
    p.add_argument("--annotations", type=Path, help="AEAnnotation*.sqlite (default: found automatically)")
    p.add_argument("--library", type=Path, help="BKLibrary*.sqlite (default: found automatically)")

    p = sub.add_parser("digest", help="email today's review (configure with MARGIN_SMTP_* env vars)")
    p.add_argument("--dry-run", action="store_true", help="print the email instead of sending it")

    p = sub.add_parser("export", help="export all highlights")
    p.add_argument("output", type=Path, help="file ending in .csv or .zip (Markdown files)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.db)
    try:
        return run(args, store)
    except (OSError, ValueError, RuntimeError, apple_books.AppleBooksError) as e:
        logging.error("%s", e)
        return 1
    finally:
        store.close()


def run(args, store) -> int:
    if args.command == "serve":
        from .server import serve
        serve(store, args.host, args.port)
    elif args.command == "import":
        content = args.file.read_text(encoding="utf-8-sig", errors="replace")
        fmt = args.format or importers.guess_format(args.file.name, content)
        kw = {"title": args.title or args.file.stem, "author": args.author} if fmt == "markdown" else {}
        items = importers.parse(fmt, content, **kw)
        result = store.add_highlights(items)
        print(f"{args.file.name} ({fmt}): {len(items)} found, {result['added']} added, {result['skipped']} already present")
    elif args.command == "import-apple-books":
        items = apple_books.read_highlights(args.annotations, args.library)
        result = store.add_highlights(items)
        print(f"Apple Books: {len(items)} found, {result['added']} added, {result['skipped']} already present")
    elif args.command == "digest":
        n = digest.send_digest(store, dry_run=args.dry_run)
        if not args.dry_run:
            print(f"Sent {n} highlights" if n else "Nothing to send")
    elif args.command == "export":
        suffix = args.output.suffix.lower()
        if suffix == ".csv":
            args.output.write_text(export.highlights_csv(store.search(limit=1_000_000)), encoding="utf-8")
        elif suffix == ".zip":
            args.output.write_bytes(export.markdown_zip([store.get_source(s["id"]) for s in store.sources()]))
        else:
            raise ValueError("output must end in .csv or .zip")
        print(f"Exported to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
