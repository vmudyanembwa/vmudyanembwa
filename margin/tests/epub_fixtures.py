"""Build small EPUB files in memory for tests (and the sample book in sample/)."""

import io
import struct
import zipfile
import zlib


def tiny_png(rgb=(201, 162, 39), size=(60, 90)) -> bytes:
    """A solid-colour PNG, written by hand so tests need no image library."""
    w, h = size
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


XHTML = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title}</title><style>p {{ color: red }}</style><script>alert('head')</script></head>
<body>{body}</body></html>"""

CHAPTERS = [
    ("Book One", """
<section epub:type="chapter" style="font-size:40px">
<h1 id="one">Book One</h1>
<p class="first" onclick="alert('x')">From my grandfather Verus I learned good morals and the government of my temper.</p>
<p>Begin the morning by saying to thyself, I shall meet with the busy-body, the ungrateful, arrogant,
deceitful, envious, unsocial.</p>
<script>document.body.innerHTML = 'pwned'</script>
<p>See <a href="chapter2.xhtml#two">Book Two</a> or <a href="https://en.wikipedia.org/wiki/Meditations">more</a>
or <a href="javascript:alert(1)">bad link</a>.</p>
<p><img src="../images/plate.png" alt="A plate" onerror="alert(1)"/></p>
</section>"""),
    ("Book Two", """
<h1 id="two">Book Two</h1>
<p>Do not act as if thou wert going to live ten thousand years. Death hangs over thee.
While thou livest, while it is in thy power, be good.</p>
<p>The happiness of your life depends upon the quality of your thoughts.</p>
<iframe src="https://evil.example"></iframe>
<table><tr><td colspan="2" style="x">Wide</td><td width="9">Cell</td></tr></table>"""),
    ("Book Three", """
<h1>Book Three</h1>
<p>Waste no more time arguing about what a good man should be. Be one.</p>
<p>Very little is needed to make a happy life; it is all within yourself, in your way of thinking.</p>"""),
]


def make_epub(version: int = 3, drm: str = "", title: str = "Meditations",
              author: str = "Marcus Aurelius", chapters=CHAPTERS, clean: bool = False,
              cover_svg: str = "", subjects=()) -> bytes:
    """clean=True leaves out the hostile test content (scripts, an SVG with script), for the sample book."""
    page = XHTML if not clean else XHTML.replace("<style>p {{ color: red }}</style><script>alert('head')</script>", "")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""")
        if drm == "adobe":
            z.writestr("META-INF/encryption.xml", """<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>
<enc:EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
<enc:CipherData><enc:CipherReference URI="OEBPS/text/chapter1.xhtml"/></enc:CipherData></enc:EncryptedData></encryption>""")
        elif drm == "fonts-only":
            z.writestr("META-INF/encryption.xml", """<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
 xmlns:enc="http://www.w3.org/2001/04/xmlenc#"><enc:EncryptedData>
<enc:EncryptionMethod Algorithm="http://www.idpf.org/2008/embedding"/>
<enc:CipherData><enc:CipherReference URI="OEBPS/fonts/a.otf"/></enc:CipherData></enc:EncryptedData></encryption>""")
        elif drm == "apple":
            z.writestr("META-INF/encryption.xml", "<encryption/>")
            z.writestr("META-INF/sinf.xml", "<fairplay/>")

        items, spine, nav_points, nav_lis = [], [], [], []
        for i, (ch_title, body) in enumerate(chapters, 1):
            z.writestr(f"OEBPS/text/chapter{i}.xhtml", page.format(title=ch_title, body=body))
            items.append(f'<item id="c{i}" href="text/chapter{i}.xhtml" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i}"/>')
            nav_lis.append(f'<li><a href="text/chapter{i}.xhtml">{ch_title}</a></li>')
            nav_points.append(f'<navPoint id="n{i}"><navLabel><text>{ch_title}</text></navLabel>'
                              f'<content src="text/chapter{i}.xhtml"/></navPoint>')
        # A cover page wrapped in SVG, as many publishers do; it's first in reading order.
        cover_file, cover_type = ("cover.svg", "image/svg+xml") if cover_svg else ("cover.png", "image/png")
        z.writestr("OEBPS/text/cover.xhtml", page.format(title="Cover", body=(
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 60 90">'
            f'<image width="60" height="90" xlink:href="../images/{cover_file}"/>'
            + ("" if clean else "<script>alert(2)</script>") + "</svg>")))
        z.writestr(f"OEBPS/images/{cover_file}", cover_svg or tiny_png())
        z.writestr("OEBPS/images/plate.png", tiny_png((60, 90, 140), (120, 80)))
        items += [
            '<item id="cover" href="text/cover.xhtml" media-type="application/xhtml+xml"/>',
            '<item id="plate" href="images/plate.png" media-type="image/png"/>',
        ]
        if not clean:
            z.writestr("OEBPS/images/evil.svg", '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(3)</script></svg>')
            items.append('<item id="evil" href="images/evil.svg" media-type="image/svg+xml"/>')
        if version == 3:
            items.append(f'<item id="cover-img" href="images/{cover_file}" media-type="{cover_type}" properties="cover-image"/>')
            items.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
            nested = nav_lis[:]
            if not clean:
                nested[0] = nested[0].replace("</li>", '<ol><li><a href="text/chapter1.xhtml#one">Of my family</a></li></ol></li>')
            z.writestr("OEBPS/nav.xhtml", XHTML.format(title="Contents", body=(
                '<nav epub:type="toc"><h1>Contents</h1><ol>' + "".join(nested) + "</ol></nav>")))
            meta_cover, toc_attr = "", ""
        else:
            items.append(f'<item id="cover-img" href="images/{cover_file}" media-type="{cover_type}"/>')
            items.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')
            z.writestr("OEBPS/toc.ncx", '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
                       + "".join(nav_points) + "</navMap></ncx>")
            meta_cover, toc_attr = '<meta name="cover" content="cover-img"/>', ' toc="ncx"'
        z.writestr("OEBPS/content.opf", f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="{version}.0" unique-identifier="id">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="id">urn:uuid:margin-test-{title}</dc:identifier>
<dc:title>{title}</dc:title><dc:creator>{author}</dc:creator><dc:language>en</dc:language>{meta_cover}{"".join(f"<dc:subject>{x}</dc:subject>" for x in subjects)}
</metadata>
<manifest>{''.join(items)}</manifest>
<spine{toc_attr}><itemref idref="cover"/>{''.join(spine)}</spine>
</package>""")
    return buf.getvalue()
