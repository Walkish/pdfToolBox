# PDF Toolbox

A localhost web tool for three PDF chores: compressing PDFs without making them
unprintable, merging PDFs, and turning images into a PDF.

## Requirements

- macOS with Homebrew
- Python 3.9+
- Ghostscript and poppler:

```bash
brew install ghostscript poppler
```

## Setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run

```bash
.venv/bin/python app.py
```

Then open http://127.0.0.1:5057

The server listens on port 5057 by default. If that port is already taken on
your machine, set `PDFTOOLBOX_PORT` to a free one:

```bash
PDFTOOLBOX_PORT=5058 .venv/bin/python app.py
```

`PDFTOOLBOX_PORT` must be a plain integer; an invalid value or an
already-occupied port stops the server with a message telling you which port
failed and how to override it. The server always binds `127.0.0.1` only, on
any port.

## Compression levels

| Level | Images resampled to | Use for |
| --- | --- | --- |
| Print 300 dpi (default) | 300 dpi | Anything going to a printer or a press |
| Print 200 dpi | 200 dpi | Office printing when 300 dpi is too heavy |
| Screen 150 dpi | 150 dpi | Email and on-screen reading — **not for print** |
| Lossless | not touched | Structure-only compression, identical quality |

Text in born-digital PDFs is vector and stays sharp at every level; only
embedded images are resampled. Scans are the exception, because there the image
resolution *is* the text resolution — so a scan that would drop below 200 dpi
gets a warning before you download it, and the readability comparison lets you
check the result at 1:1 first.

Files whose images are already at or below the target are not resampled, and a
file that would come out larger is returned unchanged.

## Known limitations

- Digital signatures do not survive compression: Ghostscript writes a new file.
- PDF/A conformance and accessibility tags may be dropped.
- Interactive form fields may lose behaviour, though field values are kept.
- Merging does not deduplicate resources shared between input files.

## Tests

```bash
.venv/bin/python -m pytest
```

## Security

The server binds `127.0.0.1` only and has no authentication or CSRF protection.
Exposing the port to a network would require adding both.
