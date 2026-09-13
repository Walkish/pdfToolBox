"""Taking one document apart: keeping, reordering and turning its pages.

Pages are copied through pypdf without re-encoding, so nothing here costs
quality: dropping a page removes it, reordering moves it, and a quarter turn
is a ``/Rotate`` entry rather than a re-render. The caller owns every
decision -- this module never guesses which pages are worth keeping.
"""

from pathlib import Path
from typing import List, Optional, Sequence

from pypdf import PageObject, PdfWriter

from . import pagesize, reader

# The only angles a page can be turned by. Deliberately not imported from
# ``images``: that module turns pictures on their way into a PDF and this one
# turns pages already in one, and a shared constant would be the only thing
# tying two unrelated modules together. That they agree is asserted by a test.
VALID_ROTATIONS = (0, 90, 180, 270)


def page_count(src) -> int:
    """How many pages ``src`` has."""
    return len(reader.read_pages(src))


def _checked_rotations(order: Sequence[int], rotations: Optional[Sequence[int]]) -> List[int]:
    if not order:
        raise ValueError("A selection needs at least one page")
    if rotations is None:
        return [0] * len(order)
    if len(rotations) != len(order):
        raise ValueError("Expected one rotation per page, got {0} for {1}".format(len(rotations), len(order)))
    for rotation in rotations:
        if rotation not in VALID_ROTATIONS:
            raise ValueError(
                "Rotation {0} is not one of {1}".format(rotation, ", ".join(str(valid) for valid in VALID_ROTATIONS))
            )
    return list(rotations)


def _checked_order(order: Sequence[int], pages: Sequence[PageObject]) -> List[int]:
    for index in order:
        # Checked rather than left to Python's own indexing: a negative index
        # is perfectly legal there and would quietly write a page from the
        # other end of the document.
        if index < 0 or index >= len(pages):
            raise ValueError("Page {0} is not in a {1}-page document".format(index, len(pages)))
    return list(order)


def _write(
    pages: Sequence[PageObject],
    order: Sequence[int],
    rotations: Sequence[int],
    dst: Path,
    normalize_pages: bool,
) -> Path:
    writer = PdfWriter()
    for index, rotation in zip(order, rotations):
        page = writer.add_page(pages[index])
        if rotation:
            # Added to the page's own rotation, not set: a scan can arrive
            # already turned, and setting would silently straighten it.
            page.rotate(rotation)
    if normalize_pages:
        target = pagesize.dominant_size(writer.pages)
        for page in writer.pages:
            pagesize.fit_page(page, target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(str(dst), "wb") as handle:
        writer.write(handle)
    return dst


def build_document(
    src,
    dst,
    order: Sequence[int],
    rotations: Optional[Sequence[int]] = None,
    normalize_pages: bool = False,
) -> Path:
    """Write the pages of ``src`` named by ``order`` into ``dst``.

    ``order`` holds zero-based page numbers of ``src`` in the order they
    should appear, and ``rotations`` one clockwise angle for each of them,
    added to whatever rotation the page already carries. Pulling a single
    page out is the same call with a one-element ``order``.

    With ``normalize_pages``, every written page is fitted to the size that
    dominates the selection, exactly as merging does. Note that this and
    ``rotations`` pull against each other: fitting turns content to match the
    target's orientation, so a page turned by hand against a batch of the
    other orientation is turned straight back.
    """
    rotations = _checked_rotations(order, rotations)
    pages = reader.read_pages(src)
    order = _checked_order(order, pages)
    return _write(pages, order, rotations, Path(dst), normalize_pages)


def build_pages(src, dest_dir, order: Sequence[int], rotations: Optional[Sequence[int]] = None) -> List[Path]:
    """One single-page PDF per chosen page, written into ``dest_dir``.

    Files are named after the page's own number in ``src`` (one-based), not
    its position in ``order``: that number is what the user saw next to the
    page on screen, and unlike a position it does not change when the pages
    are shuffled. Returned in the order given.

    Sizes are never matched here. Fitting measures one page against the rest
    of the batch, and there is no batch when every page is its own document.
    """
    rotations = _checked_rotations(order, rotations)
    # Read once for the whole set: a hundred-page document parsed once per
    # page is a hundred parses of the same file.
    pages = reader.read_pages(src)
    order = _checked_order(order, pages)
    dest_dir = Path(dest_dir)
    written = []
    for index, rotation in zip(order, rotations):
        destination = dest_dir / "page-{0}.pdf".format(index + 1)
        written.append(_write(pages, [index], [rotation], destination, False))
    return written
