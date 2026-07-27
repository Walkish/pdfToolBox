"""Tests for Ghostscript compression, presets, and print-safety guardrails."""
import subprocess

import pytest

from pdftools import compress
from pdftools.inspect import parse_pdfimages_list


def image_ppi_values(pdf_path):
    """Actual ppi of every image in a PDF, straight from pdfimages."""
    listing = subprocess.run(
        ["pdfimages", "-list", str(pdf_path)], capture_output=True, text=True, timeout=120
    ).stdout
    return [image.ppi for image in parse_pdfimages_list(listing) if image.ppi is not None]


def test_every_preset_is_registered_under_its_own_id():
    for preset_id, preset in compress.PRESETS.items():
        assert preset.id == preset_id
    assert set(compress.PRESETS) == {"print300", "print200", "screen150", "lossless"}
    assert compress.DEFAULT_PRESET == "print300"
    assert compress.PRESETS["screen150"].print_safe is False


def test_command_always_carries_the_print_safety_flags(tmp_path):
    command = compress.build_command(
        tmp_path / "in.pdf", tmp_path / "out.pdf", compress.PRESETS["print300"]
    )
    for flag in (
        "-dSAFER",
        "-dColorConversionStrategy=/LeaveColorUnchanged",
        "-dEmbedAllFonts=true",
        "-dAutoRotatePages=/None",
        "-dDetectDuplicateImages=true",
    ):
        assert flag in command


def test_command_sets_resolution_and_threshold_from_the_preset(tmp_path):
    command = compress.build_command(
        tmp_path / "in.pdf", tmp_path / "out.pdf", compress.PRESETS["print200"]
    )
    assert "-dColorImageResolution=200" in command
    assert "-dGrayImageResolution=200" in command
    assert "-dMonoImageResolution=600" in command
    assert "-dColorImageDownsampleThreshold=1.5" in command
    assert "-dMonoImageFilter=/CCITTFaxEncode" in command
    assert "0.4" in command[command.index("-c") + 1]


def test_command_omits_resampling_for_the_lossless_preset(tmp_path):
    command = compress.build_command(
        tmp_path / "in.pdf", tmp_path / "out.pdf", compress.PRESETS["lossless"]
    )
    assert not any(part.startswith("-dColorImageResolution") for part in command)
    assert "-c" not in command


def test_command_never_uses_a_shell_string(tmp_path):
    command = compress.build_command(
        tmp_path / "in.pdf", tmp_path / "out.pdf", compress.PRESETS["print300"]
    )
    assert isinstance(command, list)
    assert command[-1] == str(tmp_path / "in.pdf")
    assert command[-2] == "-f"


def test_unknown_preset_is_rejected(tmp_path, tiny_pdf):
    with pytest.raises(ValueError) as excinfo:
        compress.compress_pdf(tiny_pdf, tmp_path / "out.pdf", preset_id="nope")
    assert "nope" in str(excinfo.value)


def test_print300_resamples_a_600dpi_scan_down_to_about_300(scan_pdf_600dpi, tmp_path):
    result = compress.compress_pdf(scan_pdf_600dpi, tmp_path / "out.pdf", "print300")
    assert result.resampled is True
    assert result.returned_original is False
    for ppi in image_ppi_values(result.output_path):
        assert 255 <= ppi <= 345


def test_print300_shrinks_a_600dpi_scan_substantially(scan_pdf_600dpi, tmp_path):
    result = compress.compress_pdf(scan_pdf_600dpi, tmp_path / "out.pdf", "print300")
    assert result.saved_ratio >= 0.40


def test_size_decreases_monotonically_across_the_presets(scan_pdf_600dpi, tmp_path):
    sizes = {}
    for preset_id in ("lossless", "print300", "print200", "screen150"):
        result = compress.compress_pdf(
            scan_pdf_600dpi, tmp_path / "{0}.pdf".format(preset_id), preset_id
        )
        sizes[preset_id] = result.size_after
    assert sizes["lossless"] > sizes["print300"] > sizes["print200"] > sizes["screen150"]


def test_a_low_resolution_source_is_never_upsampled(scan_pdf_150dpi, tmp_path):
    result = compress.compress_pdf(scan_pdf_150dpi, tmp_path / "out.pdf", "print300")
    assert result.resampled is False
    assert any("already" in warning.lower() for warning in result.warnings)
    for ppi in image_ppi_values(result.output_path):
        assert ppi <= 170


def test_a_scan_below_the_print_floor_warns_before_download(scan_pdf_600dpi, tmp_path):
    result = compress.compress_pdf(scan_pdf_600dpi, tmp_path / "out.pdf", "screen150")
    assert any("150 dpi" in warning for warning in result.warnings)
    assert any(str(compress.PRINT_DPI_FLOOR) in warning for warning in result.warnings)


def test_a_vector_pdf_is_not_flagged_as_an_unreadable_scan(vector_pdf_2pages, tmp_path):
    result = compress.compress_pdf(vector_pdf_2pages, tmp_path / "out.pdf", "screen150")
    assert not any(str(compress.PRINT_DPI_FLOOR) in warning for warning in result.warnings)


def test_an_already_minimal_pdf_is_returned_unchanged(tiny_pdf, tmp_path):
    destination = tmp_path / "out.pdf"
    result = compress.compress_pdf(tiny_pdf, destination, "print300")
    assert result.returned_original is True
    assert result.size_after == result.size_before
    assert destination.read_bytes() == tiny_pdf.read_bytes()
    assert any("optimis" in warning.lower() for warning in result.warnings)


def test_a_cmyk_document_stays_cmyk(cmyk_pdf, tmp_path):
    result = compress.compress_pdf(cmyk_pdf, tmp_path / "out.pdf", "print300")
    listing = subprocess.run(
        ["pdfimages", "-list", str(result.output_path)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    assert "cmyk" in listing.lower()


def test_a_corrupt_pdf_raises_with_ghostscript_stderr(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nthis is not a pdf body\n")
    with pytest.raises(compress.ToolError) as excinfo:
        compress.compress_pdf(broken, tmp_path / "out.pdf", "print300")
    assert excinfo.value.stderr != ""
