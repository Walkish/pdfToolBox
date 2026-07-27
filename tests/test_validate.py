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


def test_an_empty_pdf_file_is_rejected(tmp_path):
    empty = tmp_path / "doc.pdf"
    empty.write_bytes(b"")
    with pytest.raises(validate.ValidationError) as excinfo:
        validate.validate_pdf_file(empty, "doc.pdf")
    assert "empty" in str(excinfo.value)


def test_an_empty_image_file_is_rejected(tmp_path):
    empty = tmp_path / "a.png"
    empty.write_bytes(b"")
    with pytest.raises(validate.ValidationError) as excinfo:
        validate.validate_image_file(empty, "a.png")
    assert "empty" in str(excinfo.value)


def test_a_file_exactly_at_the_size_limit_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "MAX_FILE_BYTES", 20)
    header = b"%PDF-1.7\n"
    at_limit = tmp_path / "doc.pdf"
    at_limit.write_bytes(header + b"x" * (20 - len(header)))
    assert at_limit.stat().st_size == 20
    # Must not raise: a file exactly at the limit is not "too large".
    validate.validate_pdf_file(at_limit, "doc.pdf")


# --- normalize_output_name ----------------------------------------------
# A caller stores the returned name under a 4-byte position prefix
# ("003_"), so the real fitness test is against NAME_MAX minus that prefix,
# not NAME_MAX itself.


def test_a_long_name_is_clamped_to_fit_name_max_after_a_position_prefix():
    name = "d" * 300 + ".pdf"
    result = validate.normalize_output_name(name, "default.pdf")
    assert result.endswith(".pdf")
    assert result != ".pdf"
    prefixed_bytes = len("003_".encode("ascii")) + len(result.encode("utf-8"))
    assert prefixed_bytes <= validate.NAME_MAX_BYTES


def test_a_short_name_is_left_alone():
    assert validate.normalize_output_name("report.pdf", "default.pdf") == "report.pdf"


def test_an_empty_name_falls_back_to_the_default():
    assert validate.normalize_output_name("", "merged.pdf") == "merged.pdf"
    assert validate.normalize_output_name(None, "merged.pdf") == "merged.pdf"


def test_a_name_that_is_only_unsafe_characters_falls_back_to_the_default():
    # secure_filename strips path separators and ".."/"." entirely, so a
    # traversal-only name has nothing left of its own to clamp.
    assert validate.normalize_output_name("../..", "merged.pdf") == "merged.pdf"


def test_clamping_never_splits_a_multibyte_utf8_character(monkeypatch):
    # secure_filename always returns pure ASCII (it NFKD-folds and then
    # encodes with errors="ignore"), so multibyte characters never actually
    # reach the byte-clamping step in practice. Bypass it here to prove the
    # clamp itself -- _clamp_utf8 -- is correct on its own terms rather than
    # merely never being exercised.
    monkeypatch.setattr(validate, "secure_filename", lambda value: value)
    # 100 copies of "字" (a 3-byte-in-UTF-8 character) is 300 bytes --
    # comfortably over the ~247-byte stem budget (255 - 4 for the prefix -
    # 4 for ".pdf"), and 247 is not a multiple of 3, so a naive byte slice
    # at the budget would land mid-character.
    name = ("字" * 100) + ".pdf"
    result = validate.normalize_output_name(name, "default.pdf")
    assert result.endswith(".pdf")
    # Must decode cleanly -- a mid-character cut would have raised already
    # inside normalize_output_name, but assert explicitly for the intent.
    result.encode("utf-8").decode("utf-8")
    prefixed_bytes = len(b"003_") + len(result.encode("utf-8"))
    assert prefixed_bytes <= validate.NAME_MAX_BYTES
