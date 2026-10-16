"""Run with:  python -m unittest discover -s tests   (from the margin/ folder)"""

import csv
import io
import json
import threading
import unittest
import unittest.mock
import urllib.error
import urllib.request
import zipfile
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from margin import digest, export, importers, review
from margin.db import Store, fingerprint
from margin.server import make_handler

SAMPLE = Path(__file__).resolve().parent.parent / "sample"


class Clock:
    """Controllable 'today' for review scheduling tests."""

    def __init__(self, day=date(2026, 10, 16)):
        self.day = day

    def __call__(self):
        return self.day


def item(text, title="Book", author="Author", **kw):
    return {"text": text, "title": title, "author": author, **kw}


class ReviewMathTests(unittest.TestCase):
    def test_floors_apply_to_new_highlights(self):
        self.assertEqual(review.next_interval(0, "soon"), 3)
        self.assertEqual(review.next_interval(0, "later"), 14)
        self.assertEqual(review.next_interval(0, "someday"), 60)

    def test_intervals_grow_and_cap(self):
        self.assertEqual(review.next_interval(14, "later"), 35)
        self.assertEqual(review.next_interval(300, "someday"), review.MAX_INTERVAL_DAYS)

    def test_favourites_come_back_sooner(self):
        self.assertEqual(review.next_interval(0, "later", favorite=True), 7)

    def test_unknown_action(self):
        with self.assertRaises(ValueError):
            review.next_interval(1, "never")

    def test_next_due(self):
        self.assertEqual(review.next_due(date(2026, 12, 30), 3), date(2027, 1, 2))


