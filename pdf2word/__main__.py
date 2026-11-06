"""``python -m pdf2word``: with arguments → CLI; without → desktop window.

The packaged window executable (``pdf2word.exe``) always opens the window; PDF files or folders
passed to it (drag-and-drop onto the icon, "Send to") are listed, and ``--start`` converts them
at once. ``pdf2word-cli.exe`` is the command line.
"""

from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

from pdf2word.cli import main


def _is_window_exe() -> bool:
    return bool(getattr(sys, "frozen", False)) and Path(sys.executable).stem.lower() == "pdf2word"


def run() -> int:
    multiprocessing.freeze_support()      # the GUI's worker process in a frozen build
    if len(sys.argv) > 1 and not _is_window_exe():
        return main(sys.argv[1:])
    from pdf2word.gui import main as gui_main

    return gui_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(run())
