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
import shutil
import subprocess
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
        resample = False
        warnings.append(
            "Source images are already at {0} dpi or below, so they were left "
            "untouched and only the file structure was compressed.".format(
                int(round(source.max_ppi))
            )
        )

    if resample and source.is_scan and preset.image_dpi < PRINT_DPI_FLOOR:
        warnings.append(
            "This looks like a scan. {0} resamples it to {1} dpi, below the "
            "{2} dpi print floor, so small text may become hard to read. "
            "Use Print 200 dpi or higher if this is going to a printer.".format(
                preset.label, preset.image_dpi, PRINT_DPI_FLOOR
            )
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
        )

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
        # A bigger output is a failed compression, not a trade-off.
        shutil.copyfile(str(src), str(dst))
        size_after = size_before
        warnings.append(
            "Already optimised: compression produced a larger file, so the "
            "original was kept unchanged."
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
