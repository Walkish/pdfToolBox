"""Route-level tests via the Flask test client."""

import io
import zipfile

import pytest
from PIL import Image
from pypdf import PdfReader

import app as app_module
from pdftools import jobs, pagesize


@pytest.fixture
def job_store(tmp_path):
    return jobs.JobStore(base_dir=tmp_path / "jobs")


@pytest.fixture
def client(job_store):
    application = app_module.create_app(job_store=job_store)
    application.config["TESTING"] = True
    with application.test_client() as test_client:
        yield test_client


def upload(path, name):
    with open(str(path), "rb") as handle:
        return (io.BytesIO(handle.read()), name)


def test_resolve_port_returns_default_when_unset(monkeypatch):
    monkeypatch.delenv("PDFTOOLBOX_PORT", raising=False)
    assert app_module.resolve_port() == app_module.PORT


def test_resolve_port_honours_a_valid_override(monkeypatch):
    monkeypatch.setenv("PDFTOOLBOX_PORT", "9999")
    assert app_module.resolve_port() == 9999


def test_resolve_port_rejects_a_non_integer_value(monkeypatch):
    monkeypatch.setenv("PDFTOOLBOX_PORT", "not-a-port")
    with pytest.raises(ValueError) as excinfo:
        app_module.resolve_port()
    assert "PDFTOOLBOX_PORT" in str(excinfo.value)


