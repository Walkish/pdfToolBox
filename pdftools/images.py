"""Turning raster images into a single PDF.

Each page is sized from its own image so proportions are exact and no
letterboxing is added. Physical size follows the image's own dpi metadata when
it has any, and falls back to 300 dpi otherwise, which keeps pages a sane size
for printing instead of the 55-inch monsters a 72 dpi assumption produces.
"""
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError

from .errors import ToolError
from .merge import merge_pdfs

DEFAULT_IMAGE_DPI = 300
# Outside this range, embedded dpi metadata is more likely wrong than useful.
_MIN_SANE_DPI = 36
_MAX_SANE_DPI = 1200


def _declared_dpi(image: Image.Image) -> float:
    value = image.info.get("dpi")
    if not value:
        return float(DEFAULT_IMAGE_DPI)
    horizontal = float(value[0])
    if _MIN_SANE_DPI <= horizontal <= _MAX_SANE_DPI:
        return horizontal
    return float(DEFAULT_IMAGE_DPI)


def prepare_image(path) -> Tuple[Image.Image, float]:
    """Return a print-ready copy of the image plus the dpi to lay it out at."""
    path = Path(path)
    try:
        with Image.open(str(path)) as opened:
            opened.load()
            dpi = _declared_dpi(opened)
            # A phone photo is stored sideways with an orientation tag; without
            # this the page comes out rotated.
            image = ImageOps.exif_transpose(opened)
            if image.mode == "CMYK":
                return image.copy(), dpi
            if image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            ):
                # PDF pages have no transparency to fall back on, and the
                # default fallback would be black.
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (255, 255, 255))
                flattened.paste(rgba, mask=rgba.split()[-1])
                return flattened, dpi
            if image.mode != "RGB":
                return image.convert("RGB"), dpi
            return image.copy(), dpi
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ToolError("Could not read image {0}: {1}".format(path.name, exc))


def images_to_pdf(paths: List[Path], dst, work_dir: Optional[Path] = None) -> Path:
    """Convert ``paths`` into a single PDF at ``dst``, one page per image."""
    if not paths:
        raise ValueError("images_to_pdf needs at least one image")
    dst = Path(dst)
    created_temp = work_dir is None
    directory = Path(tempfile.mkdtemp(prefix="img2pdf-")) if created_temp else Path(work_dir)
    try:
        page_paths = []
        for index, path in enumerate(paths):
            image, dpi = prepare_image(path)
            page_path = directory / "page_{0:04d}.pdf".format(index)
            try:
                image.save(str(page_path), "PDF", resolution=dpi)
            finally:
                image.close()
            page_paths.append(page_path)
        return merge_pdfs(page_paths, dst)
    finally:
        if created_temp:
            import shutil as _shutil

            _shutil.rmtree(str(directory), ignore_errors=True)
