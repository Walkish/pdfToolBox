"""Tests for order-preserving PDF merging."""

import subprocess

import pytest
from pypdf import PdfReader

from pdftools import compress, merge, pagesize


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