class ImporterTests(unittest.TestCase):
    def test_kindle(self):
        items = importers.parse_kindle((SAMPLE / "My Clippings.txt").read_text(encoding="utf-8"))
        texts = [i["text"] for i in items]
        self.assertEqual(len(items), 6)  # bookmark skipped, extended Deep Work highlight deduped
        first = items[0]
        self.assertEqual(first["title"], "Atomic Habits")
        self.assertEqual(first["author"], "James Clear")
        self.assertEqual(first["location"], "371-373")
        self.assertEqual(first["note"], "Apply this to the onboarding automation project.")
        deep = [t for t in texts if t.startswith("Clarity")]
        self.assertEqual(len(deep), 1)
        self.assertIn("sum of what you focus on", deep[0])
        self.assertEqual(items[-1]["author"], "David Thomas, Andrew Hunt")

    def test_kindle_title_with_parentheses(self):
        content = "Dune (Dune Chronicles 1) (Frank Herbert)\n- Your Highlight on page 1 | Location 5-6 | Added on x\n\nFear is the mind-killer.\n=========="
        [h] = importers.parse_kindle(content)
        self.assertEqual((h["title"], h["author"]), ("Dune (Dune Chronicles 1)", "Frank Herbert"))

    def test_csv(self):
        items = importers.parse_readwise_csv((SAMPLE / "articles.csv").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0]["tags"], ["automation", "productivity"])
        self.assertEqual(items[1]["note"], "Use in team talk")
        self.assertEqual(items[0]["url"], "https://example.com/automation")

    def test_csv_requires_highlight_column(self):
        with self.assertRaises(ValueError):
            importers.parse_readwise_csv("Title,Author\nA,B\n")

    def test_markdown(self):
        items = importers.parse_markdown((SAMPLE / "notes.md").read_text(encoding="utf-8"))
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["title"], "Meditations")
        self.assertEqual(items[0]["author"], "Marcus Aurelius")
        self.assertTrue(items[0]["text"].startswith("The impediment"))
        self.assertEqual(items[0]["note"], "Reframe blockers on projects.")
        self.assertEqual(items[0]["tags"], ["stoicism", "mindset"])

    def test_guess_format(self):
        self.assertEqual(importers.guess_format("x.csv", ""), "readwise_csv")
        self.assertEqual(importers.guess_format("My Clippings.txt", ""), "kindle")
        self.assertEqual(importers.guess_format("a.txt", "hello"), "markdown")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Store(today=self.clock)

    def tearDown(self):
        self.store.close()

    def test_reimport_is_idempotent(self):
        items = [item("One"), item("Two"), item("  one ")]  # third is a whitespace/case duplicate
        self.assertEqual(self.store.add_highlights(items), {"added": 2, "skipped": 1})
        self.assertEqual(self.store.add_highlights(items), {"added": 0, "skipped": 3})
        self.assertEqual(self.store.stats()["highlights"], 2)
        self.assertEqual(fingerprint("A  b", "T"), fingerprint("a b", "t"))

    def test_sources_tags_and_search(self):
        self.store.add_highlights([
            item("Systems beat goals", "Atomic Habits", "James Clear", tags=["habits", "#Systems"]),
            item("Focus is a skill", "Deep Work", "Cal Newport", note="train it daily", tags=["focus"]),
        ])
        self.assertEqual(len(self.store.sources()), 2)
        self.assertEqual([h["text"] for h in self.store.search(q="daily")], ["Focus is a skill"])  # note
        self.assertEqual(len(self.store.search(q="newport")), 1)  # author
        hits = self.store.search(tag="systems")
        self.assertEqual(hits[0]["tags"], ["habits", "Systems"])
        self.assertEqual({t["name"] for t in self.store.tags()}, {"habits", "Systems", "focus"})

    def test_update_and_delete_clean_up(self):
        self.store.add_highlights([item("Only one", tags=["x"])])
        [h] = self.store.search()
        updated = self.store.update_highlight(h["id"], {"note": " hi ", "favorite": True, "tags": ["y"]})
        self.assertEqual((updated["note"], updated["favorite"], updated["tags"]), ("hi", True, ["y"]))
        self.assertEqual([t["name"] for t in self.store.tags()], ["y"])  # orphan tag removed
        with self.assertRaises(ValueError):
            self.store.update_highlight(h["id"], {"text": "  "})
        self.assertTrue(self.store.delete_highlight(h["id"]))
        self.assertEqual(self.store.sources(), [])  # empty source removed
        self.assertIsNone(self.store.update_highlight(h["id"], {"note": "x"}))

    def test_daily_review_is_stable_and_sized(self):
        self.store.set_setting("daily_count", "3")
        self.store.add_highlights([item(f"H{i}", f"Book {i % 2}") for i in range(10)])
        first = [h["id"] for h in self.store.daily_review()]
        self.assertEqual(len(first), 3)
        self.assertEqual(first, [h["id"] for h in self.store.daily_review()])  # same on reload
        # Consecutive items come from different sources where possible.
        sources = [h["source_id"] for h in self.store.daily_review()]
        self.assertNotEqual(sources[0], sources[1])

    def test_review_actions_reschedule(self):
        self.store.add_highlights([item("A"), item("B")])
        a, b = self.store.daily_review()
        done = self.store.review_action(a["id"], "later")
        self.assertEqual(done["due"], (self.clock.day + timedelta(days=14)).isoformat())
        self.assertEqual(done["review_count"], 1)
        self.store.review_action(b["id"], "discard")
        self.assertTrue(self.store.get_highlight(b["id"])["discarded"])
        self.assertIsNone(self.store.review_action(999, "soon"))

        # Next day: A isn't due and B is discarded, so A is used as filler; B never returns.
        self.clock.day += timedelta(days=1)
        self.assertEqual([h["id"] for h in self.store.daily_review()], [a["id"]])

    def test_due_items_come_first(self):
        self.store.set_setting("daily_count", "1")
        self.store.add_highlights([item("A"), item("B")])
        [first] = self.store.daily_review()
        self.store.review_action(first["id"], "someday")
        self.clock.day += timedelta(days=1)
        [nxt] = self.store.daily_review()
        self.assertNotEqual(nxt["id"], first["id"])

    def test_streak(self):
        self.store.set_setting("daily_count", "1")
        self.store.add_highlights([item("A")])
        for _ in range(3):
            [h] = self.store.daily_review()
            self.store.review_action(h["id"], "soon")
            self.clock.day += timedelta(days=1)
        self.assertEqual(self.store.streak(), 3)  # today not done yet: counts up to yesterday
        self.clock.day += timedelta(days=1)  # skipped a day
        self.assertEqual(self.store.streak(), 0)

    def test_settings_validation(self):
        with self.assertRaises(ValueError):
            self.store.set_setting("nope", "1")


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(today=Clock())
        self.store.add_highlights([
            item("Line one\nLine two", "A/B: Title?", "Me", note="n", tags=["t x"], location="5"),
            item("Other", "A/B: Title?", "Me"),
        ])

    def test_markdown_zip(self):
        sources = [self.store.get_source(s["id"]) for s in self.store.sources()]
        with zipfile.ZipFile(io.BytesIO(export.markdown_zip(sources))) as zf:
            [name] = zf.namelist()
            self.assertEqual(name, "A B Title.md")
            md = zf.read(name).decode()
        self.assertIn("# A/B: Title?", md)
        self.assertIn("> Line one\n> Line two", md)
        self.assertIn("#t-x", md)
        self.assertIn("**Note:** n", md)

    def test_csv_round_trip(self):
        data = export.highlights_csv(self.store.search())
        self.assertEqual(next(csv.reader(io.StringIO(data)))[0], "Highlight")
        fresh = Store(today=Clock())
        self.assertEqual(fresh.add_highlights(importers.parse_readwise_csv(data))["added"], 2)
        self.assertEqual(sorted(h["text"] for h in fresh.search()), ["Line one\nLine two", "Other"])


