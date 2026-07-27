"""Ghostscript-backed compression that keeps output printable.

Ghostscript's pdfwrite device does not rasterize text: presets act on embedded
raster images and on font embedding. So born-digital PDFs stay sharp at any
setting, and scans are the only real risk. The guardrails in compress_pdf
exist for that case.

Presets set image parameters explicitly instead of using -dPDFSETTINGS,
because that macro bundles choices this tool must override (notably colour
conversion) and its lower levels target screen viewing, not print.
"""
import dataclasses
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from . import binaries
from .errors import ToolError
from .inspect import PdfProfile, profile_pdf

# Body text at 10-11 pt stays cleanly legible in print down to this
# resolution; below it, letter strokes start merging.
PRINT_DPI_FLOOR = 200


@dataclasses.dataclass(frozen=True)
class Preset:
    """A compression level. ``image_dpi is None`` means no resampling at all."""

    id: str
    label: str
    image_dpi: Optional[int]
    mono_dpi: Optional[int]
    qfactor: Optional[float]
    chroma_samples: str
    print_safe: bool


# QFactor values follow Distiller's print profiles, calibrated against
# Ghostscript 10.7's actual pdfwrite output (see tests/test_compress.py):
# lower is higher quality. Chroma subsampling stays off for the print
# presets, because subsampling is what smears coloured text edges.
PRESETS: Dict[str, Preset] = {
    "print300": Preset("print300", "Print 300 dpi", 300, 600, 0.15, "[1 1 1 1]", True),
    "print200": Preset("print200", "Print 200 dpi", 200, 600, 0.4, "[1 1 1 1]", True),
    "screen150": Preset("screen150", "Screen 150 dpi", 150, 300, 0.76, "[2 1 1 2]", False),
    "lossless": Preset("lossless", "Lossless", None, None, None, "[1 1 1 1]", True),
}

DEFAULT_PRESET = "print300"

BASE_ARGS = [
    "-sDEVICE=pdfwrite",
    "-dCompatibilityLevel=1.7",
    "-dNOPAUSE",
    "-dBATCH",
    "-dQUIET",
    "-dSAFER",
    # A logo repeated on every page is stored once. Free saving, no quality cost.
    "-dDetectDuplicateImages=true",
    # The printer must not substitute a font it does not have.
    "-dCompressFonts=true",
    "-dEmbedAllFonts=true",
    "-dSubsetFonts=true",
    # Converting CMYK to RGB shifts colours on a press and ruins rich black.
    "-dColorConversionStrategy=/LeaveColorUnchanged",
    # Never silently reorient pages; that shows up as sideways sheets.
    "-dAutoRotatePages=/None",
]


@dataclasses.dataclass
class CompressResult:
    output_path: Path
    size_before: int
    size_after: int
    preset_id: str
    resampled: bool
    returned_original: bool
    source_ppi: Optional[float]
    warnings: List[str]

    @property
    def saved_bytes(self) -> int:
        return self.size_before - self.size_after

    @property
    def saved_ratio(self) -> float:
        if self.size_before == 0:
            return 0.0
        return self.saved_bytes / float(self.size_before)


def _resample_args(preset: Preset) -> List[str]:
    """Downsampling flags. Threshold 1.5 avoids pointless generation loss:
    a 320 dpi image is left alone rather than ground down to 300 for 2%."""
    return [
        "-dDownsampleColorImages=true",
        "-dColorImageDownsampleType=/Bicubic",
        "-dColorImageResolution={0}".format(preset.image_dpi),
        "-dColorImageDownsampleThreshold=1.5",
        "-dDownsampleGrayImages=true",
        "-dGrayImageDownsampleType=/Bicubic",
        "-dGrayImageResolution={0}".format(preset.image_dpi),
        "-dGrayImageDownsampleThreshold=1.5",
        "-dDownsampleMonoImages=true",
        "-dMonoImageDownsampleType=/Subsample",
        "-dMonoImageResolution={0}".format(preset.mono_dpi),
        "-dMonoImageDownsampleThreshold=1.5",
        "-dAutoFilterColorImages=false",
        "-dColorImageFilter=/DCTEncode",
        "-dAutoFilterGrayImages=false",
        "-dGrayImageFilter=/DCTEncode",
        # Bilevel scans stay lossless. JPEG here produces grey speckle around
        # every letter, the classic "dirty scan" artefact.
        "-dMonoImageFilter=/CCITTFaxEncode",
    ]


def _lossless_args() -> List[str]:
    """Filter policy for the no-resample path (the ``lossless`` preset, and
    any preset whose target the source is already at or below).

    Without this, Ghostscript's own defaults apply --
    ``AutoFilterColorImages``/``AutoFilterGrayImages`` default to true, which
    picks a lossy JPEG encoding at roughly QFactor 0.9 for any raster image
    that is not already a JPEG. That would silently re-encode, say, a Flate
    PNG from a Word export at "lossless" quality worse than any print preset.
    JPEGs are passed through untouched; anything else stays lossless.
    """
    return [
        "-dPassThroughJPEGImages=true",
        "-dAutoFilterColorImages=false",
        "-dColorImageFilter=/FlateEncode",
        "-dAutoFilterGrayImages=false",
        "-dGrayImageFilter=/FlateEncode",
        "-dDownsampleColorImages=false",
        "-dDownsampleGrayImages=false",
        "-dDownsampleMonoImages=false",
        "-dMonoImageFilter=/CCITTFaxEncode",
    ]


