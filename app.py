"""Flask front end for the PDF toolbox.

Routes stay thin: validate the upload, call one pdftools function, shape JSON.
All real behaviour lives in pdftools/ and is tested without Flask.
"""
import os
from pathlib import Path
from typing import Dict, List, Optional

from flask import Flask, abort, jsonify, request, send_file, send_from_directory
from werkzeug.utils import secure_filename

from pdftools import binaries, compress, images, jobs, merge, preview, validate

MAX_CONTENT_LENGTH = 500 * 1024 * 1024
HOST = "127.0.0.1"
PORT = 5001


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
        safe = secure_filename(display_name) or "file-{0}".format(position)
        target = job.inputs / "{0:03d}_{1}".format(position, safe)
        storage.save(str(target))
        try:
            if kind == "pdf":
                validate.validate_pdf_file(target, display_name)
            else:
                validate.validate_image_file(target, display_name)
        except validate.ValidationError as exc:
            target.unlink()
            records.append(
                {"error": str(exc), "name": display_name, "position": position}
            )
            continue
        records.append({"path": target, "name": display_name, "position": position})
    return records


def _output_target(job, record) -> Path:
    """Where a per-file result is written.

    Prefixed with the upload position: two uploads can legitimately share a
    filename, and without the prefix the second result would silently
    overwrite the first. The user still sees the original name, because
    downloads are served under ``display_name``.
    """
    safe = secure_filename(Path(record["name"]).name) or "output.pdf"
    return job.outputs / "{0:03d}_{1}".format(record["position"], safe)


def _failure(name: str, message: str, stderr: str = "") -> Dict:
    return {
        "index": None,
        "name": name,
        "ok": False,
        "size_before": 0,
        "size_after": 0,
        "saved_pct": 0.0,
        "warnings": [],
        "print_safe": True,
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


def _payload(job, results: List[Dict]) -> Dict:
    any_output = any(result["ok"] for result in results)
    return {
        "job_id": job.id,
        "results": results,
        "zip_url": "/jobs/{0}/zip".format(job.id) if any_output else None,
    }


def create_app(job_store: Optional[jobs.JobStore] = None) -> Flask:
    application = Flask(__name__, static_folder="static", static_url_path="/static")
    application.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    store = job_store if job_store is not None else jobs.JobStore()
    application.config["JOB_STORE"] = store

    @application.route("/")
    def index():
        return send_from_directory(application.static_folder, "index.html")

    @application.route("/api/presets")
    def presets():
        ordered = ["print300", "print200", "screen150", "lossless"]
        return jsonify(
            {
                "default": compress.DEFAULT_PRESET,
                "print_floor_dpi": compress.PRINT_DPI_FLOOR,
                "presets": [
                    {
                        "id": compress.PRESETS[preset_id].id,
                        "label": compress.PRESETS[preset_id].label,
                        "print_safe": compress.PRESETS[preset_id].print_safe,
                    }
                    for preset_id in ordered
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
            except compress.ToolError as exc:
                results.append(_failure(record["name"], str(exc), exc.stderr))
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

        output_name = secure_filename(request.form.get("output_name", "") or "merged.pdf")
        if not output_name.lower().endswith(".pdf"):
            output_name += ".pdf"
        merged = job.outputs / output_name
        try:
            merge.merge_pdfs([record["path"] for record in usable], merged)
        except (compress.ToolError, ValueError) as exc:
            results.append(_failure(output_name, str(exc), getattr(exc, "stderr", "")))
            return jsonify(_payload(job, results))

        if request.form.get("compress") == "1":
            preset = _requested_preset()
            staged = job.root / "merged_raw.pdf"
            merged.replace(staged)
            try:
                result = compress.compress_pdf(staged, merged, preset.id)
            except compress.ToolError as exc:
                results.append(_failure(output_name, str(exc), exc.stderr))
                return jsonify(_payload(job, results))
            index = job.add_output(merged, output_name)
            results.append(_success(job, index, output_name, result, preset))
        else:
            index = job.add_output(merged, output_name)
            results.append(_plain_success(job, index, output_name, merged))
        return jsonify(_payload(job, results))

    @application.route("/api/images", methods=["POST"])
    def api_images():
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

        output_name = secure_filename(request.form.get("output_name", "") or "images.pdf")
        if not output_name.lower().endswith(".pdf"):
            output_name += ".pdf"
        destination = job.outputs / output_name
        try:
            images.images_to_pdf(
                [record["path"] for record in usable], destination, work_dir=job.root
            )
        except (compress.ToolError, ValueError) as exc:
            results.append(_failure(output_name, str(exc), getattr(exc, "stderr", "")))
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
        except compress.ToolError as exc:
            return jsonify({"error": str(exc)}), 500
        return jsonify(
            {
                "before": "/jobs/{0}/previews/{1}/before.png".format(job.id, index),
                "after": "/jobs/{0}/previews/{1}/after.png".format(job.id, index),
                "page": comparison["page"],
                "dpi": comparison["dpi"],
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
            download_name=entry["display_name"],
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
        raise SystemExit(
            "PDF Toolbox cannot start.\n{0}\n\nInstall it and try again.".format(missing)
        )
    print("Ghostscript {0} detected.".format(binaries.gs_version()))
    print("PDF Toolbox running at http://{0}:{1}".format(HOST, PORT))
    app.run(host=HOST, port=PORT, debug=bool(os.environ.get("PDFTOOLBOX_DEBUG")))


if __name__ == "__main__":
    main()
