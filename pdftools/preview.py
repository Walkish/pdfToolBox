"""Before/after page comparison, so legibility can be judged before printing.

Both sides are rendered at the same dpi and cropped with the same box, so the
only difference visible in the pair is the compression itself. The UI shows the
crops at 1:1 to keep the browser from resampling away the artefacts that
matter.
"""
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

from PIL import Image

from . import binaries
from .errors import ToolError
from .inspect import PdfProfile, profile_pdf

PREVIEW_DPI = 150
CROP_SIZE = (900, 600)
# Body text usually starts about a fifth of the way down a page.
_CROP_TOP_FRACTION = 0.22


def render_page(pdf_path, page: int, dpi: int, out_prefix) -> Path:
    """Render one page to PNG with pdftoppm and return the written file."""
    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    command = [
        binaries.find("pdftoppm"),
        "-png",
        "-r",
        str(dpi),
        "-f",
        str(page),
        "-l",
        str(page),
        "-singlefile",
        str(pdf_path),
        str(out_prefix),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=binaries.TIMEOUT_SECONDS
    )
    output = out_prefix.with_suffix(".png")
    if completed.returncode != 0 or not output.exists():
        raise ToolError(
            "Could not render page {0} of {1}".format(page, Path(pdf_path).name),
            stderr=completed.stderr or completed.stdout,
        )
    return output


def crop_box(size: Tuple[int, int], want: Tuple[int, int]):
    """A window of at most ``want`` pixels, centred horizontally, in the
    upper body of the page, clamped to what the render actually offers."""
    width, height = size
    crop_width = min(want[0], width)
    crop_height = min(want[1], height)
    left = (width - crop_width) // 2
    top = int(height * _CROP_TOP_FRACTION)
    if top + crop_height > height:
        top = max(0, height - crop_height)
    return (left, top, left + crop_width, top + crop_height)


def choose_page(profile: PdfProfile) -> int:
    """The first page with a raster image, since that is where compression
    shows; page 1 when the document has none."""
    pages = sorted(image.page for image in profile.images if image.page > 0)
    return pages[0] if pages else 1


def build_comparison(src_pdf, out_pdf, dest_dir, page: Optional[int] = None) -> Dict[str, object]:
    """Render and crop the same region from both PDFs at the same dpi."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if page is None:
        page = choose_page(profile_pdf(src_pdf))

    before_png = render_page(src_pdf, page, PREVIEW_DPI, dest_dir / "before_full")
    after_png = render_page(out_pdf, page, PREVIEW_DPI, dest_dir / "after_full")

    with Image.open(str(before_png)) as before, Image.open(str(after_png)) as after:
        # Rounding can make the two renders differ by a pixel; crop to the
        # smaller of the two so the pair stays directly comparable.
        common = (min(before.width, after.width), min(before.height, after.height))
        box = crop_box(common, CROP_SIZE)
        before_crop = dest_dir / "before.png"
        after_crop = dest_dir / "after.png"
        before.crop(box).save(str(before_crop), "PNG")
        after.crop(box).save(str(after_crop), "PNG")

    before_png.unlink()
    after_png.unlink()
    return {"before": before_crop, "after": after_crop, "page": page, "dpi": PREVIEW_DPI}
