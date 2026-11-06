"""GUI: worker process protocol and window wiring (the window itself is checked visually, P11)."""

from __future__ import annotations

import multiprocessing as mp
import queue
from pathlib import Path

import pytest

from pdf2word import gui


def drain(q, timeout: float = 120.0) -> list:
    out = []
    while True:
        item = q.get(timeout=timeout)
        out.append(item)
        if item[0] == "finished":
            return out


def test_worker_reports_progress_results_and_failures(tmp_path: Path, basic_fixture, synthetic) -> None:
    ctx = mp.get_context("spawn")
    q, cancel = ctx.Queue(), ctx.Event()
    p = ctx.Process(target=gui.worker, args=([(str(basic_fixture.path), str(tmp_path)),
                                              (str(synthetic["corrupt"]), str(tmp_path))], q, cancel))
    p.start()
    msgs = drain(q)
    p.join(30)
    results = {i: d for k, i, d in msgs if k == "result"}
    assert results[0]["status"].startswith("Done") and Path(results[0]["docx"]).is_file()
    assert results[1]["status"] == "Failed" and results[1]["error"]
    assert any(k == "progress" for k, _, _ in msgs)


def test_worker_honours_cancel(tmp_path: Path, basic_fixture) -> None:
    q = queue.Queue()
    ev = mp.get_context("spawn").Event()
    ev.set()
    gui.worker([(str(basic_fixture.path), str(tmp_path))], q, ev)
    assert ("result", 0, {"status": "Cancelled"}) in list(q.queue)


def test_window_lists_files_and_shows_states(tmp_path: Path) -> None:
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        app = gui.App(root)
        app._add([tmp_path / "a.pdf", tmp_path / "b.pdf", tmp_path / "a.pdf"])
        assert len(app.files) == 2
        app.jobs = app.files
        app.handle("result", 0, {"status": "Done – needs checking (3)", "docx": str(tmp_path / "a.docx"),
                                 "report": str(tmp_path / "a.report.txt"), "summary": {}})
        app.handle("result", 1, {"status": "Failed", "error": "PasswordRequired: needs a password"})
        assert app.tree.item(app.files[1]["iid"])["values"][0] == "Failed"
        app.tree.selection_set(app.files[0]["iid"])
        app.update_buttons()
        assert str(app.open_doc["state"]) == "normal"
    finally:
        root.destroy()
