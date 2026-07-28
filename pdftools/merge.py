"""Page-level PDF merging.

pypdf copies page objects without re-encoding their content streams, so
merging costs no quality. Order is exactly the order given: the caller owns
the ordering decision and this module never re-sorts.
"""

from pathlib import Path
from typing import List

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError

from .errors import ToolError


def merge_pdfs(paths: List[Path], dst) -> Path:
    """Concatenate ``paths`` into ``dst`` in the given order."""
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
    with open(str(dst), "wb") as handle:
        writer.write(handle)
    return dst
