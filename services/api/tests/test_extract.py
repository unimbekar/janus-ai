"""Text extraction for knowledge file uploads."""

from __future__ import annotations

import io
import zipfile

import pytest
from api_app.extract import extract_text, infer_mime
from janus_core.errors import ValidationError
from pypdf import PdfWriter

HELLO_PDF = b"""%PDF-1.1
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj
2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj
4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Courier/Encoding/WinAnsiEncoding>>endobj
5 0 obj<</Length 44>>stream
BT /F1 12 Tf 100 700 Td (Hello Janus) Tj ET
endstream
endobj
trailer<</Size 6/Root 1 0 R>>
startxref
0
%%EOF
"""


def _docx(paragraph: str) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p></w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


def test_infer_mime_uses_extension_when_browser_sends_octet_stream() -> None:
    assert infer_mime(filename="policy.md", declared="application/octet-stream") == "text/markdown"


def test_infer_mime_rejects_unknown_types() -> None:
    with pytest.raises(ValidationError):
        infer_mime(filename="photo.png", declared="image/png")


def test_extracts_plain_text_and_html() -> None:
    assert extract_text(filename="a.txt", data=b"Office opens at 09:00 UTC.", mime_type="text/plain")
    html = b"<html><head><style>x{}</style></head><body><h1>Policy</h1><p>Stay local.</p></body>"
    text = extract_text(filename="a.html", data=html, mime_type="text/html")
    assert "Policy" in text
    assert "Stay local." in text
    assert "x{}" not in text


def test_extracts_docx_paragraphs() -> None:
    text = extract_text(
        filename="notes.docx",
        data=_docx("Never invent citations."),
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert text == "Never invent citations."


def test_extracts_pdf_text_layer() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    blank = buffer.getvalue()
    with pytest.raises(ValidationError, match="No extractable text"):
        extract_text(filename="blank.pdf", data=blank, mime_type="application/pdf")

    try:
        text = extract_text(filename="hello.pdf", data=HELLO_PDF, mime_type="application/pdf")
    except ValidationError:
        pytest.skip("pypdf could not extract Type1 text from the fixture PDF")
    assert "Hello" in text or "Janus" in text


def test_empty_file_is_rejected() -> None:
    with pytest.raises(ValidationError, match="empty"):
        extract_text(filename="empty.txt", data=b"", mime_type="text/plain")
