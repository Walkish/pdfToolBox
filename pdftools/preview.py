"""Before/after page comparison, so legibility can be judged before printing.

Both sides are rendered at the same dpi and cropped with the same box, so the
only difference visible in the pair is the compression itself. The UI shows the
crops at 1:1 to keep the browser from resampling away the artefacts that
matter.
"""

import dataclasses
import subprocess
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from . import binaries
from .errors import ToolError
from .inspect import PdfProfile, profile_pdf

PREVIEW_DPI = 150
# Both crops are shown side by side at 1:1, so two of these plus the gap
# between them must fit the width the page gives .pair in static/style.css:
# a 900 px body, less its 20 px padding either side, less .result's 14 px
# either side, leaves about 832 px. Two 400 px columns and a 16 px gap fit;
# anything wider makes .pair scroll horizontally, and a comparison you have
# to scroll between is not a comparison. At 150 dpi, 400x560 px is still
# about 2.7 x 3.7 inches of body text -- enough to judge legibility.
# Keep this in step with `body { max-width }` and `.pair { gap }`.
CROP_SIZE = (400, 560)
# Body text usually starts about a fifth of the way down a page.
_CROP_TOP_FRACTION = 0.22


@dataclasses.dataclass(frozen=True)
class Comparison:
    """The before/after crop pair, plus what was rendered to produce it.

    A dataclass rather than a dict: callers read ``before``/``after`` as real
    paths, and a ``Dict[str, object]`` return forced every one of them to know
    the value types without being able to state them.
    """

    before: Path
    after: Path
    page: int
    dpi: int


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
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=binaries.TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        # Every other shell-out in this package converts a timeout into a
        # ToolError; without this a 120 s pdftoppm on a huge page reaches the
        # route as a raw TimeoutExpired and answers a generic HTTP 500.
        raise ToolError(
            "pdftoppm timed out after {0}s rendering page {1} of {2}".format(
                binaries.TIMEOUT_SECONDS, page, Path(pdf_path).name
            ),
            stderr=str(exc),
        ) from exc
    # pdftoppm appends ".png" to the prefix string literally -- it does not
    # replace an existing suffix the way Path.with_suffix does. A prefix
    # basename containing a dot (e.g. "page.v1") is written as "page.v1.png",
    # not "page.png", so the expected path must be built with append, not
    # replace, semantics.
    output = Path(str(out_prefix) + ".png")
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


def build_comparison(src_pdf, out_pdf, dest_dir, page: Optional[int] = None) -> Comparison:
    """Render and crop the same region from both PDFs at the same dpi."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    if page is None:
        page = choose_page(profile_pdf(src_pdf))

    before_png = None
    after_png = None
    try:
        before_png = render_page(src_pdf, page, PREVIEW_DPI, dest_dir / "before_full")
        after_png = render_page(out_pdf, page, PREVIEW_DPI, dest_dir / "after_full")

        try:
            with Image.open(str(before_png)) as before, Image.open(str(after_png)) as after:
                # Rounding can make the two renders differ by a pixel; crop to
                # the smaller of the two so the pair stays directly comparable.
                common = (
                    min(before.width, after.width),
                    min(before.height, after.height),
                )
                box = crop_box(common, CROP_SIZE)
                before_crop = dest_dir / "before.png"
                after_crop = dest_dir / "after.png"
                before.crop(box).save(str(before_crop), "PNG")
                after.crop(box).save(str(after_crop), "PNG")
        # Pillow raised nothing typed here before, so a render too large for
        # its own limits, or a disk error writing the crops, surfaced as a raw
        # traceback and a generic HTTP 500 instead of a reportable failure.
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ToolError("Could not crop the comparison for page {0}: {1}".format(page, exc)) from exc
    finally:
        # These are intermediate full-page renders, not the function's
        # return values -- remove them whether we succeeded or raised partway
        # through, so a partial failure never leaves a full-resolution PNG
        # behind in a job's previews directory.
        for full_render in (before_png, after_png):
            if full_render is not None and full_render.exists():
                full_render.unlink()

    return Comparison(before=before_crop, after=after_crop, page=page, dpi=PREVIEW_DPI)
