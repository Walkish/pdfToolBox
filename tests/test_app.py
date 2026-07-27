"""Route-level tests via the Flask test client."""
import io
import zipfile

import pytest

import app as app_module
from pdftools import jobs


@pytest.fixture
def client(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path / "jobs")
    application = app_module.create_app(job_store=store)
    application.config["TESTING"] = True
    with application.test_client() as test_client:
        yield test_client


def upload(path, name):
    with open(str(path), "rb") as handle:
        return (io.BytesIO(handle.read()), name)


def test_index_serves_the_three_tabs(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for label in ("Compress", "Merge", "Images"):
        assert label in body


def test_presets_endpoint_lists_the_ladder_and_the_default(client):
    payload = client.get("/api/presets").get_json()
    assert payload["default"] == "print300"
    ids = [preset["id"] for preset in payload["presets"]]
    assert ids == ["print300", "print200", "screen150", "lossless"]
    by_id = {preset["id"]: preset for preset in payload["presets"]}
    assert by_id["screen150"]["print_safe"] is False


def test_compress_returns_sizes_and_a_download_url(client, scan_pdf_600dpi):
    response = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["name"] == "scan.pdf"
    assert result["size_after"] < result["size_before"]
    assert result["saved_pct"] > 0
    assert result["download_url"].startswith("/jobs/")
    downloaded = client.get(result["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.get_data()[:5] == b"%PDF-"


def test_compress_below_the_print_floor_reports_the_warning(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "screen150"},
        content_type="multipart/form-data",
    ).get_json()
    warnings = payload["results"][0]["warnings"]
    assert any("200 dpi" in warning for warning in warnings)


def test_one_bad_file_does_not_fail_the_batch(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/compress",
        data={
            "files": [
                upload(scan_pdf_600dpi, "good.pdf"),
                (io.BytesIO(b"PK\x03\x04not a pdf"), "bad.pdf"),
            ],
            "preset": "print300",
        },
        content_type="multipart/form-data",
    ).get_json()
    by_name = {result["name"]: result for result in payload["results"]}
    assert by_name["good.pdf"]["ok"] is True
    assert by_name["bad.pdf"]["ok"] is False
    assert by_name["bad.pdf"]["error"]
    assert payload["zip_url"] is not None


def test_two_uploads_sharing_a_filename_produce_two_distinct_results(
    client, scan_pdf_600dpi, scan_pdf_150dpi
):
    payload = client.post(
        "/api/compress",
        data={
            "files": [
                upload(scan_pdf_600dpi, "scan.pdf"),
                upload(scan_pdf_150dpi, "scan.pdf"),
            ],
            "preset": "print300",
        },
        content_type="multipart/form-data",
    ).get_json()
    assert len(payload["results"]) == 2
    first, second = payload["results"]
    assert first["download_url"] != second["download_url"]
    # The two sources differ in size, so a collision would show up as
    # identical downloads.
    assert client.get(first["download_url"]).get_data() != client.get(
        second["download_url"]
    ).get_data()


def test_an_unknown_preset_is_a_400(client, scan_pdf_600dpi):
    response = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "ludicrous"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_a_request_with_no_files_is_a_400(client):
    response = client.post(
        "/api/compress", data={"preset": "print300"}, content_type="multipart/form-data"
    )
    assert response.status_code == 400


def test_merge_preserves_the_upload_order(client, vector_pdf_factory):
    first = vector_pdf_factory(["ALPHA"])
    second = vector_pdf_factory(["BETA"])
    payload = client.post(
        "/api/merge",
        data={"files": [upload(second, "b.pdf"), upload(first, "a.pdf")]},
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["name"] == "merged.pdf"
    downloaded = client.get(result["download_url"]).get_data()
    assert downloaded[:5] == b"%PDF-"
    # ALPHA was uploaded second, so it must appear after BETA.
    assert downloaded.index(b"BETA") < downloaded.index(b"ALPHA")


def test_merge_can_compress_the_result(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/merge",
        data={
            "files": [upload(scan_pdf_600dpi, "one.pdf"), upload(scan_pdf_600dpi, "two.pdf")],
            "compress": "1",
            "preset": "print300",
        },
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["size_after"] < result["size_before"]


def test_images_endpoint_builds_one_pdf(client, png_rgba, jpeg_300dpi):
    payload = client.post(
        "/api/images",
        data={"files": [upload(png_rgba, "a.png"), upload(jpeg_300dpi, "b.jpg")]},
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["name"] == "images.pdf"
    assert client.get(result["download_url"]).get_data()[:5] == b"%PDF-"


def test_a_pdf_uploaded_as_an_image_is_rejected(client, vector_pdf_2pages):
    payload = client.post(
        "/api/images",
        data={"files": [upload(vector_pdf_2pages, "sneaky.png")]},
        content_type="multipart/form-data",
    ).get_json()
    assert payload["results"][0]["ok"] is False


def test_preview_returns_two_image_urls_that_resolve(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "screen150"},
        content_type="multipart/form-data",
    ).get_json()
    preview_url = payload["results"][0]["preview_url"]
    preview = client.get(preview_url).get_json()
    assert preview["dpi"] == 150
    for side in ("before", "after"):
        image_response = client.get(preview[side])
        assert image_response.status_code == 200
        assert image_response.get_data()[:8] == b"\x89PNG\r\n\x1a\n"


def test_zip_download_contains_every_successful_output(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/compress",
        data={
            "files": [upload(scan_pdf_600dpi, "one.pdf"), upload(scan_pdf_600dpi, "two.pdf")],
            "preset": "print200",
        },
        content_type="multipart/form-data",
    ).get_json()
    archive = client.get(payload["zip_url"]).get_data()
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        assert sorted(zipped.namelist()) == ["one.pdf", "two.pdf"]


def test_an_unknown_job_id_is_a_404(client):
    assert client.get("/jobs/deadbeef/files/0").status_code == 404
    assert client.get("/jobs/deadbeef/zip").status_code == 404


def test_an_out_of_range_output_index_is_a_404(client, scan_pdf_600dpi):
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    ).get_json()
    assert client.get("/jobs/{0}/files/99".format(payload["job_id"])).status_code == 404


# --- Fix round 1 -------------------------------------------------------
# Findings from the first review: an over-long client-supplied filename
# (upload name or output_name) must not crash the request with a raw
# OSError, a merge+compress row must advertise a preview_url that actually
# resolves, and every error body -- not just success bodies -- must be
# JSON with a `description` field.


def test_an_over_long_upload_filename_does_not_fail_the_batch(client, scan_pdf_600dpi):
    long_name = "d" * 300 + ".pdf"
    payload = client.post(
        "/api/compress",
        data={
            "files": [
                upload(scan_pdf_600dpi, long_name),
                upload(scan_pdf_600dpi, "normal.pdf"),
            ],
            "preset": "print300",
        },
        content_type="multipart/form-data",
    )
    assert payload.status_code == 200
    body = payload.get_json()
    assert len(body["results"]) == 2
    by_name = {result["name"]: result for result in body["results"]}
    assert by_name[long_name]["ok"] is True
    assert by_name["normal.pdf"]["ok"] is True
    assert client.get(by_name[long_name]["download_url"]).status_code == 200


def test_an_over_long_output_name_on_merge_yields_a_usable_download(
    client, vector_pdf_factory
):
    first = vector_pdf_factory(["ALPHA"])
    second = vector_pdf_factory(["BETA"])
    response = client.post(
        "/api/merge",
        data={
            "files": [upload(first, "a.pdf"), upload(second, "b.pdf")],
            "output_name": "m" * 300,
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    result = response.get_json()["results"][0]
    assert result["ok"] is True
    assert result["name"].endswith(".pdf")
    assert client.get(result["download_url"]).get_data()[:5] == b"%PDF-"


def test_an_over_long_output_name_on_images_yields_a_usable_download(client, png_rgba):
    response = client.post(
        "/api/images",
        data={"files": [upload(png_rgba, "a.png")], "output_name": "i" * 300},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    result = response.get_json()["results"][0]
    assert result["ok"] is True
    assert result["name"].endswith(".pdf")
    assert client.get(result["download_url"]).get_data()[:5] == b"%PDF-"


def test_merge_with_compress_advertises_a_preview_url_that_actually_resolves(
    client, scan_pdf_600dpi
):
    payload = client.post(
        "/api/merge",
        data={
            "files": [upload(scan_pdf_600dpi, "one.pdf"), upload(scan_pdf_600dpi, "two.pdf")],
            "compress": "1",
            "preset": "screen150",
        },
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert result["preview_url"] is not None
    preview = client.get(result["preview_url"])
    assert preview.status_code == 200
    assert preview.get_json()["dpi"] == 150


def test_error_bodies_are_json_with_a_description(client, scan_pdf_600dpi):
    unknown_preset = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "ludicrous"},
        content_type="multipart/form-data",
    )
    assert unknown_preset.status_code == 400
    assert unknown_preset.is_json
    assert "ludicrous" in unknown_preset.get_json()["description"]

    no_files = client.post(
        "/api/compress", data={"preset": "print300"}, content_type="multipart/form-data"
    )
    assert no_files.status_code == 400
    assert no_files.is_json
    assert no_files.get_json()["description"]


def test_existing_404s_still_return_404_after_the_json_error_handler(client, scan_pdf_600dpi):
    # Guards against the JSON error handler regressing the earlier 404
    # coverage: those tests only assert status codes, so this reconfirms
    # they still hold now that every abort() answers JSON.
    assert client.get("/jobs/deadbeef/files/0").status_code == 404
    assert client.get("/jobs/deadbeef/zip").status_code == 404
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    ).get_json()
    assert client.get("/jobs/{0}/files/99".format(payload["job_id"])).status_code == 404


@pytest.fixture
def corrupt_but_headered_pdf(tmp_path):
    """Carries a genuine ``%PDF-`` header (so upload validation accepts it)
    but is corrupt enough that poppler's ``pdfimages`` exits nonzero -- a
    valid header over a merely-garbage body is not corrupt enough for this,
    since Ghostscript recovers and emits a blank page (see
    tests/test_compress.py's own corrupt-pdf fixture note); a missing
    xref/trailer is what actually reaches ``compress_pdf``'s internal
    ``profile_pdf`` call and raises a ``ToolError``.
    """
    path = tmp_path / "trunc.pdf"
    path.write_bytes(
        b"%PDF-1.4\nthis is garbage, not a real pdf body, no xref, no trailer.\n%%EOF"
    )
    return path


def test_a_tool_error_does_not_leak_the_internal_on_disk_path(
    client, corrupt_but_headered_pdf
):
    payload = client.post(
        "/api/compress",
        data={"files": [upload(corrupt_but_headered_pdf, "trunc.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is False
    assert "trunc.pdf" in result["error"]
    # The on-disk name is position-prefixed ("000_trunc.pdf") and lives
    # under a job's own temp directory; neither should reach the client.
    assert "000_trunc.pdf" not in result["error"]
    assert "/inputs/" not in result["error"]
