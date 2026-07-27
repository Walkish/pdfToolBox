"""Upload validation.

Extension checks alone are not enough: PDFs are an active format and this tool
hands untrusted files to Ghostscript, so content is sniffed as well and a
mislabelled file is rejected before any external tool sees it.
"""
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from werkzeug.utils import secure_filename

MAX_FILE_BYTES = 100 * 1024 * 1024

# Most filesystems this tool runs on (APFS, ext4, ...) reject filenames
# longer than this many *bytes* -- not characters -- with ENAMETOOLONG.
NAME_MAX_BYTES = 255
# Callers store files under a position-prefixed name ("003_...") so two
# uploads sharing a filename never collide; that prefix is reserved out of
# the budget here so a name normalized by this module still fits once a
# caller adds it, whether or not this particular caller actually does.
_POSITION_PREFIX_BYTES = 4

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


def _clamp_utf8(text: str, max_bytes: int) -> str:
    """Trim ``text`` to at most ``max_bytes`` UTF-8 bytes without splitting a
    multibyte character in half.

    NAME_MAX is a byte limit enforced by the OS, not a character limit.
    Measured: ``secure_filename`` (called before this, in
    ``normalize_output_name``) always returns pure ASCII -- it NFKD-folds
    and then encodes with ``errors="ignore"``, so accented Latin letters
    collapse to their bare form and non-Latin scripts (Cyrillic, CJK, ...)
    are dropped entirely -- so byte length and character length agree for
    every input this module actually clamps today. This function still
    slices by encoded bytes rather than characters, and backs off one byte
    at a time until what remains decodes cleanly: that keeps the guarantee
    correct on its own terms rather than relying on secure_filename's
    current behaviour never changing, and it costs nothing when the input
    is already ASCII.
    """
    if max_bytes <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    trimmed = encoded[:max_bytes]
    while trimmed:
        try:
            return trimmed.decode("utf-8")
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
    return ""


def normalize_output_name(name: str, default: str) -> str:
    """A filesystem-safe ``.pdf`` filename, clamped to fit a 255-byte
    NAME_MAX even after a caller prefixes it with a 4-byte upload position
    ("003_").

    ``name`` is untrusted -- a client-supplied upload filename, or the
    ``output_name`` form field -- and ``default`` is the fallback used when
    nothing usable survives sanitizing (e.g. ``"merged.pdf"``). A name that
    is merely too long is not rejected outright: a truncated stem still
    identifies the file well enough, and rejecting the whole request over
    length alone would fail otherwise-legitimate uploads (a Mac filename can
    legally sit right at the OS's own 255-byte limit).
    """
    candidate = secure_filename(name or "") or secure_filename(default) or "output.pdf"
    suffix = ".pdf"
    stem = candidate[: -len(suffix)] if candidate.lower().endswith(suffix) else candidate
    if not stem:
        stem = "output"
    budget = NAME_MAX_BYTES - _POSITION_PREFIX_BYTES - len(suffix)
    stem = _clamp_utf8(stem, budget) or "output"
    return stem + suffix
