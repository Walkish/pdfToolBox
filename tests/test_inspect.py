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


def test_profile_of_a_scan_reports_scan_and_no_text(scan_pdf_600dpi):
    profile = pdfinspect.profile_pdf(scan_pdf_600dpi)
    assert profile.page_count == 1
    assert profile.has_text is False
    assert profile.is_scan is True
    assert 550 <= profile.max_ppi <= 650


def test_profile_of_a_vector_pdf_reports_text_and_not_a_scan(vector_pdf_2pages):
    profile = pdfinspect.profile_pdf(vector_pdf_2pages)
    assert profile.page_count == 2
    assert profile.has_text is True
    assert profile.is_scan is False
    assert profile.images == []
    assert profile.max_ppi is None


def test_profile_ppi_statistics_span_min_median_and_max(scan_pdf_150dpi):
    profile = pdfinspect.profile_pdf(scan_pdf_150dpi)
    assert profile.min_ppi == profile.max_ppi
    assert profile.median_ppi == profile.min_ppi
    assert 130 <= profile.min_ppi <= 170


def test_scan_image_covers_nearly_the_whole_page(scan_pdf_600dpi):
    profile = pdfinspect.profile_pdf(scan_pdf_600dpi)
    assert profile.images[0].coverage >= 0.9


def test_profile_of_an_ocr_scan_reports_text_and_is_still_a_scan(ocr_scan_pdf):
    """The regression test for the false-negative: a full-page raster image
    with a real, extractable text layer over it must still be flagged as a
    scan, because its legibility is still bounded by the image resolution."""
    profile = pdfinspect.profile_pdf(ocr_scan_pdf)
    assert profile.has_text is True
    assert profile.is_scan is True


def test_profile_of_a_mostly_text_document_with_one_scanned_page_is_not_a_scan(
    mixed_scan_and_text_pdf,
):
    profile = pdfinspect.profile_pdf(mixed_scan_and_text_pdf)
    assert profile.page_count == 3
    assert profile.is_scan is False


def test_profile_of_a_corrupt_pdf_raises_tool_error(tmp_path):
    path = tmp_path / "corrupt.pdf"
    path.write_bytes(b"not a pdf at all, just garbage bytes")
    with pytest.raises(ToolError) as excinfo:
        pdfinspect.profile_pdf(path)
    assert excinfo.value.stderr
