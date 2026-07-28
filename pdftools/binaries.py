"""Discovery and validation of the external binaries the toolbox depends on.

Everything here fails loudly and early. A missing binary surfaces as a clear
install instruction at startup rather than as a traceback on first upload.
"""

import platform
import shutil
import subprocess
from typing import Dict, List

TIMEOUT_SECONDS = 120

# Which package provides each requirement. Two of them come from one package,
# so the install hint is derived from this rather than repeated per binary.
_PACKAGES = {
    "gs": "ghostscript",
    "pdfimages": "poppler",
    "pdftoppm": "poppler",
    "pdftotext": "poppler",
    "pdfinfo": "poppler",
}

# The keys above are *logical* names; on some platforms the file on disk is
# called something else. Ghostscript's console executable is `gswin64c.exe` on
# Windows (`gswin32c.exe` on a 32-bit install) and there is no `gs` at all, so
# looking only for "gs" makes the app refuse to start on an otherwise correct
# Windows setup. The poppler tools keep their names everywhere; on Windows
# shutil.which resolves the .exe through PATHEXT.
_EXECUTABLES = {
    "gs": ["gs", "gswin64c", "gswin32c"],
}

_INSTALL_HINTS = {
    "Darwin": {
        "ghostscript": "brew install ghostscript",
        "poppler": "brew install poppler",
    },
    "Windows": {
        "ghostscript": "choco install ghostscript (or: scoop install ghostscript)",
        "poppler": "choco install poppler (or: scoop install poppler)",
    },
    "Linux": {
        "ghostscript": "sudo apt install ghostscript",
        "poppler": "sudo apt install poppler-utils",
    },
}


def _install_hint(name: str) -> str:
    """The platform-appropriate install command for whatever provides ``name``."""
    package = _PACKAGES.get(name, name)
    # Anything not macOS or Windows is treated as Linux: apt is the most likely
    # right answer, and a slightly wrong package manager in a hint is far more
    # useful than no hint at all.
    per_platform = _INSTALL_HINTS.get(platform.system(), _INSTALL_HINTS["Linux"])
    return per_platform.get(package, "install {0}".format(package))


def executable_names(name: str) -> List[str]:
    """Executable names to try on this platform for a logical requirement."""
    return _EXECUTABLES.get(name, [name])


REQUIRED = {name: _install_hint(name) for name in _PACKAGES}


class MissingBinary(RuntimeError):
    """Raised when a required external binary is not on PATH."""

    def __init__(self, name: str, install_hint: str):
        super().__init__("Required binary {0!r} was not found on PATH. Install it with: {1}".format(name, install_hint))
        self.name = name
        self.install_hint = install_hint


def find(name: str) -> str:
    """Return the absolute path for the logical requirement ``name``.

    Tries each platform-appropriate executable name in turn, so callers can ask
    for "gs" everywhere and still get Windows' ``gswin64c.exe``. Raises
    MissingBinary when none of them is on PATH.
    """
    for candidate in executable_names(name):
        path = shutil.which(candidate)
        if path is not None:
            return path
    raise MissingBinary(name, REQUIRED.get(name, "install {0}".format(name)))


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
