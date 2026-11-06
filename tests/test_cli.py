"""CLI entry points run as real subprocesses (``python -m pdf2word``)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from pdf2word import ir
from pdf2word.cli import _parse_pages
from pdf2word.extract.backend import RawPage

REPO = Path(__file__).resolve().parents[1]


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-m", "pdf2word", *args], capture_output=True, text=True,
                          encoding="utf-8", cwd=REPO)


def test_help_lists_commands() -> None:
    r = run("--help")
    assert r.returncode == 0
    for cmd in ("convert", "bench", "dump"):
        assert cmd in r.stdout


def test_no_arguments_opens_the_window(monkeypatch) -> None:
    import pdf2word.__main__ as entry
    import pdf2word.gui as gui

    monkeypatch.setattr(sys, "argv", ["pdf2word"])
    monkeypatch.setattr(gui, "main", lambda argv=None: 7)
    assert entry.run() == 7


def test_version_flag() -> None:
    r = run("--version")
    assert r.returncode == 0 and "PyMuPDF 2.0" in r.stdout and "MuPDF 1.28.5" in r.stdout


def test_convert_command(tmp_path: Path, basic_fixture) -> None:
    r = run("convert", str(basic_fixture.path), "--out", str(tmp_path))
    assert r.returncode == 0, r.stdout + r.stderr
    stem = basic_fixture.path.stem
    assert (tmp_path / f"{stem}.docx").is_file() and (tmp_path / f"{stem}.report.txt").is_file()


def test_bench_command(tmp_path: Path, basic_fixture) -> None:
    out = tmp_path / "b.csv"
    r = run("bench", str(basic_fixture.path), "--repeat", "1", "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "PyMuPDF 2.0" in r.stdout and out.is_file()


def test_dump_command(tmp_path: Path, basic_fixture) -> None:
    r = run("dump", str(basic_fixture.path), "--pages", "1", "--out", str(tmp_path))
    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / "page_001.json").read_text(encoding="utf-8"))
    assert data["versions"]["MuPDF"] == "1.28.5" and data["extraction_settings"]["text_flags"] == "67"
    page = ir.from_jsonable(data["page"], RawPage)            # dump decodes back into the typed record
    assert page.page_no == 1 and "ﬁndﬂow" in [s.text for s in page.spans]
    assert (tmp_path / "page_001_overlay.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_dump_errors(tmp_path: Path, synthetic, basic_fixture) -> None:
    assert run("dump", str(synthetic["encrypted"]), "--out", str(tmp_path)).returncode == 1
    r = run("dump", str(basic_fixture.path), "--pages", "5", "--out", str(tmp_path))
    assert r.returncode == 1 and "outside" in r.stderr


def test_parse_pages() -> None:
    assert _parse_pages(None, 3) == [1, 2, 3]
    assert _parse_pages("1-2,3, 2", 5) == [1, 2, 3]
