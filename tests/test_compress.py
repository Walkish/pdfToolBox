"""Tests for Ghostscript compression, presets, and print-safety guardrails."""
import subprocess

import pytest

from pdftools import compress
from pdftools.inspect import PdfProfile, parse_pdfimages_list, profile_pdf


def image_ppi_values(pdf_path):
    """Actual ppi of every image in a PDF, straight from pdfimages."""
    listing = subprocess.run(
        ["pdfimages", "-list", str(pdf_path)], capture_output=True, text=True, timeout=120
    ).stdout
    return [image.ppi for image in parse_pdfimages_list(listing) if image.ppi is not None]


def image_encodings(pdf_path):
    """The `enc` column of every image row in `pdfimages -list` output.

    Poppler reports "jpeg" for a DCT-encoded image and "image" for a raw or
    Flate one, so this is the direct measurement of whether a lossless source
    was re-encoded lossily.
    """
    listing = subprocess.run(
        ["pdfimages", "-list", str(pdf_path)], capture_output=True, text=True, timeout=120
    ).stdout
    encodings = []
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) >= 16 and fields[0].isdigit():
            encodings.append(fields[8])
    return encodings


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


def test_the_skip_resample_leg_of_a_print_preset_keeps_the_lossless_filters(tmp_path):
    """Guardrail (a) turns resampling off for a source already at or below the
    target, which runs a *print* preset through the no-resample path. That is
    a different code path from the ``lossless`` preset, and it needs the same
    explicit filter policy: Ghostscript's AutoFilterColorImages/GrayImages
    default to true, so leaving this leg on the defaults silently re-encodes a
    Flate scan as JPEG while the row still claims "only the file structure was
    recompressed". Measured on the regressed build: 418322 -> 90493 bytes, enc
    "image" -> "jpeg". No existing test covered ``resample=False`` on a
    resampling preset, so the regression passed 123/123.
    """
    command = compress.build_command(
        tmp_path / "in.pdf",
        tmp_path / "out.pdf",
        compress.PRESETS["print300"],
        resample=False,
    )
    for flag in (
        "-dAutoFilterColorImages=false",
        "-dAutoFilterGrayImages=false",
        "-dColorImageFilter=/FlateEncode",
        "-dGrayImageFilter=/FlateEncode",
        "-dPassThroughJPEGImages=true",
    ):
        assert flag in command
    assert "-dColorImageFilter=/DCTEncode" not in command
    assert "-dGrayImageFilter=/DCTEncode" not in command


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


def test_the_qfactor_is_what_shrinks_the_output_not_just_the_resolution_flags(
    scan_pdf_600dpi, tmp_path
):
    """The monotonic-ladder test above only proves resolution flags shrink the
    file; it would still pass green if Ghostscript silently ignored the -c
    QFactor snippet entirely (verified: stripping it and rerunning still gives
    a monotonic ladder, just at different, uncontrolled quality). This isolates
    quality from resolution by running the identical print300 command with and
    without the -c pair, and requires the quality snippet to make a real,
    material difference to the output size.
    """
    preset = compress.PRESETS["print300"]
    dst_with = tmp_path / "with_qfactor.pdf"
    dst_without = tmp_path / "without_qfactor.pdf"

    command_with = compress.build_command(scan_pdf_600dpi, dst_with, preset)
    command_without = compress.build_command(scan_pdf_600dpi, dst_without, preset)
    c_index = command_without.index("-c")
    del command_without[c_index:c_index + 2]

    subprocess.run(command_with, capture_output=True, text=True, timeout=120)
    subprocess.run(command_without, capture_output=True, text=True, timeout=120)

    size_with = dst_with.stat().st_size
    size_without = dst_without.stat().st_size
    # Measured: 888,774 vs 365,899 bytes (2.4x) -- comfortably clears this bar.
    assert size_with > size_without * 1.2


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


def test_a_low_resolution_scan_still_warns_below_floor_even_when_not_resampled(
    scan_pdf_150dpi, tmp_path
):
    """A 150 dpi scan through print300 takes the "already at target" path
    (resample=False, no downsampling happens), but the *output* still sits at
    150 dpi -- below the print floor -- and the guardrail must say so
    regardless of which code path produced that resolution.
    """
    result = compress.compress_pdf(scan_pdf_150dpi, tmp_path / "out.pdf", "print300")
    assert result.resampled is False
    assert any(str(compress.PRINT_DPI_FLOOR) in warning for warning in result.warnings)
    assert any("150" in warning for warning in result.warnings)


def test_an_already_minimal_pdf_is_returned_unchanged(tiny_pdf, tmp_path):
    destination = tmp_path / "out.pdf"
    result = compress.compress_pdf(tiny_pdf, destination, "print300")
    assert result.returned_original is True
    assert result.resampled is False
    assert result.size_after == result.size_before
    assert destination.read_bytes() == tiny_pdf.read_bytes()
    assert any("optimis" in warning.lower() for warning in result.warnings)


