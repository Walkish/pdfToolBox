"""Fitting merged pages to one common size.

Merging documents whose pages differ in size produces one that jumps around on
screen and prints at inconsistent scales. This module measures the size that
dominates a batch and scales the odd pages into it, proportionally and centred.
Nothing is re-rendered: content is transformed, so text stays text and vector
art stays vector.
"""

from typing import Dict, Iterable, List, Tuple

from pypdf import PageObject, Transformation
from pypdf.generic import NameObject, RectangleObject

Size = Tuple[float, float]

# Two sizes closer than this are the same size as far as fitting goes: page
# boxes carry rounding noise from whichever producer wrote them, and refitting
# a page over a hundredth of a point buys nothing and costs a transformation.
_SAME_SIZE_TOLERANCE = 0.5

_BOX_NAMES = ("/MediaBox", "/CropBox", "/TrimBox", "/BleedBox", "/ArtBox")


def visible_size(page: PageObject) -> Size:
    """The page's size as displayed, in points.

    The CropBox is what viewers and printers show; the MediaBox describes the
    physical medium, and a page with an A4 MediaBox and a smaller CropBox
    presents at the smaller size. /CropBox is read straight out of the page
    dictionary because pypdf's ``cropbox`` property writes the key as a side
    effect of being read -- measured, that alone grows a one-page merge from
    729 to 754 bytes, which would break the byte-identity guarantee that
    untouched batches carry.
    """
    raw = page.get("/CropBox")
    box = page.mediabox if raw is None else RectangleObject(raw.get_object())
    width = float(box.width)
    height = float(box.height)
    # A page carrying /Rotate 90 with a 595x842 box is displayed as 842x595.
    # Counting it as portrait is how a normalization pass ends up laying half
    # the batch on its side. Rotation may be stored negative; % 360 normalizes.
    if page.rotation % 360 in (90, 270):
        return height, width
    return width, height


def dominant_size(pages: Iterable[PageObject]) -> Size:
    """The most common visible page size in ``pages``.

    Sizes are grouped by their whole-point rounding: producers disagree about
    A4 by a fraction of a point (595.276 x 841.89 from one, 595 x 842 from
    another) and counting those as two sizes defeats the measurement. The value
    returned is the unrounded size of the first page in the winning group --
    returning the rounded key instead would leave untouched pages at 595.276
    and fitted pages at 595, a third of a point apart, which is the very
    not-quite-equal this exists to remove.

    Ties go to the larger area.
    """
    groups: Dict[Tuple[int, int], List] = {}
    for page in pages:
        size = visible_size(page)
        key = (int(round(size[0])), int(round(size[1])))
        if key in groups:
            groups[key][0] += 1
        else:
            groups[key] = [1, size]
    if not groups:
        raise ValueError("dominant_size needs at least one page")
    winner = max(groups.values(), key=lambda group: (group[0], group[1][0] * group[1][1]))
    return winner[1]


def fit_page(page: PageObject, target: Size) -> None:
    """Scale and centre ``page`` into ``target``, mutating it in place.

    A page already displayed at ``target`` is returned untouched, down to not
    reading a property that would write to it: uniform batches are the common
    case and must pay nothing for this feature.

    Landscape content meeting a portrait target is rotated 90 degrees
    counter-clockwise (and the reverse), so the top of the content ends up on
    the left and the reader turns the document clockwise -- the convention
    LaTeX's sideways environments use. Fitting it without rotating would shrink
    a landscape page to a band across the middle at roughly half the printed
    size, which often takes small text below legibility.
    """
    width, height = visible_size(page)
    target_width, target_height = target
    if abs(width - target_width) < _SAME_SIZE_TOLERANCE and abs(height - target_height) < _SAME_SIZE_TOLERANCE:
        return

    # Bake /Rotate into the content stream, so everything below reasons about
    # the geometry the reader sees rather than about a rotation the viewer
    # applies afterwards.
    page.transfer_rotation_to_content()
    box = page.mediabox
    left = float(box.left)
    bottom = float(box.bottom)
    width = float(box.width)
    height = float(box.height)

    # Move the box origin to (0, 0) first: a MediaBox does not have to start
    # there, and scaling about the wrong origin slides the content off-page.
    operation = Transformation().translate(-left, -bottom)
    if (width > height) != (target_width > target_height):
        # rotate() turns counter-clockwise about the origin, which puts the
        # content in negative x; translate it back into the positive quadrant.
        operation = operation.rotate(90).translate(height, 0)
        width, height = height, width

    scale = min(target_width / width, target_height / height)
    operation = operation.scale(scale, scale).translate(
        (target_width - scale * width) / 2.0,
        (target_height - scale * height) / 2.0,
    )
    page.add_transformation(operation)

    # Every box, not just the MediaBox: a stale CropBox makes the viewer clip
    # the page back to its old size and undoes the whole pass.
    for name in _BOX_NAMES:
        page[NameObject(name)] = RectangleObject((0, 0, target_width, target_height))
