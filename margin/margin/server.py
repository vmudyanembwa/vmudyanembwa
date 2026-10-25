"""JSON API + static file server built on the standard library."""

import json
import logging
import mimetypes
import re
from datetime import date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import apple_books, epub, export, importers, pdf

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
mimetypes.add_type("text/javascript", ".mjs")  # PDF.js ships as ES modules; browsers require a JS type
MAX_BODY = 50 * 1024 * 1024  # Kindle clipping files from years of reading can be large
MAX_BOOK = 300 * 1024 * 1024  # illustrated EPUBs can be big


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"expected a number, got {value!r}")


def make_handler(store, books_dir: Path | None = None):
    """Build a request handler bound to one Store. Routes are (method, regex) -> function."""
    routes = []

    def route(method, pattern):
        def deco(fn):
            routes.append((method, re.compile(f"^{pattern}$"), fn))
            return fn
        return deco

    @route("GET", "/api/stats")
    def stats(req, q):
        return store.stats()

    @route("GET", "/api/review")
    def get_review(req, q):
        return {"day": store.today().isoformat(), "items": store.daily_review(), "stats": store.stats()}

    @route("POST", r"/api/review/(\d+)")
    def post_review(req, q, hid):
        action = req.json().get("action")
        if action not in ("soon", "later", "someday", "discard"):
            raise ApiError(HTTPStatus.BAD_REQUEST, "action must be soon, later, someday or discard")
        result = store.review_action(int(hid), action)
        if result is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "highlight not found")
        return result

    @route("GET", "/api/sources")
    def list_sources(req, q):
        return store.sources()

    @route("GET", r"/api/sources/(\d+)")
    def get_source(req, q, sid):
        source = store.get_source(int(sid))
        if not source:
            raise ApiError(HTTPStatus.NOT_FOUND, "source not found")
        return source

    @route("GET", "/api/highlights")
    def search(req, q):
        return store.search(
            q=q.get("q", ""), tag=q.get("tag", ""),
            source_id=_int(q["source"]) if q.get("source") else None,
            favorite=q.get("favorite") == "1",
        )

    @route("POST", "/api/highlights")
    def create(req, q):
        body = req.json()
        if not str(body.get("text", "")).strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "text is required")
        tags = body.get("tags", [])
        if isinstance(tags, str):
            tags = tags.split(",")
        item = {k: str(body.get(k, "")) for k in ("text", "title", "author", "note", "url", "location")}
        if body.get("doc_id") is not None:
            # Highlight made in the reader: file it under the book and remember the chapter.
            doc = store.get_document(_int(body["doc_id"]))
            if not doc:
                raise ApiError(HTTPStatus.NOT_FOUND, "book not found")
            chapter = _int(body.get("chapter", 0))
            if not 0 <= chapter < doc["chapters"]:
                raise ApiError(HTTPStatus.BAD_REQUEST, "chapter out of range")
            title = doc["chapter_titles"][chapter] if chapter < len(doc["chapter_titles"]) else ""
            where = f"p. {chapter + 1}" if doc["kind"] == "pdf" else f"Ch. {chapter + 1}"
            item.update(title=doc["title"], author=doc["author"], doc_id=doc["id"], chapter=chapter,
                        location=where + (f" · {title}" if title else ""),
                        highlighted_at=date.today().isoformat())
        highlight = store.add_highlight({**item, "tags": tags})
        if not highlight:
            raise ApiError(HTTPStatus.CONFLICT, "that highlight is already in your library")
        return {"added": 1, "skipped": 0, "highlight": highlight}

    @route("PATCH", r"/api/highlights/(\d+)")
    def update(req, q, hid):
        try:
            result = store.update_highlight(int(hid), req.json())
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
        if result is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "highlight not found")
        return result

    @route("DELETE", r"/api/highlights/(\d+)")
    def delete(req, q, hid):
        if not store.delete_highlight(int(hid)):
            raise ApiError(HTTPStatus.NOT_FOUND, "highlight not found")
        return {"deleted": True}

    @route("GET", "/api/tags")
    def tags(req, q):
        return store.tags()

    @route("PUT", "/api/settings")
    def settings(req, q):
        body = req.json()
        if "daily_count" in body:
            n = _int(body["daily_count"])
            if not 1 <= n <= 100:
                raise ApiError(HTTPStatus.BAD_REQUEST, "daily_count must be between 1 and 100")
            store.set_setting("daily_count", str(n))
        return store.stats()

    @route("POST", "/api/import")
    def do_import(req, q):
        body = req.json()
        content = body.get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ApiError(HTTPStatus.BAD_REQUEST, "nothing to import")
        fmt = body.get("format") or importers.guess_format(body.get("filename", ""), content)
        kw = {}
        if fmt == "markdown":
            kw = {"title": body.get("title", ""), "author": body.get("author", "")}
        try:
            items = importers.parse(fmt, content, **kw)
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
        result = store.add_highlights(items)
        return {**result, "format": fmt, "found": len(items)}

    @route("POST", "/api/import/apple-books")
    def import_apple_books(req, q):
        # Reads the Books databases on the machine running Margin, i.e. your Mac.
        try:
            items = apple_books.read_highlights()
        except apple_books.AppleBooksError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
        return {**store.add_highlights(items), "format": "apple_books", "found": len(items)}

    # ---------- Reader ----------

    def book_path(doc: dict) -> Path:
        if books_dir is None:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "the reader isn't configured")
        path = books_dir / doc["file"]
        if not path.exists():
            raise ApiError(HTTPStatus.GONE, "the book file is missing; remove it and import it again")
        return path

    def get_doc(doc_id) -> dict:
        doc = store.get_document(int(doc_id))
        if not doc:
            raise ApiError(HTTPStatus.NOT_FOUND, "book not found")
        return doc

    @route("GET", "/api/docs")
    def list_docs(req, q):
        return store.documents()

    @route("POST", "/api/docs")
    def upload_doc(req, q):
        if books_dir is None:
            raise ApiError(HTTPStatus.SERVICE_UNAVAILABLE, "the reader isn't configured")
        data = req.raw_body(MAX_BOOK)
        try:
            if pdf.is_pdf(data):
                doc, created = pdf.add_to_library(store, data, books_dir, q.get("filename", ""))
            else:
                doc, created = epub.add_to_library(store, data, books_dir)
        except (epub.EpubError, pdf.PdfError) as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"{q.get('filename') or 'That file'}: {e}")
        return {"document": doc, "created": created}

    @route("GET", r"/api/docs/(\d+)")
    def doc_info(req, q, doc_id):
        doc = get_doc(doc_id)
        if doc["kind"] == "epub" and (doc["chapter_chars"] is None or doc["subjects"] is None):
            # Books added before page estimates / subjects existed: read them once and remember.
            try:
                with epub.Book(book_path(doc)) as book:
                    for key, value in (("chapter_chars", book.chapter_lengths), ("subjects", lambda: book.subjects)):
                        if doc[key] is None:
                            doc[key] = value()
                            store.set_document_info(doc["id"], key, doc[key])
            except (ApiError, epub.EpubError):
                pass
        return doc

    @route("PUT", r"/api/docs/(\d+)/categories")
    def doc_categories(req, q, doc_id):
        names = req.json().get("categories")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise ApiError(HTTPStatus.BAD_REQUEST, "categories must be a list of names")
        result = store.set_document_categories(int(doc_id), names)
        if result is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "book not found")
        return {"categories": result}

    @route("GET", "/api/categories")
    def list_categories(req, q):
        return store.categories()

    @route("PATCH", r"/api/categories/(\d+)")
    def rename_category(req, q, category_id):
        try:
            result = store.rename_category(int(category_id), str(req.json().get("name", "")))
        except ValueError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
        if result is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "category not found")
        return result

    @route("DELETE", r"/api/categories/(\d+)")
    def delete_category(req, q, category_id):
        if not store.delete_category(int(category_id)):
            raise ApiError(HTTPStatus.NOT_FOUND, "category not found")
        return {"deleted": True}

    @route("GET", r"/api/docs/(\d+)/bookmarks")
    def list_bookmarks(req, q, doc_id):
        return store.bookmarks(get_doc(doc_id)["id"])

    @route("POST", r"/api/docs/(\d+)/bookmarks")
    def add_bookmark(req, q, doc_id):
        doc = get_doc(doc_id)
        body = req.json()
        chapter = _int(body.get("chapter", 0))
        if not 0 <= chapter < doc["chapters"]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "chapter out of range")
        try:
            position = float(body.get("position", 0))
        except (TypeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "position must be a number")
        return store.add_bookmark(doc["id"], chapter, position, str(body.get("label", "")))

    @route("DELETE", r"/api/bookmarks/(\d+)")
    def delete_bookmark(req, q, bookmark_id):
        if not store.delete_bookmark(int(bookmark_id)):
            raise ApiError(HTTPStatus.NOT_FOUND, "bookmark not found")
        return {"deleted": True}

    @route("GET", r"/api/docs/(\d+)/highlights")
    def doc_highlights(req, q, doc_id):
        return store.document_highlights(get_doc(doc_id)["id"])

    @route("DELETE", r"/api/docs/(\d+)")
    def delete_doc(req, q, doc_id):
        file = store.delete_document(int(doc_id))
        if file is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "book not found")
        if books_dir is not None:
            (books_dir / file).unlink(missing_ok=True)
            (books_dir / f"{file}.cover").unlink(missing_ok=True)
        return {"deleted": True}

    @route("GET", r"/api/docs/(\d+)/chapter/(\d+)")
    def doc_chapter(req, q, doc_id, index):
        doc = get_doc(doc_id)
        index = int(index)
        if index >= doc["chapters"]:
            raise ApiError(HTTPStatus.NOT_FOUND, "no such chapter")
        if doc["kind"] == "pdf":
            return {"index": index, "title": doc["chapter_titles"][index], "nodes": [],
                    "highlights": store.chapter_highlights(doc["id"], index)}
        try:
            with epub.Book(book_path(doc)) as book:
                nodes = book.chapter(index)
        except epub.EpubError as e:
            raise ApiError(HTTPStatus.UNPROCESSABLE_ENTITY, str(e))
        return {"index": index, "title": doc["chapter_titles"][index], "nodes": nodes,
                "highlights": store.chapter_highlights(doc["id"], index)}

    @route("GET", r"/api/docs/(\d+)/image")
    def doc_image(req, q, doc_id):
        doc = get_doc(doc_id)
        if doc["kind"] == "pdf":
            cover = book_path(doc).with_name(f"{doc['file']}.cover")
            data = cover.read_bytes() if cover.exists() else b""
            if not pdf.cover_type(data):
                raise ApiError(HTTPStatus.NOT_FOUND, "no cover yet")
            return Download(data, pdf.cover_type(data), None, {"Cache-Control": "private, max-age=86400"})
        path = q.get("path") or doc["cover"]
        try:
            with epub.Book(book_path(doc)) as book:
                data, ctype = book.image(path)
        except (KeyError, epub.EpubError):
            raise ApiError(HTTPStatus.NOT_FOUND, "image not found")
        # Book images (SVG especially) must never run as a page on this origin.
        return Download(data, ctype, None, {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                                            "Cache-Control": "private, max-age=86400"})

    def get_pdf(doc_id) -> dict:
        doc = get_doc(doc_id)
        if doc["kind"] != "pdf":
            raise ApiError(HTTPStatus.BAD_REQUEST, "not a PDF")
        return doc

    @route("GET", r"/api/docs/(\d+)/file")
    def pdf_file(req, q, doc_id):
        doc = get_pdf(doc_id)
        # Fetched by PDF.js; never let a PDF opened directly run as a page on this origin.
        return Download(book_path(doc).read_bytes(), "application/pdf", None,
                        {"Content-Security-Policy": "sandbox", "Cache-Control": "private, max-age=86400"})

    @route("PUT", r"/api/docs/(\d+)/meta")
    def pdf_meta(req, q, doc_id):
        doc = get_pdf(doc_id)
        try:
            meta = pdf.clean_meta(req.json())
        except pdf.PdfError as e:
            raise ApiError(HTTPStatus.BAD_REQUEST, str(e))
        store.update_document_meta(doc["id"], meta)
        return store.get_document(doc["id"])

    @route("POST", r"/api/docs/(\d+)/cover")
    def pdf_cover(req, q, doc_id):
        doc = get_pdf(doc_id)
        data = req.raw_body(pdf.MAX_COVER_BYTES)
        if not pdf.cover_type(data):
            raise ApiError(HTTPStatus.BAD_REQUEST, "cover must be a PNG or JPEG image")
        book_path(doc).with_name(f"{doc['file']}.cover").write_bytes(data)
        store.set_document_info(doc["id"], "cover", "cover")
        return {"saved": True}

    @route("PUT", r"/api/docs/(\d+)/progress")
    def doc_progress(req, q, doc_id):
        body = req.json()
        doc = get_doc(doc_id)
        chapter = _int(body.get("chapter", 0))
        try:
            position = float(body.get("position", 0))
        except (TypeError, ValueError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "position must be a number")
        if not 0 <= chapter < doc["chapters"]:
            raise ApiError(HTTPStatus.BAD_REQUEST, "chapter out of range")
        store.set_progress(doc["id"], chapter, position)
        return {"saved": True}

    @route("GET", "/api/export/markdown")
    def export_md(req, q):
        sources = [store.get_source(s["id"]) for s in store.sources()]
        return Download(export.markdown_zip(sources), "application/zip", f"margin-{date.today()}.zip")

    @route("GET", "/api/export/csv")
    def export_csv(req, q):
        data = export.highlights_csv(store.search(limit=1_000_000))
        return Download(data.encode("utf-8"), "text/csv; charset=utf-8", f"margin-{date.today()}.csv")

    class Handler(BaseHTTPRequestHandler):
        server_version = "Margin/1.0"

        def log_message(self, fmt, *args):
            log.debug("%s - %s", self.address_string(), fmt % args)

        def raw_body(self, limit: int) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > limit:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"file is larger than {limit // (1024 * 1024)} MB")
            if not length:
                raise ApiError(HTTPStatus.BAD_REQUEST, "no file received")
            return self.rfile.read(length)

        def json(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "upload too large")
            raw = self.rfile.read(length) if length else b"{}"
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "body must be JSON")
            if not isinstance(data, dict):
                raise ApiError(HTTPStatus.BAD_REQUEST, "body must be a JSON object")
            return data

        def _dispatch(self, method):
            url = urlparse(self.path)
            if method == "GET" and not url.path.startswith("/api/"):
                return self._static(url.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            for m, pattern, fn in routes:
                match = pattern.match(url.path)
                if m == method and match:
                    try:
                        # The server answers requests on several threads but shares one SQLite
                        # connection, so API handlers run one at a time (they take milliseconds).
                        with store.lock:
                            result = fn(self, query, *match.groups())
                    except ApiError as e:
                        return self._send_json({"error": str(e)}, e.status)
                    except Exception:
                        log.exception("error handling %s %s", method, self.path)
                        return self._send_json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    if isinstance(result, Download):
                        headers = dict(result.headers)
                        if result.filename:
                            headers["Content-Disposition"] = f'attachment; filename="{result.filename}"'
                        return self._send(result.data, result.content_type, headers)
                    return self._send_json(result)
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_GET(self):
            self._dispatch("GET")

        def do_POST(self):
            self._dispatch("POST")

        def do_PATCH(self):
            self._dispatch("PATCH")

        def do_PUT(self):
            self._dispatch("PUT")

        def do_DELETE(self):
            self._dispatch("DELETE")

        def _static(self, path):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC_DIR / rel).resolve()
            if not target.is_relative_to(STATIC_DIR.resolve()) or not target.is_file():
                return self._send(b"Not found", "text/plain", status=HTTPStatus.NOT_FOUND)
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript", "text/javascript"):
                ctype += "; charset=utf-8"
            self._send(target.read_bytes(), ctype, {"Cache-Control": "no-cache"})

        def _send_json(self, obj, status=HTTPStatus.OK):
            self._send(json.dumps(obj).encode(), "application/json", status=status)

        def _send(self, data: bytes, content_type: str, headers=None, status=HTTPStatus.OK):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

    return Handler


class Download:
    def __init__(self, data: bytes, content_type: str, filename: str | None, headers: dict | None = None):
        self.data, self.content_type, self.filename, self.headers = data, content_type, filename, headers or {}


def serve(store, host: str = "127.0.0.1", port: int = 8080, books_dir: Path | None = None):
    httpd = ThreadingHTTPServer((host, port), make_handler(store, books_dir))
    log.info("Margin running at http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
