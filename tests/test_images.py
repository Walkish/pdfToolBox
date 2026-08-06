"""Tests for image-to-PDF conversion."""

import subprocess

import pytest
from PIL import Image

from pdftools import compress, images


def page_boxes(pdf_path):
    """Return [(width_pt, height_pt), ...] for every page."""
    output = subprocess.run(
        ["pdfinfo", "-f", "1", "-l", "1000", str(pdf_path)],
        capture_output=True,
        text=True,
        timeout=120,
    ).stdout
    boxes = []
    for line in output.splitlines():
        if "size:" in line and "pts" in line:
            parts = line.split("size:")[1].split()
            boxes.append((float(parts[0]), float(parts[2])))
    return boxes


def test_one_page_per_image(png_rgba, jpeg_300dpi, tmp_path):
    output = images.images_to_pdf([png_rgba, jpeg_300dpi], tmp_path / "out.pdf")
    assert len(page_boxes(output)) == 2


def test_page_aspect_ratio_matches_each_image(jpeg_300dpi, tmp_path):
    with Image.open(jpeg_300dpi) as image:
        expected = image.width / float(image.height)
    output = images.images_to_pdf([jpeg_300dpi], tmp_path / "out.pdf")
    width_pt, height_pt = page_boxes(output)[0]
    assert abs(width_pt / height_pt - expected) < 0.02


def test_declared_dpi_drives_the_physical_page_size(jpeg_300dpi, tmp_path):
    # 1500 px at 300 dpi is 5 inches, i.e. 360 points.
    output = images.images_to_pdf([jpeg_300dpi], tmp_path / "out.pdf")
    width_pt, _height_pt = page_boxes(output)[0]
    assert abs(width_pt - 360.0) < 6.0


def test_an_image_without_dpi_metadata_falls_back_to_the_default(png_rgba, tmp_path):
    # 800 px at the 300 dpi default is 2.667 inches, i.e. 192 points.
    output = images.images_to_pdf([png_rgba], tmp_path / "out.pdf")
    width_pt, _height_pt = page_boxes(output)[0]
    assert abs(width_pt - 192.0) < 6.0
    assert images.DEFAULT_IMAGE_DPI == 300


def test_an_exif_rotated_photo_comes_out_upright(jpeg_rotated, tmp_path):
    with Image.open(jpeg_rotated) as image:
        assert image.width > image.height  # stored landscape, tagged portrait
    output = images.images_to_pdf([jpeg_rotated], tmp_path / "out.pdf")
    width_pt, height_pt = page_boxes(output)[0]
    assert height_pt > width_pt


def test_a_jpeg_with_only_an_exif_orientation_tag_falls_back_to_the_default_dpi(jpeg_rotated, tmp_path):
    # jpeg_rotated carries an EXIF segment (for the orientation tag) but no
    # real resolution declaration. Pillow synthesizes info["dpi"] = (72, 72)
    # for that case, which is indistinguishable from a genuine 72 dpi
    # declaration unless jfif_unit is also checked -- an absolute-size
    # assertion (not just aspect ratio) is required to catch a regression
    # back to that 72 dpi reading. After EXIF transpose the laid-out image is
    # 800x1200 px; at the 300 dpi default that is 192 x 288 pt.
    output = images.images_to_pdf([jpeg_rotated], tmp_path / "out.pdf")
    width_pt, height_pt = page_boxes(output)[0]
    assert abs(width_pt - 192.0) < 6.0
    assert abs(height_pt - 288.0) < 6.0


def test_prepare_image_reports_the_default_dpi_for_an_exif_only_jpeg(jpeg_rotated):
    _prepared, dpi = images.prepare_image(jpeg_rotated)
    assert dpi == 300.0


def test_an_absurd_but_genuinely_declared_dpi_still_falls_back_to_the_default(tmp_path):
    # dpi=(5, 5) here is written with jfif_unit=1 (a real declaration, not
    # Pillow's synthetic 72 dpi default), so this exercises the sanity window
    # rather than the "no metadata at all" fallback path.
    source = tmp_path / "absurd.jpg"
    Image.new("RGB", (800, 600), (0, 0, 0)).save(str(source), "JPEG", dpi=(5, 5), quality=90)
    _prepared, dpi = images.prepare_image(source)
    assert dpi == 300.0


def test_transparency_is_flattened_onto_white_not_black(png_rgba):
    prepared, _dpi = images.prepare_image(png_rgba)
    assert prepared.mode == "RGB"
    assert prepared.getpixel((prepared.width - 2, prepared.height - 2)) == (255, 255, 255)


def test_a_cmyk_image_stays_cmyk(tmp_path):
    source = tmp_path / "cmyk.jpg"
    Image.new("CMYK", (600, 400), (10, 20, 30, 5)).save(str(source), "JPEG")
    prepared, _dpi = images.prepare_image(source)
    assert prepared.mode == "CMYK"


