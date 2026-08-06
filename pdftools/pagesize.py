"""Fitting merged pages to one common size.

Merging documents whose pages differ in size produces one that jumps around on
screen and prints at inconsistent scales. This module measures the size that
dominates a batch and scales the odd pages into it, proportionally and centred.
Nothing is re-rendered: content is transformed, so text stays text and vector
art stays vector.
"""

from typing import Dict, Iterable, List, Tuple

from pypdf import PageObject
from pypdf.generic import RectangleObject

Size = Tuple[float, float]


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
