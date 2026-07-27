"""Synthetic fixtures.

Everything is generated at test time with Pillow or by hand-assembling PDF
bytes, so the repository carries no binary test assets and the fixtures stay
readable and adjustable.
"""
import random

import pytest
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial.ttf",
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
    words = ["compression", "legible", "printer", "resample", "document",
             "quality", "threshold", "embedded", "grayscale", "resolution"]
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
    out += "trailer\n<< /Size {0} /Root 1 0 R >>\nstartxref\n{1}\n%%EOF\n".format(
        size, xref_offset
    ).encode("ascii")
    return bytes(out)


def _vector_pdf(path, markers):
    """Build a PDF with one page per marker, each holding real Helvetica text."""
    page_count = len(markers)
    font_number = 3 + 2 * page_count
    kids = " ".join("{0} 0 R".format(3 + 2 * index) for index in range(page_count))

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [{0}] /Count {1} >>".format(kids, page_count).encode("ascii"),
    ]
    for index, marker in enumerate(markers):
        page_number = 3 + 2 * index
        content_number = page_number + 1
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                "/Resources << /Font << /F1 {0} 0 R >> >> /Contents {1} 0 R >>"
            ).format(font_number, content_number).encode("ascii")
        )
        stream = (
            "BT /F1 24 Tf 72 700 Td ({0}) Tj ET\n"
            "BT /F1 11 Tf 72 660 Td (Vector body text that must stay sharp.) Tj ET\n"
        ).format(marker).encode("ascii")
        objects.append(
            "<< /Length {0} >>\nstream\n".format(len(stream)).encode("ascii")
            + stream
            + b"endstream"
        )
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


@pytest.fixture
def png_rgba(tmp_path):
    image = Image.new("RGBA", (800, 600), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.text((40, 40), "TRANSPARENT", fill=(10, 10, 10, 255), font=_font(48))
    path = tmp_path / "alpha.png"
    image.save(str(path), "PNG")
    return path


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
