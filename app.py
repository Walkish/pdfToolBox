"""Flask front end for the PDF toolbox.

Routes stay thin: validate the upload, call one pdftools function, shape JSON.
All real behaviour lives in pdftools/ and is tested without Flask.
"""

import io
import os
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.exceptions import HTTPException

from pdftools import binaries, compress, images, jobs, merge, preview, validate

# Imported from its own module rather than as compress.ToolError: merge,
# images and preview raise this same class, and naming it after compress
# implies a dependency on compress that none of them have.
from pdftools.errors import ToolError

_DEBUG_TRUE_VALUES = ("1", "true", "yes")

MAX_CONTENT_LENGTH = 500 * 1024 * 1024
# Named once and used both to configure Flask and to serve the index, rather
# than read back off ``application.static_folder`` -- which Flask types as
# Optional, so reading it back loses the guarantee that it is set.
STATIC_FOLDER = "static"
HOST = "127.0.0.1"
# 5001 and 5000 are both bad defaults on macOS: 5000 is commonly held by
# AirPlay Receiver and 5001 by Docker's own port-forwarding daemon, so a
# fresh checkout of this project would fail to start with a confusing
# "Address already in use" unless the default lands somewhere unlikely to
# already be taken.
PORT = 5057


def resolve_port() -> int:
    """The TCP port to bind: ``PDFTOOLBOX_PORT`` if set, else ``PORT``.

    Raises ``ValueError`` with a message naming the offending value when the
    override is set but is not a plain integer, so ``main()`` can fail with a
    clear message instead of a bare traceback.
    """
    raw = os.environ.get("PDFTOOLBOX_PORT")
    if raw is None or raw.strip() == "":
        return PORT
    try:
        return int(raw.strip())
    except ValueError:
        raise ValueError(
            "PDFTOOLBOX_PORT={0!r} is not a valid port number; it must be an "
            "integer, e.g. PDFTOOLBOX_PORT=5057.".format(raw)
        )


def _saved_uploads(job, kind: str) -> List[Dict]:
    """Persist uploads into the job's input directory, validating each.

    Returns one record per upload, in upload order, each either
    {"path": Path, "name": str, "position": int} or
    {"error": str, "name": str, "position": int}.
    """
    uploads = request.files.getlist("files")
    records = []
    for position, storage in enumerate(uploads):
        display_name = storage.filename or "file-{0}".format(position)
        # A client-supplied filename can be arbitrarily long (a name right
        # at a Mac's own 255-byte NAME_MAX is entirely legal); normalize_
        # output_name clamps it to fit even after the position prefix below
        # is added, so a legal upload can never blow the filesystem's own
        # limit and take the request down with it.
        safe = validate.normalize_output_name(display_name, "file-{0}.pdf".format(position))
        target = job.inputs / "{0:03d}_{1}".format(position, safe)
        try:
            storage.save(str(target))
        except OSError as exc:
            records.append(
                {
                    "error": "Could not save {0}: {1}".format(display_name, exc),
                    "name": display_name,
                    "position": position,
                }
            )
            continue
        try:
            if kind == "pdf":
                validate.validate_pdf_file(target, display_name)
            else:
                validate.validate_image_file(target, display_name)
        except validate.ValidationError as exc:
            target.unlink(missing_ok=True)
            records.append({"error": str(exc), "name": display_name, "position": position})
            continue
        records.append({"path": target, "name": display_name, "position": position})
    return records


def _output_target(job, record) -> Path:
    """Where a per-file result is written.

    Prefixed with the upload position: two uploads can legitimately share a
    filename, and without the prefix the second result would silently
    overwrite the first. The user still sees the original name, because
    downloads are served under ``display_name``. Clamped through the same
    length budget as the input side, for the same reason.
    """
    safe = validate.normalize_output_name(record["name"], "output.pdf")
    return job.outputs / "{0:03d}_{1}".format(record["position"], safe)


