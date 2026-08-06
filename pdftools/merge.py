"""Page-level PDF merging.

pypdf copies page objects without re-encoding their content streams, so
merging costs no quality. Order is exactly the order given: the caller owns
the ordering decision and this module never re-sorts.
"""

from pathlib import Path
from typing import List

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from . import pagesize
from .errors import ToolError


def merge_pdfs(paths: List[Path], dst, normalize_pages: bool = False) -> Path:
    """Concatenate ``paths`` into ``dst`` in the given order.

    With ``normalize_pages``, every page is fitted to the size that dominates
    the merged batch, so a document mixing A4 scans with pages built from
    photos stops jumping around on screen and printing at inconsistent scales.
    It defaults to off because ``images_to_pdf`` merges its per-image pages
    through this same function and must keep them sized from their own images.
    """
    if not paths:
        raise ValueError("merge_pdfs needs at least one input PDF")
    dst = Path(dst)
    writer = PdfWriter()
    for path in paths:
        path = Path(path)
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                # An empty user password covers the common "owner-locked but
                # readable" case; anything else needs a password we do not have.
                if reader.decrypt("") == 0:
                    raise ToolError("{0} is password protected and cannot be merged".format(path.name))
            for page in reader.pages:
                writer.add_page(page)
        except ToolError:
            raise
        except (PdfReadError, OSError, ValueError) as exc:
            raise ToolError("Could not read {0}: {1}".format(path.name, exc)) from exc
    if normalize_pages:
        # Measured across the whole batch, so this cannot run until every input
        # has been read.
        target = pagesize.dominant_size(writer.pages)
        for page in writer.pages:
            pagesize.fit_page(page, target)
    with open(str(dst), "wb") as handle:
        writer.write(handle)
    return dst
