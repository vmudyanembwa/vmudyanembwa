"""Read EPUB books: metadata, table of contents, and chapters as a safe, simplified node tree.

An EPUB is a zip of XHTML pages plus an OPF "package" file listing them in reading order (the spine).
Book HTML can contain scripts, styles and tracking, so chapters are never passed through as HTML:
they are parsed into a small tree of allowed elements that the frontend rebuilds with DOM APIs.
"""

import hashlib
import io
import posixpath
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


class EpubError(Exception):
    pass


MAX_ENTRY_BYTES = 60 * 1024 * 1024  # guard against zip bombs
IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
NS = {
    "c": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
}
FONT_OBFUSCATION = {"http://www.idpf.org/2008/embedding", "http://ns.adobe.com/pdf/enc#RC"}


# ---------- Opening ----------

def read_bytes(path: Path) -> bytes:
    """Return EPUB bytes from a .epub file, or zip up an unpacked EPUB folder (as Apple Books stores them)."""
    path = Path(path)
    if path.is_dir():
        if not (path / "META-INF" / "container.xml").exists():
            raise EpubError(f"{path.name} isn't an EPUB folder (no META-INF/container.xml)")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            for f in sorted(path.rglob("*")):
                if f.is_file() and f.name != "mimetype":
                    zf.write(f, f.relative_to(path).as_posix())
        return buf.getvalue()
    return path.read_bytes()


def clean_subjects(raw, limit: int = 5) -> list[str]:
    """Turn dc:subject entries into short category suggestions.

    Publishers often write BISAC codes ("FIC019000") or paths ("Fiction / Literary"); codes are
    dropped and paths split, so "Fiction / Literary" suggests both "Fiction" and "Literary".
    """
    seen, out = set(), []
    for entry in raw:
        for part in re.split(r"\s*(?:/|--|;|>)\s*", (entry or "").strip()):
            part = re.sub(r"\s+", " ", part).strip(" .,")
            if not part or len(part) > 40 or re.fullmatch(r"[A-Z]{2,4}\d{3,}|[\d.\s]+", part):
                continue
            if part.lower() in ("general", "fiction & related items"):
                continue
            if part.lower() not in seen:
                seen.add(part.lower())
                out.append(part[0].upper() + part[1:])
    return out[:limit]


def content_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


