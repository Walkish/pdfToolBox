# PDF Toolbox

A localhost web tool for three PDF chores: compressing PDFs without making them
unprintable, merging PDFs, and turning images into a PDF.

## Contents

- [Requirements](#requirements)
- [Setup](#setup)
- [Run](#run)
- [Running on Windows](#running-on-windows)
- [Compression levels](#compression-levels)
- [Matching page sizes when merging](#matching-page-sizes-when-merging)
- [Known limitations](#known-limitations)
- [Tests](#tests)
- [Lint and type checks](#lint-and-type-checks)
- [Security](#security)

## Requirements

The instructions below are for macOS, where this was built and tested. For
Windows see [Running on Windows](#running-on-windows).

- macOS with Homebrew
- [uv](https://docs.astral.sh/uv/) — `make setup` installs the pinned version if
  you do not have it, and manages the Python toolchain itself
- Ghostscript and poppler, which are not Python packages and so are the one
  thing uv cannot install for you:

```bash
brew install ghostscript poppler
```

Check everything at once:

```bash
make local-env-check
```

## Setup

```bash
make setup
```

That creates `.venv` with the Python pinned in `pyproject.toml` and installs
every dependency from `uv.lock`.

## Run

```bash
make dev
```

Then open http://127.0.0.1:5057

The server listens on port 5057 by default. If that port is already taken on
your machine, set `PDFTOOLBOX_PORT` to a free one:

```bash
PDFTOOLBOX_PORT=5058 make dev
```

`PDFTOOLBOX_PORT` must be a plain integer; an invalid value or an
already-occupied port stops the server with a message telling you which port
failed and how to override it. The server always binds `127.0.0.1` only, on
any port.

## Running on Windows

> **Not verified on Windows.** This was built and tested on macOS. The code
> handles Windows' differences deliberately — see the notes at the end of this
> section — but nobody has run it on a Windows machine, so treat the commands
> below as untested. If something here is wrong, the most likely culprits are
> the poppler package name and PATH.

Windows has no `make` and no Homebrew, so the steps below drive `uv` directly.
All commands are PowerShell, run from the repository root.

### 1. Install uv

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.11.8/install.ps1 | iex"
```

The version is pinned to match `[tool.uv] required-version` in
`pyproject.toml`. `winget install --id=astral-sh.uv` also works if you would
rather not pipe a script, but it installs whatever version is current.

### 2. Install Ghostscript and poppler

These are ordinary Windows programs, not Python packages, so uv cannot install
them. With [Scoop](https://scoop.sh/):

```powershell
scoop install ghostscript poppler
```

With [Chocolatey](https://chocolatey.org/):

```powershell
choco install ghostscript
choco install poppler
```

If neither package manager has a usable poppler build, download one from
[poppler-windows releases](https://github.com/oschwartz10612/poppler-windows/releases),
unzip it, and add its `Library\bin` folder to `PATH`.

**Both must be on `PATH`.** Check it before going further:

```powershell
gswin64c --version
pdfimages -h
```

If `gswin64c` is not found, add Ghostscript's `bin` folder (typically
`C:\Program Files\gs\gs10.07.1\bin`) to `PATH`. Package-manager installs
normally do this for you; the standalone installer sometimes does not.

### 3. Install Python dependencies

```powershell
uv sync
```

This creates `.venv` with the Python pinned in `pyproject.toml`, downloading
that version if you do not have it.

### 4. Run the server

```powershell
uv run python app.py
```

Then open http://127.0.0.1:5057. To use a different port:

```powershell
$env:PDFTOOLBOX_PORT = "5058"; uv run python app.py
```

### 5. Tests and lint

Tests need the repository root on `PYTHONPATH`, because `uv run pytest` invokes
a console script and Python only adds the working directory automatically for
`python -m`. Running the server does not need this — Python adds `app.py`'s own
directory for you.

```powershell
$env:PYTHONPATH = "$PWD"; uv run pytest tests/ -v
```

```powershell
uv run ruff check .
uv run ruff format .
uv run ty check
```

### Using the Makefile on Windows

`make setup`, `make dev`, `make lint` and `make test` all work under Git Bash
or WSL if you have GNU Make installed (`scoop install make`), since
`setup/local_env_check.sh` is a bash script. In plain PowerShell or `cmd`,
use the `uv` commands above instead.

### What the code does differently on Windows

- **Ghostscript is called `gswin64c.exe`**, not `gs`, and a 32-bit install
  provides `gswin32c.exe`. `pdftools/binaries.py` tries all three names, so no
  configuration is needed — but a lookup for `gs` alone would have made the app
  refuse to start on an otherwise correct machine.
- **Install hints are chosen per platform**, so a missing binary suggests
  `choco`/`scoop` rather than `brew`.
- **poppler keeps its executable names** everywhere; Windows resolves the
  `.exe` through `PATHEXT`.
- **Paths containing spaces are safe.** Every external tool is invoked with an
  argument list and never through a shell, so `C:\Program Files\...` needs no
  quoting or escaping.
- **Test fixtures look for Windows fonts** (`C:\Windows\Fonts\arial.ttf` and
  friends). Without a real TrueType font Pillow falls back to a small bitmap
  face, and the compression fixtures would carry far less detail than a real
  scan — which is what makes the calibration assertions meaningful in the
  first place.

## Compression levels

| Level | Images resampled to | Use for |
| --- | --- | --- |
| Print 600 dpi | 600 dpi | Archival and fine detail; saves least |
| Print 300 dpi (default) | 300 dpi | Anything going to a printer or a press |
| Lossless | not touched | Structure-only compression, identical quality |

Every level is print-safe: the lowest target is 300 dpi, well above the 200 dpi
floor, so no setting here can make a document unprintable. The levels differ in
resolution, not in JPEG quality — both print levels use the same quality, so a
600 dpi result is strictly better than a 300 dpi one rather than a different
trade-off.

Ghostscript only resamples an image that exceeds the target by half again, so
Print 600 dpi leaves a 600 dpi scan alone — and on an already-JPEG scan it
passes the image through untouched rather than spending a JPEG generation for
no saving. Expect it to shrink files that were scanned above 600 dpi, and to
return most others unchanged.

Text in born-digital PDFs is vector and stays sharp at every level; only
embedded images are resampled. Scans are the exception, because there the image
resolution *is* the text resolution. Every compressed file is therefore
re-measured afterwards, page by page: if any page whose image covers the whole
sheet lands below 200 dpi you get a warning before you download it — including a
mostly-text bundle where just one sheet came in at a low resolution — and the
readability comparison lets you check the result at 1:1 first. Since no level
can put a page below the floor, that warning always means the page arrived that
way and needs rescanning, not a different setting.

Files whose images are already at or below the target are not resampled, and a
file that would come out larger is returned unchanged.

## Matching page sizes when merging

Merging documents whose pages differ in size gives you one that jumps around on
screen and prints at inconsistent scales — an A4 scan next to a page built from
a phone photo. **Match page sizes**, in the merge tab and on by default, fits
every page to the size that dominates the batch.

The target is the most common page size among the merged pages, so 20 A4 pages
plus 2 photos produce an A4 document. Ties go to the larger size. Pages already
at that size are not touched at all: with a batch that is uniform to begin with,
ticking the box produces a byte-for-byte identical file to leaving it unticked.

Odd-sized pages are scaled by a single factor and centred, so proportions are
exact and nothing is stretched. A landscape page meeting a portrait target is
turned 90° counter-clockwise rather than shrunk into a band across the middle —
you turn the document clockwise to read it, the usual convention. Content is
transformed, never re-rendered, so text stays text and vector art stays vector:
matching page sizes costs no quality.

Untick the box for documents where the geometry is the point — drawings to
scale, forms — and you get the plain page-for-page merge.

## Known limitations

- Digital signatures do not survive compression: Ghostscript writes a new file.
- When **Match page sizes** resizes a page whose CropBox is smaller than its
  MediaBox, content hidden outside that CropBox — printer crop marks, most
  likely — is not clipped away and can appear in the new margins. Pages already
  at the target size are untouched, so this cannot affect a page merely for
  having a CropBox.
- PDF/A conformance and accessibility tags may be dropped.
- Interactive form fields may lose behaviour, though field values are kept.
- Merging does not deduplicate resources shared between input files.

## Tests

```bash
make test
```

## Lint and type checks

```bash
make lint
```

Runs `ruff check`, `ruff format` and `ty`. Locally it fixes and formats in
place; set `IS_CI_BUILD=1` to check without writing, which is what CI should
use.

The ruff rule set is pinned explicitly in `pyproject.toml` rather than left to
ruff's defaults, which widen between releases — otherwise upgrading ruff turns
into a mass rewrite of untouched code.

## Security

The server binds `127.0.0.1` only and has no authentication or CSRF protection.
Exposing the port to a network would require adding both.
