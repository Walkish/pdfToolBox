"""Tests for straightening and clearing the background of page photos."""

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage

from pdftools import pagephoto


def _rgb(path):
    return pagephoto._load_rgb(path)


def _worst_line_bow(rgb):
    """How far, in pixels, the most bent line of text strays from straight.

    Never zero, even on a flat page: the measuring points are column medians
    of the letters, and ascenders and descenders move them a few pixels.
    """
    scale = pagephoto._scale(rgb)
    lines = pagephoto._line_samples(pagephoto._text_mask(rgb, scale), scale)
    assert len(lines) >= pagephoto._MIN_TEXT_LINES
    worst = 0.0
    for points in lines:
        fit = np.polyfit(points[:, 0], points[:, 1], 1)
        straight = np.polyval(fit, points[:, 0])
        worst = max(worst, float(np.percentile(np.abs(points[:, 1] - straight), 90)))
    return worst


def test_straightening_takes_the_bow_out_of_the_lines(curved_page_photo, flat_page_photo):
    before = _rgb(curved_page_photo)
    after, warnings = pagephoto.straighten(before, transparent_border=False)
    assert warnings == []
    # What the letters alone account for: the same text, never bent.
    floor = _worst_line_bow(_rgb(flat_page_photo))
    assert _worst_line_bow(before) > floor + 5
    assert _worst_line_bow(after) < floor + 1


def test_straightening_levels_the_tilt_too(curved_page_photo):
    after, _warnings = pagephoto.straighten(_rgb(curved_page_photo), transparent_border=False)
    scale = pagephoto._scale(after)
    for points in pagephoto._line_samples(pagephoto._text_mask(after, scale), scale):
        slope = np.polyfit(points[:, 0], points[:, 1], 1)[0]
        assert abs(slope) < 0.01


def test_straightening_grows_the_canvas_rather_than_cutting_text_off(curved_page_photo):
    before = _rgb(curved_page_photo)
    after, _warnings = pagephoto.straighten(before, transparent_border=True)
    assert after.shape[1] == before.shape[1]
    assert after.shape[0] >= before.shape[0]
    # The uncovered strip is see-through, not black.
    assert after.shape[2] == 4
    assert after[..., 3].min() == 0


def test_a_flat_page_is_left_essentially_alone(flat_page_photo):
    before = _rgb(flat_page_photo)
    after, warnings = pagephoto.straighten(before, transparent_border=False)
    assert warnings == []
    assert abs(after.shape[0] - before.shape[0]) <= 2


def test_a_page_without_text_is_returned_unchanged_with_a_warning(picture_only_photo):
    before = _rgb(picture_only_photo)
    after, warnings = pagephoto.straighten(before, transparent_border=False)
    assert np.array_equal(after, before)
    assert len(warnings) == 1
    assert "text" in warnings[0]


def test_background_removal_clears_the_paper_and_keeps_the_ink(flat_page_photo, page_photo_layout):
    source = _rgb(flat_page_photo)
    alpha = _uncropped_alpha(source, pagephoto.remove_background(source))
    paper = np.abs(source.astype(int) - page_photo_layout["paper"]).max(axis=2) <= 2
    # Away from anything drawn: the pixel right next to an edge is rightly
    # part-opaque, as the edge's own antialiasing.
    open_paper = ndimage.minimum_filter(paper, size=7)
    assert alpha[open_paper].max() == 0
    # The body of every letter stays solid.
    assert alpha[source.mean(axis=2) < 80].min() > 200


def test_background_removal_keeps_a_picture_whole_including_its_pale_parts(flat_page_photo, page_photo_layout):
    source = _rgb(flat_page_photo)
    alpha = _uncropped_alpha(source, pagephoto.remove_background(source))
    x0, y0, x1, y1 = page_photo_layout["picture"]
    assert alpha[(y0 + y1) // 2 - 20 : (y0 + y1) // 2 + 20, x0 + 40 : x0 + 80].min() == 255
    hx0, hy0, hx1, hy1 = page_photo_layout["highlight"]
    assert alpha[hy0 + 5 : hy1 - 5, hx0 + 5 : hx1 - 5].min() == 255


def _uncropped_alpha(source, result):
    """``result``'s alpha laid back over ``source``'s full frame.

    The result is cropped to what stays visible; where it sat is found from
    the green picture, the one feature both hold pixel for pixel.
    """
    ys, xs = np.nonzero((result[..., 1] > 120) & (result[..., 0] < 90) & (result[..., 3] == 255))
    source_ys, source_xs = np.nonzero((source[..., 1] > 120) & (source[..., 0] < 90))
    top, left = int(source_ys.min() - ys.min()), int(source_xs.min() - xs.min())
    alpha = np.zeros(source.shape[:2], np.uint8)
    alpha[top : top + result.shape[0], left : left + result.shape[1]] = result[..., 3]
    return alpha


def test_clean_page_writes_a_transparent_png(curved_page_photo, tmp_path):
    result = pagephoto.clean_page(curved_page_photo, tmp_path / "out.png")
    with Image.open(str(result.output_path)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGBA"
        assert image.getextrema()[3][0] == 0


def test_clean_page_can_straighten_only(curved_page_photo, tmp_path):
    result = pagephoto.clean_page(curved_page_photo, tmp_path / "out.png", transparent=False)
    with Image.open(str(result.output_path)) as image:
        assert image.mode == "RGB"


def test_clean_page_can_clear_the_background_only(flat_page_photo, tmp_path):
    result = pagephoto.clean_page(flat_page_photo, tmp_path / "out.png", straighten_text=False)
    with Image.open(str(result.output_path)) as image:
        assert image.mode == "RGBA"
    assert result.warnings == []


def test_clean_page_refuses_to_do_nothing(flat_page_photo, tmp_path):
    with pytest.raises(ValueError):
        pagephoto.clean_page(flat_page_photo, tmp_path / "out.png", straighten_text=False, transparent=False)
