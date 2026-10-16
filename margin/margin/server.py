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

from . import apple_books, export, importers

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
MAX_BODY = 50 * 1024 * 1024  # Kindle clipping files from years of reading can be large


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"expected a number, got {value!r}")


def make_handler(store):
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
        result = store.add_highlights([{**item, "tags": tags}])
        if not result["added"]:
            raise ApiError(HTTPStatus.CONFLICT, "that highlight is already in your library")
        return result

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
                        result = fn(self, query, *match.groups())
                    except ApiError as e:
                        return self._send_json({"error": str(e)}, e.status)
                    except Exception:
                        log.exception("error handling %s %s", method, self.path)
                        return self._send_json({"error": "internal error"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    if isinstance(result, Download):
                        return self._send(result.data, result.content_type,
                                          {"Content-Disposition": f'attachment; filename="{result.filename}"'})
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
    def __init__(self, data: bytes, content_type: str, filename: str):
        self.data, self.content_type, self.filename = data, content_type, filename


def serve(store, host: str = "127.0.0.1", port: int = 8080):
    httpd = ThreadingHTTPServer((host, port), make_handler(store))
    log.info("Margin running at http://%s:%d", host, port)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
