"""Discovery and validation of the external binaries the toolbox depends on.

Everything here fails loudly and early. A missing binary surfaces as a clear
install instruction at startup rather than as a traceback on first upload.
"""
import shutil
import subprocess
from typing import Dict

TIMEOUT_SECONDS = 120

REQUIRED = {
    "gs": "brew install ghostscript",
    "pdfimages": "brew install poppler",
    "pdftoppm": "brew install poppler",
    "pdftotext": "brew install poppler",
    "pdfinfo": "brew install poppler",
}


class MissingBinary(RuntimeError):
    """Raised when a required external binary is not on PATH."""

    def __init__(self, name: str, install_hint: str):
        super().__init__(
            "Required binary {0!r} was not found on PATH. Install it with: {1}".format(
                name, install_hint
            )
        )
        self.name = name
        self.install_hint = install_hint


def find(name: str) -> str:
    """Return the absolute path to ``name``, or raise MissingBinary."""
    path = shutil.which(name)
    if path is None:
        raise MissingBinary(name, REQUIRED.get(name, "install {0}".format(name)))
    return path


def check_all() -> Dict[str, str]:
    """Return {binary name: path} for every requirement, raising on the first gap."""
    return {name: find(name) for name in REQUIRED}


def gs_version() -> str:
    """Return the Ghostscript version string, e.g. ``10.7.1``."""
    completed = subprocess.run(
        [find("gs"), "--version"],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    return completed.stdout.strip()
