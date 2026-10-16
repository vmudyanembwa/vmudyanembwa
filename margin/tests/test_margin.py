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
        self.assertEqual(items[-1]["author"], "David Thomas;Andrew Hunt")

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




if __name__ == "__main__":
    unittest.main()
