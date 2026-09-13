"""Tests for taking a document apart: keeping, reordering and turning pages."""

import subprocess

import pytest
from PIL import Image, ImageOps
from pypdf import PdfReader

from pdftools import merge, pagesize, split
from pdftools.errors import ToolError


def extracted_text(pdf_path):
    return subprocess.run(["pdftotext", str(pdf_path), "-"], capture_output=True, text=True, timeout=120).stdout


def page_count(pdf_path):
    output = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True, timeout=120).stdout
    for line in output.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise AssertionError("pdfinfo reported no page count")


def ink_bbox(pdf_path, page):
    """The bounding box of everything inked on ``page``, in points."""
    subprocess.run(
        ["pdftoppm", "-png", "-r", "72", "-f", str(page), "-l", str(page), str(pdf_path), str(pdf_path.parent / "ink")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    rendered = sorted(pdf_path.parent.glob("ink-*.png"))[0]
    with Image.open(str(rendered)) as image:
        return ImageOps.invert(image.convert("L")).getbbox()


def visible_sizes(pdf_path):
    return [pagesize.visible_size(page) for page in PdfReader(str(pdf_path)).pages]


def test_page_count_reports_every_page(vector_pdf_factory):
    assert split.page_count(vector_pdf_factory(["A", "B", "C"])) == 3


def test_only_the_chosen_pages_are_kept(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["KEEP-A", "DROP-ME", "KEEP-C"])
    output = split.build_document(source, tmp_path / "out.pdf", [0, 2])
    assert page_count(output) == 2
    assert "DROP-ME" not in extracted_text(output)


def test_pages_are_written_in_the_chosen_order(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["ALPHA", "BETA", "GAMMA"])
    output = split.build_document(source, tmp_path / "out.pdf", [2, 0, 1])
    text = extracted_text(output)
    assert text.index("GAMMA") < text.index("ALPHA") < text.index("BETA")


def test_a_single_page_can_be_pulled_out(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["ONE", "TWO", "THREE"])
    output = split.build_document(source, tmp_path / "page.pdf", [1])
    assert page_count(output) == 1
    assert "TWO" in extracted_text(output)


def test_the_source_document_is_left_alone(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["A", "B"])
    before = source.read_bytes()
    split.build_document(source, tmp_path / "out.pdf", [1], [90])
    assert source.read_bytes() == before


def test_a_quarter_turn_is_recorded_on_the_page(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["A", "B"])
    output = split.build_document(source, tmp_path / "out.pdf", [0, 1], [0, 90])
    written = PdfReader(str(output)).pages
    assert written[0].rotation == 0
    assert written[1].rotation == 90


def test_a_turn_adds_to_one_the_page_already_carries(vector_pdf_factory, tmp_path):
    """A scan can arrive already rotated. Turning it once more must land on
    180, not reset it to 90."""
    source = vector_pdf_factory(["A"])
    once = split.build_document(source, tmp_path / "once.pdf", [0], [90])
    twice = split.build_document(once, tmp_path / "twice.pdf", [0], [90])
    assert PdfReader(str(twice)).pages[0].rotation == 180


def test_turning_a_page_does_not_re_encode_it(vector_pdf_factory, tmp_path):
    """Rotation is a dictionary entry, not a re-render: the text stays text."""
    source = vector_pdf_factory(["STILL-TEXT"])
    output = split.build_document(source, tmp_path / "out.pdf", [0], [270])
    assert "STILL-TEXT" in extracted_text(output)


def test_an_empty_selection_is_refused(vector_pdf_factory, tmp_path):
    with pytest.raises(ValueError):
        split.build_document(vector_pdf_factory(["A"]), tmp_path / "out.pdf", [])


def test_a_page_past_the_end_is_refused(vector_pdf_factory, tmp_path):
    with pytest.raises(ValueError):
        split.build_document(vector_pdf_factory(["A", "B"]), tmp_path / "out.pdf", [2])


def test_a_negative_page_is_refused(vector_pdf_factory, tmp_path):
    """Python would read -1 as the last page and write the wrong one
    silently, which is worse than an error."""
    with pytest.raises(ValueError):
        split.build_document(vector_pdf_factory(["A", "B"]), tmp_path / "out.pdf", [-1])


def test_one_rotation_per_chosen_page_is_required(vector_pdf_factory, tmp_path):
    with pytest.raises(ValueError):
        split.build_document(vector_pdf_factory(["A", "B"]), tmp_path / "out.pdf", [0, 1], [90])


def test_an_angle_that_is_not_a_quarter_turn_is_refused(vector_pdf_factory, tmp_path):
    with pytest.raises(ValueError):
        split.build_document(vector_pdf_factory(["A"]), tmp_path / "out.pdf", [0], [45])


def test_a_password_protected_document_is_refused_by_name(password_protected_pdf, tmp_path):
    with pytest.raises(ToolError) as excinfo:
        split.build_document(password_protected_pdf, tmp_path / "out.pdf", [0])
    assert password_protected_pdf.name in str(excinfo.value)


def test_matching_sizes_fits_an_odd_page_to_the_rest(box_pdf_factory, tmp_path):
    source = merge.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((612, 792)), box_pdf_factory((1224, 1584))],
        tmp_path / "mixed.pdf",
    )
    output = split.build_document(source, tmp_path / "out.pdf", [0, 1, 2], normalize_pages=True)
    assert visible_sizes(output) == [(612.0, 792.0)] * 3


