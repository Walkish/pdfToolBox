"""Tests for opening a PDF once, with read failures named the same way."""

import gc

import pytest
from pypdf import PdfWriter

from pdftools import reader
from pdftools.errors import ToolError


def test_every_page_is_returned(vector_pdf_factory):
    pages = reader.read_pages(vector_pdf_factory(["A", "B", "C"]))
    assert len(pages) == 3


def test_pages_come_back_in_document_order(vector_pdf_factory):
    pages = reader.read_pages(vector_pdf_factory(["FIRST", "SECOND"]))
    assert "FIRST" in pages[0].extract_text()
    assert "SECOND" in pages[1].extract_text()


def test_an_owner_locked_pdf_is_read_anyway(owner_locked_pdf):
    """Locked against editing but readable without a password: the common
    case for a document out of a scanner or a bank."""
    assert len(reader.read_pages(owner_locked_pdf)) == 1


def test_a_password_protected_pdf_is_refused_by_name(password_protected_pdf):
    with pytest.raises(ToolError) as excinfo:
        reader.read_pages(password_protected_pdf)
    assert password_protected_pdf.name in str(excinfo.value)
    assert "password protected" in str(excinfo.value)


def test_a_file_that_is_not_a_pdf_is_refused_by_name(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(ToolError) as excinfo:
        reader.read_pages(path)
    assert "broken.pdf" in str(excinfo.value)


def test_the_pages_outlive_the_reader(vector_pdf_factory):
    """The caller never sees the PdfReader, so the pages must keep it alive
    themselves; collected out from under them, reading one later fails."""
    pages = reader.read_pages(vector_pdf_factory(["KEPT"]))
    gc.collect()
    assert "KEPT" in pages[0].extract_text()


def test_an_aes_256_pdf_with_an_empty_user_password_is_read(vector_pdf_factory, tmp_path):
    # Acrobat's default for an owner-locked file. pypdf needs the
    # cryptography package for it; without that, the read fails outright.
    writer = PdfWriter(clone_from=str(vector_pdf_factory(["A", "B"])))
    writer.encrypt(user_password="", owner_password="owner", algorithm="AES-256")
    locked = tmp_path / "locked.pdf"
    with open(str(locked), "wb") as handle:
        writer.write(handle)
    assert len(reader.read_pages(locked)) == 2
