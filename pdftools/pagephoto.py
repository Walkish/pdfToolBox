"""Cleaning up a phone photo of a printed page.

Two independent steps, either or both:

* straightening -- a page photographed on a desk is curved and seen at an
  angle, so its lines of text bow. The lines themselves are the ruler: each is
  found, a smooth vertical displacement is fitted that makes all of them
  horizontal at once, and the photo is resampled through it.
* transparent background -- the paper is made transparent while ink and
  pictures stay. The paper is never pure white in a photo (shadows, a colour
  cast), so it is measured locally rather than assumed.

Every threshold was tuned on a photo about 1600 px on its long edge and is
scaled from there, so a 12-megapixel original behaves like its small preview.

Built on SciPy rather than OpenCV on purpose. Measured: the OpenCV wheels
bundle their own ``libx265.215.dylib``, under the same install name as the
one pillow-heif ships, and with OpenCV loaded first decoding any HEIC upload
segfaults the whole server.
"""

from pathlib import Path
from typing import List, NamedTuple, Tuple

import numpy as np
from PIL import Image
from scipy import ndimage

from .errors import ToolError
from .images import prepare_image

# The long edge the pixel thresholds below are expressed at.
_REFERENCE_EDGE = 1600.0

# Fewer lines than this and the fit is guessing: the displacement has six
# terms, and two lines of text cannot pin down how the page bends between them.
_MIN_TEXT_LINES = 4
# A real page bends by a few percent of its height. A fit that wants to move
# rows by more than this has latched onto something that is not text, and
# applying it would wreck the photo rather than straighten it.
_MAX_SHIFT_FRACTION = 0.12

# Displacement terms (x power, y power). Every term has x in it: a shift that
# depends on y alone moves whole rows up or down, which straightens nothing
# and only trades off against each line's own unknown height.
_TERMS = [(a, b) for a in (1, 2) for b in (0, 1, 2)]

_EIGHT_CONNECTED = np.ones((3, 3), bool)


class PageResult(NamedTuple):
    output_path: Path
    warnings: List[str]


class _Components(NamedTuple):
    """Connected regions of a mask: a label image plus per-label boxes.

    Index 0 of every array is the background, so arrays line up with labels.
    """

    labels: np.ndarray
    tops: np.ndarray
    lefts: np.ndarray
    heights: np.ndarray
    widths: np.ndarray
    areas: np.ndarray


def _components(mask: np.ndarray, structure=_EIGHT_CONNECTED) -> _Components:
    labels, count = ndimage.label(mask, structure=structure)
    tops = np.zeros(count + 1, int)
    lefts = np.zeros(count + 1, int)
    heights = np.zeros(count + 1, int)
    widths = np.zeros(count + 1, int)
    for index, box in enumerate(ndimage.find_objects(labels), start=1):
        if box is None:
            continue
        tops[index], lefts[index] = box[0].start, box[1].start
        heights[index] = box[0].stop - box[0].start
        widths[index] = box[1].stop - box[1].start
    areas = np.bincount(labels.ravel(), minlength=count + 1)
    return _Components(labels, tops, lefts, heights, widths, areas)


def _scale(image: np.ndarray) -> float:
    return max(image.shape[:2]) / _REFERENCE_EDGE


def _size(value: float) -> int:
    """A filter size in pixels: at least 3, and odd so it has a centre."""
    size = max(3, int(round(value)))
    return size if size % 2 else size + 1


def _gray(rgb: np.ndarray) -> np.ndarray:
    return rgb[..., :3].astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)


def _saturation(rgb: np.ndarray) -> np.ndarray:
    """HSV saturation on a 0-255 scale."""
    channels = rgb[..., :3].astype(np.float32)
    high = channels.max(axis=2)
    low = channels.min(axis=2)
    return np.where(high > 0, (high - low) / np.maximum(high, 1.0) * 255.0, 0.0)


def _text_mask(rgb: np.ndarray, scale: float) -> np.ndarray:
    """Pixels that look like printed text: dark, grey, letter-sized blobs.

    Saturation is what keeps the dark veins of a photographed leaf out of it.
    """
    regions = _components((_gray(rgb) < 120) & (_saturation(rgb) < 90))
    keep = (
        (regions.heights >= 6 * scale)
        & (regions.heights <= 90 * scale)
        & (regions.widths <= 90 * scale)
        & (regions.areas >= 8 * scale * scale)
    )
    keep[0] = False
    return keep[regions.labels]