def test_sizes_are_left_alone_unless_asked(box_pdf_factory, tmp_path):
    source = merge.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((1224, 1584))],
        tmp_path / "mixed.pdf",
    )
    output = split.build_document(source, tmp_path / "out.pdf", [0, 1])
    assert visible_sizes(output) == [(612.0, 792.0), (1224.0, 1584.0)]


def test_matching_sizes_turns_a_turned_page_back_upright(box_pdf_factory, tmp_path):
    """The two options fight, and this pins which one wins.

    Fitting a page into a target of the other orientation turns its content to
    suit -- that is what stops a landscape page being squashed into a band. A
    page the user turned by hand is landscape by the same measure, so matching
    sizes against a portrait batch turns it straight back. Rotation is for
    straightening a sideways scan, which needs no size matching; deliberately
    sideways pages and size matching cannot both be had.
    """
    source = merge.merge_pdfs([box_pdf_factory((612, 792)), box_pdf_factory((612, 792))], tmp_path / "portrait.pdf")
    output = split.build_document(source, tmp_path / "out.pdf", [0, 1], [0, 90], normalize_pages=True)
    assert visible_sizes(output) == [(612.0, 792.0)] * 2
    left, top, right, bottom = ink_bbox(output, 2)
    # The box is 80% of a 612x792 page: 489.6 x 633.6, upright again.
    assert (right - left) == pytest.approx(489.6, abs=2)
    assert (bottom - top) == pytest.approx(633.6, abs=2)


def test_each_chosen_page_can_be_written_as_its_own_file(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["ALPHA", "BETA", "GAMMA"])
    written = split.build_pages(source, tmp_path / "pages", [2, 0])
    assert [path.name for path in written] == ["page-3.pdf", "page-1.pdf"]
    assert "GAMMA" in extracted_text(written[0])
    assert "ALPHA" in extracted_text(written[1])
    assert page_count(written[0]) == 1


def test_pages_written_one_by_one_carry_their_rotations(vector_pdf_factory, tmp_path):
    source = vector_pdf_factory(["A", "B"])
    written = split.build_pages(source, tmp_path / "pages", [0, 1], [90, 0])
    assert [PdfReader(str(path)).pages[0].rotation for path in written] == [90, 0]


def test_writing_pages_one_by_one_reads_the_source_once(vector_pdf_factory, tmp_path, monkeypatch):
    """A hundred-page document must not be parsed a hundred times."""
    from pdftools import reader

    reads = []
    original = reader.read_pages
    monkeypatch.setattr(reader, "read_pages", lambda path: reads.append(path) or original(path))
    split.build_pages(vector_pdf_factory(["A", "B", "C"]), tmp_path / "pages", [0, 1, 2])
    assert len(reads) == 1


def test_the_angles_a_page_can_be_turned_by_match_the_image_tab():
    # app.py validates a rotation from either tab through one helper, so the
    # two modules must agree on what a legal angle is.
    from pdftools import images

    assert split.VALID_ROTATIONS == images.VALID_ROTATIONS
