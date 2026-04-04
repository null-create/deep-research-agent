"""
Unit tests for the file handler MCP read and write tools.

Verifies that all supported file types are returned as plain, LLM-readable text
strings regardless of the underlying binary format, and that the write tool
produces files that can be round-tripped correctly.

Run from the mcp/file_handler/tools/ directory:
    python tests.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

# Allow running from any working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from file_reader import (
    FileChunk,
    FileReaderError,
    read_file_simple,
    stream_file_chunks,
)
from file_writer import stream_write_chunks, write_file


# ─── Fixture helpers ──────────────────────────────────────────────────────────


def _tmp_path(suffix: str) -> str:
    """Return a unique temp file path that does NOT yet exist."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    os.unlink(path)
    return path


def _create_docx(path: str, text: str) -> None:
    import docx

    doc = docx.Document()
    doc.add_paragraph(text)
    doc.save(path)


def _create_odt(path: str, text: str) -> None:
    from odf.opendocument import OpenDocumentText
    from odf.text import P

    doc = OpenDocumentText()
    p = P(text=text)
    doc.text.addElement(p)
    doc.save(path)


def _create_pdf(path: str, text: str) -> None:
    """Write a minimal PDF with a single page containing the given text."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)

    content_bytes = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    stream = DecodedStreamObject()
    stream.set_data(content_bytes)
    page[NameObject("/Contents")] = stream

    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject(
                {
                    NameObject("/F1"): font,
                }
            )
        }
    )

    with open(path, "wb") as f:
        writer.write(f)


# ─── Read tests ───────────────────────────────────────────────────────────────


def test_read_plain_text():
    """Plain .txt file returns the verbatim content as a string."""
    path = _tmp_path(".txt")
    try:
        Path(path).write_text("Hello plain text world.", encoding="utf-8")
        result = read_file_simple(path)
        assert isinstance(result, str), "Result must be a str"
        assert "Hello plain text world." in result
        print("✅ read plain text (.txt)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_markdown():
    """.md files are returned as plain text (no binary encoding)."""
    path = _tmp_path(".md")
    content = (
        "# Heading\n\nSome **markdown** content with a [link](http://example.com)."
    )
    try:
        Path(path).write_text(content, encoding="utf-8")
        result = read_file_simple(path)
        assert isinstance(result, str)
        assert "# Heading" in result
        assert "markdown" in result
        print("✅ read markdown (.md)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_json():
    """.json files are returned as a string that is valid JSON."""
    path = _tmp_path(".json")
    data = {"query": "research topic", "results": [1, 2, 3]}
    try:
        Path(path).write_text(json.dumps(data), encoding="utf-8")
        result = read_file_simple(path)
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed["query"] == "research topic"
        print("✅ read JSON (.json)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_csv():
    """.csv files are returned as plain comma-separated text."""
    path = _tmp_path(".csv")
    csv_content = "name,value\nalpha,1\nbeta,2\n"
    try:
        Path(path).write_text(csv_content, encoding="utf-8")
        result = read_file_simple(path)
        assert isinstance(result, str)
        assert "name,value" in result
        assert "alpha" in result
        print("✅ read CSV (.csv)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_docx_extracts_text():
    """DOCX files have their text extracted and returned as a plain string."""
    path = _tmp_path(".docx")
    expected = "Research findings from the DOCX document."
    try:
        _create_docx(path, expected)
        result = read_file_simple(path)
        assert isinstance(result, str), "DOCX content must be a str"
        assert len(result) > 0, "DOCX extraction should produce non-empty text"
        assert expected in result, f"Expected text not found in: {result!r}"
        assert "\x00" not in result, "Result must contain no null bytes"
        print("✅ read DOCX (text extraction)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_odt_extracts_text():
    """ODT files have their text extracted and returned as a plain string."""
    path = _tmp_path(".odt")
    expected = "Research findings from the ODT document."
    try:
        _create_odt(path, expected)
        result = read_file_simple(path)
        assert isinstance(result, str), "ODT content must be a str"
        assert len(result) > 0, "ODT extraction should produce non-empty text"
        assert expected in result, f"Expected text not found in: {result!r}"
        assert "\x00" not in result, "Result must contain no null bytes"
        print("✅ read ODT (text extraction)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_pdf_returns_string():
    """PDF files are returned as a plain text string, not raw binary bytes."""
    path = _tmp_path(".pdf")
    try:
        _create_pdf(path, "Test PDF Research Content")
        result = read_file_simple(path)
        assert isinstance(result, str), "PDF content must be a str"
        assert "\x00" not in result, "Result must contain no null bytes"
        print("✅ read PDF (returns str, no binary bytes)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_read_missing_file_raises():
    """Reading a non-existent path raises FileReaderError."""
    try:
        read_file_simple("/tmp/definitely_does_not_exist_research_xyz_123.txt")
        assert False, "Expected FileReaderError"
    except FileReaderError:
        pass
    print("✅ missing file raises FileReaderError")


def test_stream_chunks_reassemble_to_full_content():
    """stream_file_chunks yields chunks that concatenate to the original content."""
    path = _tmp_path(".txt")
    # Use content large enough to span several 64-byte chunks
    content = "Line of text.\n" * 50
    try:
        Path(path).write_text(content, encoding="utf-8")
        chunks = []
        for chunk in stream_file_chunks(path, chunk_size=64):
            assert isinstance(chunk, FileChunk)
            assert chunk.error is None, f"Unexpected stream error: {chunk.error}"
            if chunk.chunk:
                chunks.append(chunk.chunk)
            if chunk.eof:
                break
        assert "".join(chunks) == content
        print("✅ stream_file_chunks reassembles to full content")
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ─── Write tests ──────────────────────────────────────────────────────────────


def test_write_creates_file():
    """write mode (default) creates a new file with the given content."""
    path = _tmp_path(".txt")
    content = "Written by the file handler write tool."
    try:
        results = list(stream_write_chunks(path, content, create_dirs=False))
        assert len(results) == 1
        result = results[0]
        assert result.success is True, f"Expected success, got error: {result.error}"
        assert os.path.exists(path)
        assert Path(path).read_text("utf-8") == content
        print("✅ write creates file (write mode)")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_write_append_mode():
    """append mode adds content to an existing file without overwriting it."""
    path = _tmp_path(".txt")
    try:
        Path(path).write_text("First line.\n", encoding="utf-8")
        results = list(
            stream_write_chunks(
                path, "Second line.\n", mode="append", create_dirs=False
            )
        )
        assert results[-1].success is True, f"Append failed: {results[-1].error}"
        written = Path(path).read_text("utf-8")
        assert "First line." in written
        assert "Second line." in written
        print("✅ write append mode")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_write_create_mode_fails_if_exists():
    """create mode yields a failure result (not an exception) when the file exists."""
    path = _tmp_path(".txt")
    try:
        Path(path).write_text("existing content", encoding="utf-8")
        results = list(
            stream_write_chunks(path, "new content", mode="create", create_dirs=False)
        )
        assert any(
            not r.success for r in results
        ), "Expected a failure result for create mode on an existing file"
        # Original file must be unchanged
        assert Path(path).read_text("utf-8") == "existing content"
        print("✅ write create mode fails on existing file")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_write_overwrite_mode_replaces_content():
    """write mode replaces an existing file's content entirely."""
    path = _tmp_path(".txt")
    try:
        Path(path).write_text("old content", encoding="utf-8")
        results = list(
            stream_write_chunks(path, "new content", mode="write", create_dirs=False)
        )
        assert results[-1].success is True, f"Expected success: {results[-1].error}"
        assert Path(path).read_text("utf-8") == "new content"
        print("✅ write overwrite mode replaces content")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_round_trip_write_then_read():
    """Content written via stream_write_chunks is read back identically by read_file_simple."""
    path = _tmp_path(".txt")
    original = "Round-trip test: special chars: <>&\"'\nLine 2.\n"
    try:
        results = list(stream_write_chunks(path, original, create_dirs=False))
        assert results[-1].success is True
        assert read_file_simple(path) == original
        print("✅ round-trip write → read")
    finally:
        if os.path.exists(path):
            os.unlink(path)


