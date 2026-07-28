"""Profiling a source PDF before deciding how to compress it.

The compressor needs to know two things that are not visible from the file
size: how much raster resolution is actually in there, and whether the document
is a scan. A scan's image resolution *is* its text resolution, which is the
only case where compression can render a page illegible.

Scan detection is based on image coverage, not on the presence of a text
layer: scan-to-PDF workflows routinely run OCR and embed an invisible text
layer over the raster page, so "no text" would false-negative on exactly the
files this module exists to protect. A page is a scan page when a raster
image covers essentially all of it; a document is a scan when most of its
pages are scan pages.
"""

import dataclasses
import math
import re
import statistics
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import binaries
from .errors import ToolError

# Column indices in `pdfimages -list` output.
_COL_PAGE = 0
_COL_TYPE = 2
_COL_WIDTH = 3
_COL_HEIGHT = 4
_COL_COLOR = 5
_COL_BPC = 7
_COL_X_PPI = 12
_COL_Y_PPI = 13
_MIN_COLUMNS = 16

# Below this many characters per page, a document is treated as having no
# meaningful text layer. This is reported on its own merits (a legibility
# report is more useful when it also says whether a text layer exists) but
# no longer gates `is_scan` -- see the module docstring.
_TEXT_CHARS_PER_PAGE = 10

# An image covering at least this fraction of its page's area is treated as
# *being* the page, i.e. this page is a raster scan rather than a text page
# that merely contains an embedded photo or figure.
SCAN_COVERAGE_THRESHOLD = 0.9

# A document counts as a scan once at least this fraction of its pages are
# scan pages. A report with one full-page photo is not a scan; a 40-page
# scanned contract is, even if one page failed to image.
SCAN_PAGE_FRACTION = 0.8

# `pdfinfo -f 1 -l N` output, one line per page: "Page    3 size:  612 x 792 pts (letter)".
_PAGE_SIZE_RE = re.compile(r"^Page\s+(\d+)\s+size:\s+([\d.]+)\s*x\s*([\d.]+)\s*pts")
# Fallback seen when poppler reports one page size for the whole document.
_DEFAULT_PAGE_SIZE_RE = re.compile(r"^Page size:\s+([\d.]+)\s*x\s*([\d.]+)\s*pts")


@dataclasses.dataclass
class ImageInfo:
    """One raster image embedded in the PDF."""

    page: int
    width: int
    height: int
    color: str
    bpc: int
    ppi: Optional[float]
    # Fraction of the page's area this image covers; None when it cannot be
    # computed (unknown ppi, or unknown page size).
    coverage: Optional[float] = None


@dataclasses.dataclass
class PdfProfile:
    """What we know about a source PDF before compressing it."""

    page_count: int
    images: List[ImageInfo]
    has_text: bool
    is_scan: bool
    min_ppi: Optional[float]
    median_ppi: Optional[float]
    max_ppi: Optional[float]


def _to_float(token: str) -> Optional[float]:
    try:
        return float(token)
    except ValueError:
        return None


def _to_int(token: str, default: int = 0) -> int:
    try:
        return int(token)
    except ValueError:
        return default


def parse_pdfimages_list(text: str) -> List[ImageInfo]:
    """Parse `pdfimages -list` output, skipping headers and separators."""
    images = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < _MIN_COLUMNS or not fields[_COL_PAGE].isdigit():
            continue
        x_ppi = _to_float(fields[_COL_X_PPI])
        y_ppi = _to_float(fields[_COL_Y_PPI])
        candidates = [value for value in (x_ppi, y_ppi) if value is not None and value > 0]
        images.append(
            ImageInfo(
                page=int(fields[_COL_PAGE]),
                width=_to_int(fields[_COL_WIDTH]),
                height=_to_int(fields[_COL_HEIGHT]),
                color=fields[_COL_COLOR],
                bpc=_to_int(fields[_COL_BPC], default=8),
                # The lower axis governs legibility, so keep the pessimistic one.
                ppi=min(candidates) if candidates else None,
                coverage=None,
            )
        )
    return images


def _run(args: List[str], path: Path) -> str:
    """Run an external tool and return its stdout, raising ToolError on failure."""
    binary_name = Path(args[0]).name
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=binaries.TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "{0} timed out after {1}s on {2}".format(binary_name, binaries.TIMEOUT_SECONDS, path),
            stderr=str(exc),
        ) from exc
    if completed.returncode != 0:
        raise ToolError(
            "{0} exited with status {1} on {2}".format(binary_name, completed.returncode, path),
            stderr=completed.stderr or completed.stdout,
        )
    return completed.stdout


def _page_count(path: Path) -> int:
    output = _run([binaries.find("pdfinfo"), str(path)], path)
    for line in output.splitlines():
        if line.startswith("Pages:"):
            return _to_int(line.split(":", 1)[1].strip())
    return 0


