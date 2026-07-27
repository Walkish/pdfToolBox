"""The fixtures are inputs to every other test, so they are verified themselves."""
import subprocess

from PIL import Image


def _pdftotext(path):
    completed = subprocess.run(
        ["pdftotext", str(path), "-"], capture_output=True, text=True, timeout=120
    )
    return completed.stdout


def test_scan_fixture_is_a_pdf_at_roughly_600_dpi(scan_pdf_600dpi):
    with open(scan_pdf_600dpi, "rb") as handle:
        assert handle.read(5) == b"%PDF-"
    listing = subprocess.run(
        ["pdfimages", "-list", str(scan_pdf_600dpi)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    ppi_values = [
        int(line.split()[12]) for line in listing.splitlines() if line.split()[:1] and line.split()[0].isdigit()
    ]
    assert ppi_values
    assert all(550 <= value <= 650 for value in ppi_values)


def test_scan_fixture_has_no_extractable_text(scan_pdf_600dpi):
    assert len(_pdftotext(scan_pdf_600dpi).strip()) < 10


def test_vector_fixture_has_two_pages_of_extractable_text_in_order(vector_pdf_2pages):
    text = _pdftotext(vector_pdf_2pages)
    assert text.index("MARKER-1") < text.index("MARKER-2")


def test_vector_factory_builds_the_requested_markers(vector_pdf_factory):
    path = vector_pdf_factory(["ALPHA", "BETA", "GAMMA"])
    text = _pdftotext(path)
    assert text.index("ALPHA") < text.index("BETA") < text.index("GAMMA")


def test_cmyk_fixture_page_image_is_cmyk(cmyk_pdf):
    listing = subprocess.run(
        ["pdfimages", "-list", str(cmyk_pdf)], capture_output=True, text=True, timeout=120
    ).stdout
    assert "cmyk" in listing.lower()


def test_rgba_fixture_has_an_alpha_channel(png_rgba):
    with Image.open(png_rgba) as image:
        assert image.mode == "RGBA"


def test_rotated_fixture_carries_exif_orientation_six(jpeg_rotated):
    with Image.open(jpeg_rotated) as image:
        assert image.getexif()[274] == 6


def test_dpi_fixture_declares_300_dpi(jpeg_300dpi):
    with Image.open(jpeg_300dpi) as image:
        assert image.info["dpi"] == (300, 300)