def test_write_file_mcp_dict_invocation():
    """write_file() accepts the MCP dict invocation format and yields a success dict."""
    path = _tmp_path(".txt")
    content = "MCP tool invocation test."
    try:
        results = list(
            write_file(
                {
                    "file_name": path,
                    "content": content,
                    "encoding": "utf-8",
                    "mode": "write",
                    "create_dirs": True,
                    "max_file_size": 10 * 1024 * 1024,
                    "chunk_size": 8192,
                }
            )
        )
        assert any(
            r.get("success") for r in results
        ), f"Expected success dict: {results}"
        assert Path(path).read_text("utf-8") == content
        print("✅ write_file MCP dict invocation interface")
    finally:
        if os.path.exists(path):
            os.unlink(path)


# ─── Runner ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    errors = []

    tests = [
        test_read_plain_text,
        test_read_markdown,
        test_read_json,
        test_read_csv,
        test_read_docx_extracts_text,
        test_read_odt_extracts_text,
        test_read_pdf_returns_string,
        test_read_missing_file_raises,
        test_stream_chunks_reassemble_to_full_content,
        test_write_creates_file,
        test_write_append_mode,
        test_write_create_mode_fails_if_exists,
        test_write_overwrite_mode_replaces_content,
        test_round_trip_write_then_read,
        test_write_file_mcp_dict_invocation,
    ]

    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"❌ {t.__name__}: {e}")
            errors.append(t.__name__)

    print()
    if errors:
        print(f"❌ {len(errors)} test(s) failed: {errors}")
        sys.exit(1)
    else:
        print(f"✅ All {len(tests)} tests passed.")
        sys.exit(0)
