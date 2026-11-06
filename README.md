# K-Doc PDFScanner

Free, open-source software that converts born-digital PDFs into editable Word documents —
preserving layout, native Word equations, and formatting as faithfully as possible, entirely on
your own computer. No files are ever uploaded anywhere.

Part of the planned **K-Doc Tools** family of free document utilities.

## Download

Grab the latest Windows installer from the
[Releases page](https://github.com/KevinLeve/k-doc-pdfscanner/releases/latest). No installation
of Python or any other dependency is required — everything needed is bundled.

## Features

- Converts PDFs to `.docx` while keeping text editable wherever possible
- Native Word equations (OMML) for simple math, not just pictures
- Preserves figures, margin icons, and layout detail that can't be made editable, as images
- Built-in "Check for Updates"
- Runs entirely offline — nothing you convert is ever sent anywhere

## Building from source

Requires Python 3.12+.

```bash
pip install -r requirements-dev.txt
python -m pdf2word convert path/to/file.pdf
```

Run the test suite with:

```bash
pytest tests/ -q
```

To build the Windows installer yourself, see the comments at the top of
`packaging/pdf2word.spec` and `packaging/pdf2word.iss`.

## License

K-Doc PDFScanner is free software, released under the
[GNU Affero General Public License v3.0](LICENSE). It uses [PyMuPDF](https://pymupdf.io/) and
MuPDF, themselves licensed under AGPL-3.0 (a commercial license is also available from
[Artifex Software, Inc.](https://artifex.com/) for anyone who needs different terms).

See [licenses/README.md](licenses/README.md) for a full list of third-party components and their
licenses.

## Privacy

K-Doc PDFScanner does not collect any data, telemetry, or analytics. The only network request it
ever makes is a small, optional check against this repository for a newer version — nothing about
your files or usage is ever transmitted.