def test_index_serves_every_tab(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for label in ("Compress", "Merge", "Images", "Split"):
        assert label in body


def test_index_wires_up_the_split_tab(client):
    """The split tab owns its own script and grid; served without them the
    tab is a drop zone that does nothing."""
    body = client.get("/").get_data(as_text=True)
    assert 'id="split-pages"' in body
    assert "/static/split.js" in body


def test_the_split_script_is_served(client):
    assert client.get("/static/split.js").status_code == 200


def test_presets_endpoint_lists_the_ladder_and_the_default(client):
    payload = client.get("/api/presets").get_json()
    assert payload["default"] == "print300"
    ids = [preset["id"] for preset in payload["presets"]]
    assert ids == ["print600", "print300", "lossless"]
    by_id = {preset["id"]: preset for preset in payload["presets"]}
    # Every remaining level targets 300 dpi or better, so all are print-safe.
    assert all(preset["print_safe"] for preset in payload["presets"])
    assert by_id["print600"]["label"] == "Print 600 dpi"
    assert payload["print_floor_dpi"] == 200


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
    assert result["below_print_floor"] is False
    assert result["download_url"].startswith("/jobs/")
    downloaded = client.get(result["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.get_data()[:5] == b"%PDF-"


def test_compress_below_the_print_floor_reports_the_warning(client, scan_pdf_150dpi):
    """No preset can push a page below the floor any more, so the case that
    still matters is a source that arrived below it."""
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_150dpi, "scan.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert any("200 dpi" in warning for warning in result["warnings"])
    # The machine-readable form of the same measurement, so the UI can badge
    # the print risk instead of hoping the user reads the prose.
    assert result["below_print_floor"] is True


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


def test_two_uploads_sharing_a_filename_produce_two_distinct_results(client, scan_pdf_600dpi, scan_pdf_150dpi):
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
    assert client.get(first["download_url"]).get_data() != client.get(second["download_url"]).get_data()


def test_an_unknown_preset_is_a_400(client, scan_pdf_600dpi):
    response = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "ludicrous"},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_a_request_with_no_files_is_a_400(client):
    response = client.post("/api/compress", data={"preset": "print300"}, content_type="multipart/form-data")
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
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "print300"},
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
            "preset": "print600",
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


def test_an_over_long_output_name_on_merge_yields_a_usable_download(client, vector_pdf_factory):
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


def test_merge_with_compress_advertises_a_preview_url_that_actually_resolves(client, scan_pdf_600dpi):
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

    no_files = client.post("/api/compress", data={"preset": "print300"}, content_type="multipart/form-data")
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
    path.write_bytes(b"%PDF-1.4\nthis is garbage, not a real pdf body, no xref, no trailer.\n%%EOF")
    return path


def test_a_tool_error_does_not_leak_the_internal_on_disk_path(client, corrupt_but_headered_pdf):
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


def test_a_failed_preview_uses_the_standard_envelope_and_leaks_no_paths(client, tmp_path, scan_pdf_600dpi):
    """Measured before the fix, this route answered
    ``500 {"error": "pdfimages exited with status 1 on
    <tmp>/jobs/<id>/inputs/000_scan.pdf"}`` -- the only route with no
    redaction and its own private error shape. The internal path, the
    position prefix, and the odd envelope all had to go.
    """
    payload = client.post(
        "/api/compress",
        data={"files": [upload(scan_pdf_600dpi, "scan.pdf")], "preset": "print300"},
        content_type="multipart/form-data",
    ).get_json()
    preview_url = payload["results"][0]["preview_url"]

    # Break the retained source so rendering the "before" side cannot work.
    source = tmp_path / "jobs" / payload["job_id"] / "inputs" / "000_scan.pdf"
    assert source.exists()
    source.write_bytes(b"%PDF-1.4\nno xref, no trailer, nothing renderable\n%%EOF")

    response = client.get(preview_url)
    assert response.status_code == 500
    body = response.get_json()
    # The same {error, description} envelope every other route returns.
    assert body["error"] == "Internal Server Error"
    assert "scan.pdf" in body["description"]
    for field in (body["error"], body["description"]):
        assert "000_scan.pdf" not in field
        assert "/inputs/" not in field
        assert str(tmp_path) not in field


def test_an_unknown_job_preview_says_why_not_just_not_found(client):
    """The envelope carries the reason in `description`; `error` is only the
    exception's name, which is why the UI must read the former."""
    response = client.get("/api/preview/deadbeef/0")
    assert response.status_code == 404
    assert response.get_json()["description"] == "Unknown job"


def test_a_wrong_method_still_advertises_the_methods_it_allows(client):
    """The JSON error handler builds its own response, which drops Werkzeug's
    error headers. `Allow` is protocol information, not decoration: measured
    before the fix, GET /api/compress answered 405 with no Allow header at
    all. Content-Type must not be copied along with it -- it describes the
    HTML body being replaced."""
    response = client.get("/api/compress")
    assert response.status_code == 405
    assert "POST" in response.headers["Allow"]
    assert response.headers["Content-Type"].startswith("application/json")
    assert response.get_json()["description"]


def test_an_image_bomb_fails_its_own_row_and_leaves_the_batch_alone(client, png_past_pillows_own_ceiling, png_rgba):
    """Measured before the fix: a 511 KB PNG declaring 484 megapixels made the
    whole request answer 500 {"error":"Internal Server Error"} -- Pillow's
    DecompressionBombError was raised inside Image.open and escaped both
    except tuples -- and the valid image uploaded next to it was never
    processed. One bad file must not fail the batch.
    """
    response = client.post(
        "/api/images",
        data={
            "files": [
                upload(png_past_pillows_own_ceiling, "bomb.png"),
                upload(png_rgba, "good.png"),
            ]
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    payload = response.get_json()
    by_name = {result["name"]: result for result in payload["results"]}

    assert by_name["bomb.png"]["ok"] is False
    assert "bomb.png" in by_name["bomb.png"]["error"]
    # The surviving image still becomes a PDF, under the default output name.
    built = by_name["images.pdf"]
    assert built["ok"] is True
    assert client.get(built["download_url"]).get_data()[:5] == b"%PDF-"


def test_index_wires_up_the_assets_and_the_print_warning_copy(client):
    body = client.get("/").get_data(as_text=True)
    assert "/static/app.js" in body
    assert "/static/style.css" in body
    # All three levels must be offered by id, and the default pre-selected.
    for preset_id in ("print600", "print300", "lossless"):
        assert 'value="{0}"'.format(preset_id) in body
    assert 'value="print300" checked' in body
    # No level is unsuitable for print now, so that copy must be gone rather
    # than left behind next to a preset that no longer exists.
    assert "not for print" not in body.lower()


def test_static_assets_are_served(client):
    for path in ("/static/app.js", "/static/style.css"):
        assert client.get(path).status_code == 200


def merged_page_sizes(client, payload):
    downloaded = client.get(payload["results"][0]["download_url"]).get_data()
    return [pagesize.visible_size(page) for page in PdfReader(io.BytesIO(downloaded)).pages]


def test_merge_normalizes_page_sizes_when_asked(client, vector_pdf_factory):
    # The A4 input carries two pages and the photo one, so A4 wins the count
    # outright. With one page each the batch would tie, and a tie goes to the
    # larger area -- correct behaviour, but the opposite of the point here.
    a4 = vector_pdf_factory(["ALPHA", "GAMMA"], page_size=(595, 842))
    photo = vector_pdf_factory(["BETA"], page_size=(1200, 1600))
    payload = client.post(
        "/api/merge",
        data={"files": [upload(a4, "a4.pdf"), upload(photo, "photo.pdf")], "normalize": "1"},
        content_type="multipart/form-data",
    ).get_json()
    assert payload["results"][0]["ok"] is True
    assert merged_page_sizes(client, payload) == [(595.0, 842.0)] * 3


def test_merge_leaves_page_sizes_alone_without_the_field(client, vector_pdf_factory):
    a4 = vector_pdf_factory(["ALPHA", "GAMMA"], page_size=(595, 842))
    photo = vector_pdf_factory(["BETA"], page_size=(1200, 1600))
    payload = client.post(
        "/api/merge",
        data={"files": [upload(a4, "a4.pdf"), upload(photo, "photo.pdf")]},
        content_type="multipart/form-data",
    ).get_json()
    assert merged_page_sizes(client, payload) == [(595.0, 842.0), (595.0, 842.0), (1200.0, 1600.0)]


def test_images_endpoint_accepts_a_heic(client, heic_image):
    payload = client.post(
        "/api/images",
        data={"files": [upload(heic_image, "photo.heic")]},
        content_type="multipart/form-data",
    ).get_json()
    result = payload["results"][0]
    assert result["ok"] is True
    assert client.get(result["download_url"]).get_data()[:5] == b"%PDF-"


def _page_sizes(client, result):
    downloaded = client.get(result["download_url"]).get_data()
    return [
        (float(page.mediabox.width), float(page.mediabox.height)) for page in PdfReader(io.BytesIO(downloaded)).pages
    ]


def test_thumbnail_returns_a_png(client, jpeg_300dpi):
    response = client.post(
        "/api/thumbnail",
        data={"file": upload(jpeg_300dpi, "photo.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.get_data()[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_works_for_heic(client, heic_image):
    response = client.post(
        "/api/thumbnail",
        data={"file": upload(heic_image, "photo.heic")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.get_data()[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_rejects_a_pdf_renamed_to_png(client, vector_pdf_2pages):
    response = client.post(
        "/api/thumbnail",
        data={"file": upload(vector_pdf_2pages, "sneaky.png")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_images_endpoint_applies_a_rotation(client, jpeg_300dpi):
    turned = _page_sizes(
        client,
        client.post(
            "/api/images",
            data={"files": [upload(jpeg_300dpi, "a.jpg")], "rotations": ["90"]},
            content_type="multipart/form-data",
        ).get_json()["results"][0],
    )
    upright = _page_sizes(
        client,
        client.post(
            "/api/images",
            data={"files": [upload(jpeg_300dpi, "a.jpg")]},
            content_type="multipart/form-data",
        ).get_json()["results"][0],
    )
    assert turned[0] == pytest.approx((upright[0][1], upright[0][0]), abs=0.5)


def test_a_wrong_length_rotation_list_is_a_400(client, jpeg_300dpi):
    response = client.post(
        "/api/images",
        data={"files": [upload(jpeg_300dpi, "a.jpg")], "rotations": ["90", "180"]},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_an_illegal_rotation_angle_is_a_400(client, jpeg_300dpi):
    response = client.post(
        "/api/images",
        data={"files": [upload(jpeg_300dpi, "a.jpg")], "rotations": ["45"]},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_rotations_stay_with_their_images_when_one_upload_fails(client, jpeg_300dpi, png_rgba):
    # Three uploads, the middle one unreadable, three different angles. If the
    # route matched rotations against the surviving files by their position in
    # the filtered list, the third file would receive the second file's angle
    # and come out unrotated.
    payload = client.post(
        "/api/images",
        data={
            "files": [
                upload(jpeg_300dpi, "first.jpg"),
                (io.BytesIO(b"not an image at all"), "broken.png"),
                upload(png_rgba, "third.png"),
            ],
            "rotations": ["0", "180", "90"],
        },
        content_type="multipart/form-data",
    ).get_json()
    by_name = {result["name"]: result for result in payload["results"]}
    assert by_name["broken.png"]["ok"] is False
    built = [result for result in payload["results"] if result.get("ok")][0]
    sizes = _page_sizes(client, built)
    with Image.open(str(png_rgba)) as third:
        third_ratio = third.width / float(third.height)
    # The surviving second page is the third upload, which asked for 90.
    assert sizes[1][0] / sizes[1][1] == pytest.approx(1 / third_ratio, abs=0.05)


def test_thumbnail_renders_a_pdf(client, vector_pdf_2pages):
    response = client.post(
        "/api/thumbnail",
        data={"file": upload(vector_pdf_2pages, "doc.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.get_data()[:8] == b"\x89PNG\r\n\x1a\n"


def test_thumbnail_rejects_a_png_renamed_to_pdf(client, png_rgba):
    response = client.post(
        "/api/thumbnail",
        data={"file": upload(png_rgba, "disguised.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def open_split(client, path, name="doc.pdf"):
    """Start a split session and return its payload."""
    response = client.post("/api/split", data={"files": upload(path, name)}, content_type="multipart/form-data")
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def pdf_pages(data):
    return PdfReader(io.BytesIO(data)).pages


def test_opening_a_document_reports_its_page_count(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B", "C"]), "report.pdf")
    assert payload["page_count"] == 3
    assert payload["name"] == "report.pdf"
    assert payload["job_id"]


def test_opening_refuses_a_png_renamed_to_pdf(client, png_rgba):
    response = client.post(
        "/api/split", data={"files": upload(png_rgba, "sneaky.pdf")}, content_type="multipart/form-data"
    )
    assert response.status_code == 400


def test_opening_refuses_an_empty_request(client):
    assert client.post("/api/split", data={}, content_type="multipart/form-data").status_code == 400


def test_opening_refuses_a_second_document(client, vector_pdf_factory):
    """One document at a time: a silently ignored second upload would leave
    the user looking at pages they did not ask for."""
    first = vector_pdf_factory(["A"])
    second = vector_pdf_factory(["B"])
    response = client.post(
        "/api/split",
        data={"files": [upload(first, "one.pdf"), upload(second, "two.pdf")]},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_a_page_preview_is_a_png(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    response = client.get("/api/split/{0}/pages/2.png".format(payload["job_id"]))
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    with Image.open(io.BytesIO(response.get_data())) as image:
        assert image.format == "PNG"


def test_a_page_preview_shows_the_page_that_was_asked_for(client, box_pdf_factory, tmp_path, vector_pdf_factory):
    from pdftools import merge as merge_module

    document = merge_module.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((842, 595))], tmp_path / "mixed.pdf"
    )
    payload = open_split(client, document)
    response = client.get("/api/split/{0}/pages/2.png".format(payload["job_id"]))
    with Image.open(io.BytesIO(response.get_data())) as image:
        assert image.width > image.height


def test_a_page_preview_is_rendered_once_and_then_cached(client, job_store, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    url = "/api/split/{0}/pages/1.png".format(payload["job_id"])
    client.get(url)
    cached = list((job_store.get(payload["job_id"]).previews).glob("pages/*.png"))
    assert len(cached) == 1
    stamped = cached[0].stat().st_mtime_ns
    client.get(url)
    assert cached[0].stat().st_mtime_ns == stamped


def test_a_preview_of_a_page_past_the_end_is_not_found(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    assert client.get("/api/split/{0}/pages/3.png".format(payload["job_id"])).status_code == 404


def test_page_numbers_start_at_one(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    assert client.get("/api/split/{0}/pages/0.pdf".format(payload["job_id"])).status_code == 404


def test_an_unknown_split_job_is_not_found(client):
    assert client.get("/api/split/nosuchjob/pages/1.png").status_code == 404


def test_one_page_downloads_on_its_own(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["ONE", "TWO", "THREE"]))
    response = client.get("/api/split/{0}/pages/2.pdf".format(payload["job_id"]))
    assert response.status_code == 200
    pages = pdf_pages(response.get_data())
    assert len(pages) == 1
    assert "TWO" in pages[0].extract_text()


def test_a_downloaded_page_says_which_page_it_was(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]), "report.pdf")
    response = client.get("/api/split/{0}/pages/2.pdf".format(payload["job_id"]))
    assert "report-page-2.pdf" in response.headers["Content-Disposition"]


def test_a_downloaded_page_can_be_turned(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    response = client.get("/api/split/{0}/pages/1.pdf?rotate=90".format(payload["job_id"]))
    assert pdf_pages(response.get_data())[0].rotation == 90


def test_an_angle_that_is_not_a_quarter_turn_is_refused(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A"]))
    assert client.get("/api/split/{0}/pages/1.pdf?rotate=45".format(payload["job_id"])).status_code == 400


def test_building_keeps_the_chosen_pages_in_the_chosen_order(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["ALPHA", "BETA", "GAMMA"]))
    built = client.post(
        "/api/split/{0}/build".format(payload["job_id"]),
        data={"order": ["3", "1"], "rotations": ["0", "0"]},
    ).get_json()
    assert built["results"][0]["ok"] is True
    downloaded = client.get(built["results"][0]["download_url"]).get_data()
    pages = pdf_pages(downloaded)
    assert len(pages) == 2
    assert "GAMMA" in pages[0].extract_text()
    assert "ALPHA" in pages[1].extract_text()


def test_building_turns_the_pages_it_was_told_to(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    built = client.post(
        "/api/split/{0}/build".format(payload["job_id"]),
        data={"order": ["1", "2"], "rotations": ["0", "270"]},
    ).get_json()
    pages = pdf_pages(client.get(built["results"][0]["download_url"]).get_data())
    assert [page.rotation for page in pages] == [0, 270]


def test_building_can_match_the_page_sizes(client, box_pdf_factory, tmp_path):
    from pdftools import merge as merge_module

    document = merge_module.merge_pdfs(
        [box_pdf_factory((612, 792)), box_pdf_factory((612, 792)), box_pdf_factory((1224, 1584))],
        tmp_path / "mixed.pdf",
    )
    payload = open_split(client, document)
    built = client.post(
        "/api/split/{0}/build".format(payload["job_id"]),
        data={"order": ["1", "2", "3"], "normalize": "1"},
    ).get_json()
    pages = pdf_pages(client.get(built["results"][0]["download_url"]).get_data())
    assert [pagesize.visible_size(page) for page in pages] == [(612.0, 792.0)] * 3


def test_building_nothing_is_refused(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A"]))
    response = client.post("/api/split/{0}/build".format(payload["job_id"]), data={})
    assert response.status_code == 400


def test_building_a_page_past_the_end_is_refused(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    response = client.post("/api/split/{0}/build".format(payload["job_id"]), data={"order": ["3"]})
    assert response.status_code == 400


def test_the_built_pages_are_also_offered_one_file_each(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["ALPHA", "BETA", "GAMMA"]), "report.pdf")
    built = client.post(
        "/api/split/{0}/build".format(payload["job_id"]),
        data={"order": ["3", "1"], "rotations": ["0", "90"]},
    ).get_json()
    archive = client.get(built["zip_url"])
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.get_data())) as zipped:
        names = sorted(zipped.namelist())
        assert names == ["report-page-1.pdf", "report-page-3.pdf"]
        first = pdf_pages(zipped.read("report-page-3.pdf"))
        assert len(first) == 1
        assert "GAMMA" in first[0].extract_text()
        assert pdf_pages(zipped.read("report-page-1.pdf"))[0].rotation == 90


def test_the_page_zip_is_not_there_before_anything_is_built(client, vector_pdf_factory):
    payload = open_split(client, vector_pdf_factory(["A"]))
    assert client.get("/api/split/{0}/pages.zip".format(payload["job_id"])).status_code == 404


def test_looking_at_pages_keeps_the_session_from_expiring(client, job_store, vector_pdf_factory):
    """A document is edited over minutes, not in one request: the sweep must
    not take it away mid-edit."""
    import os
    import time

    job_store.ttl_seconds = 60
    payload = open_split(client, vector_pdf_factory(["A", "B"]))
    job = job_store.get(payload["job_id"])
    old = time.time() - 3600
    os.utime(str(job.root), (old, old))
    client.get("/api/split/{0}/pages/1.png".format(payload["job_id"]))
    assert job_store.cleanup_expired() == 0
    assert job.root.exists()


def test_the_page_zip_is_named_like_the_pages_inside_it(client, vector_pdf_factory):
    """The members go through normalize_output_name; the archive holding them
    must not be the one name that escapes it."""
    payload = open_split(client, vector_pdf_factory(["A", "B"]), "Holiday Scan.pdf")
    client.post(
        "/api/split/{0}/build".format(payload["job_id"]),
        data={"order": ["1", "2"]},
    )
    response = client.get("/api/split/{0}/pages.zip".format(payload["job_id"]))
    assert "Holiday_Scan-pages.zip" in response.headers["Content-Disposition"]
