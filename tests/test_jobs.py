"""Tests for job directories and output registration."""

import os
import shutil
import time
import zipfile

import pytest

from pdftools import jobs


def test_a_new_job_has_isolated_directories(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    first = store.create()
    second = store.create()
    assert first.id != second.id
    assert first.root != second.root
    for directory in (first.inputs, first.outputs, first.previews):
        assert directory.is_dir()


def test_outputs_are_addressed_by_integer_index(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    target = job.outputs / "a.pdf"
    target.write_bytes(b"%PDF-1.4\n")
    index = job.add_output(target, "report.pdf", {"size_after": 9})
    assert index == 0
    entry = job.output(0)
    assert entry["display_name"] == "report.pdf"
    assert entry["path"] == target
    assert entry["meta"]["size_after"] == 9


def test_an_unknown_output_index_raises(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    with pytest.raises(IndexError):
        job.output(3)


def test_zip_contains_every_output_under_its_display_name(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    for name in ("one.pdf", "two.pdf"):
        path = job.outputs / name
        path.write_bytes(b"%PDF-1.4\n" + name.encode())
        job.add_output(path, name)
    archive = job.zip_outputs()
    with zipfile.ZipFile(str(archive)) as zipped:
        assert sorted(zipped.namelist()) == ["one.pdf", "two.pdf"]


def test_duplicate_display_names_stay_distinct_in_the_zip(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    # Distinct byte content per entry: a de-dup implementation that aliased
    # both archive members to the same underlying file would still produce
    # two distinct *names*, so the payloads must be checked individually too.
    payloads = [b"%PDF-1.4\nFIRST PAYLOAD", b"%PDF-1.4\nSECOND PAYLOAD"]
    for index, payload in enumerate(payloads):
        path = job.outputs / "out{0}.pdf".format(index)
        path.write_bytes(payload)
        job.add_output(path, "same.pdf")
    archive = job.zip_outputs()
    with zipfile.ZipFile(str(archive)) as zipped:
        names = zipped.namelist()
        assert len(set(names)) == 2
        assert {zipped.read(name) for name in names} == set(payloads)


def test_zip_outputs_sanitizes_a_path_traversal_display_name(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    target = job.outputs / "a.pdf"
    target.write_bytes(b"%PDF-1.4\nEVIL PAYLOAD")
    job.add_output(target, "../../../../tmp/evil_escape.pdf")
    archive = job.zip_outputs()
    with zipfile.ZipFile(str(archive)) as zipped:
        names = zipped.namelist()
        assert len(names) == 1
        name = names[0]
        assert ".." not in name
        assert "/" not in name
        assert "\\" not in name
        assert zipped.read(name) == b"%PDF-1.4\nEVIL PAYLOAD"


def test_zip_outputs_keeps_distinct_payloads_when_traversal_names_collapse(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    first = job.outputs / "first.pdf"
    first.write_bytes(b"%PDF-1.4\nFIRST")
    second = job.outputs / "second.pdf"
    second.write_bytes(b"%PDF-1.4\nSECOND")
    # Differ only in directory prefix, so sanitizing to a bare filename
    # collapses both to "shared.pdf" -- the de-dup logic must still keep
    # them as two distinct archive members with their own payloads.
    job.add_output(first, "../../a/shared.pdf")
    job.add_output(second, "../../b/shared.pdf")
    archive = job.zip_outputs()
    with zipfile.ZipFile(str(archive)) as zipped:
        names = zipped.namelist()
        assert len(names) == 2
        assert len(set(names)) == 2
        for name in names:
            assert ".." not in name
            assert "/" not in name
        assert {zipped.read(name) for name in names} == {
            b"%PDF-1.4\nFIRST",
            b"%PDF-1.4\nSECOND",
        }


def test_get_returns_the_same_job_and_raises_for_unknown_ids(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path)
    job = store.create()
    assert store.get(job.id) is job
    with pytest.raises(KeyError):
        store.get("not-a-job")


def test_cleanup_removes_jobs_past_the_ttl_and_keeps_fresh_ones(tmp_path):
    store = jobs.JobStore(base_dir=tmp_path, ttl_seconds=60)
    stale = store.create()
    fresh = store.create()
    old = time.time() - 3600
    os.utime(str(stale.root), (old, old))
    removed = store.cleanup_expired()
    assert removed == 1
    assert not stale.root.exists()
    assert fresh.root.exists()
    with pytest.raises(KeyError):
        store.get(stale.id)


def test_cleanup_sweeps_aged_orphan_directories_but_keeps_fresh_ones(tmp_path):
    """Directories left behind by a previous process run, never registered
    with this JobStore instance, are swept once past the TTL -- and only
    once past it."""
    store = jobs.JobStore(base_dir=tmp_path, ttl_seconds=60)

    aged_orphan = tmp_path / "aged-orphan-from-a-previous-run"
    aged_orphan.mkdir()
    old = time.time() - 3600
    os.utime(str(aged_orphan), (old, old))

    fresh_orphan = tmp_path / "fresh-orphan"
    fresh_orphan.mkdir()

    removed = store.cleanup_expired()

    assert removed == 1
    assert not aged_orphan.exists()
    assert fresh_orphan.exists()


def test_cleanup_survives_its_base_directory_being_swept_away(tmp_path):
    """macOS sweeps $TMPDIR, so the base directory can vanish under a
    long-running process. Unguarded, the orphan sweep's iterdir() then raised
    FileNotFoundError and every later request 500'd from inside cleanup."""
    store = jobs.JobStore(base_dir=tmp_path / "jobs")
    shutil.rmtree(str(store.base_dir))

    assert store.cleanup_expired() == 0
    assert store.base_dir.is_dir()
    # And the store is still usable afterwards.
    job = store.create()
    assert job.inputs.is_dir()


def test_touching_a_job_saves_it_from_the_sweep(tmp_path):
    """A split session outlives the request that made it: the document stays
    on disk while the user works through its pages. Without a way to say "this
    one is still in use", an hour of editing ends in a 404 on the job's own
    directory."""
    store = jobs.JobStore(base_dir=tmp_path, ttl_seconds=60)
    job = store.create()
    old = time.time() - 3600
    os.utime(str(job.root), (old, old))
    job.touch()
    assert store.cleanup_expired() == 0
    assert job.root.exists()
    assert store.get(job.id) is job


def test_a_new_job_carries_an_empty_note(tmp_path):
    """Somewhere for the route that owns a job to remember what it is working
    on between requests, the way each output already carries its own meta."""
    store = jobs.JobStore(base_dir=tmp_path, ttl_seconds=60)
    assert store.create().meta == {}


def test_zip_files_packs_each_file_under_the_name_it_was_given(tmp_path):
    first = tmp_path / "a.pdf"
    first.write_bytes(b"first")
    second = tmp_path / "b.pdf"
    second.write_bytes(b"second")
    archive = jobs.zip_files(tmp_path / "out.zip", [("one.pdf", first), ("two.pdf", second)])
    with zipfile.ZipFile(str(archive)) as zipped:
        assert sorted(zipped.namelist()) == ["one.pdf", "two.pdf"]
        assert zipped.read("two.pdf") == b"second"


def test_zip_files_keeps_repeated_names_apart(tmp_path):
    first = tmp_path / "a.pdf"
    first.write_bytes(b"first")
    second = tmp_path / "b.pdf"
    second.write_bytes(b"second")
    archive = jobs.zip_files(tmp_path / "out.zip", [("same.pdf", first), ("same.pdf", second)])
    with zipfile.ZipFile(str(archive)) as zipped:
        assert sorted(zipped.namelist()) == ["same.pdf", "same_2.pdf"]


def test_zip_files_refuses_a_name_that_climbs_out_of_the_archive(tmp_path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"payload")
    archive = jobs.zip_files(tmp_path / "out.zip", [("../../etc/passwd", source)])
    with zipfile.ZipFile(str(archive)) as zipped:
        assert zipped.namelist() == ["passwd"]
