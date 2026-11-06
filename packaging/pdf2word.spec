# PyInstaller spec for the internal pilot build (one folder, two executables).
# Build:  .venv\Scripts\pyinstaller --noconfirm --clean packaging\pdf2word.spec --distpath dist --workpath build
# Output: dist\pdf2word\pdf2word.exe (window), dist\pdf2word\pdf2word-cli.exe (console), licenses\.
# INTERNAL PILOT ONLY — contains AGPL components; see licenses\README.md.
import os

from PyInstaller.utils.hooks import copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

# Only the two files an installed copy actually needs ship with the app; the detailed
# per-dependency notices stay in the repo (https://github.com/KevinLeve/k-doc-tools) instead.
datas = [(os.path.join(ROOT, "licenses", "LICENSE.txt"), "licenses"),
         (os.path.join(ROOT, "licenses", "NOTICE.txt"), "licenses"),
         (os.path.join(SPECPATH, "assets", "kdoc_pdfscanner.ico"), "assets")]
for dist in ("PyMuPDF", "python-docx", "lxml", "psutil", "typing_extensions"):
    datas += copy_metadata(dist)          # version report reads importlib.metadata at runtime

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["pdf2word.gui", "pdf2word.pipeline", "pdf2word.report.report"],
    excludes=["pymupdf4llm", "pymupdf.layout", "onnxruntime", "numpy", "networkx", "yaml", "tabulate",
              "pytest", "PIL", "matplotlib", "IPython"],
)


def _keep(entry) -> bool:
    """Pilot: drop PyMuPDF's unused optional layout package (ONNX models) and C headers (tried once)."""
    dest = entry[0].replace("\\", "/")
    return not (dest.startswith("pymupdf/layout") or dest.startswith("pymupdf/mupdf-devel")
                or dest.startswith("pymupdf4llm") or dest.endswith(".onnx"))


a.datas = [e for e in a.datas if _keep(e)]
a.binaries = [e for e in a.binaries if _keep(e)]

pyz = PYZ(a.pure)
VERSION_FILE = os.path.join(SPECPATH, "version_info.txt")   # publisher metadata (Properties > Details)
ICON_FILE = os.path.join(SPECPATH, "assets", "kdoc_pdfscanner.ico")
gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pdf2word", console=False, upx=False,
         version=VERSION_FILE, icon=ICON_FILE)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="pdf2word-cli", console=True, upx=False,
         version=VERSION_FILE, icon=ICON_FILE)
coll = COLLECT(gui, cli, a.binaries, a.datas, upx=False, name="pdf2word")
