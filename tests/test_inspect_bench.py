"""Inspection (scanned pages, fingerprint) and benchmark building blocks."""

from __future__ import annotations

import csv
import math
import time
from pathlib import Path

import pytest

from pdf2word.bench import timing
from pdf2word.extract import create_backend
from pdf2word.extract.inspect import inspect_document


def test_inspect_text_page(basic_fixture) -> None:
    b = create_backend()
    try:
        rep = inspect_document(b, basic_fixture.path)
    finally:
        b.close()
    assert rep.info.page_count == 1 and rep.scanned_pages == ()
    assert rep.font_fingerprint == ("Helvetica", "TimesNewRomanPSMT")
    assert rep.pages[0].n_chars > 20


def test_inspect_detects_scanned_page(synthetic) -> None:
    b = create_backend()
    try:
        rep = inspect_document(b, synthetic["scanned"])
    finally:
        b.close()
    assert rep.scanned_pages == (1,)
    assert rep.pages[0].image_area_ratio > 0.5


def test_stage_timer_accumulates() -> None:
    t = timing.StageTimer()
    for _ in range(2):
        with t.stage("extract"):
            time.sleep(0.005)
    assert t.ms["extract"] >= 9.0


def test_peak_rss_sampler() -> None:
    with timing.PeakRssSampler() as s:
        blob = bytearray(30 * 2**20)
        time.sleep(0.05)
        del blob
    assert s.peak_mb > 30


@pytest.mark.parametrize("values, q, expected", [([], 50, math.nan), ([3.0], 95, 3.0),
                                                 ([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95, 10),
                                                 ([1, 2, 3, 4], 50, 2)])
def test_percentile(values, q, expected) -> None:
    got = timing.percentile(values, q)
    assert (math.isnan(got) and math.isnan(expected)) or got == expected


def test_machine_info_keys() -> None:
    info = timing.machine_info()
    for key in ("computer", "cpu_model", "cores_physical", "cores_logical", "ram_total_gb", "os", "os_build",
                "disk_type"):
        assert info[key]


def test_run_bench_writes_csv(tmp_path: Path, synthetic) -> None:
    out = tmp_path / "bench.csv"
    header, runs = timing.run_bench(create_backend, [synthetic["basic"], synthetic["corrupt"]], repeat=2,
                                    out_csv=out, no_docx=True)
    assert [r.cold for r in runs] == [True, True, False, False]
    assert runs[0].error == "" and runs[1].error.startswith("BackendError")
    rows = list(csv.reader(out.open(encoding="utf-8")))
    meta = {r[1]: r[2] for r in rows if r and r[0] == "meta"}
    assert meta["version.PyMuPDF"] == "2.0" and meta["version.MuPDF"] == "1.28.5"
    assert meta["stages_available"] == "inspect,extract"
    table_header = next(r for r in rows if r and r[0] == "file")
    data = [dict(zip(table_header, r)) for r in rows if r and r[0] == "basic.pdf"]
    assert len(data) == 2 and data[0]["ms_extract"] != "" and data[0]["ms_layout"] == ""  # not yet implemented → empty
    summary = {r[1]: r[2] for r in rows if r and r[0] == "summary"}
    assert summary["failed_runs"] == "2"


def test_run_bench_rejects_bad_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        timing.run_bench(create_backend, [tmp_path], repeat=0, out_csv=tmp_path / "x.csv", no_docx=True)
    with pytest.raises(FileNotFoundError):
        timing.run_bench(create_backend, [tmp_path], repeat=1, out_csv=tmp_path / "x.csv", no_docx=True)
