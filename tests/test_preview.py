"""Tests for the before/after readability comparison."""
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
    compress.compress_pdf(scan_pdf_600dpi, compressed, "screen150")
    result = preview.build_comparison(scan_pdf_600dpi, compressed, tmp_path / "preview")
    with Image.open(result["before"]) as before, Image.open(result["after"]) as after:
        assert before.size == after.size
        assert before.size[0] > 100
    assert result["dpi"] == preview.PREVIEW_DPI
    assert result["page"] == 1


def test_comparison_of_a_harshly_compressed_scan_actually_differs(scan_pdf_600dpi, tmp_path):
    compressed = tmp_path / "small.pdf"
    compress.compress_pdf(scan_pdf_600dpi, compressed, "screen150")
    result = preview.build_comparison(scan_pdf_600dpi, compressed, tmp_path / "preview")
    before_bytes = result["before"].read_bytes()
    after_bytes = result["after"].read_bytes()
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
