"""Export highlights to Markdown (one file per source, Obsidian-friendly) or a single CSV."""

import csv
import io
import re
import zipfile


def source_markdown(source: dict) -> str:
    lines = [f"# {source['title']}", ""]
    if source.get("author"):
        lines.append(f"- Author: {source['author']}")
    if source.get("url"):
        lines.append(f"- URL: {source['url']}")
    lines += [f"- Highlights: {len(source['highlights'])}", "", "## Highlights", ""]
    for h in source["highlights"]:
        quote = "\n".join(f"> {line}" if line else ">" for line in h["text"].splitlines())
        lines.append(quote)
        meta = []
        if h.get("location"):
            meta.append(f"Location {h['location']}")
        if h.get("favorite"):
            meta.append("★ favourite")
        if h.get("tags"):
            meta.append(" ".join(f"#{t.replace(' ', '-')}" for t in h["tags"]))
        if meta:
            lines.append(f"\n{' · '.join(meta)}")
        if h.get("note"):
            lines.append(f"\n**Note:** {h['note']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\s]+', " ", title).strip(" .")
    return (name[:100] or "Untitled") + ".md"


def markdown_zip(sources: list[dict]) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for source in sources:
            name = safe_filename(source["title"])
            stem, n = name[:-3], 2
            while name.lower() in used:
                name = f"{stem} ({n}).md"
                n += 1
            used.add(name.lower())
            zf.writestr(name, source_markdown(source))
    return buf.getvalue()


CSV_COLUMNS = ["Highlight", "Book Title", "Book Author", "Note", "Location", "Highlighted at", "Tags",
               "URL", "Favorite"]


def highlights_csv(highlights: list[dict]) -> str:
    """Same column names the importer reads, so an export round-trips."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    for h in highlights:
        writer.writerow([h["text"], h["title"], h["author"], h["note"], h["location"],
                         h["highlighted_at"], ", ".join(h["tags"]), h["url"], "yes" if h["favorite"] else ""])
    return buf.getvalue()
