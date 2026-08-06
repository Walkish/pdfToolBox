"""Tests for measuring the page size that dominates a batch."""

import pytest
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject

from pdftools import pagesize


def pages(*sizes):
    """Blank pages at the given (width, height) point sizes."""
    writer = PdfWriter()
    return [writer.add_blank_page(width=width, height=height) for width, height in sizes]


def test_the_most_common_size_wins_over_a_larger_minority():
    batch = pages((612, 792), (612, 792), (1224, 1584))
    assert pagesize.dominant_size(batch) == (612.0, 792.0)


def test_a_tie_is_broken_by_the_larger_area():
    batch = pages((612, 792), (842, 1191))
    assert pagesize.dominant_size(batch) == (842.0, 1191.0)


def test_sizes_a_fraction_of_a_point_apart_count_as_one_size():
    # Real A4 arrives as both of these depending on the producer. Grouped, A4
    # wins two to one; ungrouped, every page is its own size and the 612x792
    # page would win on a tie-break it should never reach.
    batch = pages((595.276, 841.89), (595, 842), (612, 792))
    width, height = pagesize.dominant_size(batch)
    assert (width, height) == pytest.approx((595.276, 841.89))


def test_a_rotated_page_is_measured_by_its_visible_size():
    batch = pages((595, 842))
    batch[0].rotate(90)
    assert pagesize.visible_size(batch[0]) == (842.0, 595.0)


def test_a_negative_rotation_is_measured_the_same_way():
    batch = pages((595, 842))
    batch[0][NameObject("/Rotate")] = FloatObject(-90)
    assert pagesize.visible_size(batch[0]) == (842.0, 595.0)


def test_a_cropbox_wins_over_the_mediabox():
    batch = pages((612, 792))
    batch[0][NameObject("/CropBox")] = ArrayObject(
        [FloatObject(50), FloatObject(50), FloatObject(400), FloatObject(600)]
    )
    assert pagesize.visible_size(batch[0]) == (350.0, 550.0)


def test_measuring_does_not_add_a_cropbox_to_the_page():
    # pypdf's cropbox property writes the key when read, which would change the
    # bytes of a merge that is supposed to be untouched.
    batch = pages((612, 792))
    pagesize.visible_size(batch[0])
    assert "/CropBox" not in batch[0]


def test_an_empty_batch_is_rejected():
    with pytest.raises(ValueError):
        pagesize.dominant_size([])
