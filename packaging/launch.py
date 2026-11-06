"""PyInstaller entry point for both executables (pdf2word.exe = window, pdf2word-cli.exe = console)."""

import sys

from pdf2word.__main__ import run

if __name__ == "__main__":
    sys.exit(run())