def test_returned_original_path_reports_no_resampling_and_no_stale_floor_warning(
    tiny_pdf, tmp_path
):
    """When Ghostscript's re-encode comes out larger, the original is kept --
    but before this fix, ``resampled`` still reported the pre-run intent and
    a below-floor warning could describe resampling that never actually
    reached the file the caller receives. Inject a profile that looks like a
    high-resolution scan (so the below-floor guardrail would fire if it used
    the preset's target instead of the real output) through a preset whose
    target is below the print floor, on a fixture (``tiny_pdf``) that
    Ghostscript's own re-encode cannot shrink -- so returned_original is
    guaranteed to trigger for real, via a real subprocess run.
    """
    high_res_scan = PdfProfile(
        page_count=1,
        images=[],
        has_text=True,
        is_scan=True,
        min_ppi=1000,
        median_ppi=1000,
        max_ppi=1000,
    )
    result = compress.compress_pdf(
        tiny_pdf, tmp_path / "out.pdf", "screen150", profile=high_res_scan
    )
    assert result.returned_original is True
    assert result.resampled is False
    assert not any(str(compress.PRINT_DPI_FLOOR) in warning for warning in result.warnings)


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
    """A file with no ``%PDF-`` header at all: poppler rejects it outright
    (Syntax Error: Couldn't find trailer dictionary), so a real
    ``profile_pdf`` call would raise ToolError before Ghostscript ever runs,
    and this test would silently be exercising poppler's error path instead
    of Ghostscript's. Bypass profiling with an injected profile so
    Ghostscript itself is what fails here. (A file with a valid header but
    garbage body -- e.g. "%PDF-1.4\nnot a pdf body\n" -- is not corrupt enough
    for this: Ghostscript recovers from it and emits a valid blank-page PDF
    with exit code 0.)
    """
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"this is not a pdf file at all, no header, just junk bytes")
    dummy_profile = PdfProfile(
        page_count=1,
        images=[],
        has_text=True,
        is_scan=False,
        min_ppi=None,
        median_ppi=None,
        max_ppi=None,
    )
    with pytest.raises(compress.ToolError) as excinfo:
        compress.compress_pdf(
            broken, tmp_path / "out.pdf", "print300", profile=dummy_profile
        )
    assert excinfo.value.stderr != ""
    assert "ghostscript" in str(excinfo.value).lower()


