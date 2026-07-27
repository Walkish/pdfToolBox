"""Tests for job directories and output registration."""
import os
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
    for index in range(2):
        path = job.outputs / "out{0}.pdf".format(index)
        path.write_bytes(b"%PDF-1.4\n")
        job.add_output(path, "same.pdf")
    archive = job.zip_outputs()
    with zipfile.ZipFile(str(archive)) as zipped:
        assert len(set(zipped.namelist())) == 2


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
