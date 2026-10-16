"""Command line entry point: python -m margin <command>"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import apple_books, digest, export, importers, macos
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

    p = sub.add_parser("digest", help="email today's review (set up first with setup-email)")
    p.add_argument("--dry-run", action="store_true", help="print the email instead of sending it")

    sub.add_parser("setup-email", help="interactively set up the daily email (password goes in the macOS Keychain)")

    sub.add_parser("daily", help="morning job: import from Apple Books, then email the digest if set up")

    p = sub.add_parser("install-mac", help="run Margin in the background at login and do the morning job daily")
    p.add_argument("--time", default="06:00", help="when to run the morning job, 24h local time (default 06:00)")
    p.add_argument("--port", type=int, default=8080)

    sub.add_parser("uninstall-mac", help="stop and remove the background jobs")

    p = sub.add_parser("export", help="export all highlights")
    p.add_argument("output", type=Path, help="file ending in .csv or .zip (Markdown files)")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    args.email_config = Path(args.db).parent / "email.env"
    store = Store(args.db)
    try:
        return run(args, store)
    except (OSError, ValueError, RuntimeError, apple_books.AppleBooksError) as e:
        logging.error("%s", e)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.")
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
        n = digest.send_digest(store, dry_run=args.dry_run, config=digest.load_config(args.email_config))
        if not args.dry_run:
            print(f"Sent {n} highlights" if n else "Nothing to send")
    elif args.command == "setup-email":
        return setup_email(args.email_config)
    elif args.command == "daily":
        return daily(store, args.email_config)
    elif args.command == "install-mac":
        for path in macos.install(args.db, args.port, args.time):
            print(f"Installed {path}")
        log_dir = Path(args.db).expanduser().resolve().parent / "logs"
        print(f"""
Margin now runs in the background: http://127.0.0.1:{args.port}
It starts at login, and every day at {args.time} it imports from Apple Books and sends the email (if set up).
Logs: {log_dir}

One more step for the Apple Books import to work in the background:
  System Settings → Privacy & Security → Full Disk Access → + → press Cmd+Shift+G, paste
  {macos.python_app_path()}
  then add it and make sure it's switched on.
(Terminal's access doesn't carry over to background jobs, so Python needs its own.)

Test the morning job now:  python3 -m margin daily""")
    elif args.command == "uninstall-mac":
        removed = macos.uninstall()
        print("\n".join(f"Removed {p}" for p in removed) or "Nothing was installed.")
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


def daily(store, email_config: Path) -> int:
    """Morning job. Each step runs even if the other fails; exit code 1 if anything went wrong."""
    failed = False
    if sys.platform == "darwin":
        try:
            items = apple_books.read_highlights()
            result = store.add_highlights(items)
            logging.info("Apple Books: %d found, %d new", len(items), result["added"])
        except apple_books.AppleBooksError as e:
            logging.error("Apple Books import failed: %s", e)
            failed = True
    else:
        logging.info("Apple Books import skipped (not a Mac)")

    config = digest.load_config(email_config)
    if digest.is_configured(config):
        try:
            n = digest.send_digest(store, config=config)
            logging.info("email: %s", f"sent {n} highlights" if n else "nothing to send")
        except RuntimeError as e:
            logging.error("email failed: %s", e)
            failed = True
    else:
        logging.info("email not set up; skipping (run: python3 -m margin setup-email)")
    return 1 if failed else 0


def _ask(prompt: str, default: str = "") -> str:
    answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return answer or default


def setup_email(config_file: Path) -> int:
    current = digest.read_env_file(config_file)
    print("Set up the daily highlights email. Press Enter to keep the value in [brackets].\n"
          "Common servers: Gmail smtp.gmail.com · Microsoft 365 smtp.office365.com · both use port 587.\n")
    config = {
        "MARGIN_SMTP_HOST": _ask("SMTP server", current.get("MARGIN_SMTP_HOST", "")),
        "MARGIN_SMTP_PORT": _ask("Port (587 or 465)", current.get("MARGIN_SMTP_PORT", "587")),
        "MARGIN_SMTP_USER": _ask("Login email / username", current.get("MARGIN_SMTP_USER", "")),
    }
    config["MARGIN_EMAIL_TO"] = _ask("Send the digest to", current.get("MARGIN_EMAIL_TO") or config["MARGIN_SMTP_USER"])
    config["MARGIN_APP_URL"] = current.get("MARGIN_APP_URL", "http://127.0.0.1:8080")
    if not (config["MARGIN_SMTP_HOST"] and config["MARGIN_SMTP_USER"] and config["MARGIN_EMAIL_TO"]):
        raise ValueError("server, login and recipient are all required")
    int(config["MARGIN_SMTP_PORT"])  # raises ValueError for a typo

    if shutil.which("security"):
        print("\nNow enter the password (for Gmail/Microsoft 365, an app password). "
              "It is stored in your macOS Keychain, not in a file. You'll be asked twice.")
        result = subprocess.run(["security", "add-generic-password", "-U", "-a", config["MARGIN_SMTP_USER"],
                                 "-s", digest.KEYCHAIN_SERVICE, "-l", "Margin email (SMTP)", "-w"])
        if result.returncode != 0:
            raise RuntimeError("couldn't save the password to the Keychain")
    else:
        print("\nNo macOS Keychain here: set the password in the MARGIN_SMTP_PASSWORD environment variable.")

    digest.write_env_file(config_file, config)
    print(f"Saved settings to {config_file}")

    if _ask("Send a test email now? (y/n)", "y").lower().startswith("y"):
        sample = [{"text": "If this arrived, Margin's daily email is working.", "title": "Margin",
                   "author": "", "note": ""}]
        digest.send(digest.build_message(sample, "test", config["MARGIN_APP_URL"]), digest.load_config(config_file))
        print(f"Test email sent to {config['MARGIN_EMAIL_TO']}. Check your inbox (and spam folder).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