class DigestTests(unittest.TestCase):
    def test_message_escapes_html(self):
        h = {"text": "<script>x</script>", "title": "T", "author": "", "note": "a & b"}
        msg = digest.build_message([h], "2026-10-16", "http://localhost:8080")
        html_part = msg.get_body(("html",)).get_content()
        self.assertIn("&lt;script&gt;", html_part)
        self.assertNotIn("<script>", html_part)
        self.assertIn("a &amp; b", html_part)
        self.assertIn("http://localhost:8080", msg.get_body(("plain",)).get_content())

    def test_requires_smtp_config(self):
        store = Store(today=Clock())
        store.add_highlights([item("A")])
        with unittest.mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                digest.send_digest(store)


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Store(today=Clock())
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.store))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as res:
                return res.status, res.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_flow(self):
        content = (SAMPLE / "My Clippings.txt").read_text(encoding="utf-8")
        status, body = self.call("POST", "/api/import", {"content": content, "filename": "My Clippings.txt"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["format"], "kindle")

        status, body = self.call("POST", "/api/highlights", {"text": "Manual", "title": "Mine", "tags": "a, b"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", "/api/highlights", {"text": "Manual", "title": "Mine"})[0], 409)

        status, body = self.call("GET", "/api/review")
        items = json.loads(body)["items"]
        self.assertTrue(items)
        status, body = self.call("POST", f"/api/review/{items[0]['id']}", {"action": "soon"})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("POST", f"/api/review/{items[0]['id']}", {"action": "bad"})[0], 400)

        status, body = self.call("GET", "/api/highlights?q=manual")
        [h] = json.loads(body)
        self.assertEqual(h["tags"], ["a", "b"])
        status, body = self.call("PATCH", f"/api/highlights/{h['id']}", {"favorite": True})
        self.assertTrue(json.loads(body)["favorite"])
        self.assertEqual(self.call("DELETE", f"/api/highlights/{h['id']}")[0], 200)
        self.assertEqual(self.call("DELETE", f"/api/highlights/{h['id']}")[0], 404)

        self.assertEqual(self.call("PUT", "/api/settings", {"daily_count": 0})[0], 400)
        self.assertEqual(self.call("GET", "/api/export/csv")[0], 200)
        self.assertEqual(self.call("GET", "/api/export/markdown")[0], 200)

    def test_bad_requests(self):
        self.assertEqual(self.call("POST", "/api/import", {"content": ""})[0], 400)
        self.assertEqual(self.call("GET", "/api/nope")[0], 404)
        status, _ = self.call("POST", "/api/import", None)
        self.assertEqual(status, 400)

    def test_static_and_traversal(self):
        status, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Margin</title>", body)
        self.assertEqual(self.call("GET", "/../db.py")[0], 404)
        self.assertEqual(self.call("GET", "/%2e%2e/db.py")[0], 404)





def make_fake_apple_books(root: Path):
    """Minimal copies of Apple Books' two databases, using the real table and column names."""
    (root / "AEAnnotation").mkdir(parents=True)
    (root / "BKLibrary").mkdir(parents=True)
    import sqlite3
    lib = sqlite3.connect(root / "BKLibrary" / "BKLibrary-1-091020131601.sqlite")
    lib.executescript("""
        CREATE TABLE ZBKLIBRARYASSET (Z_PK INTEGER PRIMARY KEY, ZASSETID TEXT, ZTITLE TEXT, ZAUTHOR TEXT);
        INSERT INTO ZBKLIBRARYASSET (ZASSETID, ZTITLE, ZAUTHOR) VALUES
            ('BOOK1', 'Born a Crime', 'Trevor Noah'),
            ('BOOK2', 'Thinking, Fast and Slow', 'Daniel Kahneman');
    """)
    lib.commit()
    lib.close()
    ann = sqlite3.connect(root / "AEAnnotation" / "AEAnnotation_v10312011_1727_local.sqlite")
    ann.executescript("""
        CREATE TABLE ZAEANNOTATION (
            Z_PK INTEGER PRIMARY KEY, ZANNOTATIONASSETID TEXT, ZANNOTATIONSELECTEDTEXT TEXT,
            ZANNOTATIONNOTE TEXT, ZANNOTATIONCREATIONDATE TIMESTAMP, ZANNOTATIONDELETED INTEGER,
            ZPLLOCATIONRANGESTART INTEGER);
        INSERT INTO ZAEANNOTATION (ZANNOTATIONASSETID, ZANNOTATIONSELECTEDTEXT, ZANNOTATIONNOTE,
                                   ZANNOTATIONCREATIONDATE, ZANNOTATIONDELETED, ZPLLOCATIONRANGESTART) VALUES
            ('BOOK1', 'Language, even more than colour, defines who you are to people.', 'Great line', 750000000, 0, 20),
            ('BOOK1', 'We tell people to follow their dreams.', NULL, 749000000, 0, 10),
            ('BOOK1', 'This one was deleted', NULL, 749000000, 1, 30),
            ('BOOK1', NULL, NULL, 749000000, 0, 40),
            ('BOOK2', 'Nothing in life is as important as you think it is, while you are thinking about it.', NULL, 760000000, 0, 5),
            ('UNKNOWN', 'Highlight from a book no longer in the library', NULL, NULL, 0, 1);
    """)
    ann.commit()
    ann.close()


class AppleBooksTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "Documents"
        make_fake_apple_books(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_highlights_in_order(self):
        from margin import apple_books
        items = apple_books.read_highlights(*apple_books.find_databases(self.root))
        texts = [i["text"] for i in items]
        self.assertEqual(len(items), 4)  # deleted and empty rows skipped
        self.assertNotIn("This one was deleted", texts)
        crime = [i for i in items if i["title"] == "Born a Crime"]
        self.assertEqual(crime[0]["text"], "We tell people to follow their dreams.")  # reading order
        self.assertEqual(crime[1]["note"], "Great line")
        self.assertEqual(crime[1]["author"], "Trevor Noah")
        self.assertTrue(crime[1]["highlighted_at"].startswith("2024-"))
        self.assertIn("Unknown book", {i["title"] for i in items})

    def test_missing_data_gives_clear_error(self):
        from margin import apple_books
        with self.assertRaises(apple_books.AppleBooksError) as ctx:
            apple_books.find_databases(Path(self.tmp.name) / "nothing")
        self.assertIn("Open Books", str(ctx.exception))

    def test_permission_denied_is_reported_as_such(self):
        from margin import apple_books
        with unittest.mock.patch("os.listdir", side_effect=PermissionError("Operation not permitted")):
            with self.assertRaises(apple_books.AppleBooksError) as ctx:
                apple_books.find_databases(self.root)
        self.assertIn("Full Disk Access", str(ctx.exception))

    def test_cli_import_is_idempotent(self):
        import contextlib
        from margin.__main__ import main
        from margin import apple_books
        ann, lib = apple_books.find_databases(self.root)
        db = Path(self.tmp.name) / "m.db"
        args = ["--db", str(db), "import-apple-books", "--annotations", str(ann), "--library", str(lib)]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(main(args), 0)
            self.assertEqual(main(args), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual(lines[0], "Apple Books: 4 found, 4 added, 0 already present")
        self.assertEqual(lines[1], "Apple Books: 4 found, 0 added, 4 already present")

    def test_api_reports_missing_books(self):
        from margin import apple_books
        store = Store(today=Clock())
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with unittest.mock.patch.object(apple_books, "CONTAINER", Path(self.tmp.name) / "none"):
                req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/import/apple-books", method="POST")
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(req)
                self.assertEqual(ctx.exception.code, 400)
                self.assertIn("Apple Books", json.loads(ctx.exception.read())["error"])
            with unittest.mock.patch.object(apple_books, "CONTAINER", self.root):
                with urllib.request.urlopen(req) as res:
                    self.assertEqual(json.loads(res.read())["added"], 4)
        finally:
            httpd.shutdown()
            httpd.server_close()



class EmailConfigTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "email.env"

    def tearDown(self):
        self.tmp.cleanup()

    def test_file_never_contains_password_and_env_wins(self):
        digest.write_env_file(self.file, {"MARGIN_SMTP_HOST": "smtp.example.com", "MARGIN_SMTP_USER": "v@example.com",
                                          "MARGIN_EMAIL_TO": "v@example.com", "MARGIN_SMTP_PASSWORD": "secret"})
        self.assertNotIn("secret", self.file.read_text())
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        with unittest.mock.patch.dict("os.environ", {"MARGIN_EMAIL_TO": "other@example.com",
                                                     "MARGIN_SMTP_PASSWORD": "pw"}, clear=True):
            config = digest.load_config(self.file)
        self.assertEqual(config["MARGIN_SMTP_HOST"], "smtp.example.com")
        self.assertEqual(config["MARGIN_EMAIL_TO"], "other@example.com")
        self.assertEqual(config["MARGIN_SMTP_PASSWORD"], "pw")
        self.assertTrue(digest.is_configured(config))

    def test_send_uses_starttls_and_login(self):
        config = {"MARGIN_SMTP_HOST": "smtp.example.com", "MARGIN_SMTP_PORT": "587",
                  "MARGIN_SMTP_USER": "v@example.com", "MARGIN_SMTP_PASSWORD": "pw", "MARGIN_EMAIL_TO": "t@example.com"}
        with unittest.mock.patch("smtplib.SMTP") as smtp:
            digest.send(digest.build_message([], "d"), config)
        server = smtp.return_value.__enter__.return_value
        smtp.assert_called_once_with("smtp.example.com", 587, timeout=30)
        tls = server.starttls.call_args.kwargs["context"]
        self.assertEqual(tls.verify_mode, __import__("ssl").CERT_REQUIRED)  # certificate is checked
        server.login.assert_called_once_with("v@example.com", "pw")
        sent = server.send_message.call_args[0][0]
        self.assertEqual(sent["To"], "t@example.com")
        self.assertTrue(sent["Date"])
        self.assertTrue(sent["Message-ID"].endswith("@example.com>"))

    def test_bad_password_gives_helpful_error(self):
        import smtplib
        config = {"MARGIN_SMTP_HOST": "h", "MARGIN_SMTP_USER": "u", "MARGIN_EMAIL_TO": "t"}
        with unittest.mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"no")
            with self.assertRaises(RuntimeError) as ctx:
                digest.send(digest.build_message([], "d"), config)
        self.assertIn("app password", str(ctx.exception))

    def test_setup_email_without_keychain(self):
        from margin.__main__ import setup_email
        answers = iter(["smtp.example.com", "", "v@example.com", "", "n"])
        with unittest.mock.patch("builtins.input", lambda _: next(answers)), \
             unittest.mock.patch("shutil.which", return_value=None), \
             contextlib_redirect():
            self.assertEqual(setup_email(self.file), 0)
        saved = digest.read_env_file(self.file)
        self.assertEqual(saved["MARGIN_SMTP_PORT"], "587")
        self.assertEqual(saved["MARGIN_EMAIL_TO"], "v@example.com")


def contextlib_redirect():
    import contextlib
    return contextlib.redirect_stdout(io.StringIO())


class DailyJobTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "email.env"
        self.store = Store(today=Clock())
        self.store.add_highlights([item("A")])

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_daily(self):
        from margin.__main__ import daily
        with unittest.mock.patch.dict("os.environ", {}, clear=True), \
             unittest.mock.patch("sys.platform", "linux"):
            return daily(self.store, self.file)

    def test_skips_email_when_not_set_up(self):
        self.assertEqual(self.run_daily(), 0)

    def test_sends_email_when_set_up(self):
        digest.write_env_file(self.file, {"MARGIN_SMTP_HOST": "h", "MARGIN_SMTP_PORT": "465", "MARGIN_EMAIL_TO": "t"})
        with unittest.mock.patch("smtplib.SMTP_SSL") as smtp:
            self.assertEqual(self.run_daily(), 0)
        smtp.return_value.__enter__.return_value.send_message.assert_called_once()
        self.assertTrue(smtp.call_args.kwargs["context"].check_hostname)

    def test_reports_failure(self):
        digest.write_env_file(self.file, {"MARGIN_SMTP_HOST": "h", "MARGIN_EMAIL_TO": "t"})
        with unittest.mock.patch("smtplib.SMTP", side_effect=OSError("no route")), self.assertLogs(level="ERROR"):
            self.assertEqual(self.run_daily(), 1)


class MacOSTests(unittest.TestCase):
    def test_plists(self):
        import plistlib
        from margin import macos
        plists = macos.build_plists("/usr/local/bin/python3", "/Users/v/.margin/margin.db", 8080, 6, 30,
                                    Path("/Users/v/.margin/logs"))
        server, daily_job = plists[macos.SERVER_LABEL], plists[macos.DAILY_LABEL]
        self.assertEqual(server["ProgramArguments"][-3:], ["serve", "--port", "8080"])
        self.assertTrue(server["RunAtLoad"] and server["KeepAlive"])
        self.assertEqual(daily_job["ProgramArguments"][-1], "daily")
        self.assertEqual(daily_job["StartCalendarInterval"], {"Hour": 6, "Minute": 30})
        self.assertIn("--db", daily_job["ProgramArguments"])
        self.assertTrue((Path(server["WorkingDirectory"]) / "margin" / "__main__.py").exists())
        for p in plists.values():
            plistlib.loads(plistlib.dumps(p))  # valid plist

    def test_parse_time(self):
        from margin import macos
        self.assertEqual(macos.parse_time("06:00"), (6, 0))
        for bad in ("6am", "24:00", "07:60"):
            with self.assertRaises(ValueError):
                macos.parse_time(bad)

    def test_install_refuses_off_mac(self):
        from margin import macos
        with unittest.mock.patch("sys.platform", "linux"), self.assertRaises(RuntimeError):
            macos.install("/tmp/x.db", 8080, "06:00")


if __name__ == "__main__":
    unittest.main()