def _line_samples(text: np.ndarray, scale: float) -> List[np.ndarray]:
    """One array of (x, y) centre points per line of text found."""
    merged = ndimage.maximum_filter(text, size=(_size(3 * scale), _size(35 * scale)))
    regions = _components(merged)
    step = max(4, int(round(10 * scale)))
    lines = []
    for label in range(1, len(regions.areas)):
        x, y = regions.lefts[label], regions.tops[label]
        width, height = regions.widths[label], regions.heights[label]
        # Short runs are a word or a page number: too little span to measure
        # a slope by. Tall ones are two lines run together, or a picture.
        if width < 250 * scale or height > 120 * scale:
            continue
        inside = (regions.labels[y : y + height, x : x + width] == label) & text[y : y + height, x : x + width]
        ys, xs = np.nonzero(inside)
        points = []
        for start in range(0, width, step):
            column = (xs >= start) & (xs < start + step)
            if column.sum() >= 5:
                points.append((x + start + step / 2.0, y + float(np.median(ys[column]))))
        if len(points) < 10:
            continue
        points = np.array(points)
        # Ascenders, descenders and punctuation pull single columns off the
        # line; a quadratic fit with its worst residuals dropped ignores them.
        for _ in range(2):
            fit = np.polyfit(points[:, 0], points[:, 1], 2)
            residual = points[:, 1] - np.polyval(fit, points[:, 0])
            points = points[np.abs(residual) < max(3 * np.std(residual), 2.0)]
        if len(points) >= 10:
            lines.append(points)
    return lines


def _displacement(
    coefficients: np.ndarray, xs: np.ndarray, ys: np.ndarray, width: int, height: int, measured
) -> np.ndarray:
    """The vertical shift at each (x, y), held constant outside ``measured``.

    ``measured`` is the (x0, y0, x1, y1) box the text lines span. A polynomial
    fitted inside it says nothing about the page beyond it, and left to run
    there it swings wildly -- a margin with no text on it would be bent by
    whatever the fit happened to do at its edge. Clamping keeps the last
    measured shift instead.
    """
    x0, y0, x1, y1 = measured
    x_norm = (np.clip(xs, x0, x1) - width / 2.0) / width
    y_norm = np.clip(ys, y0, y1) / float(height)
    shift = np.zeros(np.broadcast(xs, ys).shape, dtype=np.float32)
    for coefficient, (a, b) in zip(coefficients, _TERMS):
        shift += coefficient * x_norm**a * y_norm**b
    return shift


def _resample(channel: np.ndarray, rows: np.ndarray, columns: np.ndarray, edge_mode: str) -> np.ndarray:
    resampled = ndimage.map_coordinates(channel.astype(np.float32), [rows, columns], order=3, mode=edge_mode, cval=0.0)
    return np.clip(resampled, 0, 255).astype(np.uint8)


