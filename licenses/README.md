# Third-party components in K-Doc PDFScanner

K-Doc PDFScanner is free, open-source software released under the GNU Affero General Public
License v3 (AGPL-3.0). The full source code is published at
https://github.com/KevinLeve/k-doc-pdfscanner, kept in step with every release, as AGPL-3.0
requires. The packaged application contains PyMuPDF and MuPDF, themselves licensed under
AGPL-3.0 (a commercial licence is also available from Artifex Software, Inc. for anyone who
needs different terms than AGPL-3.0 provides).

| Component | Version | Licence | File |
|---|---|---|---|
| PyMuPDF (includes MuPDF 1.28.5) | 2.0 | AGPL-3.0 (or Artifex commercial) | PyMuPDF-licence-notice.txt (the wheel's own 64-byte notice) |
| python-docx | 1.2.0 | MIT | python-docx-MIT.txt |
| lxml (with bundled libxml2/libxslt) | 6.1.3 | BSD-3-Clause (libraries: MIT-style) | lxml-BSD.txt, lxml-bundled-libraries.txt |
| psutil | 7.2.2 | BSD-3-Clause | psutil-BSD.txt |
| typing_extensions | 4.16.0 | PSF-2.0 | typing_extensions-PSF.txt |
| Python runtime | 3.12.10 | PSF-2.0 | Python-PSF.txt |
| Tcl/Tk (Tkinter) | 8.6 | Tcl/Tk licence (BSD-style) | Tcl-Tk.txt |
| PyInstaller bootloader | 6.16.0 | GPL-2.0 with bootloader exception (no obligations for the bundled app) | — (build tool only) |

The build excludes PyMuPDF's optional `layout` package (ONNX models) and the `mupdf-devel`
headers; they are not used by K-Doc PDFScanner.

The full AGPL-3.0 licence text is included alongside this file as `LICENSE.txt` and at the
repository root as `LICENSE`.
