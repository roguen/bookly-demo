"""Deterministic book covers, drawn as inline SVG.

A demo that shows a customer's orders needs cover art, and every ordinary way
to get it is a liability here: image files bloat the clone, downloads break
the no-network rule, and stock art raises a licensing question nobody wants
to answer in an interview. So the cover is *computed* — hashed from the title
and author, which means the same book always gets the same jacket, on every
machine, forever, with no files and no requests.

The palette is deliberately outside the interface's provenance vocabulary.
Purple means deterministic and grey means the model everywhere else in this
build; a cover is neither, so it draws from jewel and earth tones that cannot
be mistaken for either side of the boundary.

An `covers/<order_id>.svg` file, if one exists, wins. Real art should always
be able to beat generated art.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

WIDTH = 300
HEIGHT = 450  # 2:3, the proportion of an actual trade paperback
MARGIN = 26

OVERRIDE_DIR = Path(__file__).resolve().parent / "covers"
# Order ids are matched against this before touching the filesystem, so an
# override lookup can never be talked into walking out of the directory.
ORDER_ID_RE = re.compile(r"^BK-\d{4}$")

# Named sets so a profile can pick one (`catalog.cover_palette`) without
# touching this file.
PALETTES = {
    "jacket": [
        {"bg": "#123F3C", "ink": "#F3EFE6", "accent": "#E0A140"},  # forest
        {"bg": "#4B1220", "ink": "#F6EAE6", "accent": "#D8A24A"},  # oxblood
        {"bg": "#1B2A4A", "ink": "#EDF0F7", "accent": "#C9743B"},  # navy
        {"bg": "#3C2A12", "ink": "#F4ECDE", "accent": "#B8935A"},  # umber
        {"bg": "#2A3B1E", "ink": "#EFF2E6", "accent": "#CBA65C"},  # olive
        {"bg": "#5A2415", "ink": "#F6EAE2", "accent": "#D9A05B"},  # sienna
    ]
}
DEFAULT_PALETTE = "jacket"

# System faces only. A webfont would be a network request, and the demo has
# to work on a plane.
FONT_STACK = (
    "system-ui, -apple-system, 'Segoe UI', Roboto, 'Helvetica Neue', "
    "Arial, sans-serif"
)

# Tried largest first; the first size whose wrap fits the box wins.
TITLE_SIZES = (32, 27, 23, 19, 16)
MAX_TITLE_LINES = 5
TITLE_TOP = 132
# The author sits on the bottom margin, mirroring the top. Everything between
# the longest possible title and that line belongs to the motif and nothing
# else, which is what keeps the two from ever colliding.
AUTHOR_Y = HEIGHT - MARGIN
MOTIF_BAND = (TITLE_TOP + (MAX_TITLE_LINES - 1) * int(TITLE_SIZES[0] * 1.22)
              + 8, AUTHOR_Y - 28)
# Rough advance width of the bold sans at 1px, measured against the stack
# above. Only used to choose a line break, so approximate is fine.
CHAR_WIDTH_RATIO = 0.56


def _esc(value: str) -> str:
    """Escape record text for anywhere in the document.

    `escape()` alone leaves quotes intact, which is harmless in element
    content and an attribute break-out inside an attribute. Rather than track
    which is which, quotes are escaped everywhere: `&quot;` renders as a
    quote, and the invariant becomes one sentence — no raw quote or angle
    bracket from the record ever reaches the SVG.
    """
    return escape(value, {'"': "&quot;", "'": "&apos;"})


def _digest(title: str, author: str) -> bytes:
    """The whole source of variation. Not `hash()`, which is salted per
    process and would give a different cover on every run."""
    return hashlib.sha256(("%s|%s" % (title, author)).encode("utf-8")).digest()


def _wrap(text: str, size: int) -> Optional[List[str]]:
    """Greedy wrap at the widest line the box allows. None means it does not
    fit at this size and the caller should try a smaller one."""
    budget = int((WIDTH - MARGIN * 2) / (size * CHAR_WIDTH_RATIO))
    lines: List[str] = []
    for word in text.split():
        if len(word) > budget:
            return None  # one word alone overflows; shrink instead of clipping
        if lines and len(lines[-1]) + 1 + len(word) <= budget:
            lines[-1] = "%s %s" % (lines[-1], word)
        else:
            lines.append(word)
    if len(lines) > MAX_TITLE_LINES:
        return None
    return lines


def _title_block(title: str) -> Tuple[List[str], int]:
    for size in TITLE_SIZES:
        lines = _wrap(title, size)
        if lines is not None:
            return lines, size
    # Nothing fit: set it at the smallest size and let the last line clip.
    return (_wrap(title, TITLE_SIZES[-1]) or [title])[:MAX_TITLE_LINES], (
        TITLE_SIZES[-1]
    )


def _motif(index: int, accent: str) -> str:
    """One restrained geometric element, always in the accent alone.

    Every motif is confined to a band the type never enters: either the
    corner above the title rule, or `MOTIF_BAND`, which sits below the
    longest possible title and above the author line. Nothing here is
    allowed to sit underneath text — a jacket where the author's name is
    fighting a pattern reads as generated, which is the one thing this is
    trying not to look like.
    """
    top, bottom = MOTIF_BAND
    motifs = [
        # concentric arcs breaking off the top-right corner, above the rule
        '<g fill="none" stroke="%s" stroke-width="3" opacity="0.70">'
        '<circle cx="300" cy="0" r="40"/><circle cx="300" cy="0" r="72"/>'
        '<circle cx="300" cy="0" r="104"/></g>' % accent,
        # a pair of rules, long over short
        '<g fill="%s" opacity="0.85"><rect x="26" y="%d" width="248" '
        'height="4"/><rect x="26" y="%d" width="150" height="4"/></g>'
        % (accent, top + 18, top + 32),
        # a single disc, offset toward the edge
        '<circle cx="256" cy="%d" r="56" fill="%s" opacity="0.30"/>'
        % (top + 44, accent),
        # a diagonal band
        '<polygon points="0,%d 300,%d 300,%d 0,%d" fill="%s" opacity="0.38"/>'
        % (top + 56, top - 4, top + 30, bottom - 2, accent),
        # a small grid, lower left
        '<g fill="%s" opacity="0.70">%s</g>'
        % (
            accent,
            "".join(
                '<rect x="%d" y="%d" width="14" height="14"/>'
                % (26 + column * 22, top + 36 + row * 22)
                for row in range(2)
                for column in range(5)
            ),
        ),
        # a vertical rule with a terminal dot
        '<g fill="%s" opacity="0.80"><rect x="268" y="%d" width="4" '
        'height="%d"/><circle cx="270" cy="%d" r="6"/></g>'
        % (accent, top, bottom - top - 20, bottom - 6),
    ]
    return motifs[index % len(motifs)]


def render(
    title: str, author: str, palette_name: str = DEFAULT_PALETTE
) -> str:
    """The cover for one book. Same inputs, same bytes, every time."""
    palette = PALETTES.get(palette_name) or PALETTES[DEFAULT_PALETTE]
    digest = _digest(title, author)
    colors = palette[digest[0] % len(palette)]
    lines, size = _title_block(title)

    leading = int(size * 1.22)
    title_svg = "".join(
        '<text x="%d" y="%d" font-size="%d" font-weight="700" fill="%s">%s'
        "</text>"
        % (MARGIN, TITLE_TOP + i * leading, size, colors["ink"], _esc(t))
        for i, t in enumerate(lines)
    )

    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
        'width="%d" height="%d" role="img" aria-label="%s by %s" '
        'font-family="%s">'
        '<rect width="%d" height="%d" fill="%s"/>'
        "%s"
        '<rect x="%d" y="92" width="46" height="4" fill="%s"/>'
        "%s"
        '<text x="%d" y="%d" font-size="15" fill="%s" opacity="0.86">%s'
        "</text>"
        '<rect x="6" y="6" width="%d" height="%d" fill="none" stroke="%s" '
        'stroke-width="1" opacity="0.22"/>'
        "</svg>"
        % (
            WIDTH, HEIGHT, WIDTH, HEIGHT,
            _esc(title), _esc(author), FONT_STACK,
            WIDTH, HEIGHT, colors["bg"],
            _motif(digest[1], colors["accent"]),
            MARGIN, colors["accent"],
            title_svg,
            MARGIN, AUTHOR_Y, colors["ink"], _esc(author),
            WIDTH - 12, HEIGHT - 12, colors["ink"],
        )
    )


def override_for(order_id: str) -> Optional[str]:
    """Hand-drawn art for one order, if someone dropped it in `covers/`."""
    if not ORDER_ID_RE.match(order_id or ""):
        return None
    path = OVERRIDE_DIR / ("%s.svg" % order_id)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def for_order(order, palette_name: str = DEFAULT_PALETTE) -> str:
    """The cover an order should show: its override, or its generated one."""
    return override_for(order.order_id) or render(
        order.title, order.author, palette_name
    )