def straighten(rgb: np.ndarray, transparent_border: bool) -> Tuple[np.ndarray, List[str]]:
    """Make the lines of text horizontal. Returns (image, warnings).

    The result is RGBA when ``transparent_border`` is set, so the strip the
    resampling uncovers at the top and bottom edges is see-through, and RGB
    with the edge pixels repeated otherwise. A page with too little text to
    measure comes back unchanged, with a warning saying so.
    """
    height, width = rgb.shape[:2]
    scale = _scale(rgb)
    lines = _line_samples(_text_mask(rgb, scale), scale)
    opaque = np.full((height, width, 1), 255, np.uint8)
    unchanged = np.concatenate([rgb, opaque], axis=2) if transparent_border else rgb
    if len(lines) < _MIN_TEXT_LINES:
        return unchanged, ["Not enough lines of text to straighten by; the photo was left as it is."]

    # Least squares over every sample of every line at once. Unknowns: one
    # height per line (where that line sits once flat) plus the displacement
    # terms shared by the whole page.
    samples = np.concatenate(lines)
    line_ids = np.concatenate([np.full(len(points), index) for index, points in enumerate(lines)])
    design = np.zeros((len(samples), len(lines) + len(_TERMS)))
    design[np.arange(len(samples)), line_ids] = 1.0
    x_norm = (samples[:, 0] - width / 2.0) / width
    y_norm = samples[:, 1] / float(height)
    for column, (a, b) in enumerate(_TERMS):
        design[:, len(lines) + column] = x_norm**a * y_norm**b
    solution, _, _, _ = np.linalg.lstsq(design, samples[:, 1], rcond=None)
    coefficients = solution[len(lines) :]
    measured = (samples[:, 0].min(), samples[:, 1].min(), samples[:, 0].max(), samples[:, 1].max())

    xs = np.arange(width, dtype=np.float32)[None, :]
    ys = np.arange(height, dtype=np.float32)[:, None]
    shift = _displacement(coefficients, xs, ys, width, height, measured)
    if np.abs(shift).max() > _MAX_SHIFT_FRACTION * height:
        return unchanged, ["The page could not be measured reliably; the photo was left as it is."]

    # Content pulled up past the top edge, or down past the bottom, would be
    # cut off; the canvas grows by exactly as much as is needed to keep it.
    top = int(np.ceil(max(0.0, float(shift[0].max()))))
    bottom = int(np.ceil(max(0.0, -float(shift[-1].min()))))
    ys = np.arange(height + top + bottom, dtype=np.float32)[:, None] - top
    rows = ys + _displacement(coefficients, xs, ys, width, height, measured)
    columns = np.broadcast_to(xs, rows.shape)
    if transparent_border:
        source = np.concatenate([rgb, opaque], axis=2)
        result = np.dstack([_resample(source[..., c], rows, columns, "constant") for c in range(4)])
    else:
        result = np.dstack([_resample(rgb[..., c], rows, columns, "nearest") for c in range(3)])
    return result, []


def _resize(channel: np.ndarray, size: Tuple[int, int], resample) -> np.ndarray:
    """A float channel resized with Pillow; ``size`` is (width, height)."""
    return np.asarray(Image.fromarray(channel.astype(np.float32)).resize(size, resample))


