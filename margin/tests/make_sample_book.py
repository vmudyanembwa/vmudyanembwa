"""Regenerate sample/Meditations (sample).epub — public-domain text (George Long translation, 1862)."""

from pathlib import Path

from epub_fixtures import make_epub, tiny_png  # noqa: F401  (tiny_png used inside make_epub)

P = lambda *paras: "".join(f"<p>{p}</p>" for p in paras)  # noqa: E731

CHAPTERS = [
    ("Book One", '<h1 id="book1">Book One</h1>' + P(
        "From my grandfather Verus I learned good morals and the government of my temper.",
        "From the reputation and remembrance of my father, modesty and a manly character.",
        "From my mother, piety and beneficence, and abstinence, not only from evil deeds, but even from evil "
        "thoughts; and further, simplicity in my way of living, far removed from the habits of the rich.",
        "From Rusticus I received the impression that my character required improvement and discipline; and from "
        "him I learned not to be led astray to sophistic emulation, nor to writing on speculative matters.",
        "From Apollonius I learned freedom of will and undeviating steadiness of purpose; and to look to nothing "
        "else, not even for a moment, except to reason.",
    ) + P("Continue to <a href=\"chapter2.xhtml#book2\">Book Two</a>.")),
    ("Book Two", '<h1 id="book2">Book Two</h1>' + P(
        "Begin the morning by saying to thyself, I shall meet with the busy-body, the ungrateful, arrogant, "
        "deceitful, envious, unsocial. All these things happen to them by reason of their ignorance of what is "
        "good and evil.",
        "Do not act as if thou wert going to live ten thousand years. Death hangs over thee. While thou livest, "
        "while it is in thy power, be good.",
        "Though thou shouldst be going to live three thousand years, and as many times ten thousand years, still "
        "remember that no man loses any other life than this which he now lives.",
        "Let it make no difference to thee whether thou art cold or warm, if thou art doing thy duty.",
    )),
    ("Book Three", '<h1>Book Three</h1>' + P(
        "Men seek retreats for themselves, houses in the country, sea-shores, and mountains. But it is in thy "
        "power whenever thou shalt choose to retire into thyself. For nowhere either with more quiet or more "
        "freedom from trouble does a man retire than into his own soul.",
        "The universe is change; our life is what our thoughts make it.",
        "Waste no more time arguing about what a good man should be. Be one.",
    )),
    ("Book Four", '<h1>Book Four</h1>' + P(
        "How much trouble he avoids who does not look to see what his neighbour says or does or thinks, but only "
        "to what he does himself, that it may be just and pure.",
        "Very little is needed to make a happy life; it is all within thyself, in thy way of thinking.",
        "Look within. Within is the fountain of good, and it will ever bubble up, if thou wilt ever dig.",
    )),
]

COVER = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 900" width="600" height="900">
<rect width="600" height="900" fill="#1f3b33"/>
<rect x="36" y="36" width="528" height="828" fill="none" stroke="#c9a227" stroke-width="3"/>
<rect x="48" y="48" width="504" height="804" fill="none" stroke="#c9a227" stroke-width="1"/>
<text x="300" y="360" text-anchor="middle" font-family="Georgia, serif" font-size="70" fill="#f3ead2"
 letter-spacing="2">MEDITATIONS</text>
<line x1="220" y1="410" x2="380" y2="410" stroke="#c9a227" stroke-width="2"/>
<circle cx="300" cy="410" r="6" fill="#c9a227"/>
<text x="300" y="480" text-anchor="middle" font-family="Georgia, serif" font-size="34" font-style="italic"
 fill="#d9c58f">Marcus Aurelius</text>
<text x="300" y="790" text-anchor="middle" font-family="Georgia, serif" font-size="22" fill="#a8b5a0"
 letter-spacing="3">TRANSLATED BY GEORGE LONG</text>
</svg>"""

if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "sample" / "Meditations (sample).epub"
    out.write_bytes(make_epub(chapters=CHAPTERS, clean=True, cover_svg=COVER))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
