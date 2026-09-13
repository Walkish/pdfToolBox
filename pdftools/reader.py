"""Reading a PDF's pages, with every read failure named the same way.

Merging and splitting both start by opening an untrusted PDF and both have to
answer the same three questions: is it readable at all, is it encrypted, and
is the encryption the harmless kind. Keeping that in one place is what stops
the two from drifting into telling the user different things about the same
file.
"""

from pathlib import Path
from typing import List

from pypdf import PageObject, PdfReader
from pypdf.errors import PdfReadError

from .errors import ToolError


def read_pages(path) -> List[PageObject]:
    """Every page of ``path``, in document order.

    The ``PdfReader`` itself is deliberately not returned: the pages hold a
    reference back to it, so it stays alive as long as they do, and a caller
    that never sees it cannot start reading the file a second way.

    An encrypted PDF with an empty user password is decrypted and read --
    that covers the everyday "owner-locked but readable" document, which
    opens in any viewer without a prompt. Anything needing a real password is
    refused: we do not have one and cannot ask for it.
    """
    path = Path(path)
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ToolError("{0} is password protected and cannot be read".format(path.name))
        # Materialized here, inside the guard: pypdf resolves a page lazily,
        # so a damaged page object would otherwise raise later, somewhere
        # with no idea which file it came from.
        return list(reader.pages)
    except ToolError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise ToolError("Could not read {0}: {1}".format(path.name, exc)) from exc