def test_page_order_follows_the_input_order(tmp_path):
    paths = []
    for index, size in enumerate([(400, 800), (900, 300)]):
        path = tmp_path / "img{0}.png".format(index)
        Image.new("RGB", size, (200, 200, 200)).save(str(path), "PNG")
        paths.append(path)
    output = images.images_to_pdf(paths, tmp_path / "out.pdf")
    first, second = page_boxes(output)
    assert first[1] > first[0]  # portrait first
    assert second[0] > second[1]  # landscape second


def test_an_empty_list_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        images.images_to_pdf([], tmp_path / "out.pdf")


def test_a_non_image_file_raises_a_tool_error(tmp_path):
    bogus = tmp_path / "fake.png"
    bogus.write_bytes(b"definitely not an image")
    with pytest.raises(compress.ToolError):
        images.images_to_pdf([bogus], tmp_path / "out.pdf")


def test_a_heic_becomes_one_page(heic_image, tmp_path):
    output = images.images_to_pdf([heic_image], tmp_path / "out.pdf")
    assert len(page_boxes(output)) == 1


def test_a_sideways_heic_is_not_rotated_twice(heic_rotated, tmp_path):
    # The source is 400x200 landscape with EXIF Orientation=6, so it is meant
    # to be displayed as 200x400 portrait. pillow-heif applies that on open;
    # if exif_transpose applied it a second time the page would come out
    # landscape again, which is the failure this guards.
    with Image.open(str(heic_rotated)) as opened:
        assert opened.size == (200, 400)
    width_pt, height_pt = page_boxes(images.images_to_pdf([heic_rotated], tmp_path / "out.pdf"))[0]
    assert height_pt > width_pt
    assert abs(width_pt / height_pt - 200 / 400.0) < 0.02


def test_a_heic_falls_back_to_the_default_dpi(heic_image, tmp_path):
    # HEIC carries no resolution metadata, so the page must come out at
    # DEFAULT_IMAGE_DPI rather than at a 72 dpi assumption, which would give a
    # 400x300 photo a page over five inches wide.
    output = images.images_to_pdf([heic_image], tmp_path / "out.pdf")
    width_pt, _ = page_boxes(output)[0]
    assert width_pt == pytest.approx(400 / float(images.DEFAULT_IMAGE_DPI) * 72, abs=1)


def test_rotating_by_90_swaps_the_page_sides(jpeg_300dpi, tmp_path):
    upright = page_boxes(images.images_to_pdf([jpeg_300dpi], tmp_path / "a.pdf"))[0]
    turned = page_boxes(images.images_to_pdf([jpeg_300dpi], tmp_path / "b.pdf", rotations=[90]))[0]
    assert (turned[0], turned[1]) == pytest.approx((upright[1], upright[0]), abs=0.5)


def test_rotating_by_180_keeps_the_page_shape(jpeg_300dpi, tmp_path):
    upright = page_boxes(images.images_to_pdf([jpeg_300dpi], tmp_path / "a.pdf"))[0]
    turned = page_boxes(images.images_to_pdf([jpeg_300dpi], tmp_path / "b.pdf", rotations=[180]))[0]
    assert turned == pytest.approx(upright, abs=0.5)


def test_rotation_is_applied_on_top_of_the_exif_correction(heic_rotated):
    # The fixture is 400x200 landscape with EXIF Orientation=6, so it arrives
    # as 200x400 portrait. A further 90 must give landscape again -- if the
    # rotation were applied before the EXIF correction, or instead of it, this
    # would come out portrait.
    image, _ = images.prepare_image(heic_rotated, rotation=90)
    try:
        assert image.size == (400, 200)
    finally:
        image.close()


def test_a_clockwise_rotation_turns_clockwise(tmp_path):
    # A marked corner, so the direction is pinned and not merely the shape.
    # Clockwise sends the top-left pixel to the top-right.
    source = tmp_path / "marked.png"
    marked = Image.new("RGB", (100, 40), (255, 255, 255))
    marked.putpixel((0, 0), (255, 0, 0))
    marked.save(str(source))
    image, _ = images.prepare_image(source, rotation=90)
    try:
        assert image.size == (40, 100)
        # PNG is lossless and a quarter turn moves whole pixels, so the mark
        # arrives exactly, not approximately.
        assert image.getpixel((image.width - 1, 0)) == (255, 0, 0)
    finally:
        image.close()


def test_a_rotation_list_of_the_wrong_length_is_rejected(jpeg_300dpi, tmp_path):
    with pytest.raises(ValueError):
        images.images_to_pdf([jpeg_300dpi], tmp_path / "out.pdf", rotations=[90, 180])


def test_an_illegal_rotation_is_rejected(jpeg_300dpi, tmp_path):
    with pytest.raises(ValueError):
        images.images_to_pdf([jpeg_300dpi], tmp_path / "out.pdf", rotations=[45])