def _redact(message: str, *pairs) -> str:
    """Replace any internal on-disk path or filename embedded in a pdftools
    error message with the name the user actually typed.

    pdftools modules format some ``ToolError`` messages with the *internal*
    path (a temp-directory path, or a position-prefixed on-disk name like
    ``"003_report.pdf"``) because that is what they were handed -- they have
    no notion of a "display name". That internal detail is noise (or worse,
    an accidental disclosure of the server's filesystem layout) once it
    reaches a client response, so it is swapped out here, at the boundary,
    for the name the user recognizes. Each pair is
    ``(internal_path, display_name)``.
    """
    for internal_path, display_name in pairs:
        internal_path = Path(internal_path)
        message = message.replace(str(internal_path), display_name)
        message = message.replace(internal_path.name, display_name)
    return message


def _failure(name: str, message: str, stderr: str = "") -> Dict:
    return {
        "index": None,
        "name": name,
        "ok": False,
        "size_before": 0,
        "size_after": 0,
        "saved_pct": 0.0,
        "warnings": [],
        # None, not True: this row produced no output, so "print safe"
        # does not apply -- a UI iterating rows must not read this as a
        # positive claim about a file that was never written. Same for the
        # print floor: nothing was written, so nothing was measured.
        "print_safe": None,
        "below_print_floor": None,
        "download_url": None,
        "preview_url": None,
        "error": message,
        "stderr": stderr or None,
    }


def _success(job, index: int, name: str, result: compress.CompressResult, preset: compress.Preset) -> Dict:
    return {
        "index": index,
        "name": name,
        "ok": True,
        "size_before": result.size_before,
        "size_after": result.size_after,
        "saved_pct": round(result.saved_ratio * 100.0, 1),
        "warnings": list(result.warnings),
        "print_safe": preset.print_safe,
        # Measured from the finished file, so the UI can flag the print risk
        # without parsing the warning prose.
        "below_print_floor": result.below_print_floor,
        "download_url": "/jobs/{0}/files/{1}".format(job.id, index),
        "preview_url": "/api/preview/{0}/{1}".format(job.id, index),
        "error": None,
        "stderr": None,
    }


def _plain_success(job, index: int, name: str, path: Path) -> Dict:
    size = path.stat().st_size
    return {
        "index": index,
        "name": name,
        "ok": True,
        "size_before": size,
        "size_after": size,
        "saved_pct": 0.0,
        "warnings": [],
        "print_safe": True,
        # None, not False: no compression ran on this file, so its resolution
        # was never measured and this row makes no claim either way.
        "below_print_floor": None,
        "download_url": "/jobs/{0}/files/{1}".format(job.id, index),
        "preview_url": None,
        "error": None,
        "stderr": None,
    }


def _requested_preset() -> compress.Preset:
    preset_id = request.form.get("preset", compress.DEFAULT_PRESET)
    if preset_id not in compress.PRESETS:
        abort(400, description="Unknown preset {0!r}".format(preset_id))
    return compress.PRESETS[preset_id]


def _requested_rotations(count: int) -> List[int]:
    """One clockwise angle per upload, indexed by upload position.

    Absent entirely means no rotation, so a client that knows nothing about
    this field -- an older page, or curl -- keeps working.
    """
    raw = request.form.getlist("rotations")
    if not raw:
        return [0] * count
    if len(raw) != count:
        abort(400, description="Expected one rotation per file, got {0} for {1}".format(len(raw), count))
    rotations = []
    for value in raw:
        try:
            rotation = int(value)
        except ValueError:
            abort(400, description="Rotation {0!r} is not a number".format(value))
        if rotation not in images.VALID_ROTATIONS:
            abort(
                400,
                description="Rotation {0} is not one of {1}".format(
                    rotation, ", ".join(str(valid) for valid in images.VALID_ROTATIONS)
                ),
            )
        rotations.append(rotation)
    return rotations


