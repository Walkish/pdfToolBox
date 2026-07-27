"""Tests for upload validation."""
import pytest

from pdftools import validate


def test_a_real_pdf_passes(vector_pdf_2pages):
    validate.validate_pdf_file(vector_pdf_2pages, "doc.pdf")


def test_a_zip_renamed_to_pdf_is_rejected(tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_bytes(b"PK\x03\x04" + b"\x00" * 200)
    with pytest.raises(validate.ValidationError) as excinfo:
        validate.validate_pdf_file(fake, "doc.pdf")
    assert "not a PDF" in str(excinfo.value)


def test_a_wrong_extension_is_rejected(vector_pdf_2pages):
    with pytest.raises(validate.ValidationError):
        validate.validate_pdf_file(vector_pdf_2pages, "doc.txt")


def test_a_pdf_header_after_leading_junk_is_still_accepted(tmp_path):
    lenient = tmp_path / "doc.pdf"
    lenient.write_bytes(b"\n\n   " + b"%PDF-1.7\n" + b"rest of file")
    validate.validate_pdf_file(lenient, "doc.pdf")


def test_an_oversized_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "MAX_FILE_BYTES", 10)
    big = tmp_path / "doc.pdf"
    big.write_bytes(b"%PDF-1.7\n" + b"x" * 100)
    with pytest.raises(validate.ValidationError) as excinfo:
        validate.validate_pdf_file(big, "doc.pdf")
    assert "too large" in str(excinfo.value)


def test_real_images_pass(png_rgba, jpeg_300dpi):
    validate.validate_image_file(png_rgba, "a.png")
    validate.validate_image_file(jpeg_300dpi, "b.jpg")


def test_a_pdf_renamed_to_png_is_rejected(vector_pdf_2pages):
    with pytest.raises(validate.ValidationError):
        validate.validate_image_file(vector_pdf_2pages, "sneaky.png")


def test_a_jpeg_named_png_is_rejected(jpeg_300dpi):
    with pytest.raises(validate.ValidationError) as excinfo:
        validate.validate_image_file(jpeg_300dpi, "mislabelled.png")
    assert "does not match" in str(excinfo.value)