def _distiller_snippet(preset: Preset) -> str:
    """JPEG quality for pdfwrite goes through setdistillerparams; the
    -dJPEGQ switch is not reliably honoured by this device."""
    image_dict = (
        "<< /QFactor {0} /Blend 1 /HSamples {1} /VSamples {1} >>".format(
            preset.qfactor, preset.chroma_samples
        )
    )
    return "<< /ColorImageDict {0} /GrayImageDict {0} >> setdistillerparams".format(
        image_dict
    )


def build_command(src, dst, preset: Preset, resample: bool = True) -> List[str]:
    """Assemble the Ghostscript argument list. Never a shell string.

    ``-o`` must come before ``-c``: Ghostscript treats every token after
    ``-c`` as PostScript to execute, up to the next ``-f``, so an ``-o``
    placed in that span is swallowed as PostScript instead of being parsed
    as the output-file switch.
    """
    command = [binaries.find("gs")] + list(BASE_ARGS)
    do_resample = resample and preset.image_dpi is not None
    if do_resample:
        command += _resample_args(preset)
    else:
        command += _lossless_args()
    command += ["-o", str(dst)]
    if do_resample:
        command += ["-c", _distiller_snippet(preset)]
    # -c must precede -f, and the input file comes last.
    command += ["-f", str(src)]
    return command


def _timeout_stderr(exc) -> str:
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        return stderr.decode("utf-8", "replace")
    return stderr or ""


def _restore_original(src_path: Path, dst_path: Path) -> None:
    """Copy ``src_path`` over ``dst_path`` atomically.

    A plain ``shutil.copyfile(src, dst)`` truncates ``dst`` before streaming
    the copy, so a mid-copy I/O failure would leave a truncated PDF where
    Ghostscript's valid output used to be. Copying to a sibling temp file and
    renaming it into place means ``dst`` is either the old file or the fully
    copied one, never a partial write.
    """
    fd, temp_name = tempfile.mkstemp(
        dir=str(dst_path.parent), prefix=".compress-restore-", suffix=".tmp"
    )
    os.close(fd)
    try:
        shutil.copyfile(str(src_path), temp_name)
        os.replace(temp_name, str(dst_path))
    except OSError as exc:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise ToolError(
            "Could not restore the original file at {0}: {1}".format(dst_path, exc)
        ) from exc


def compress_pdf(src, dst, preset_id: str = DEFAULT_PRESET, profile: Optional[PdfProfile] = None) -> CompressResult:
    """Compress ``src`` into ``dst`` at ``preset_id``, guarding legibility."""
    if preset_id not in PRESETS:
        raise ValueError(
            "Unknown preset {0!r}. Known presets: {1}".format(
                preset_id, ", ".join(sorted(PRESETS))
            )
        )
    preset = PRESETS[preset_id]
    src = Path(src)
    dst = Path(dst)
    source = profile if profile is not None else profile_pdf(src)

    warnings = []
    resample = preset.image_dpi is not None

    if resample and source.max_ppi is not None and source.max_ppi <= preset.image_dpi:
        # Nothing exceeds the target, so resampling would only re-encode.
        # The file is still rewritten below -- just without downsampling --
        # so this must not claim the images were left untouched.
        resample = False
        warnings.append(
            "Source images are already at {0} dpi, at or below the {1} dpi "
            "target, so they were not downsampled -- only the file structure "
            "was recompressed.".format(int(round(source.max_ppi)), preset.image_dpi)
        )

    command = build_command(src, dst, preset, resample=resample)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=binaries.TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(
            "Ghostscript timed out after {0}s on {1}".format(
                binaries.TIMEOUT_SECONDS, src.name
            ),
            stderr=_timeout_stderr(exc),
        ) from exc

    if completed.returncode != 0 or not dst.exists():
        raise ToolError(
            "Ghostscript failed on {0} with exit code {1}".format(
                src.name, completed.returncode
            ),
            stderr=completed.stderr or completed.stdout,
        )

    size_before = src.stat().st_size
    size_after = dst.stat().st_size
    returned_original = size_after >= size_before
    if returned_original:
        # A bigger (or equal) output is a failed compression, not a
        # trade-off. Nothing was actually resampled in the file the caller
        # receives, so the intent computed above no longer applies.
        _restore_original(src, dst)
        size_after = size_before
        resample = False
        warnings.append(
            "Already optimised: compression produced a larger file, so the "
            "original was kept unchanged."
        )

    # Compute the below-floor warning from what the output *actually* is,
    # not from the pre-run intent: if resampling was skipped or discarded
    # above, the image resolution the reader will see is the source's, not
    # the preset's target.
    effective_dpi = preset.image_dpi if resample else source.max_ppi
    if source.is_scan and effective_dpi is not None and effective_dpi < PRINT_DPI_FLOOR:
        warnings.append(
            "This looks like a scan at about {0} dpi, below the {1} dpi "
            "print floor most printers need for small text to stay "
            "legible.".format(int(round(effective_dpi)), PRINT_DPI_FLOOR)
        )

    return CompressResult(
        output_path=dst,
        size_before=size_before,
        size_after=size_after,
        preset_id=preset_id,
        resampled=resample,
        returned_original=returned_original,
        source_ppi=source.median_ppi,
        warnings=warnings,
    )
