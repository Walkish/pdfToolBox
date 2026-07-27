"""Upload validation.

Extension checks alone are not enough: PDFs are an active format and this tool
hands untrusted files to Ghostscript, so content is sniffed as well and a
mislabelled file is rejected before any external tool sees it.
"""
from pathlib import Path

from PIL import Image, UnidentifiedImageError

MAX_FILE_BYTES = 100 * 1024 * 1024

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}

_PDF_MAGIC = b"%PDF-"
# Some real-world PDFs carry a little junk before the header; the spec allows
# the marker anywhere in the first kilobyte.
_PDF_MAGIC_WINDOW = 1024


class ValidationError(ValueError):
    """An upload failed validation. The message is safe to show the user."""


def _check_size(path: Path, display_name: str) -> None:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValidationError(
            "{0} is too large ({1:.1f} MB); the limit is {2} MB".format(
                display_name, size / 1048576.0, MAX_FILE_BYTES // 1048576
            )
        )
    if size == 0:
        raise ValidationError("{0} is empty".format(display_name))


def validate_pdf_file(path, display_name: str) -> None:
    path = Path(path)
    if Path(display_name).suffix.lower() not in PDF_EXTENSIONS:
        raise ValidationError("{0} is not a .pdf file".format(display_name))
    _check_size(path, display_name)
    with open(str(path), "rb") as handle:
        head = handle.read(_PDF_MAGIC_WINDOW)
    if _PDF_MAGIC not in head:
        raise ValidationError(
            "{0} is not a PDF: the %PDF- header is missing".format(display_name)
        )


def validate_image_file(path, display_name: str) -> None:
    path = Path(path)
    suffix = Path(display_name).suffix.lower()
    expected = IMAGE_EXTENSIONS.get(suffix)
    if expected is None:
        raise ValidationError(
            "{0} is not a supported image type ({1})".format(
                display_name, ", ".join(sorted(IMAGE_EXTENSIONS))
            )
        )
    _check_size(path, display_name)
    try:
        with Image.open(str(path)) as image:
            image.verify()
            actual = image.format
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("{0} is not a readable image".format(display_name))
    if actual != expected:
        raise ValidationError(
            "{0} does not match its extension: the file is {1}".format(
                display_name, actual
            )
        )
