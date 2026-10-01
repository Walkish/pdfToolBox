"""Tests for source-PDF profiling."""

import pytest

from pdftools import inspect as pdfinspect
from pdftools.errors import ToolError

SAMPLE_LISTING = """page   num  type   width height color comp bpc  enc interp  object ID x-ppi y-ppi size ratio
--------------------------------------------------------------------------------------------
   1     0 image    1275  1650  gray     1   8  jpeg   no        10  0   150   150 46.6K 2.3%
   2     1 image    3400  4400  rgb      3   8  jpeg   no        14  0   400   400  1.2M 1.0%
"""


def test_parse_extracts_geometry_and_ppi_for_every_row():
    images = pdfinspect.parse_pdfimages_list(SAMPLE_LISTING)
    assert len(images) == 2
    first, second = images
    assert (first.page, first.width, first.height, first.color, first.bpc) == (1, 1275, 1650, "gray", 8)
    assert first.ppi == 150.0
    assert second.page == 2
    assert second.ppi == 400.0


def test_parse_ignores_headers_and_separator_lines():
    assert pdfinspect.parse_pdfimages_list("no numeric rows here\n---\n") == []


def test_parse_tolerates_unparseable_ppi_columns():
    listing = SAMPLE_LISTING.replace("  150   150", "  ---   ---")
    images = pdfinspect.parse_pdfimages_list(listing)
    assert images[0].ppi is None


def test_profile_of_a_scan_reports_its_resolution(scan_pdf_600dpi):
    profile = pdfinspect.profile_pdf(scan_pdf_600dpi)
    assert len(profile.images) == 1
    # Assert not-None separately: the field is Optional, and a None here
    # should fail as "expected a measurement, got none" rather than blowing up
    # inside the comparison.
    assert profile.max_ppi is not None
    assert 550 <= profile.max_ppi <= 650


def test_profile_of_a_vector_pdf_has_no_images(vector_pdf_2pages):
    profile = pdfinspect.profile_pdf(vector_pdf_2pages)
    assert profile.images == []
    assert profile.max_ppi is None


def test_the_scan_floor_of_a_scan_is_its_resolution(scan_pdf_150dpi):
    floor = pdfinspect.scan_floor_ppi(scan_pdf_150dpi)
    assert floor is not None
    assert 130 <= floor <= 170


def test_an_ocr_scan_still_has_a_scan_floor(ocr_scan_pdf):
    """The regression test for the false-negative: a full-page raster image
    with a real, extractable text layer over it is still a scan page, because
    its legibility is still bounded by the image resolution."""
    assert pdfinspect.scan_floor_ppi(ocr_scan_pdf) is not None


def test_a_vector_pdf_has_no_scan_floor(vector_pdf_2pages):
    assert pdfinspect.scan_floor_ppi(vector_pdf_2pages) is None


def test_profile_of_a_corrupt_pdf_raises_tool_error(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a pdf at all, just garbage bytes")
    with pytest.raises(ToolError) as excinfo:
        pdfinspect.profile_pdf(path)
    assert excinfo.value.stderr
