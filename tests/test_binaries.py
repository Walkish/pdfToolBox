"""Tests for external binary discovery."""

import pytest

from pdftools import binaries


def test_find_returns_an_absolute_path_for_an_installed_binary():
    path = binaries.find("gs")
    assert path.startswith("/")
    assert path.endswith("gs")


def test_find_raises_with_an_install_hint_when_the_binary_is_absent(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda name: None)
    with pytest.raises(binaries.MissingBinary) as excinfo:
        binaries.find("gs")
    assert excinfo.value.name == "gs"
    assert "brew install ghostscript" in str(excinfo.value)


def test_check_all_reports_every_required_binary():
    found = binaries.check_all()
    assert set(found) == set(binaries.REQUIRED)
    assert all(path.startswith("/") for path in found.values())


def test_gs_version_is_at_least_major_10():
    major = int(binaries.gs_version().split(".")[0])
    assert major >= 10