def _payload(job, results: List[Dict]) -> Dict:
    any_output = any(result["ok"] for result in results)
    return {
        "job_id": job.id,
        "results": results,
        "zip_url": "/jobs/{0}/zip".format(job.id) if any_output else None,
    }


def create_app(job_store: Optional[jobs.JobStore] = None) -> Flask:
    application = Flask(__name__, static_folder=STATIC_FOLDER, static_url_path="/static")
    application.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    store = job_store if job_store is not None else jobs.JobStore()

    @application.errorhandler(HTTPException)
    def _json_errors(exc):
        # The success path always answers JSON; an API that answers JSON on
        # success and HTML on failure is an inconsistent contract, and Task
        # 10 parses `description` out of every error body.
        response = jsonify({"error": exc.name, "description": exc.description})
        response.status_code = exc.code
        # Some error headers carry protocol information the client needs, not
        # decoration: 405's `Allow`, 401's `WWW-Authenticate`. Building a
        # fresh response drops them, so they are copied over. `Content-Type`
        # is skipped deliberately -- it describes the HTML body this handler
        # exists to replace, and copying it would relabel JSON as text/html.
        for header, value in exc.get_headers():
            if header.lower() != "content-type":
                response.headers[header] = value
        return response

    @application.route("/")
    def index():
        return send_from_directory(STATIC_FOLDER, "index.html")

    @application.route("/api/presets")
    def presets():
        return jsonify(
            {
                "default": compress.DEFAULT_PRESET,
                "print_floor_dpi": compress.PRINT_DPI_FLOOR,
                "presets": [
                    {
                        "id": preset.id,
                        "label": preset.label,
                        "print_safe": preset.print_safe,
                    }
                    # PRESETS' own insertion order is the ladder's order; a
                    # hand-written id list here would silently omit a fifth
                    # preset added later while leaving it selectable.
                    for preset in compress.PRESETS.values()
                ],
            }
        )

    @application.route("/api/compress", methods=["POST"])
    def api_compress():
        preset = _requested_preset()
        job = store.create()
        records = _saved_uploads(job, "pdf")
        if not records:
            abort(400, description="No files were uploaded")
        results = []
        for record in records:
            if "error" in record:
                results.append(_failure(record["name"], record["error"]))
                continue
            destination = _output_target(job, record)
            try:
                result = compress.compress_pdf(record["path"], destination, preset.id)
            except ToolError as exc:
                message = _redact(str(exc), (record["path"], record["name"]))
                results.append(_failure(record["name"], message, exc.stderr))
                continue
            index = job.add_output(
                result.output_path,
                record["name"],
                {"source": str(record["path"])},
            )
            results.append(_success(job, index, record["name"], result, preset))
        return jsonify(_payload(job, results))

    @application.route("/api/merge", methods=["POST"])
    def api_merge():
        # Validate the preset before doing any merge work: api_compress
        # validates first for the same reason -- a bad preset must not burn
        # a real merge and then 400, abandoning a written output to the TTL
        # sweep.
        # ``compress_preset`` doubles as the "was compression requested"
        # flag: the branch below tests it for None, so the value the branch
        # uses cannot be None by construction.
        compress_preset = _requested_preset() if request.form.get("compress") == "1" else None

        job = store.create()
        records = _saved_uploads(job, "pdf")
        if not records:
            abort(400, description="No files were uploaded")
        results = []
        usable = [record for record in records if "error" not in record]
        for record in records:
            if "error" in record:
                results.append(_failure(record["name"], record["error"]))
        if not usable:
            return jsonify(_payload(job, results))

        output_name = validate.normalize_output_name(request.form.get("output_name", ""), "merged.pdf")
        merged = job.outputs / output_name
        try:
            merge.merge_pdfs(
                [record["path"] for record in usable],
                merged,
                normalize_pages=request.form.get("normalize") == "1",
            )
        except (ToolError, ValueError, OSError) as exc:
            message = _redact(str(exc), *[(record["path"], record["name"]) for record in usable])
            results.append(_failure(output_name, message, getattr(exc, "stderr", "")))
            return jsonify(_payload(job, results))

        if compress_preset is not None:
            staged = job.root / "merged_raw.pdf"
            merged.replace(staged)
            try:
                result = compress.compress_pdf(staged, merged, compress_preset.id)
            except (ToolError, OSError) as exc:
                message = _redact(str(exc), (staged, output_name))
                results.append(_failure(output_name, message, getattr(exc, "stderr", "")))
                return jsonify(_payload(job, results))
            # The compressed merge result keeps its pre-compression source
            # so /api/preview can render a before/after comparison for it,
            # same as a single-file compress -- without this, the
            # preview_url _success advertises 404s for exactly the rows
            # where a legibility warning makes the user want to check.
            index = job.add_output(merged, output_name, {"source": str(staged)})
            results.append(_success(job, index, output_name, result, compress_preset))
        else:
            index = job.add_output(merged, output_name)
            results.append(_plain_success(job, index, output_name, merged))
        return jsonify(_payload(job, results))

    @application.route("/api/thumbnail", methods=["POST"])
    def api_thumbnail():
        """A preview image for one upload. Stateless: nothing is stored."""
        storage = request.files.get("file")
        if storage is None:
            abort(400, description="No file was uploaded")
        display_name = storage.filename or "upload"
        # Validated exactly like a real upload. Without this the endpoint is a
        # second door into the image decoder that skips the size and
        # pixel-count limits, which is a hole rather than a convenience.
        with tempfile.TemporaryDirectory(prefix="thumbnail-") as directory:
            target = Path(directory) / "upload"
            storage.save(str(target))
            try:
                validate.validate_image_file(target, display_name)
                data = images.thumbnail_png(target)
            except validate.ValidationError as exc:
                abort(400, description=str(exc))
            except ToolError as exc:
                abort(400, description=_redact(str(exc), (target, display_name)))
        response = send_file(io.BytesIO(data), mimetype="image/png")
        # A preview of a file the user may replace under the same name.
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.route("/api/images", methods=["POST"])
    def api_images():
        # Read before any upload is saved, the way api_merge validates its
        # preset first: a malformed request must not burn real conversion work
        # and then 400, abandoning an output to the TTL sweep.
        rotations = _requested_rotations(len(request.files.getlist("files")))
        job = store.create()
        records = _saved_uploads(job, "image")
        if not records:
            abort(400, description="No files were uploaded")
        results = []
        usable = [record for record in records if "error" not in record]
        for record in records:
            if "error" in record:
                results.append(_failure(record["name"], record["error"]))
        if not usable:
            return jsonify(_payload(job, results))

        output_name = validate.normalize_output_name(request.form.get("output_name", ""), "images.pdf")
        destination = job.outputs / output_name
        try:
            images.images_to_pdf(
                [record["path"] for record in usable],
                destination,
                work_dir=job.root,
                # By upload position, not by index in the filtered list: when
                # an upload in the middle fails validation, matching on the
                # filtered index shifts every later rotation onto the wrong
                # picture.
                rotations=[rotations[record["position"]] for record in usable],
            )
        except (ToolError, ValueError, OSError) as exc:
            message = _redact(str(exc), *[(record["path"], record["name"]) for record in usable])
            results.append(_failure(output_name, message, getattr(exc, "stderr", "")))
            return jsonify(_payload(job, results))
        index = job.add_output(destination, output_name)
        results.append(_plain_success(job, index, output_name, destination))
        return jsonify(_payload(job, results))

    def _job_or_404(job_id: str):
        try:
            return store.get(job_id)
        except KeyError:
            abort(404, description="Unknown job")

    def _output_or_404(job, index: int):
        try:
            return job.output(index)
        except IndexError:
            abort(404, description="Unknown output")

    @application.route("/api/preview/<job_id>/<int:index>")
    def api_preview(job_id, index):
        job = _job_or_404(job_id)
        entry = _output_or_404(job, index)
        source = entry["meta"].get("source")
        if not source:
            abort(404, description="No source retained for this output")
        destination = job.previews / str(index)
        try:
            comparison = preview.build_comparison(source, entry["path"], destination)
        except ToolError as exc:
            # Same treatment as every other route: redact the internal path
            # and the position-prefixed on-disk name, then let the shared
            # HTTPException handler shape the {error, description} envelope
            # the client already parses.
            message = _redact(
                str(exc),
                (source, entry["display_name"]),
                (entry["path"], entry["display_name"]),
            )
            abort(500, description=message)
        return jsonify(
            {
                "before": "/jobs/{0}/previews/{1}/before.png".format(job.id, index),
                "after": "/jobs/{0}/previews/{1}/after.png".format(job.id, index),
                "page": comparison.page,
                "dpi": comparison.dpi,
            }
        )

    @application.route("/jobs/<job_id>/previews/<int:index>/<side>.png")
    def preview_image(job_id, index, side):
        if side not in ("before", "after"):
            abort(404)
        job = _job_or_404(job_id)
        path = job.previews / str(index) / "{0}.png".format(side)
        if not path.exists():
            abort(404, description="Preview not generated yet")
        return send_file(str(path), mimetype="image/png")

    @application.route("/jobs/<job_id>/files/<int:index>")
    def download(job_id, index):
        job = _job_or_404(job_id)
        entry = _output_or_404(job, index)
        return send_file(
            str(entry["path"]),
            as_attachment=True,
            # The display name is a client-supplied upload filename. It is
            # normalized on its way into a zip member; a Content-Disposition
            # filename is the same untrusted value leaving by another door,
            # so it goes through the same normalization rather than relying
            # on Werkzeug to object to whatever arrives.
            download_name=jobs.safe_display_name(entry["display_name"]),
            mimetype="application/pdf",
        )

    @application.route("/jobs/<job_id>/zip")
    def download_zip(job_id):
        job = _job_or_404(job_id)
        if not job.outputs_list():
            abort(404, description="This job produced no files")
        archive = job.zip_outputs()
        return send_file(
            str(archive),
            as_attachment=True,
            download_name="pdf-toolbox-{0}.zip".format(job.id[:8]),
            mimetype="application/zip",
        )

    return application


