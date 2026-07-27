"""Profiling a source PDF before deciding how to compress it.

The compressor needs to know two things that are not visible from the file
size: how much raster resolution is actually in there, and whether the document
is a scan. A scan's image resolution *is* its text resolution, which is the
only case where compression can render a page illegible.
"""
import dataclasses
import statistics
import subprocess
from pathlib import Path
from typing import List, Optional

from . import binaries

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
# meaningful text layer.
_TEXT_CHARS_PER_PAGE = 10


@dataclasses.dataclass
class ImageInfo:
    """One raster image embedded in the PDF."""

    page: int
    width: int
    height: int
    color: str
    bpc: int
    ppi: Optional[float]


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
            )
        )
    return images


def _run(args: List[str]) -> str:
    completed = subprocess.run(
        args, capture_output=True, text=True, timeout=binaries.TIMEOUT_SECONDS
    )
    return completed.stdout


def _page_count(path: Path) -> int:
    output = _run([binaries.find("pdfinfo"), str(path)])
    for line in output.splitlines():
        if line.startswith("Pages:"):
            return _to_int(line.split(":", 1)[1].strip())
    return 0


def _extracted_text(path: Path) -> str:
    return _run([binaries.find("pdftotext"), str(path), "-"])


def profile_pdf(path) -> PdfProfile:
    """Inspect ``path`` and return everything the compressor needs to decide."""
    path = Path(path)
    images = parse_pdfimages_list(
        _run([binaries.find("pdfimages"), "-list", str(path)])
    )
    pages_from_images = max((image.page for image in images), default=0)
    page_count = _page_count(path) or pages_from_images

    text = _extracted_text(path)
    text_budget = _TEXT_CHARS_PER_PAGE * max(1, page_count)
    has_text = len(text.strip()) >= text_budget

    ppi_values = sorted(image.ppi for image in images if image.ppi is not None)
    min_ppi = ppi_values[0] if ppi_values else None
    max_ppi = ppi_values[-1] if ppi_values else None
    median_ppi = statistics.median(ppi_values) if ppi_values else None

    # A scan is a document whose pages are images and whose text layer is empty.
    is_scan = bool(images) and not has_text and page_count > 0 and len(images) >= page_count

    return PdfProfile(
        page_count=page_count,
        images=images,
        has_text=has_text,
        is_scan=is_scan,
        min_ppi=min_ppi,
        median_ppi=median_ppi,
        max_ppi=max_ppi,
    )
