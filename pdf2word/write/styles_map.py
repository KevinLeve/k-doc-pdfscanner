"""Built-in style names used by the writer, with a fallback when a style is missing (pilot: no templates)."""

from __future__ import annotations

FALLBACK = "Normal"


def resolve(doc, name: str) -> str:
    try:
        doc.styles[name]
        return name
    except KeyError:
        return FALLBACK
