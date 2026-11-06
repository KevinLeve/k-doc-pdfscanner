"""Shared pytest fixtures: synthetic PDFs (generated per session) and the sample manifest."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools import make_synthetic  # noqa: E402


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """All M0 synthetic fixtures, written once per test session."""
    return make_synthetic.make_all(tmp_path_factory.mktemp("synthetic"))


@pytest.fixture(scope="session")
def basic_fixture(synthetic: dict[str, Path]) -> make_synthetic.BasicFixture:
    return make_synthetic.BasicFixture(path=synthetic["basic"])


@pytest.fixture(scope="session")
def samples() -> dict[str, Path]:
    """Sample PDFs by short name; tests needing them are skipped if they are absent."""
    manifest = json.loads((REPO / "samples" / "manifest.json").read_text(encoding="utf-8"))
    found = {k: REPO / v for k, v in manifest["pdf"].items() if (REPO / v).is_file()}
    if not found:
        pytest.skip("sample PDFs not present in this checkout")
    return found


def sample(samples: dict[str, Path], name: str) -> Path:
    if name not in samples:
        pytest.skip(f"sample {name} not present")
    return samples[name]