def _page_sizes(path: Path, page_count: int) -> Dict[int, Tuple[float, float]]:
    """Return {page number: (width pt, height pt)} for every page."""
    if page_count <= 0:
        return {}
    output = _run([binaries.find("pdfinfo"), "-f", "1", "-l", str(page_count), str(path)], path)
    sizes = {}
    default_size = None
    for line in output.splitlines():
        match = _PAGE_SIZE_RE.match(line)
        if match:
            sizes[int(match.group(1))] = (float(match.group(2)), float(match.group(3)))
            continue
        default_match = _DEFAULT_PAGE_SIZE_RE.match(line)
        if default_match:
            default_size = (float(default_match.group(1)), float(default_match.group(2)))
    if default_size is not None:
        for page_number in range(1, page_count + 1):
            sizes.setdefault(page_number, default_size)
    return sizes


def _with_coverage(image: ImageInfo, page_sizes: Dict[int, Tuple[float, float]]) -> ImageInfo:
    """Return a copy of ``image`` with ``coverage`` filled in, if computable."""
    page_size = page_sizes.get(image.page)
    if image.ppi is None or image.ppi <= 0 or page_size is None:
        return image
    page_width_pt, page_height_pt = page_size
    page_area = page_width_pt * page_height_pt
    if page_area <= 0:
        return image
    image_width_pt = (image.width / image.ppi) * 72.0
    image_height_pt = (image.height / image.ppi) * 72.0
    return dataclasses.replace(image, coverage=(image_width_pt * image_height_pt) / page_area)


def _extracted_text(path: Path) -> str:
    return _run([binaries.find("pdftotext"), str(path), "-"], path)


def scan_floor_ppi(path) -> Optional[float]:
    """The lowest resolution a reader will actually meet in ``path``: the
    minimum ppi among its *scan pages*.

    This exists to be run on a finished file, because predicting a
    compressed PDF's resolution from the preset that produced it does not
    work. Ghostscript only downsamples an image that exceeds the target by
    its DownsampleThreshold (1.5), so a 200 dpi image survives a 150 dpi
    target untouched; and a scanned bundle whose pages came from different
    devices has no single resolution at all -- one page at 600 dpi and one
    at 100 dpi through a 300 dpi target leaves the second page at 100 dpi.
    Measuring answers both questions and assumes nothing.

    Only images covering at least ``SCAN_COVERAGE_THRESHOLD`` of their page
    count: those are the pages whose image resolution *is* their text
    resolution. A small low-resolution thumbnail on one page of an otherwise
    sharp document is not what the reader has to read, and counting it would
    raise a false alarm.

    Returns None when the file has no such page (a born-digital document, or
    one with no raster images at all).
    """
    path = Path(path)
    images = parse_pdfimages_list(_run([binaries.find("pdfimages"), "-list", str(path)], path))
    last_page_with_image = max((image.page for image in images), default=0)
    if last_page_with_image <= 0:
        return None
    # Only pages that carry an image need their size, so the page range stops
    # at the last such page rather than covering the whole document.
    page_sizes = _page_sizes(path, last_page_with_image)
    scan_page_ppi = [
        measured.ppi
        for measured in (_with_coverage(image, page_sizes) for image in images)
        if measured.ppi is not None and measured.coverage is not None and measured.coverage >= SCAN_COVERAGE_THRESHOLD
    ]
    return min(scan_page_ppi) if scan_page_ppi else None


def profile_pdf(path) -> PdfProfile:
    """Inspect ``path`` and return everything the compressor needs to decide."""
    path = Path(path)
    images = parse_pdfimages_list(_run([binaries.find("pdfimages"), "-list", str(path)], path))
    pages_from_images = max((image.page for image in images), default=0)
    page_count = _page_count(path) or pages_from_images

    page_sizes = _page_sizes(path, page_count)
    images = [_with_coverage(image, page_sizes) for image in images]

    text = _extracted_text(path)
    text_budget = _TEXT_CHARS_PER_PAGE * max(1, page_count)
    has_text = len(text.strip()) >= text_budget

    ppi_values = sorted(image.ppi for image in images if image.ppi is not None)
    min_ppi = ppi_values[0] if ppi_values else None
    max_ppi = ppi_values[-1] if ppi_values else None
    median_ppi = statistics.median(ppi_values) if ppi_values else None

    # A scan page is one whose raster image covers essentially the whole
    # page. A document is a scan when most of its pages are scan pages.
    scan_pages = {
        image.page for image in images if image.coverage is not None and image.coverage >= SCAN_COVERAGE_THRESHOLD
    }
    required_scan_pages = max(1, math.ceil(SCAN_PAGE_FRACTION * page_count))
    is_scan = page_count > 0 and len(scan_pages) >= required_scan_pages

    return PdfProfile(
        page_count=page_count,
        images=images,
        has_text=has_text,
        is_scan=is_scan,
        min_ppi=min_ppi,
        median_ppi=median_ppi,
        max_ppi=max_ppi,
    )