class Book:
    """An open EPUB. Use as a context manager or call close()."""

    def __init__(self, source):
        try:
            self.zip = zipfile.ZipFile(source if not isinstance(source, bytes) else io.BytesIO(source))
        except (zipfile.BadZipFile, OSError) as e:
            raise EpubError(f"not a valid EPUB file ({e})")
        self.names = set(self.zip.namelist())
        self._check_drm()
        self._load_package()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.zip.close()

    def read(self, name: str) -> bytes:
        if name not in self.names:
            raise KeyError(name)
        if self.zip.getinfo(name).file_size > MAX_ENTRY_BYTES:
            raise EpubError(f"{name} is too large")
        return self.zip.read(name)

    def _xml(self, name: str) -> ET.Element:
        try:
            return ET.fromstring(self.read(name))
        except (KeyError, ET.ParseError) as e:
            raise EpubError(f"couldn't read {name} ({e})")

    def _check_drm(self):
        if "META-INF/encryption.xml" not in self.names:
            return
        if "META-INF/sinf.xml" in self.names:
            raise EpubError("this book is protected by Apple's DRM, so it can only be read in Apple Books")
        root = self._xml("META-INF/encryption.xml")
        for data in root.iter("{http://www.w3.org/2001/04/xmlenc#}EncryptedData"):
            method = data.find("{http://www.w3.org/2001/04/xmlenc#}EncryptionMethod")
            ref = data.find(".//{http://www.w3.org/2001/04/xmlenc#}CipherReference")
            algorithm = method.get("Algorithm", "") if method is not None else ""
            uri = ref.get("URI", "") if ref is not None else ""
            # Publishers obfuscate embedded fonts; that's fine. Anything else encrypted means DRM.
            if algorithm in FONT_OBFUSCATION or re.search(r"\.(otf|ttf|woff2?)$", uri, re.I):
                continue
            raise EpubError("this book is DRM-protected, so it can't be opened outside the store's own app")

    def _load_package(self):
        container = self._xml("META-INF/container.xml")
        rootfile = container.find(".//c:rootfile", NS)
        if rootfile is None or not rootfile.get("full-path"):
            raise EpubError("EPUB has no package file")
        self.opf_path = rootfile.get("full-path")
        self.opf_dir = posixpath.dirname(self.opf_path)
        opf = self._xml(self.opf_path)

        md = opf.find("opf:metadata", NS)
        text = lambda tag: (md.findtext(tag, default="", namespaces=NS) or "").strip() if md is not None else ""  # noqa: E731
        self.title = text("dc:title") or "Untitled"
        creators = [c.text.strip() for c in md.findall("dc:creator", NS) if c.text] if md is not None else []
        self.author = ", ".join(creators)
        self.language = text("dc:language")
        self.subjects = clean_subjects(e.text for e in md.findall("dc:subject", NS)) if md is not None else []

        self.manifest = {}  # id -> {href(path in zip), type, properties}
        for item in opf.findall("opf:manifest/opf:item", NS):
            href = self.resolve(self.opf_dir, item.get("href", ""))
            self.manifest[item.get("id")] = {"href": href, "type": item.get("media-type", ""),
                                             "properties": item.get("properties", "")}
        self.spine = []
        for ref in opf.findall("opf:spine/opf:itemref", NS):
            item = self.manifest.get(ref.get("idref"))
            if item and item["href"] in self.names and "html" in item["type"]:
                self.spine.append(item["href"])
        if not self.spine:
            raise EpubError("EPUB has no readable chapters")
        self.spine_index = {href: i for i, href in enumerate(self.spine)}
        self.cover = self._find_cover(opf)
        self.toc = self._load_toc(opf)
        titles = {}
        for entry in self.toc:
            titles.setdefault(entry["chapter"], entry["title"])
        self.chapter_titles = [titles.get(i, "") or ("Cover" if "cover" in href.lower() else "")
                               for i, href in enumerate(self.spine)]

    @staticmethod
    def resolve(base_dir: str, href: str) -> str:
        path = unquote(urlsplit(href).path)
        return posixpath.normpath(posixpath.join(base_dir, path)) if path else ""

    def _find_cover(self, opf) -> str:
        for item in self.manifest.values():
            if "cover-image" in item["properties"].split() and item["href"] in self.names:
                return item["href"]
        meta = opf.find("opf:metadata/opf:meta[@name='cover']", NS)
        if meta is not None and meta.get("content") in self.manifest:
            item = self.manifest[meta.get("content")]
            if item["type"] in IMAGE_TYPES and item["href"] in self.names:
                return item["href"]
        for item_id, item in self.manifest.items():
            if "cover" in (item_id + item["href"]).lower() and item["type"] in IMAGE_TYPES and item["href"] in self.names:
                return item["href"]
        return ""

    # ---------- Table of contents ----------

    def _load_toc(self, opf) -> list[dict]:
        nav = next((i for i in self.manifest.values() if "nav" in i["properties"].split()), None)
        if nav and nav["href"] in self.names:
            toc = self._toc_from_nav(nav["href"])
            if toc:
                return toc
        spine_el = opf.find("opf:spine", NS)
        ncx = self.manifest.get(spine_el.get("toc")) if spine_el is not None else None
        ncx = ncx or next((i for i in self.manifest.values() if i["type"] == "application/x-dtbncx+xml"), None)
        if ncx and ncx["href"] in self.names:
            return self._toc_from_ncx(ncx["href"])
        return []

    def _toc_entry(self, base_dir: str, href: str, title: str, level: int) -> dict | None:
        path = self.resolve(base_dir, href)
        if path not in self.spine_index or not title.strip():
            return None
        return {"title": re.sub(r"\s+", " ", title).strip(), "chapter": self.spine_index[path],
                "anchor": urlsplit(href).fragment, "level": level}

    def _toc_from_nav(self, href: str) -> list[dict]:
        try:
            root = ET.fromstring(self.read(href))
        except ET.ParseError:
            return []
        base = posixpath.dirname(href)
        toc_nav = None
        for nav in root.iter(f"{{{NS['xhtml']}}}nav"):
            if nav.get(f"{{{NS['epub']}}}type") == "toc":
                toc_nav = nav
                break
        if toc_nav is None:
            return []
        out = []

        def walk(ol, level):
            for li in ol.findall(f"{{{NS['xhtml']}}}li"):
                a = li.find(f"{{{NS['xhtml']}}}a")
                if a is not None and a.get("href"):
                    entry = self._toc_entry(base, a.get("href"), "".join(a.itertext()), level)
                    if entry:
                        out.append(entry)
                sub = li.find(f"{{{NS['xhtml']}}}ol")
                if sub is not None:
                    walk(sub, level + 1)

        ol = toc_nav.find(f"{{{NS['xhtml']}}}ol")
        if ol is not None:
            walk(ol, 0)
        return out

    def _toc_from_ncx(self, href: str) -> list[dict]:
        root = self._xml(href)
        base = posixpath.dirname(href)
        out = []

        def walk(parent, level):
            for point in parent.findall("ncx:navPoint", NS):
                label = point.findtext("ncx:navLabel/ncx:text", default="", namespaces=NS)
                content = point.find("ncx:content", NS)
                if content is not None:
                    entry = self._toc_entry(base, content.get("src", ""), label, level)
                    if entry:
                        out.append(entry)
                walk(point, level + 1)

        nav_map = root.find("ncx:navMap", NS)
        if nav_map is not None:
            walk(nav_map, 0)
        return out

    # ---------- Content ----------

    def chapter(self, index: int) -> list:
        """Sanitised node tree for one spine item."""
        if not 0 <= index < len(self.spine):
            raise IndexError(index)
        href = self.spine[index]
        raw = self.read(href).decode("utf-8", errors="replace")
        parser = _Sanitiser(self, posixpath.dirname(href))
        parser.feed(raw)
        parser.close()
        return parser.root["c"]

    def image(self, path: str) -> tuple[bytes, str]:
        """Bytes and type of an image inside the book. Only images are ever served."""
        item_type = next((i["type"] for i in self.manifest.values() if i["href"] == path), "")
        if item_type not in IMAGE_TYPES:
            raise KeyError(path)
        return self.read(path), item_type

    def chapter_lengths(self) -> list[int]:
        """Characters of readable text per chapter; used to estimate pages left in the book."""
        def count(nodes):
            return sum(len(n) if isinstance(n, str) else count(n["c"]) for n in nodes)
        lengths = []
        for i in range(len(self.spine)):
            try:
                lengths.append(count(self.chapter(i)))
            except (KeyError, EpubError):
                lengths.append(0)
        return lengths

    def chapter_text(self, index: int) -> str:
        """Plain text of a chapter, with a line break between blocks so words never run together."""
        def walk(nodes, out):
            for n in nodes:
                if isinstance(n, str):
                    out.append(n)
                else:
                    walk(n["c"], out)
                    if n["t"] not in INLINE:
                        out.append("\n")
            return out
        return "".join(walk(self.chapter(index), []))

    def search(self, query: str, limit: int = 200) -> list[dict]:
        """Matches for `query` (case-insensitive, whitespace-tolerant) across the book, in reading order.

        `hit` numbers the matches within a chapter, so the reader can find the same one in the page."""
        letters = re.sub(r"\s+", "", query)
        if not letters:
            return []
        pattern = re.compile(r"\s*".join(re.escape(c) for c in letters), re.IGNORECASE)
        results = []
        for i in range(len(self.spine)):
            try:
                text = self.chapter_text(i)
            except (KeyError, EpubError):
                continue
            for hit, m in enumerate(pattern.finditer(text)):
                # A little context either side, trimmed to whole words.
                start, end = max(0, m.start() - 60), m.end() + 80
                before = text[start:m.start()]
                after = text[m.end():end]
                if start > 0:
                    before = before.split(None, 1)[-1] if " " in before.strip() else before
                if end < len(text):
                    after = after.rsplit(None, 1)[0] if " " in after.strip() else after
                before, after = " ".join(before.split()), " ".join(after.split())
                if m.start() > 0 and text[m.start() - 1].isspace():
                    before += " "
                if m.end() < len(text) and text[m.end()].isspace():
                    after = " " + after
                results.append({"chapter": i, "hit": hit, "before": before, "match": " ".join(m.group().split()),
                                "after": after})
                if len(results) >= limit:
                    return results
        return results

    def info(self) -> dict:
        return {"title": self.title, "author": self.author, "language": self.language, "cover": self.cover,
                "chapters": len(self.spine), "toc": self.toc, "chapter_titles": self.chapter_titles,
                "chapter_chars": self.chapter_lengths(), "subjects": self.subjects}


