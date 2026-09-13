"""Tests for order-preserving PDF merging."""

import subprocess

import pytest
from PIL import Image, ImageOps
from pypdf import PdfReader

from pdftools import compress, merge, pagesize
from pdftools.errors import ToolError


def extracted_text(pdf_path):
    return subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, timeout=120).stdout


def page_count(pdf_path):
    output = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=120).stdout
    for line in output.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise AssertionError("pdfinfo reported no page count")


def test_merged_page_count_is_the_sum_of_the_inputs(vector_pdf_factory, tmp_path):
    first = vector_pdf_factory(["A1", "A2"])
    second = vector_pdf_factory(["B1", "B2", "B3"])
    output = merge.merge_pdfs([first, second], tmp_path / "merged.pdf")
    assert page_count(output) == 5


def test_pages_appear_in_the_requested_order(vector_pdf_factory, tmp_path):
    first = vector_pdf_factory(["ALPHA"])
    second = vector_pdf_factory(["BETA"])
    third = vector_pdf_factory(["GAMMA"])
    output = merge.merge_pdfs([third, first, second], tmp_path / "merged.pdf")
    text = extracted_text(output)
    assert text.index("GAMMA") < text.index("ALPHA") < text.index("BETA")


def test_a_single_input_still_merges(vector_pdf_2pages, tmp_path):
    output = merge.merge_pdfs([vector_pdf_2pages], tmp_path / "merged.pdf")
    assert page_count(output) == 2


def test_an_empty_list_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        merge.merge_pdfs([], tmp_path / "merged.pdf")


def test_an_unreadable_input_raises_a_tool_error(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    with pytest.raises(compress.ToolError):
        merge.merge_pdfs([broken], tmp_path / "merged.pdf")


def visible_sizes(pdf_path):
    return [pagesize.visible_size(page) for page in PdfReader(str(pdf_path)).pages]


def test_normalization_fits_every_page_to_the_most_common_size(vector_pdf_factory, tmp_path):
    a4 = vector_pdf_factory(["A", "B"], page_size=(595, 842))
    photo = vector_pdf_factory(["C"], page_size=(1200, 1600))
    output = merge.merge_pdfs([a4, photo], tmp_path / "merged.pdf", normalize_pages=True)
    assert visible_sizes(output) == [(595.0, 842.0)] * 3


def test_without_normalization_the_sizes_are_left_mixed(vector_pdf_factory, tmp_path):
    a4 = vector_pdf_factory(["A"], page_size=(595, 842))
    photo = vector_pdf_factory(["B"], page_size=(1200, 1600))
    output = merge.merge_pdfs([a4, photo], tmp_path / "merged.pdf")
    assert visible_sizes(output) == [(595.0, 842.0), (1200.0, 1600.0)]


def test_a_uniform_batch_is_untouched_byte_for_byte(vector_pdf_factory, tmp_path):
    # The strongest statement of "uniform batches pay nothing": not merely the
    # same page sizes out, the same file. This is also what catches an
    # accidental read of pypdf's destructive cropbox property.
    first = vector_pdf_factory(["A"])
    second = vector_pdf_factory(["B"])
    plain = merge.merge_pdfs([first, second], tmp_path / "plain.pdf")
    normalized = merge.merge_pdfs([first, second], tmp_path / "normalized.pdf", normalize_pages=True)
    assert normalized.read_bytes() == plain.read_bytes()


def test_normalization_keeps_the_page_count_and_order(vector_pdf_factory, tmp_path):
    first = vector_pdf_factory(["ALPHA"], page_size=(595, 842))
    second = vector_pdf_factory(["BETA"], page_size=(1200, 1600))
    output = merge.merge_pdfs([first, second], tmp_path / "merged.pdf", normalize_pages=True)
    assert page_count(output) == 2
    text = extracted_text(output)
    assert text.index("ALPHA") < text.index("BETA")


def ink_bbox(pdf_path, page):
    """(left, top, right, bottom) of the ink on ``page``, in points.

    Rendered at 72 dpi so one pixel is one point and the numbers below can be
    read as page geometry directly.
    """
    prefix = pdf_path.parent / "render"
    subprocess.run(
        ["pdftoppm", "-png", "-r", "72", "-f", str(page), "-l", str(page), str(pdf_path), str(prefix)],
        check=True,
        timeout=120,
    )
    rendered = sorted(pdf_path.parent.glob("render-*.png"))[0]
    with Image.open(str(rendered)) as image:
        return ImageOps.invert(image.convert("L")).getbbox()


def test_a_landscape_page_is_rotated_rather_than_squashed(box_pdf_factory, tmp_path):
    # Two portrait pages set the target; the landscape one has to move.
    portrait = box_pdf_factory((612, 792))
    landscape = box_pdf_factory((792, 612))
    output = merge.merge_pdfs(
        [portrait, box_pdf_factory((612, 792)), landscape], tmp_path / "merged.pdf", normalize_pages=True
    )
    left, top, right, bottom = ink_bbox(output, 3)
    # The source rectangle is 80% of a 792x612 page: 633.6 x 489.6. Rotated to
    # meet a 612x792 target it needs no scaling at all (612/612 = 792/792 = 1),
    # so it lands 489.6 wide and 633.6 tall, centred: 61.2 and 79.2 of margin.
    assert (right - left) == pytest.approx(489.6, abs=2)
    assert (bottom - top) == pytest.approx(633.6, abs=2)
    assert left == pytest.approx(61.2, abs=2)
    assert top == pytest.approx(79.2, abs=2)


def test_a_landscape_page_is_rotated_counter_clockwise(corner_mark_pdf_factory, box_pdf_factory, tmp_path):
    # The mark sits in the source's bottom-left corner. Turned counter-
    # clockwise it lands bottom-right; clockwise it would land top-left. Both
    # directions are readable, but the spec picked one, and a centred shape
    # cannot tell them apart -- hence this asymmetric fixture.
    landscape = corner_mark_pdf_factory((792, 612))
    output = merge.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((612, 792)), landscape],
        tmp_path / "merged.pdf",
        normalize_pages=True,
    )
    left, top, right, bottom = ink_bbox(output, 3)
    assert right == pytest.approx(612, abs=2)
    assert bottom == pytest.approx(792, abs=2)


def test_a_smaller_page_is_scaled_up_with_its_proportions_intact(box_pdf_factory, tmp_path):
    # Half-size, same aspect ratio, so it scales by exactly 2 and its ink must
    # land in the same place as the landscape case above.
    small = box_pdf_factory((306, 396))
    output = merge.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((612, 792)), small],
        tmp_path / "merged.pdf",
        normalize_pages=True,
    )
    left, top, right, bottom = ink_bbox(output, 3)
    assert (right - left) == pytest.approx(489.6, abs=2)
    assert (bottom - top) == pytest.approx(633.6, abs=2)
    assert left == pytest.approx(61.2, abs=2)
    assert top == pytest.approx(79.2, abs=2)


def test_an_owner_locked_input_still_merges(owner_locked_pdf, vector_pdf_factory, tmp_path):
    """Locked against editing but readable without a password: a bank
    statement merges like any other file."""
    output = merge.merge_pdfs([owner_locked_pdf, vector_pdf_factory(["PLAIN"])], tmp_path / "merged.pdf")
    assert page_count(output) == 2


def test_a_password_protected_input_is_refused_by_name(password_protected_pdf, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        merge.merge_pdfs([password_protected_pdf], tmp_path / "merged.pdf")
    assert password_protected_pdf.name in str(excinfo.value)
    assert "password protected" in str(excinfo.value)
