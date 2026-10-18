"""Build small, valid PDFs by hand for tests and the sample book (no PDF library needed)."""


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap(text: str, width: int = 70) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def make_pdf(pages: list[tuple[str, list[str]]], title: str = "Meditations",
             author: str = "Marcus Aurelius", outline: bool = True) -> bytes:
    """pages = [(heading, [paragraph, ...]), ...]. Headings become outline entries."""
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)

    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>")
    bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    pages_ref = len(objects) + 1 + 2 * len(pages)  # reserved: page/content pairs come first
    page_ids = []
    for heading, paragraphs in pages:
        ops = ["BT", f"/F2 20 Tf 72 740 Td ({_esc(heading)}) Tj", "/F1 12 Tf 0 -36 Td 16 TL"]
        for para in paragraphs:
            for line in _wrap(para):
                ops.append(f"({_esc(line)}) Tj T*")
            ops.append("T*")
        ops.append("ET")
        stream = "\n".join(ops)
        content = add(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent {pages_ref} 0 R /MediaBox [0 0 612 792] "
                            f"/Resources << /Font << /F1 {font} 0 R /F2 {bold} 0 R >> >> /Contents {content} 0 R >>"))
    assert add(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {len(page_ids)} >>") == pages_ref

    outline_ref = ""
    if outline:
        first_item = len(objects) + 2
        items = list(range(first_item, first_item + len(pages)))
        root = add(f"<< /Type /Outlines /First {items[0]} 0 R /Last {items[-1]} 0 R /Count {len(items)} >>")
        for i, (heading, _) in enumerate(pages):
            links = (f" /Prev {items[i - 1]} 0 R" if i else "") + (f" /Next {items[i + 1]} 0 R" if i < len(items) - 1 else "")
            add(f"<< /Title ({_esc(heading)}) /Parent {root} 0 R /Dest [{page_ids[i]} 0 R /XYZ 0 792 0]{links} >>")
        outline_ref = f" /Outlines {root} 0 R /PageMode /UseOutlines"
    catalog = add(f"<< /Type /Catalog /Pages {pages_ref} 0 R{outline_ref} >>")
    info = add(f"<< /Title ({_esc(title)}) /Author ({_esc(author)}) /Producer (Margin tests) >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


SAMPLE_PAGES = [
    ("Book One", ["From my grandfather Verus I learned good morals and the government of my temper.",
                  "From the reputation and remembrance of my father, modesty and a manly character.",
                  "From my mother, piety and beneficence, and abstinence, not only from evil deeds, but even "
                  "from evil thoughts; and further, simplicity in my way of living, far removed from the habits of the rich."]),
    ("Book Two", ["Begin the morning by saying to thyself, I shall meet with the busy-body, the ungrateful, arrogant, "
                  "deceitful, envious, unsocial.",
                  "Do not act as if thou wert going to live ten thousand years. Death hangs over thee. While thou "
                  "livest, while it is in thy power, be good."]),
    ("Book Three", ["Men seek retreats for themselves, houses in the country, sea-shores, and mountains. But it is in "
                    "thy power whenever thou shalt choose to retire into thyself.",
                    "Waste no more time arguing about what a good man should be. Be one."]),
    ("Book Four", ["Very little is needed to make a happy life; it is all within thyself, in thy way of thinking.",
                   "Look within. Within is the fountain of good, and it will ever bubble up, if thou wilt ever dig."]),
]
