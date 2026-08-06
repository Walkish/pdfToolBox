"""Turning raster images into a single PDF.

Each page is sized from its own image so proportions are exact and no
letterboxing is added. Physical size follows the image's own dpi metadata when
it has any, and falls back to 300 dpi otherwise, which keeps pages a sane size
for printing instead of the 55-inch monsters a 72 dpi assumption produces.
"""

import shutil
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

VALID_ROTATIONS = (0, 90, 180, 270)

# Pillow's Transpose constants turn counter-clockwise, so the clockwise angle
# the UI sends maps to its mirror image here. Measured: ROTATE_270 sends the
# top-left pixel to the top-right, which is the clockwise quarter turn, and
# matches CSS rotate(90deg) so the preview and the page agree.
_CLOCKWISE_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def _declared_dpi(image: Image.Image) -> float:
    value = image.info.get("dpi")
    if not value:
        return float(DEFAULT_IMAGE_DPI)
    if image.format == "JPEG" and image.info.get("jfif_unit", 0) not in (1, 2):
        # Pillow's JpegImagePlugin synthesizes info["dpi"] = (72, 72) for any
        # JPEG that carries an EXIF segment (say, just an orientation tag)
        # but no genuine resolution declaration -- see _read_dpi_from_exif in
        # PIL/JpegImagePlugin.py. jfif_unit 0 means "no physical unit" per the
        # JFIF spec, so a (72, 72) reading under that condition is a synthetic
        # default, not real metadata, and must not be trusted.
        return float(DEFAULT_IMAGE_DPI)
    horizontal = float(value[0])
    if _MIN_SANE_DPI <= horizontal <= _MAX_SANE_DPI:
        return horizontal
    return float(DEFAULT_IMAGE_DPI)


def prepare_image(path, rotation: int = 0) -> Tuple[Image.Image, float]:
    """Return a print-ready copy of the image plus the dpi to lay it out at.

    ``rotation`` is clockwise degrees and must be one of ``VALID_ROTATIONS``.
    It is applied after the EXIF correction, so a user's rotation lands on top
    of the orientation the camera already recorded rather than fighting it.
    """
    if rotation not in VALID_ROTATIONS:
        raise ValueError("rotation must be one of {0}, got {1!r}".format(VALID_ROTATIONS, rotation))
    path = Path(path)
    try:
        with Image.open(str(path)) as opened:
            opened.load()
            dpi = _declared_dpi(opened)
            # A phone photo is stored sideways with an orientation tag; without
            # this the page comes out rotated.
            image = ImageOps.exif_transpose(opened)
            if rotation:
                image = image.transpose(_CLOCKWISE_TRANSPOSE[rotation])
            if image.mode == "CMYK":
                return image.copy(), dpi
            if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                # PDF pages have no transparency to fall back on, and the
                # default fallback would be black.
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (255, 255, 255))
                flattened.paste(rgba, mask=rgba.split()[-1])
                return flattened, dpi
            if image.mode != "RGB":
                return image.convert("RGB"), dpi
            return image.copy(), dpi
    # DecompressionBombError derives straight from Exception, not from
    # OSError or ValueError, so it needs naming explicitly: without it a
    # bomb-sized image escapes as a bare Pillow exception. validate.py
    # rejects those before they get here, but this module is usable on its
    # own and must not depend on a caller having validated first.
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        Image.DecompressionBombError,
    ) as exc:
        raise ToolError("Could not read image {0}: {1}".format(path.name, exc)) from exc


def images_to_pdf(
    paths: List[Path],
    dst,
    work_dir: Optional[Path] = None,
    rotations: Optional[List[int]] = None,
) -> Path:
    """Convert ``paths`` into a single PDF at ``dst``, one page per image.

    ``rotations`` is one clockwise angle per path, or None for no rotation at
    all -- which is what every caller that does not care passes implicitly.
    """
    if not paths:
        raise ValueError("images_to_pdf needs at least one image")
    if rotations is None:
        rotations = [0] * len(paths)
    elif len(rotations) != len(paths):
        raise ValueError(
            "images_to_pdf needs one rotation per image, got {0} for {1}".format(len(rotations), len(paths))
        )
    dst = Path(dst)
    created_temp = work_dir is None
    directory = Path(tempfile.mkdtemp(prefix="img2pdf-")) if created_temp else Path(work_dir)
    try:
        page_paths = []
        for index, path in enumerate(paths):
            image, dpi = prepare_image(path, rotations[index])
            page_path = directory / "page_{0:04d}.pdf".format(index)
            try:
                image.save(str(page_path), "PDF", resolution=dpi)
            finally:
                image.close()
            page_paths.append(page_path)
        return merge_pdfs(page_paths, dst)
    finally:
        if created_temp:
            shutil.rmtree(str(directory), ignore_errors=True)
