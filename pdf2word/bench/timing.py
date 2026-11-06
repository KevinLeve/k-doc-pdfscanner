"""Benchmark utility (spec §10): stage timers, peak RSS, machine info, versions.

At M0 only the stages that exist are measured (inspect, skim, extract);
the remaining spec stage columns are written empty — never as zero — until
the milestone that implements them.
"""

from __future__ import annotations

import csv
import math
import os
import platform
import statistics
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator

import psutil

from pdf2word.extract.backend import PdfBackend
from pdf2word.extract.inspect import inspect_document
from pdf2word.version import collect_versions

#: Stage columns from spec §10, in report order.
SPEC_STAGES: tuple[str, ...] = (
    "inspect", "extract", "cleanup", "classify", "layout", "inline_math", "display_math",
    "figures", "tables", "styles", "render", "write", "validate",
)
#: Stages implemented at the current milestone (M0). "skim" is pass-1, reported with inspect.
AVAILABLE_STAGES: tuple[str, ...] = ("inspect", "extract")


class StageTimer:
    """Accumulates wall-clock milliseconds per named stage."""

    def __init__(self) -> None:
        self.ms: dict[str, float] = defaultdict(float)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.ms[name] += (time.perf_counter() - t0) * 1000.0


class PeakRssSampler:
    """Samples this process's resident set size in a background thread."""

    def __init__(self, interval_s: float = 0.01) -> None:
        self._proc = psutil.Process()
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_bytes = 0

    def _sample(self) -> None:
        self.peak_bytes = max(self.peak_bytes, self._proc.memory_info().rss)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self._sample()

    def __enter__(self) -> PeakRssSampler:
        self._sample()
        self._thread = threading.Thread(target=self._run, name="rss-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        self._sample()

    @property
    def peak_mb(self) -> float:
        return self.peak_bytes / 2**20


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100). Empty input → nan."""
    if not values:
        return math.nan
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(ordered)))
    return ordered[rank - 1]


def _cpu_model() -> str:
    if platform.system() == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
            ) as key:
                return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor() or "unknown"


def machine_info() -> dict[str, str]:
    """Machine section of the benchmark header (spec §10)."""
    win_edition = getattr(platform, "win32_edition", lambda: None)() or ""
    return {
        "computer": platform.node() or os.environ.get("COMPUTERNAME", "unknown"),
        "cpu_model": _cpu_model(),
        "cores_physical": str(psutil.cpu_count(logical=False) or "unknown"),
        "cores_logical": str(psutil.cpu_count(logical=True) or "unknown"),
        "ram_total_gb": f"{psutil.virtual_memory().total / 2**30:.1f}",
        "os": f"{platform.system()} {platform.release()} {win_edition}".strip(),
        "os_build": platform.version(),
        "disk_type": "not detected",
    }


@dataclass
class FileRun:
    file: str
    repeat: int
    cold: bool
    pages: int
    total_s: float
    page_s: list[float]
    stage_ms: dict[str, float]
    peak_rss_mb: float
    error: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def median_s_per_page(self) -> float:
        return statistics.median(self.page_s) if self.page_s else math.nan

    @property
    def p95_s_per_page(self) -> float:
        return percentile(self.page_s, 95)


def bench_file(backend_factory: Callable[[], PdfBackend], path: Path, repeat: int, cold: bool) -> FileRun:
    """Run the currently available pipeline stages on one PDF and measure them."""
    timer = StageTimer()
    page_s: list[float] = []
    pages = 0
    error = ""
    backend = backend_factory()
    t0 = time.perf_counter()
    with PeakRssSampler() as rss:
        try:
            with timer.stage("inspect"):
                report = inspect_document(backend, path)
            pages = report.info.page_count
            for page_no in range(1, pages + 1):
                p0 = time.perf_counter()
                with timer.stage("extract"):
                    raw = backend.read_page(page_no)
                del raw
                page_s.append(time.perf_counter() - p0)
        except Exception as exc:  # recorded per file; the benchmark continues
            error = f"{type(exc).__name__}: {exc}"
        finally:
            backend.close()
    return FileRun(
        file=path.name, repeat=repeat, cold=cold, pages=pages,
        total_s=time.perf_counter() - t0, page_s=page_s, stage_ms=dict(timer.ms),
        peak_rss_mb=rss.peak_mb, error=error,
    )


def collect_pdfs(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    if target.is_dir():
        return sorted(p for p in target.rglob("*") if p.suffix.lower() == ".pdf" and p.is_file())
    raise FileNotFoundError(f"No such file or folder: {target}")


def run_bench(
    backend_factory: Callable[[], PdfBackend],
    targets: list[Path],
    repeat: int,
    out_csv: Path,
    no_docx: bool,
    progress: Callable[[str], None] = lambda _m: None,
) -> tuple[dict[str, str], list[FileRun]]:
    """Benchmark every PDF in ``targets`` ``repeat`` times and write the CSV (spec §10)."""
    if repeat < 1:
        raise ValueError("--repeat must be >= 1")
    pdfs: list[Path] = []
    for t in targets:
        pdfs.extend(collect_pdfs(t))
    if not pdfs:
        raise FileNotFoundError("No PDF files found to benchmark")

    probe = backend_factory()
    header = {**machine_info(), **{f"version.{k}": v for k, v in collect_versions(probe).items()}}
    settings = getattr(probe, "extraction_settings", None)  # optional, backend-specific
    if callable(settings):
        header.update({f"backend.{k}": v for k, v in settings().items()})
    header["stages_available"] = ",".join(AVAILABLE_STAGES)
    header["docx_stages"] = "skipped (--no-docx)" if no_docx else "not available until M4"
    probe.close()

    runs: list[FileRun] = []
    for r in range(1, repeat + 1):
        for pdf in pdfs:
            progress(f"repeat {r}/{repeat}: {pdf.name}")
            runs.append(bench_file(backend_factory, pdf, repeat=r, cold=(r == 1)))
    write_csv(out_csv, header, runs)
    return header, runs


def summary(runs: list[FileRun]) -> dict[str, str]:
    ok = [r for r in runs if not r.error]
    page_s = [s for r in ok for s in r.page_s]
    return {
        "files": str(len({r.file for r in runs})),
        "runs": str(len(runs)),
        "failed_runs": str(len(runs) - len(ok)),
        "pages_measured": str(len(page_s)),
        "median_s_per_page": f"{statistics.median(page_s):.4f}" if page_s else "nan",
        "p95_s_per_page": f"{percentile(page_s, 95):.4f}" if page_s else "nan",
        "max_peak_rss_mb": f"{max((r.peak_rss_mb for r in runs), default=math.nan):.1f}",
    }


def write_csv(out_csv: Path, header: dict[str, str], runs: list[FileRun]) -> None:
    """Sections: ``meta`` rows, one data table, ``summary`` rows."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for k, v in header.items():
            w.writerow(["meta", k, v])
        w.writerow([])
        cols = ["file", "repeat", "cold", "pages", "total_s", "median_s_per_page", "p95_s_per_page"]
        cols += [f"ms_{s}" for s in SPEC_STAGES]
        cols += ["peak_rss_mb", "fallback_pages", "escalations", "error"]
        w.writerow(cols)
        for r in runs:
            row = [r.file, r.repeat, r.cold, r.pages, f"{r.total_s:.4f}",
                   f"{r.median_s_per_page:.4f}", f"{r.p95_s_per_page:.4f}"]
            row += [f"{r.stage_ms[s]:.2f}" if s in r.stage_ms else "" for s in SPEC_STAGES]
            row += [f"{r.peak_rss_mb:.1f}", "", "", r.error]
            w.writerow(row)
        w.writerow([])
        for k, v in summary(runs).items():
            w.writerow(["summary", k, v])
