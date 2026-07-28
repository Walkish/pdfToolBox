"""Synthetic fixtures.

Everything is generated at test time with Pillow or by hand-assembling PDF
bytes, so the repository carries no binary test assets and the fixtures stay
readable and adjustable.
"""

import random
import zlib

import pytest
from PIL import Image, ImageChops, ImageDraw, ImageFont
from pypdf import PdfReader, PdfWriter

# Checked in order; the first that opens wins. Covering Windows and Linux as
# well as macOS is not cosmetic: without a real TrueType font Pillow falls back
# to its small bitmap default, and the compression fixtures would then carry
# far less detail than a scanned page does -- which is exactly what makes the
# calibration assertions meaningful.
FONT_CANDIDATES = [
    # macOS
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
    # Windows
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\calibri.ttf",
    # Linux
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]


def _font(size):
    """A real TrueType font if one is available, else Pillow's bitmap default.

    Fixture realism matters here: the compression tests need detailed, text-like
    content, because a flat image compresses to nothing and would make any
    preset look good.
    """
    for candidate in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_text_page(width, height, mode="L"):
    """Draw a page of pseudo-text lines plus noise, sized in pixels."""
    background = 255 if mode in ("L", "RGB") else 255
    image = Image.new(mode, (width, height), color=background)
    draw = ImageDraw.Draw(image)
    font = _font(max(12, height // 60))
    rng = random.Random(1234)
    words = [
        "compression",
        "legible",
        "printer",
        "resample",
        "document",
        "quality",
        "threshold",
        "embedded",
        "grayscale",
        "resolution",
    ]
    margin = width // 12
    y = margin
    line_height = max(16, height // 45)
    while y < height - margin:
        line = " ".join(rng.choice(words) for _ in range(6))
        draw.text((margin, y), line, fill=0, font=font)
        y += line_height
    # Fine speckle, so the image cannot be compressed away to nothing.
    for _ in range(width * height // 400):
        x = rng.randrange(width)
        sy = rng.randrange(height)
        draw.point((x, sy), fill=rng.randrange(90, 190))
    return image


def _save_image_pdf(image, path, dpi):
    image.save(str(path), "PDF", resolution=float(dpi))
    return path


@pytest.fixture
def scan_pdf_600dpi(tmp_path):
    """A 5x7 inch grayscale page at 600 dpi: the worst case for legibility."""
    image = _render_text_page(5 * 600, 7 * 600, mode="L")
    return _save_image_pdf(image, tmp_path / "scan600.pdf", 600)


@pytest.fixture
def scan_pdf_150dpi(tmp_path):
    """The same page already at 150 dpi, to prove nothing gets upsampled."""
    image = _render_text_page(5 * 150, 7 * 150, mode="L")
    return _save_image_pdf(image, tmp_path / "scan150.pdf", 150)


@pytest.fixture
def scan_pdf_1200dpi(tmp_path):
    """A 2x3 inch grayscale page at 1200 dpi.

    The preset ladder can only be measured on a source above every target:
    Ghostscript leaves an image alone unless it exceeds the target by its
    1.5 DownsampleThreshold, so a 600 dpi source gives ``print600`` nothing to
    do and the ladder collapses. Kept small in inches to stay under 9
    megapixels despite the resolution.
    """
    image = _render_text_page(2 * 1200, 3 * 1200, mode="L")
    return _save_image_pdf(image, tmp_path / "scan1200.pdf", 1200)


@pytest.fixture
def mixed_resolution_scan_pdf(tmp_path):
    """A two-page scan whose pages were captured at different resolutions.

    Page 1 is a 600 dpi raster, well above any preset's target; page 2 is a
    100 dpi raster, well below the print floor -- the shape of a scanned
    bundle where one sheet arrived from a fax, a phone photo, or an
    already-downsampled PDF. Both pages are full-page images, so both count
    as scan pages, and the document's single "max ppi" of 600 says nothing
    at all about what page 2 will look like afterwards.
    """
    pages = []
    for dpi in (600, 100):
        image = _render_text_page(5 * dpi, 7 * dpi, mode="L")
        pages.append(_save_image_pdf(image, tmp_path / "_mixed_{0}.pdf".format(dpi), dpi))

    writer = PdfWriter()
    for page_pdf in pages:
        writer.add_page(PdfReader(str(page_pdf)).pages[0])

    path = tmp_path / "mixed_resolution_scan.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def text_bundle_with_one_low_res_scan_page(tmp_path):
    """Four vector text pages plus one full-page 100 dpi scanned page.

    This is the shape that document-level scan detection cannot see: only one
    page in five is a scan page, so ``is_scan`` is False, yet that page will
    print at 100 dpi -- half the print floor. A reader meets the worst page,
    not the document average.
    """
    text_path = _vector_pdf(
        tmp_path / "_bundle_text.pdf",
        ["PAGE-1", "PAGE-2", "PAGE-3", "PAGE-4"],
        page_size=_OCR_PAGE_SIZE,
    )
    image = _render_text_page(5 * 100, 7 * 100, mode="L")
    image_path = _save_image_pdf(image, tmp_path / "_bundle_scan.pdf", 100)

    writer = PdfWriter()
    for page in PdfReader(str(text_path)).pages:
        writer.add_page(page)
    writer.add_page(PdfReader(str(image_path)).pages[0])

    path = tmp_path / "bundle_with_low_res_scan.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def cmyk_pdf(tmp_path):
    """A CMYK page, to prove the colour space survives compression."""
    image = _render_text_page(1000, 1400, mode="RGB").convert("CMYK")
    return _save_image_pdf(image, tmp_path / "cmyk.pdf", 300)


def _build_pdf_bytes(objects):
    """Assemble numbered PDF objects into a file with a correct xref table.

    Written by hand rather than with a PDF library because these fixtures must
    contain genuine vector text objects, which the libraries in this project
    cannot author.
    """
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for number, body in enumerate(objects, start=1):
        offsets[number] = len(out)
        out += "{0} 0 obj\n".format(number).encode("ascii")
        out += body
        out += b"\nendobj\n"
    xref_offset = len(out)
    size = len(objects) + 1
    out += "xref\n0 {0}\n".format(size).encode("ascii")
    out += b"0000000000 65535 f \n"
    for number in range(1, size):
        out += "{0:010d} 00000 n \n".format(offsets[number]).encode("ascii")
    out += "trailer\n<< /Size {0} /Root 1 0 R >>\nstartxref\n{1}\n%%EOF\n".format(size, xref_offset).encode("ascii")
    return bytes(out)


def _continuous_tone_gray(width, height):
    """A smooth gradient plus fine noise: content with enough continuous-tone
    variation that Ghostscript's own AutoFilterGrayImages heuristic picks a
    lossy JPEG encoding for it, given the chance. Flat or text-like content
    does not reliably trigger that heuristic, so this fixture would not
    exercise the bug it exists to guard against.
    """
    gradient = Image.linear_gradient("L").resize((width, height))
    noise = Image.effect_noise((width, height), 20)
    # Centre the noise image (mean ~128) around zero before adding it in, so
    # it perturbs the gradient instead of just brightening it.
    zero_centred_noise = ImageChops.subtract(noise, Image.new("L", (width, height), 128))
    return ImageChops.add(gradient, zero_centred_noise)


def _flate_gray_pdf(path, width, height, page_size):
    """A one-page PDF whose raster image is genuinely ``/Filter /FlateDecode``.

    Pillow's own PDF writer picks the encoding it wants (often DCT even for
    grayscale), so this is assembled by hand: raw grayscale samples, zlib
    compressed, wrapped in an Image XObject with an explicit Flate filter.
    This is the encoding a PNG from a Word/Excel/PowerPoint export carries,
    and is the case the "lossless" preset must not silently re-encode as JPEG.
    """
    page_width, page_height = page_size
    image = _continuous_tone_gray(width, height)
    raw = image.tobytes()
    compressed = zlib.compress(raw, 6)

    image_obj = (
        (
            "<< /Type /XObject /Subtype /Image /Width {0} /Height {1} "
            "/ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode "
            "/Length {2} >>\nstream\n"
        )
        .format(width, height, len(compressed))
        .encode("ascii")
        + compressed
        + b"\nendstream"
    )

    content = "q {0} 0 0 {1} 0 0 cm /Im0 Do Q".format(page_width, page_height).encode("ascii")
    content_obj = "<< /Length {0} >>\nstream\n".format(len(content)).encode("ascii") + content + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {0} {1}] "
            "/Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>"
        )
        .format(page_width, page_height)
        .encode("ascii"),
        image_obj,
        content_obj,
    ]
    with open(path, "wb") as handle:
        handle.write(_build_pdf_bytes(objects))
    return path


@pytest.fixture
def flate_gray_pdf(tmp_path):
    """A full-page Flate-encoded grayscale scan, at 150 dpi (5x7in page)."""
    return _flate_gray_pdf(tmp_path / "flate_gray.pdf", 5 * 150, 7 * 150, (5 * 72, 7 * 72))


def _vector_pdf(path, markers, page_size=(612, 792)):
    """Build a PDF with one page per marker, each holding real Helvetica text.

    ``page_size`` is (width, height) in points. Pass the same size as any
    raster page this will be combined with, so image-coverage math (image
    area over page area) stays meaningful.
    """
    page_width, page_height = page_size
    page_count = len(markers)
    font_number = 3 + 2 * page_count
    kids = " ".join("{0} 0 R".format(3 + 2 * index) for index in range(page_count))

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [{0}] /Count {1} >>".format(kids, page_count).encode("ascii"),
    ]
    # Same offsets from the top as the original hard-coded 612x792 layout
    # (792 - 92 = 700, 792 - 132 = 660), so smaller pages keep the text on-page.
    top_y = page_height - 92
    body_y = page_height - 132
    for index, marker in enumerate(markers):
        page_number = 3 + 2 * index
        content_number = page_number + 1
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {2} {3}] "
                "/Resources << /Font << /F1 {0} 0 R >> >> /Contents {1} 0 R >>"
            )
            .format(font_number, content_number, page_width, page_height)
            .encode("ascii")
        )
        stream = (
            (
                "BT /F1 24 Tf 72 {1:.0f} Td ({0}) Tj ET\n"
                "BT /F1 11 Tf 72 {2:.0f} Td (Vector body text that must stay sharp.) Tj ET\n"
            )
            .format(marker, top_y, body_y)
            .encode("ascii")
        )
        objects.append("<< /Length {0} >>\nstream\n".format(len(stream)).encode("ascii") + stream + b"endstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    with open(path, "wb") as handle:
        handle.write(_build_pdf_bytes(objects))
    return path


@pytest.fixture
def vector_pdf_2pages(tmp_path):
    return _vector_pdf(tmp_path / "vector2.pdf", ["MARKER-1", "MARKER-2"])


@pytest.fixture
def vector_pdf_factory(tmp_path):
    counter = {"n": 0}

    def build(markers):
        counter["n"] += 1
        return _vector_pdf(tmp_path / "vector_{0}.pdf".format(counter["n"]), markers)

    return build


@pytest.fixture
def tiny_pdf(tmp_path):
    """A one-page text-only PDF that Ghostscript cannot make smaller."""
    return _vector_pdf(tmp_path / "tiny.pdf", ["TINY"])


# Page size (points) shared by the OCR and mixed fixtures below, matching the
# 5x7 inch physical size of the raster pages they are combined with.
_OCR_PAGE_SIZE = (360, 504)


@pytest.fixture
def ocr_scan_pdf(tmp_path):
    """A scanned page that has already been OCR'd.

    It carries a full-page raster image (so it looks like a scan) *and* a
    genuine, extractable text layer overlaid on top of it (as real OCR
    software would add). This is the case scan detection must not miss.
    """
    image = _render_text_page(5 * 300, 7 * 300, mode="L")
    image_path = _save_image_pdf(image, tmp_path / "_ocr_image.pdf", 300)
    text_path = _vector_pdf(tmp_path / "_ocr_text.pdf", ["OCR-LAYER"], page_size=_OCR_PAGE_SIZE)

    text_page = PdfReader(str(text_path)).pages[0]

    writer = PdfWriter()
    writer.append(str(image_path))
    # Attach the page to the writer before merging onto it: merging into a
    # page that isn't yet owned by a writer is deprecated in pypdf.
    writer.pages[0].merge_page(text_page)

    path = tmp_path / "ocr_scan.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def mixed_scan_and_text_pdf(tmp_path):
    """Two ordinary text pages followed by one full-page scanned image page.

    Mostly a text document, with one page that is a genuine scan -- this
    exercises the "most pages must be scan pages" fraction rather than a
    "does any page look like a scan" check.
    """
    text_path = _vector_pdf(tmp_path / "_mixed_text.pdf", ["MARKER-1", "MARKER-2"], page_size=_OCR_PAGE_SIZE)
    image = _render_text_page(5 * 300, 7 * 300, mode="L")
    image_path = _save_image_pdf(image, tmp_path / "_mixed_image.pdf", 300)

    writer = PdfWriter()
    for page in PdfReader(str(text_path)).pages:
        writer.add_page(page)
    writer.add_page(PdfReader(str(image_path)).pages[0])

    path = tmp_path / "mixed.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    return path


@pytest.fixture
def png_rgba(tmp_path):
    image = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "TRANSPARENT", fill=(10, 10, 10, 255), font=_font(48))
    path = tmp_path / "alpha.png"
    image.save(str(path), "PNG")
    return path


