"""Check GitHub for a newer release (silent on any failure - no internet, repo private, rate
limited, malformed response - this must never block or interrupt normal use of the app).

Reads ``packaging/assets/version.json`` from the repo's default branch over plain HTTPS; no
server, no account, no cost. While the repo stays private this request simply gets a 403 and the
check reports "no update found", which is exactly the right behaviour until the repo goes public.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

from pdf2word import __version__

MANIFEST_URL = "https://raw.githubusercontent.com/KevinLeve/k-doc-pdfscanner/main/packaging/assets/version.json"
TIMEOUT_S = 4.0


@dataclass
class UpdateInfo:
    version: str
    notes: str
    url: str


def _parse_version(v: str) -> tuple[int, ...]:
    parts = []
    for p in v.split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def check_for_update(current: str = __version__, url: str = MANIFEST_URL) -> UpdateInfo | None:
    """Return update info if a newer version is published, else None - never raises."""
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        latest = str(data["version"])
        if _parse_version(latest) > _parse_version(current):
            return UpdateInfo(version=latest, notes=str(data.get("notes", "")), url=str(data.get("url", "")))
    except Exception:     # noqa: BLE001 - a failed check must look exactly like "no update found"
        pass
    return None
