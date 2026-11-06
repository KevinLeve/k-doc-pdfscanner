"""Paragraph roles → built-in Word styles (spec §4.8; pilot: built-in styles only, no profiles).

Sizes are compared with the body size (character-weighted most common size of emitted text).
"""

from __future__ import annotations

import re
from collections import Counter

from pdf2word.ir import Role, Token

CAPTION = re.compile(r"^\s*(figure|table)\s+\d+\s*[-–.]\s*\d+", re.I)

STYLE_OF = {
    Role.CHAPTER_TITLE: "Title",
    Role.HEADING_1: "Heading 1",
    Role.HEADING_2: "Heading 2",
    Role.HEADING_3: "Heading 3",
    Role.CAPTION: "Caption",
    Role.BODY: "Normal",
    Role.LIST_BULLET: "Normal",
    Role.LIST_NUMBER: "Normal",
    Role.EXAMPLE_Q: "Normal",
    Role.EXAMPLE_A: "Normal",
    Role.OTHER: "Normal",
}

BULLET = re.compile(r"^\s*[•▪■●◆♦▶➤✓»]")
NUMBERED = re.compile(r"^\s*(\d+[.)]|step\s+\d+)", re.I)
EXAMPLE = re.compile(r"^\s*([QA])\.", re.I)


def body_size(tokens: list[Token]) -> float:
    c: Counter[float] = Counter()
    for t in tokens:
        c[round(t.font.size * 2) / 2] += len(t.raw_text)
    return c.most_common(1)[0][0] if c else 10.0


def role_for(text: str, size: float, bold: bool, body: float, words: int, title_size: float | None) -> Role:
    if CAPTION.match(text):
        return Role.CAPTION
    m = EXAMPLE.match(text)
    if m:
        return Role.EXAMPLE_Q if m.group(1).upper() == "Q" else Role.EXAMPLE_A
    if BULLET.match(text):
        return Role.LIST_BULLET
    if title_size is not None and size >= title_size - 0.25 and size >= 1.35 * body:
        return Role.CHAPTER_TITLE
    if size >= 1.35 * body:
        return Role.HEADING_1
    if size >= 1.15 * body:
        return Role.HEADING_2
    if bold and size >= 0.95 * body and words <= 12 and not text.rstrip().endswith((".", ",", ";")):
        return Role.HEADING_3
    if BULLET.match(text):
        return Role.LIST_BULLET
    if NUMBERED.match(text):
        return Role.LIST_NUMBER
    return Role.BODY
