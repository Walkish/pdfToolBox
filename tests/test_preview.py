"""Tests for the before/after readability comparison."""

import re
from pathlib import Path

import pytest
from PIL import Image

from pdftools import compress, preview
from pdftools.inspect import PdfProfile, profile_pdf


def test_render_page_writes_a_png_of_the_requested_page(vector_pdf_2pages, tmp_path):
    output = preview.render_page(vector_pdf_2pages, 2, 72, tmp_path / "page2")
    assert output.exists()
    with Image.open(output) as image:
        # A 612x792 pt page at 72 dpi is 612x792 px.
        assert abs(image.width - 612) <= 2
        assert abs(image.height - 792) <= 2


def test_crop_box_is_clamped_to_the_available_image(tmp_path):
    box = preview.crop_box((400, 300), (900, 600))
    assert box == (0, 0, 400, 300)


def test_crop_box_sits_in_the_upper_body_of_a_large_page():
    left, top, right, bottom = preview.crop_box((2000, 3000), (900, 600))
    assert right - left == 900
    assert bottom - top == 600
    assert left == (2000 - 900) // 2
    assert 0 < top < 1500


def test_choose_page_prefers_the_first_page_carrying_an_image(scan_pdf_600dpi):
    assert preview.choose_page(profile_pdf(scan_pdf_600dpi)) == 1


def test_choose_page_falls_back_to_page_one_without_images():
    profile = PdfProfile(
        page_count=3,
        images=[],
        has_text=True,
        is_scan=False,
        min_ppi=None,
        median_ppi=None,
        max_ppi=None,
    )
    assert preview.choose_page(profile) == 1


def test_comparison_produces_two_identically_sized_crops(scan_pdf_600dpi, tmp_path):
    compressed = tmp_path / "small.pdf"
    compress.compress_pdf(scan_pdf_600dpi, compressed, "print300")
    result = preview.build_comparison(scan_pdf_600dpi, compressed, tmp_path / "preview")
    with Image.open(result.before) as before, Image.open(result.after) as after:
        assert before.size == after.size
        assert before.size[0] > 100
    assert result.dpi == preview.PREVIEW_DPI
    assert result.page == 1


def test_comparison_of_a_harshly_compressed_scan_actually_differs(scan_pdf_600dpi, tmp_path):
    compressed = tmp_path / "small.pdf"
    compress.compress_pdf(scan_pdf_600dpi, compressed, "print300")
    result = preview.build_comparison(scan_pdf_600dpi, compressed, tmp_path / "preview")
    before_bytes = result.before.read_bytes()
    after_bytes = result.after.read_bytes()
    assert before_bytes != after_bytes


def test_rendering_a_broken_pdf_raises_a_tool_error(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnope\n")
    with pytest.raises(compress.ToolError):
        preview.render_page(broken, 1, 72, tmp_path / "out")


def test_render_page_handles_a_prefix_that_already_contains_a_dot(vector_pdf_2pages, tmp_path):
    # pdftoppm appends ".png" to the prefix string literally; it does not
    # replace an existing suffix the way Path.with_suffix does. A prefix
    # basename with a dot in it (e.g. derived from a versioned filename)
    # must still resolve to the file pdftoppm actually wrote.
    output = preview.render_page(vector_pdf_2pages, 1, 72, tmp_path / "page.v1")
    assert output.exists()
    assert output.name == "page.v1.png"


def test_a_failed_second_render_leaves_no_full_page_renders_behind(scan_pdf_600dpi, tmp_path):
    broken_out = tmp_path / "broken_out.pdf"
    broken_out.write_bytes(b"%PDF-1.4\nnope\n")
    dest_dir = tmp_path / "preview"
    with pytest.raises(compress.ToolError):
        preview.build_comparison(scan_pdf_600dpi, broken_out, dest_dir)
    leftover_full_renders = list(dest_dir.glob("*_full.png"))
    assert leftover_full_renders == []


def test_both_crops_fit_side_by_side_at_one_to_one_in_the_real_layout():
    """The spec's whole point is that the pair is shown side by side at 1:1
    with no scaling. Flex items do not shrink below their intrinsic width, so
    a crop wider than half the available column pushes the "after" image
    behind .pair's horizontal scrollbar -- and a comparison you have to
    scroll between is not a comparison. Measured with CROP_SIZE = (900, 600)
    against a 900 px body: 1816 px of images into about 832 px of space.

    The numbers are read out of the stylesheet so the two cannot drift apart
    silently; if a rewrite makes these patterns stop matching, this test fails
    loudly, which is the point.
    """
    style = (Path(__file__).resolve().parent.parent / "static" / "style.css").read_text()

    def matched(pattern):
        match = re.search(pattern, style, re.S)
        assert match, "style.css no longer matches {0!r}".format(pattern)
        return int(match.group(1))

    body_max_width = matched(r"body\s*\{[^}]*max-width:\s*(\d+)px")
    body_padding = matched(r"body\s*\{[^}]*padding:\s*\d+px\s+(\d+)px")
    result_padding = matched(r"\.result\s*\{[^}]*padding:\s*\d+px\s+(\d+)px")
    pair_gap = matched(r"\.pair\s*\{[^}]*gap:\s*(\d+)px")

    available = body_max_width - 2 * body_padding - 2 * result_padding
    assert 2 * preview.CROP_SIZE[0] + pair_gap <= available
    # Still a useful amount of page: at 150 dpi this is inches of body text.
    assert preview.CROP_SIZE[0] / float(preview.PREVIEW_DPI) >= 2.5
    assert preview.CROP_SIZE[1] / float(preview.PREVIEW_DPI) >= 3.5
