"""Tests for external binary discovery."""

import os

import pytest

from pdftools import binaries


def test_find_returns_an_absolute_path_for_an_installed_binary():
    path = binaries.find("gs")
    # os.path.isabs rather than a leading "/": on Windows this resolves to
    # something like C:\Program Files\gs\...\gswin64c.exe.
    assert os.path.isabs(path)
    assert os.path.basename(path).startswith("gs")


def test_find_raises_with_an_install_hint_when_the_binary_is_absent(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    with pytest.raises(binaries.MissingBinary) as excinfo:
        binaries.find("gs")
    assert excinfo.value.name == "gs"
    # Compared against REQUIRED rather than a hardcoded "brew install ...":
    # the hint is platform-derived, so hardcoding one would make this test
    # fail on the platforms it is meant to help.
    assert binaries.REQUIRED["gs"] in str(excinfo.value)


def test_the_install_hint_names_a_package_manager_that_exists_on_this_platform():
    hints = " ".join(binaries.REQUIRED.values())
    assert any(manager in hints for manager in ("brew", "choco", "scoop", "apt"))


@pytest.mark.parametrize(
    "system, expected",
    [
        ("Darwin", "brew"),
        ("Windows", "choco"),
        ("Linux", "apt"),
        # Anything unrecognised falls back to the Linux hints rather than to no
        # hint at all: a slightly wrong package manager still points somewhere.
        ("FreeBSD", "apt"),
    ],
)
def test_install_hints_are_chosen_per_platform(monkeypatch, system, expected):
    """The Windows and Linux hints are unreachable from a macOS test run
    otherwise, so they would ship entirely unverified."""
    monkeypatch.setattr(binaries.platform, "system", lambda: system)
    for name in binaries.REQUIRED:
        assert expected in binaries._install_hint(name)


def test_ghostscript_is_looked_up_under_its_windows_name_too():
    """Ghostscript's console executable is gswin64c.exe on Windows and there is
    no `gs` at all, so a lookup that only tried "gs" would make the app refuse
    to start on a correctly installed Windows machine."""
    assert binaries.executable_names("gs") == ["gs", "gswin64c", "gswin32c"]


def test_find_falls_back_to_the_windows_executable_name(monkeypatch):
    """Simulates Windows without needing Windows: only gswin64c resolves."""
    monkeypatch.setattr(
        binaries.shutil,
        "which",
        lambda name: r"C:\Program Files\gs\bin\gswin64c.exe" if name == "gswin64c" else None,
    )
    assert binaries.find("gs").endswith("gswin64c.exe")


def test_the_poppler_tools_are_looked_up_under_their_own_names():
    """Unlike Ghostscript, poppler keeps its executable names on every platform;
    Windows resolves the .exe through PATHEXT."""
    for name in ("pdfimages", "pdftoppm", "pdftotext", "pdfinfo"):
        assert binaries.executable_names(name) == [name]


def test_check_all_reports_every_required_binary():
    found = binaries.check_all()
    assert set(found) == set(binaries.REQUIRED)
    assert all(os.path.isabs(path) for path in found.values())


def test_gs_version_is_at_least_major_10():
    major = int(binaries.gs_version().split(".")[0])
    assert major >= 10