app = create_app()


def main():
    missing = None
    try:
        binaries.check_all()
    except binaries.MissingBinary as exc:
        missing = exc
    if missing is not None:
        raise SystemExit("PDF Toolbox cannot start.\n{0}\n\nInstall it and try again.".format(missing))
    try:
        port = resolve_port()
    except ValueError as exc:
        raise SystemExit("PDF Toolbox cannot start.\n{0}".format(exc))
    print("Ghostscript {0} detected.".format(binaries.gs_version()))
    print("PDF Toolbox running at http://{0}:{1}".format(HOST, port))
    # bool(os.environ.get(...)) treats *any* non-empty value as on, so
    # PDFTOOLBOX_DEBUG=0 or =false would enable the Werkzeug debugger
    # console. Compare against known "on" spellings instead.
    debug_flag = os.environ.get("PDFTOOLBOX_DEBUG", "").strip().lower() in _DEBUG_TRUE_VALUES
    try:
        app.run(host=HOST, port=port, debug=debug_flag)
    except OSError as exc:
        raise SystemExit(
            "PDF Toolbox cannot start.\nCould not bind {0}:{1} ({2}).\n\n"
            "Something else is already listening on that port. Set "
            "PDFTOOLBOX_PORT to a free port and try again, e.g.:\n"
            "  PDFTOOLBOX_PORT=5058 .venv/bin/python app.py".format(HOST, port, exc)
        )


if __name__ == "__main__":
    main()