def _flat_png(path, size):
    """A flat single-colour PNG: enormous in pixels, tiny on disk.

    A decompression bomb needs no craft -- 22000x22000 white pixels compress
    to about 500 KB -- and that is exactly why a byte-size limit cannot see
    one coming.
    """
    image = Image.new("L", size, 255)
    try:
        image.save(str(path), "PNG", compress_level=9)
    finally:
        image.close()
    return path


@pytest.fixture
def png_past_pillows_own_ceiling(tmp_path):
    """484 megapixels (about 500 KB on disk). Past twice Pillow's
    MAX_IMAGE_PIXELS, so ``Image.open`` raises DecompressionBombError instead
    of returning an image at all."""
    return _flat_png(tmp_path / "bomb.png", (22000, 22000))


@pytest.fixture
def png_over_the_pixel_limit(tmp_path):
    """169 megapixels (about 190 KB on disk). Inside Pillow's warn-and-load
    band -- over its MAX_IMAGE_PIXELS but under twice it -- so Pillow loads it
    happily and only an explicit bound stops it becoming ~500 MB of RGB."""
    return _flat_png(tmp_path / "huge.png", (13000, 13000))


@pytest.fixture
def jpeg_rotated(tmp_path):
    """Landscape pixels tagged orientation 6, i.e. displayed as portrait."""
    image = Image.new("RGB", (1200, 800), (240, 240, 240))
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "UPRIGHT", fill=(0, 0, 0), font=_font(64))
    exif = image.getexif()
    exif[274] = 6
    path = tmp_path / "rotated.jpg"
    image.save(str(path), "JPEG", exif=exif, quality=92)
    return path


@pytest.fixture
def jpeg_300dpi(tmp_path):
    image = _render_text_page(1500, 1200, mode="RGB")
    path = tmp_path / "photo300.jpg"
    image.save(str(path), "JPEG", dpi=(300, 300), quality=92)
    return path
