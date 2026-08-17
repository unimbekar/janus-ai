"""Turn uploaded knowledge files into UTF-8 text.

Chat attachments stay unparsed on purpose. Knowledge ingest is the path that
opens files, so the allow-list here is only types we can extract text from
without executing them. Scanned PDFs with no text layer are rejected rather than
silently storing empty chunks.
"""

from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path

from janus_core.errors import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_EXTRACTED_CHARS = 500_000
_DOCX_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>([^<]*)</w:t>")

# Extension → canonical mime. Browser declarations are often wrong (octet-stream).
EXTENSION_MIME: dict[str, str] = {
    ".txt": "text/plain",
    ".text": "text/plain",
    ".log": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ACCEPTED_LABELS = sorted({f"*{ext}" for ext in EXTENSION_MIME})


def infer_mime(*, filename: str, declared: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    from_name = EXTENSION_MIME.get(suffix)
    declared_type = (declared or "").split(";")[0].strip().lower()
    if declared_type in EXTENSION_MIME.values():
        if from_name and from_name != declared_type:
            raise ValidationError(
                "The file extension does not match its type.",
                param="file",
                details={"declared": declared_type, "extension": suffix},
            )
        return declared_type
    if from_name:
        return from_name
    raise ValidationError(
        "That file type cannot be ingested as knowledge yet.",
        param="file",
        details={"accepted": ACCEPTED_LABELS},
    )


def extract_text(*, filename: str, data: bytes, mime_type: str) -> str:
    if not data:
        raise ValidationError("The file is empty.", param="file")

    if mime_type == "application/pdf":
        text = _from_pdf(data)
    elif mime_type.endswith("wordprocessingml.document"):
        text = _from_docx(data)
    elif mime_type == "text/html":
        text = _from_html(_decode_utf8(data))
    else:
        text = _decode_utf8(data)

    cleaned = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not cleaned:
        raise ValidationError(
            "No extractable text was found in this file.",
            param="file",
            details={"filename": filename},
        )
    if len(cleaned) > MAX_EXTRACTED_CHARS:
        raise ValidationError(
            "The extracted text is larger than the ingest limit.",
            param="file",
            details={"limit_chars": MAX_EXTRACTED_CHARS, "chars": len(cleaned)},
        )
    return cleaned


def _decode_utf8(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _from_pdf(data: bytes) -> str:
    if not data.startswith(b"%PDF-"):
        raise ValidationError("The file contents do not look like a PDF.", param="file")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except (PdfReadError, OSError) as exc:
        raise ValidationError("This PDF could not be read.", param="file") from exc
    return "\n\n".join(pages)


def _from_docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
    except (KeyError, zipfile.BadZipFile, UnicodeError) as exc:
        raise ValidationError("This Word document could not be read.", param="file") from exc
    parts: list[str] = []
    for block in xml.split("</w:p>"):
        runs = _DOCX_TEXT.findall(block)
        if runs:
            parts.append("".join(runs))
    return "\n\n".join(parts)


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3", "h4"}:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._chunks.append(data)

    def text(self) -> str:
        return "".join(self._chunks)


def _from_html(markup: str) -> str:
    parser = _HtmlText()
    parser.feed(markup)
    parser.close()
    return parser.text()