def _paper_colour(rgb: np.ndarray, paper: np.ndarray, scale: float) -> np.ndarray:
    """The colour of the bare paper at every pixel, shadows included.

    A blur of the pixels already judged to be paper, normalised by how much
    paper each neighbourhood holds, so ink and pictures do not darken the
    estimate around them. Worked at a quarter size: the paper's colour changes
    over hundreds of pixels, and the full-size blur would cost sixteen times
    as much for the same answer.
    """
    height, width = rgb.shape[:2]
    small = (max(1, width // 4), max(1, height // 4))
    weight = _resize(paper, small, Image.Resampling.BOX)
    weight_blur = np.maximum(ndimage.gaussian_filter(weight, 12 * scale), 1e-6)
    channels = []
    for channel in range(3):
        reduced = _resize(rgb[..., channel], small, Image.Resampling.BOX)
        estimate = ndimage.gaussian_filter(reduced * weight, 12 * scale) / weight_blur
        channels.append(_resize(estimate, (width, height), Image.Resampling.BILINEAR))
    return np.clip(np.dstack(channels), 1.0, 254.0)


def _picture_mask(saturation: np.ndarray, darkness: np.ndarray, valid: np.ndarray, scale: float) -> np.ndarray:
    """Photographs and drawings on the page, which stay fully opaque.

    Seeded from coloured pixels only, since printed text is grey and must go
    through the soft ink-against-paper treatment instead. Small holes are
    filled, so a pale highlight inside a leaf is not punched out; big ones are
    not, so the inside of a ruled box still becomes transparent.
    """
    seeds = valid & (saturation > 45) & (darkness > 0.12)
    seeds = ndimage.binary_opening(seeds, structure=_EIGHT_CONNECTED)
    seeds = ndimage.binary_closing(seeds, structure=np.ones((_size(7 * scale),) * 2, bool))
    regions = _components(seeds)
    large = regions.areas > 1500 * scale * scale
    large[0] = False
    pictures = large[regions.labels]

    # A hole is a region of non-picture that does not reach the image edge.
    gaps = _components(~pictures, structure=None)
    height, width = pictures.shape
    touches_edge = (
        (gaps.lefts == 0)
        | (gaps.tops == 0)
        | (gaps.lefts + gaps.widths == width)
        | (gaps.tops + gaps.heights == height)
    )
    fill = ~touches_edge & (gaps.areas < 15000 * scale * scale)
    fill[0] = False
    pictures = pictures | fill[gaps.labels]

    # Grown a little into the dark outlines and shading drawn around them.
    grown = ndimage.maximum_filter(pictures, size=_size(9 * scale))
    return pictures | (grown & (darkness > 0.25))


def remove_background(image: np.ndarray) -> np.ndarray:
    """RGBA with the paper made transparent and ink and pictures kept.

    ``image`` is RGB, or RGBA whose fully transparent pixels are not part of
    the page (the strip ``straighten`` uncovers). The result is cropped to
    what is left visible.
    """
    if image.shape[2] == 4:
        # Eroded so the half-transparent seam the resampling leaves along the
        # page edge does not turn into a dark line.
        valid = ndimage.minimum_filter(image[..., 3] == 255, size=5)
    else:
        valid = np.ones(image.shape[:2], bool)
    scale = _scale(image)
    rgb = image[..., :3].astype(np.float32)
    saturation = _saturation(image)

    paper = _paper_colour(rgb, valid & (_gray(image) > 150) & (saturation < 45), scale)
    lighter = ((rgb - paper) / (255.0 - paper)).max(axis=2)
    darker = ((paper - rgb) / paper).max(axis=2)
    # Only ink darker than the paper counts as ink. A phone sharpens edges,
    # leaving a bright rim around every letter; counted as ink, it would
    # survive as a white halo.
    ink = np.clip(darker, 0.0, 1.0)
    pictures = _picture_mask(saturation, np.clip(np.maximum(lighter, darker), 0.0, 1.0), valid, scale)

    # Mapped so faint paper texture becomes fully clear and the body of a
    # letter fully solid, with the antialiased edge in between.
    ink_alpha = np.clip((ink - 0.17) / 0.28, 0.0, 1.0)
    picture_alpha = ndimage.gaussian_filter(pictures.astype(np.float32), 1.0)
    alpha = np.maximum(ink_alpha, picture_alpha) * valid

    # An edge pixel is part ink and part paper. Taking the paper back out
    # leaves the ink's own colour, so the letter edge does not carry a pale
    # fringe of the old background onto whatever the image is placed over.
    weight = np.maximum(ink, 1e-3)[..., None]
    unmixed = np.clip((rgb - (1.0 - weight) * paper) / weight, 0.0, 255.0)
    keep_original = pictures[..., None] | (ink[..., None] > 0.5)
    colour = np.where(keep_original, rgb, unmixed)
    result = np.dstack([colour, alpha * 255.0]).astype(np.uint8)

    ys, xs = np.nonzero(alpha > 0.02)
    if len(ys) == 0:
        return result
    return result[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def _load_rgb(path: Path) -> np.ndarray:
    """The upload as an RGB array, upright and with HEIC handled.

    Read through prepare_image so EXIF orientation is applied exactly as the
    images tab applies it.
    """
    image, _dpi = prepare_image(path)
    try:
        return np.asarray(image.convert("RGB"))
    finally:
        image.close()


def clean_page(src, dst, straighten_text: bool = True, transparent: bool = True) -> PageResult:
    """Clean up the page photo at ``src`` and write a PNG to ``dst``.

    At least one of the two steps must be asked for: with neither, there is
    nothing to do and the call is a mistake rather than a copy.
    """
    if not straighten_text and not transparent:
        raise ValueError("clean_page needs at least one of straighten_text or transparent")
    dst = Path(dst)
    image = _load_rgb(Path(src))
    warnings: List[str] = []
    if straighten_text:
        image, warnings = straighten(image, transparent_border=transparent)
    if transparent:
        image = remove_background(image)
    try:
        Image.fromarray(np.ascontiguousarray(image)).save(str(dst), "PNG")
    except OSError as exc:
        raise ToolError("Could not write {0}: {1}".format(dst.name, exc)) from exc
    return PageResult(output_path=dst, warnings=warnings)
