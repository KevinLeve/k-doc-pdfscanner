"""Tkinter desktop window; runs the pipeline in a worker process (spec §2, L9; pilot record §4).

The window never converts in its own process: a worker process converts the queued files one by
one and reports progress through a queue, so a crash or hang in one PDF cannot take the window
down. Cancel asks the worker to stop between pages and terminates it if it does not stop within
``CANCEL_GRACE_S`` seconds. Per-file states: Done / Done – needs checking (N) / Done – some pages
kept as images (N) / Failed (plus Cancelled).
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from pdf2word import APP_NAME, __version__
from pdf2word.update_check import UpdateInfo, check_for_update

AUTO_UPDATE_CHECK_DELAY_MS = 1500   # let the window paint first; a slow/absent network must never stall startup

CANCEL_GRACE_S = 5.0
POLL_MS = 150


def _set_window_icon(root: tk.Tk) -> None:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    ico = base / "assets" / "kdoc_pdfscanner.ico"
    if ico.exists():
        try:
            root.iconbitmap(default=str(ico))
        except tk.TclError:
            pass


def log_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME / "logs"
    base.mkdir(parents=True, exist_ok=True)
    return base


def setup_logging() -> Path:
    path = log_dir() / "kdoc_pdfscanner.log"
    root = logging.getLogger()
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        h = logging.FileHandler(path, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(process)d %(levelname)s %(name)s: %(message)s"))
        root.addHandler(h)
        root.setLevel(logging.INFO)
    return path


# ------------------------------------------------------------------ worker process
def worker(jobs: list[tuple[str, str | None]], q, cancel) -> None:
    """Convert ``jobs`` [(pdf, out_dir or None)] in order; post messages to ``q``."""
    setup_logging()
    from pdf2word.pipeline import convert

    for i, (pdf, out) in enumerate(jobs):
        if cancel.is_set():
            q.put(("result", i, {"status": "Cancelled"}))
            continue
        q.put(("start", i, pdf))
        logging.getLogger("pdf2word.gui").info("converting %s", pdf)
        try:
            res = convert(Path(pdf), Path(out) if out else None,
                          progress=lambda m, k, n, i=i: q.put(("progress", i, (m, k, n))),
                          should_cancel=cancel.is_set)
            q.put(("result", i, {"status": res.status, "docx": str(res.docx) if res.docx else None,
                                 "report": str(res.report_txt) if res.report_txt else None,
                                 "error": (res.summary or {}).get("error"), "summary": res.summary}))
        except Exception as exc:     # noqa: BLE001 — convert reports its own failures; this is a last resort
            logging.getLogger("pdf2word.gui").exception("worker failed on %s", pdf)
            q.put(("result", i, {"status": "Failed", "error": f"{type(exc).__name__}: {exc}"}))
    q.put(("finished", -1, None))


# ------------------------------------------------------------------ window
class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title(f"K-Doc PDFScanner {__version__}")
        _set_window_icon(root)
        root.geometry("900x520")
        root.minsize(720, 400)
        self.files: list[dict] = []
        self.proc: mp.Process | None = None
        self.q = None
        self.cancel = None
        self.cancel_at: float | None = None
        self.out_dir = tk.StringVar(value="")

        bar = ttk.Frame(root, padding=(8, 8, 8, 0))
        bar.pack(fill="x")
        ttk.Button(bar, text="Add PDF files…", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Add folder…", command=self.add_folder).pack(side="left", padx=4)
        ttk.Button(bar, text="Clear", command=self.clear).pack(side="left")
        self.convert_btn = ttk.Button(bar, text="Convert", command=self.start)
        self.convert_btn.pack(side="right")
        self.cancel_btn = ttk.Button(bar, text="Cancel", command=self.stop, state="disabled")
        self.cancel_btn.pack(side="right", padx=4)
        ttk.Button(bar, text="Check for Updates", command=lambda: self.check_updates(silent=False)).pack(
            side="right", padx=4)

        self.update_frame = ttk.Frame(root, padding=(8, 4, 8, 0))
        self._update_info: UpdateInfo | None = None
        self.update_label = ttk.Label(self.update_frame, text="", foreground="#0a5fd1")
        self.update_label.pack(side="left")
        self.update_btn = ttk.Button(self.update_frame, text="Download", command=self._open_update)
        self.update_btn.pack(side="left", padx=6)
        # hidden until an update is actually found - .pack() happens only inside _show_update

        outf = ttk.Frame(root, padding=(8, 6, 8, 0))
        outf.pack(fill="x")
        ttk.Label(outf, text="Save Word files to:").pack(side="left")
        ttk.Entry(outf, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(outf, text="Choose…", command=self.choose_out).pack(side="left")
        ttk.Label(outf, text="(empty = a 'K-Doc PDFScanner output' folder next to each PDF)").pack(side="left", padx=4)

        cols = ("status", "detail")
        self.tree = ttk.Treeview(root, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="PDF")
        self.tree.heading("status", text="Status")
        self.tree.heading("detail", text="Details")
        self.tree.column("#0", width=300)
        self.tree.column("status", width=230)
        self.tree.column("detail", width=340)
        self.tree.pack(fill="both", expand=True, padx=8, pady=6)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_buttons())

        prog = ttk.Frame(root, padding=(8, 0, 8, 0))
        prog.pack(fill="x")
        self.pbar = ttk.Progressbar(prog, mode="determinate")
        self.pbar.pack(fill="x")
        self.msg = tk.StringVar(value="Add PDF files or a folder, then press Convert.")
        ttk.Label(prog, textvariable=self.msg).pack(anchor="w", pady=2)

        acts = ttk.Frame(root, padding=8)
        acts.pack(fill="x")
        self.open_doc = ttk.Button(acts, text="Open document", command=lambda: self.open_selected("docx"))
        self.open_rep = ttk.Button(acts, text="Open report", command=lambda: self.open_selected("report"))
        self.open_dir = ttk.Button(acts, text="Open folder", command=lambda: self.open_selected("folder"))
        for b in (self.open_doc, self.open_rep, self.open_dir):
            b.pack(side="left", padx=(0, 4))
        ttk.Button(acts, text="Open log", command=lambda: _open(log_dir() / "kdoc_pdfscanner.log")).pack(side="right")
        self.update_buttons()
        root.after(AUTO_UPDATE_CHECK_DELAY_MS, lambda: self.check_updates(silent=True))

    # -------------------------------------------------------------- updates
    def check_updates(self, *, silent: bool) -> None:
        def run() -> None:
            info = check_for_update()
            self.root.after(0, lambda: self._on_update_checked(info, silent))

        threading.Thread(target=run, daemon=True).start()

    def _on_update_checked(self, info: UpdateInfo | None, silent: bool) -> None:
        if info is not None:
            self._update_info = info
            self.update_label.config(text=f"Update available: v{info.version} — {info.notes}".rstrip(" —"))
            self.update_frame.pack(fill="x", before=self.tree)
        elif not silent:
            messagebox.showinfo("Check for Updates", "You're on the latest version.")

    def _open_update(self) -> None:
        if self._update_info and self._update_info.url:
            webbrowser.open(self._update_info.url)

    # -------------------------------------------------------------- file list
    def _add(self, paths: list[Path]) -> None:
        known = {f["pdf"] for f in self.files}
        for p in paths:
            s = str(p)
            if s in known:
                continue
            known.add(s)
            iid = self.tree.insert("", "end", text=p.name, values=("Waiting", s))
            self.files.append({"pdf": s, "iid": iid, "status": "Waiting"})
        self.msg.set(f"{len(self.files)} file(s) in the list.")
        self.update_buttons()

    def add_files(self) -> None:
        names = filedialog.askopenfilenames(title="Choose PDF files", filetypes=[("PDF files", "*.pdf")])
        self._add([Path(n) for n in names])

    def add_folder(self) -> None:
        d = filedialog.askdirectory(title="Choose a folder with PDF files")
        if d:
            found = sorted(Path(d).rglob("*.pdf"))
            if not found:
                messagebox.showinfo("No PDF files", f"No PDF files were found in\n{d}")
            self._add(found)

    def clear(self) -> None:
        if self.busy:
            return
        self.tree.delete(*self.tree.get_children())
        self.files = []
        self.update_buttons()

    def choose_out(self) -> None:
        d = filedialog.askdirectory(title="Folder for the Word files and reports")
        if d:
            self.out_dir.set(d)

    # -------------------------------------------------------------- run
    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.is_alive()

    def start(self) -> None:
        todo = [f for f in self.files if f["status"] in ("Waiting", "Cancelled", "Failed")]
        if not todo or self.busy:
            return
        out = self.out_dir.get().strip() or None
        self.jobs = todo
        for f in todo:
            self._set(f, "Waiting", "")
        ctx = mp.get_context("spawn")
        self.q = ctx.Queue()
        self.cancel = ctx.Event()
        self.cancel_at = None
        self.proc = ctx.Process(target=worker, args=([(f["pdf"], out) for f in todo], self.q, self.cancel),
                                daemon=True)
        self.proc.start()
        self.pbar.configure(maximum=len(todo) * 100, value=0)
        self.msg.set("Converting…")
        self.update_buttons()
        self.root.after(POLL_MS, self.poll)

    def stop(self) -> None:
        if self.busy and self.cancel is not None:
            self.cancel.set()
            self.cancel_at = time.monotonic()
            self.msg.set("Cancelling after the current page…")

    def poll(self) -> None:
        try:
            while True:
                kind, i, data = self.q.get_nowait()
                self.handle(kind, i, data)
        except queue.Empty:
            pass
        if self.cancel_at and self.busy and time.monotonic() - self.cancel_at > CANCEL_GRACE_S:
            self.proc.terminate()
            for f in self.jobs:
                if f["status"] in ("Waiting", "Converting"):
                    self._set(f, "Cancelled", "stopped")
            self.msg.set("Cancelled.")
        if self.busy:
            self.root.after(POLL_MS, self.poll)
        else:
            if self.proc is not None and self.proc.exitcode not in (0, None) and not self.cancel_at:
                for f in self.jobs:
                    if f["status"] in ("Waiting", "Converting"):
                        self._set(f, "Failed", f"worker stopped (exit code {self.proc.exitcode}); see log")
            self.proc = None
            self.update_buttons()

    def handle(self, kind: str, i: int, data) -> None:
        if kind == "start":
            self._set(self.jobs[i], "Converting", "")
            self.pbar.configure(value=i * 100)
        elif kind == "progress":
            m, k, n = data
            self._set(self.jobs[i], "Converting", m)
            self.pbar.configure(value=i * 100 + (100 * k // max(n, 1)))
        elif kind == "result":
            f = self.jobs[i]
            f.update(data)
            detail = data.get("error") or ""
            s = data.get("summary") or {}
            if not detail and s:
                detail = (f"editable words {s.get('E_editable_text')}, kept as image "
                          f"{s.get('V_image_preserved')}, prose as image {s.get('P_prose_as_image')}")
            self._set(f, data["status"], detail)
            self.pbar.configure(value=(i + 1) * 100)
        elif kind == "finished":
            done = sum(1 for f in self.jobs if f["status"].startswith("Done"))
            failed = sum(1 for f in self.jobs if f["status"] == "Failed")
            self.msg.set(f"Finished: {done} converted, {failed} failed."
                         + (" Select a file to open its document or report." if done else ""))
            if failed:
                names = "\n".join(Path(f["pdf"]).name + ": " + (f.get("error") or "")
                                  for f in self.jobs if f["status"] == "Failed")
                messagebox.showerror("Some files could not be converted", names[:1500])

    def _set(self, f: dict, status: str, detail: str) -> None:
        f["status"] = status
        self.tree.item(f["iid"], values=(status, detail))
        self.update_buttons()

    # -------------------------------------------------------------- open
    def selected(self) -> dict | None:
        sel = self.tree.selection()
        return next((f for f in self.files if sel and f["iid"] == sel[0]), None)

    def update_buttons(self) -> None:
        f = self.selected()
        has_doc = bool(f and f.get("docx"))
        has_rep = bool(f and f.get("report"))
        self.open_doc.configure(state="normal" if has_doc else "disabled")
        self.open_rep.configure(state="normal" if has_rep else "disabled")
        self.open_dir.configure(state="normal" if (has_doc or has_rep) else "disabled")
        self.convert_btn.configure(state="disabled" if self.busy or not self.files else "normal")
        self.cancel_btn.configure(state="normal" if self.busy else "disabled")

    def open_selected(self, what: str) -> None:
        f = self.selected()
        if not f:
            return
        target = f.get("docx") if what == "docx" else f.get("report")
        if what == "folder":
            target = str(Path(f.get("docx") or f.get("report")).parent)
        if target:
            _open(Path(target))


def _open(path: Path) -> None:
    try:
        os.startfile(str(path))           # type: ignore[attr-defined]  # Windows
    except AttributeError:
        subprocess.Popen(["xdg-open", str(path)])
    except OSError as exc:
        messagebox.showerror("Cannot open", f"{path}\n\n{exc}")


def main(argv: list[str] | None = None) -> int:
    """``argv``: PDF files/folders to list; ``--out DIR`` output folder; ``--start`` convert at once."""
    mp.freeze_support()
    setup_logging()
    argv = list(argv or [])
    start = "--start" in argv
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1] if i + 1 < len(argv) else None
        del argv[i:i + 2]
    paths: list[Path] = []
    for a in argv:
        if a.startswith("--"):
            continue
        p = Path(a)
        paths += sorted(p.rglob("*.pdf")) if p.is_dir() else [p]
    root = tk.Tk()
    app = App(root)
    if out:
        app.out_dir.set(out)
    if paths:
        app._add(paths)
        if start:
            root.after(300, app.start)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
