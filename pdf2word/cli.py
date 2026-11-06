"""Command line interface (spec §2, §10): ``convert``, ``bench``, ``dump``.

Exit codes: 0 success · 1 error · 2 usage / feature not yet available.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from pdf2word import APP_NAME, __version__
from pdf2word.config import DEFAULTS
from pdf2word.extract import create_backend
from pdf2word.extract.backend import BackendError, PasswordRequired, SupportsDebugOverlay
from pdf2word.ir import to_jsonable
from pdf2word.version import collect_versions, format_versions

EXIT_OK, EXIT_ERROR, EXIT_UNAVAILABLE = 0, 1, 2


def _parse_pages(spec: str | None, page_count: int) -> list[int]:
    """'6' | '1-3,6' | None (all pages) → sorted 1-based page numbers."""
    if not spec:
        return list(range(1, page_count + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
            pages.update(range(a, b + 1))
        elif part:
            pages.add(int(part))
    bad = [p for p in pages if not 1 <= p <= page_count]
    if bad:
        raise ValueError(f"page(s) {bad} outside 1..{page_count}")
    return sorted(pages)


def cmd_convert(args: argparse.Namespace) -> int:
    from pdf2word.pipeline import convert

    target = Path(args.target)
    pdfs = sorted(target.rglob("*.pdf")) if target.is_dir() else [target]
    if not pdfs or not all(p.is_file() for p in pdfs):
        print(f"{APP_NAME} convert: no PDF found at {target}", file=sys.stderr)
        return EXIT_ERROR
    failed = 0
    for pdf in pdfs:
        out = Path(args.out) if args.out else pdf.parent
        res = convert(pdf, out, password=args.password, transparent_math=not args.opaque_math,
                      progress=lambda m, i, n: print(f"  {m}", file=sys.stderr) if args.verbose else None)
        s = res.summary
        print(f"{pdf.name}: {res.status}")
        if s:
            print(f"  E {s.get('E_editable_text')} · V {s.get('V_image_preserved')} · "
                  f"P {s.get('P_prose_as_image')} · R {s.get('R_removed_by_rule')} · "
                  f"pictures {s.get('pictures')} · {s.get('seconds')} s")
        if s.get("error"):
            print(f"  error: {s['error']}")
        print(f"  docx: {res.docx}")
        print(f"  report: {res.report_txt}")
        failed += res.status in ("Failed", "Cancelled")
    return EXIT_ERROR if failed else EXIT_OK


def cmd_bench(args: argparse.Namespace) -> int:
    from pdf2word.bench.timing import run_bench, summary

    out = Path(args.out) if args.out else Path(f"bench_{platform.node() or 'unknown'}.csv")
    try:
        header, runs = run_bench(
            create_backend, [Path(t) for t in args.targets], args.repeat, out, args.no_docx,
            progress=lambda m: print(f"  {m}", file=sys.stderr),
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"{APP_NAME} bench: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Machine: {header['cpu_model']} · {header['cores_physical']} cores / "
          f"{header['cores_logical']} threads · {header['ram_total_gb']} GB · {header['os']}")
    print("Versions: " + format_versions({k[8:]: v for k, v in header.items() if k.startswith("version.")}))
    print(f"Stages measured: {header['stages_available']} (docx: {header['docx_stages']})")
    print(f"{'file':40} {'rep':>3} {'pages':>5} {'total s':>8} {'med s/pg':>9} {'p95 s/pg':>9} {'peak MB':>8}  error")
    for r in runs:
        print(f"{r.file[:40]:40} {r.repeat:>3} {r.pages:>5} {r.total_s:>8.3f} "
              f"{r.median_s_per_page:>9.4f} {r.p95_s_per_page:>9.4f} {r.peak_rss_mb:>8.1f}  {r.error}")
    s = summary(runs)
    print(f"Summary: {s['runs']} runs, {s['failed_runs']} failed, {s['pages_measured']} pages, "
          f"median {s['median_s_per_page']} s/page, p95 {s['p95_s_per_page']} s/page, "
          f"max peak RSS {s['max_peak_rss_mb']} MB")
    print(f"CSV written: {out.resolve()}")
    return EXIT_ERROR if int(s["failed_runs"]) else EXIT_OK


def cmd_dump(args: argparse.Namespace) -> int:
    backend = create_backend()
    out_dir = Path(args.out)
    try:
        info = backend.open(Path(args.pdf), args.password)
        pages = _parse_pages(args.pages, info.page_count)
        out_dir.mkdir(parents=True, exist_ok=True)
        versions = collect_versions(backend)
        for page_no in pages:
            raw = backend.read_page(page_no)
            doc = {
                "_dump_version": 1,
                "source": str(info.path),
                "source_sha256": info.sha256,
                "versions": versions,
                "extraction_settings": info.extraction_settings,
                "stage": "M0 raw backend page (before tokenisation/classification)",
                "page": to_jsonable(raw),
            }
            jpath = out_dir / f"page_{page_no:03d}.json"
            jpath.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
            line = f"page {page_no}: {len(raw.spans)} spans, {sum(len(s.chars) for s in raw.spans)} chars, " \
                   f"{len(raw.paths)} paths, {len(raw.images)} images → {jpath.name}"
            if isinstance(backend, SupportsDebugOverlay):
                boxes = [(s.bbox, (0, 90, 255)) for s in raw.spans]
                boxes += [(p.bbox, (230, 0, 0)) for p in raw.paths]
                boxes += [(i.bbox, (0, 170, 0)) for i in raw.images]
                ppath = out_dir / f"page_{page_no:03d}_overlay.png"
                ppath.write_bytes(backend.render_overlay(page_no, boxes, args.dpi))
                line += f", {ppath.name}"
            print(line)
    except PasswordRequired as exc:
        print(f"{APP_NAME} dump: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except (BackendError, ValueError) as exc:
        print(f"{APP_NAME} dump: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        backend.close()
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="K-Doc PDFScanner",
        description="Convert born-digital PDFs into editable Word manuscripts.",
    )
    p.add_argument("--version", action="store_true", help="print app and library versions and exit")
    sub = p.add_subparsers(dest="command", metavar="{convert,bench,dump}")

    c = sub.add_parser("convert", help="convert a PDF or a folder of PDFs")
    c.add_argument("target", help="PDF file or folder")
    c.add_argument("--out", help="output folder (default: a 'K-Doc PDFScanner output' folder next to the PDF); "
                                   "existing files are never overwritten")
    c.add_argument("--password", help="password for encrypted PDFs")
    c.add_argument("--verbose", action="store_true", help="print progress")
    c.add_argument("--opaque-math", action="store_true",
                   help="render maths pictures on white as in Release 2 (default: transparent background)")
    c.set_defaults(func=cmd_convert)

    b = sub.add_parser("bench", help="measure processing time and memory on this machine")
    b.add_argument("targets", nargs="+", help="PDF files and/or folders (searched recursively)")
    b.add_argument("--repeat", type=int, default=2, help="runs per file (default 2; run 1 is flagged cold)")
    b.add_argument("--out", help="CSV path (default bench_<COMPUTERNAME>.csv)")
    b.add_argument("--no-docx", action="store_true", help="skip DOCX writing stages")
    b.set_defaults(func=cmd_bench)

    d = sub.add_parser("dump", help="write raw page JSON and an overlay PNG for debugging")
    d.add_argument("pdf", help="PDF file")
    d.add_argument("--pages", help="pages, e.g. '6' or '1-3,6' (default: all)")
    d.add_argument("--out", default="dump", help="output folder (default ./dump)")
    d.add_argument("--dpi", type=int, default=DEFAULTS.dump_dpi, help=f"overlay resolution (default {DEFAULTS.dump_dpi})")
    d.add_argument("--password", help="password for encrypted PDFs")
    d.set_defaults(func=cmd_dump)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252 (see IMPLEMENTATION_LOG D8)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.version:
        backend = create_backend()
        print(f"{APP_NAME} {__version__}")
        print(format_versions(collect_versions(backend)))
        return EXIT_OK
    if not getattr(args, "func", None):
        parser.print_help()
        return EXIT_UNAVAILABLE
    return args.func(args)