def test_lossless_preset_does_not_jpeg_encode_a_flate_source(flate_gray_pdf, tmp_path):
    """The encoding every PNG image in a Word/Excel/PowerPoint export uses is
    Flate, not JPEG. Ghostscript's own AutoFilterColorImages/GrayImages
    default to true, which can pick a lossy JPEG re-encode for exactly this
    kind of continuous-tone content when no explicit filter policy overrides
    it -- turning "lossless" into a quality regression worse than any print
    preset, silently and with no warning. Confirm the fixture is genuinely
    non-JPEG to start with, then confirm the lossless preset's output still
    is, whether or not the returned-original guardrail also fires.
    """
    listing_before = subprocess.run(
        ["pdfimages", "-list", str(flate_gray_pdf)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    assert "jpeg" not in listing_before.lower()

    result = compress.compress_pdf(flate_gray_pdf, tmp_path / "out.pdf", "lossless")

    listing_after = subprocess.run(
        ["pdfimages", "-list", str(result.output_path)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    assert "jpeg" not in listing_after.lower()


# --- Final review -------------------------------------------------------
# The below-floor guardrail is now derived from the *measured* output rather
# than from the preset's target, which is the only way it can see a scan
# whose pages carry different resolutions; and the skip-resample filter
# policy gets a direct test on both its legs.


def test_a_flate_source_at_the_target_is_not_jpeg_encoded_by_a_print_preset(
    flate_gray_pdf, tmp_path
):
    """The end-to-end half of the skip-resample filter policy: a 150 dpi Flate
    scan through ``print300`` takes guardrail (a)'s no-resample path, and must
    come back with its lossless encoding intact. On the regressed build this
    returned a 90493-byte file whose image encoding was "jpeg".
    """
    assert "jpeg" not in image_encodings(flate_gray_pdf)

    destination = tmp_path / "out.pdf"
    result = compress.compress_pdf(flate_gray_pdf, destination, "print300")

    assert result.resampled is False
    assert "jpeg" not in image_encodings(destination)


def test_a_mixed_resolution_scan_is_judged_by_its_worst_page(
    mixed_resolution_scan_pdf, tmp_path
):
    """A 600 dpi page next to a 100 dpi page, through the *default* preset.

    ``max_ppi`` is 600, so resampling proceeds and the preset's own target
    (300) is comfortably above the floor -- but the 100 dpi page is never
    touched and lands in the output as-is. Measured on the pre-fix build:
    output page 2 at 100 dpi with ``warnings: []``. The measured output is
    what the guardrail has to answer to, so assert the real per-page ppi as
    well as the warning.
    """
    destination = tmp_path / "out.pdf"
    result = compress.compress_pdf(mixed_resolution_scan_pdf, destination, "print300")

    ppi_values = image_ppi_values(destination)
    assert len(ppi_values) == 2
    assert max(ppi_values) <= 345
    assert min(ppi_values) <= 110

    assert result.below_print_floor is True
    floor_warnings = [
        warning
        for warning in result.warnings
        if str(compress.PRINT_DPI_FLOOR) in warning
    ]
    assert len(floor_warnings) == 1
    assert "100 dpi" in floor_warnings[0]


def test_one_low_res_scan_page_in_a_text_bundle_is_still_reported(
    text_bundle_with_one_low_res_scan_page, tmp_path
):
    """A mostly-text document is not a "scan", but its one scanned page still
    prints at 100 dpi.

    Document-level scan detection cannot see this: one scan page in five
    leaves ``is_scan`` False. The floor check must answer to the measured
    worst page instead, because that is the page the reader meets. Claiming
    ``below_print_floor is False`` here would be an explicit false statement
    about print safety, which is worse than the silence it replaced.
    """
    destination = tmp_path / "out.pdf"
    source_profile = profile_pdf(text_bundle_with_one_low_res_scan_page)
    assert source_profile.is_scan is False, "fixture must not read as a whole-doc scan"

    result = compress.compress_pdf(
        text_bundle_with_one_low_res_scan_page, destination, "print300"
    )

    assert min(image_ppi_values(destination)) <= 110
    assert result.below_print_floor is True
    floor_warnings = [
        warning
        for warning in result.warnings
        if str(compress.PRINT_DPI_FLOOR) in warning
    ]
    assert len(floor_warnings) == 1
    assert "100 dpi" in floor_warnings[0]


def test_a_uniform_600dpi_scan_through_print300_stays_above_the_floor(
    scan_pdf_600dpi, tmp_path
):
    """The other side of the measurement: every page really does land above
    the floor, so nothing is reported."""
    destination = tmp_path / "out.pdf"
    result = compress.compress_pdf(scan_pdf_600dpi, destination, "print300")

    for ppi in image_ppi_values(destination):
        assert ppi >= compress.PRINT_DPI_FLOOR
    assert result.below_print_floor is False
    assert not any(
        str(compress.PRINT_DPI_FLOOR) in warning for warning in result.warnings
    )


def test_the_below_floor_warning_offers_the_next_preset_up(scan_pdf_600dpi, tmp_path):
    """design.md requires the warning to name the resulting dpi *and* the next
    preset up. Here the preset's own 150 dpi target is what put the output
    below the floor, so re-running at Print 200 dpi genuinely fixes it."""
    result = compress.compress_pdf(scan_pdf_600dpi, tmp_path / "out.pdf", "screen150")
    floor_warnings = [
        warning
        for warning in result.warnings
        if str(compress.PRINT_DPI_FLOOR) in warning
    ]
    assert len(floor_warnings) == 1
    assert "150 dpi" in floor_warnings[0]
    assert compress.PRESETS["print200"].label in floor_warnings[0]


def test_a_genuinely_low_resolution_scan_is_not_offered_a_higher_preset(
    scan_pdf_150dpi, tmp_path
):
    """A source that was already at 150 dpi cannot be rescued by a higher
    target -- Ghostscript never upsamples -- so the advice must not appear."""
    result = compress.compress_pdf(scan_pdf_150dpi, tmp_path / "out.pdf", "screen150")
    floor_warnings = [
        warning
        for warning in result.warnings
        if str(compress.PRINT_DPI_FLOOR) in warning
    ]
    assert len(floor_warnings) == 1
    assert compress.PRESETS["print200"].label not in floor_warnings[0]
    assert "Re-run at" not in floor_warnings[0]


def test_the_returned_original_row_does_not_contradict_itself(flate_gray_pdf, tmp_path):
    """Measured on the pre-fix build, an ordinary 150 dpi Flate scan through
    the default preset produced three warnings at once, the first two of which
    contradict each other: "only the file structure was recompressed" describes
    a rewrite that the returned-original guardrail then threw away.
    """
    result = compress.compress_pdf(flate_gray_pdf, tmp_path / "out.pdf", "print300")
    assert result.returned_original is True

    joined = " ".join(result.warnings)
    assert "original was kept unchanged" in joined
    assert "file structure was recompressed" not in joined
    # Exactly the two that describe this file: kept unchanged, and below floor.
    assert len(result.warnings) == 2
    assert result.below_print_floor is True