# ---------- Sanitiser ----------

KEEP = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "ul", "ol", "li", "em", "strong", "i", "b", "u",
    "s", "sub", "sup", "small", "br", "hr", "a", "img", "pre", "code", "figure", "figcaption", "table",
    "thead", "tbody", "tr", "th", "td", "dl", "dt", "dd", "cite", "q", "abbr", "span", "div",
}
RENAME = {  # structural tags we flatten to plain containers
    "section": "div", "article": "div", "aside": "div", "header": "div", "footer": "div", "main": "div",
    "nav": "div", "center": "div", "big": "span", "font": "span", "tt": "code", "var": "em", "dfn": "em",
    "mark": "span", "time": "span", "address": "div", "caption": "div",
}
DROP_WITH_CONTENT = {"script", "style", "head", "title", "iframe", "object", "embed", "noscript", "template",
                     "audio", "video", "canvas", "form", "button", "input", "select", "textarea", "math"}
VOID = {"br", "hr", "img"}
INLINE = {"em", "strong", "i", "b", "u", "s", "sub", "sup", "small", "a", "img", "code", "cite", "q", "abbr", "span"}


class _Sanitiser(HTMLParser):
    """Turns chapter XHTML into nodes: {"t": tag, "a": {attrs}, "c": [children]} or plain strings."""

    def __init__(self, book: Book, base_dir: str):
        super().__init__(convert_charrefs=True)
        self.book, self.base = book, base_dir
        self.root = {"t": "div", "a": {}, "c": []}
        self.stack = [self.root]
        self.skip = 0  # depth inside dropped elements
        self.in_body = False
        self.svg_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "body":
            self.in_body = True
            return
        if not self.in_body:
            return
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "svg":
            self.svg_depth += 1
            return
        if self.svg_depth:
            # Covers are often an <svg><image xlink:href=...>; keep just the image.
            if tag == "image":
                src = attrs.get("xlink:href") or attrs.get("href")
                if src:
                    self._append({"t": "img", "a": {"src": self.book.resolve(self.base, src), "alt": ""}, "c": []})
            return
        tag = RENAME.get(tag, tag)
        if tag not in KEEP:
            tag = "span"
        node = {"t": tag, "a": self._attrs(tag, attrs), "c": []}
        if tag == "img" and not node["a"].get("src"):
            return
        self._append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "body":
            self.in_body = False
            return
        if self.skip:
            if tag in DROP_WITH_CONTENT:
                self.skip -= 1
            return
        if tag == "svg":
            self.svg_depth = max(0, self.svg_depth - 1)
            return
        if self.svg_depth or not self.in_body:
            return
        tag = RENAME.get(tag, tag)
        if tag not in KEEP:
            tag = "span"
        # Close up to the matching open element (tolerates sloppy HTML).
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i]["t"] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.skip or self.svg_depth or not self.in_body or not data:
            return
        parent = self.stack[-1]["c"]
        if parent and isinstance(parent[-1], str):
            parent[-1] += data
        else:
            parent.append(data)

    def _append(self, node):
        self.stack[-1]["c"].append(node)

    def _attrs(self, tag: str, attrs: dict) -> dict:
        out = {}
        if attrs.get("id"):
            out["id"] = attrs["id"]
        if tag == "img":
            src = attrs.get("src", "")
            if src and not urlsplit(src).scheme:
                out["src"] = self.book.resolve(self.base, src)
            out["alt"] = attrs.get("alt", "")
        elif tag == "a" and attrs.get("href"):
            href = attrs["href"].strip()
            parts = urlsplit(href)
            if parts.scheme in ("http", "https", "mailto"):
                out["href"] = href
            elif not parts.scheme:
                path = self.book.resolve(self.base, href) if parts.path else ""
                if not parts.path:
                    out["anchor"] = parts.fragment
                elif path in self.book.spine_index:
                    out["chapter"] = self.book.spine_index[path]
                    out["anchor"] = parts.fragment
        elif tag in ("td", "th"):
            for k in ("colspan", "rowspan"):
                if str(attrs.get(k, "")).isdigit():
                    out[k] = attrs[k]
        return out


# ---------- Library ----------

def add_to_library(store, data: bytes, books_dir: Path) -> tuple[dict, bool]:
    """Validate an EPUB, copy it into the books folder and register it. Safe to re-run: same file → same book."""
    with Book(data) as book:
        info = book.info()
    sha1 = content_hash(data)
    books_dir = Path(books_dir)
    books_dir.mkdir(parents=True, exist_ok=True)
    file = f"{sha1}.epub"
    target = books_dir / file
    if not target.exists():
        tmp = target.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(target)  # atomic, so a crash never leaves a half-written book
    doc_id, created = store.add_document(sha1, file, info)
    return store.get_document(doc_id), created
