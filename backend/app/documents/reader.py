"""Turning an uploaded file into page text.

Text PDFs are read directly. Pages with almost no text layer (scans, phone
photos saved as PDF) and image uploads go through OCR.
"""

import hashlib
from dataclasses import dataclass
from typing import Any, Literal

import pymupdf

from app.config import AgentSettings, StorageSettings
from app.documents.ocr import OcrEngine
from app.errors import DocumentError
from app.llm.types import Image

MimeType = Literal["application/pdf", "image/png", "image/jpeg", "image/webp"]
TextSource = Literal["text_layer", "vision_model", "local_ocr"]

_SIGNATURES: tuple[tuple[bytes, MimeType], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)


@dataclass(frozen=True, slots=True)
class Page:
    number: int
    text: str
    source: TextSource


@dataclass(frozen=True, slots=True)
class ReadDocument:
    sha256: str
    mime_type: MimeType
    pages: list[Page]

    @property
    def text(self) -> str:
        return "\n\n".join(f"[page {p.number}]\n{p.text}" for p in self.pages)


def sniff_mime(data: bytes) -> MimeType:
    """Detect the real type from magic bytes; the client's Content-Type is not trusted."""
    for signature, mime in _SIGNATURES:
        if data.startswith(signature):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise DocumentError("only PDF, PNG, JPEG or WEBP files are accepted")


class DocumentReader:
    def __init__(self, ocr: OcrEngine, storage: StorageSettings, agents: AgentSettings) -> None:
        self._ocr = ocr
        self._storage = storage
        self._agents = agents

    def validate(self, data: bytes) -> MimeType:
        if not data:
            raise DocumentError("the file is empty")
        if len(data) > self._storage.max_upload_mb * 1024 * 1024:
            raise DocumentError(f"files above {self._storage.max_upload_mb} MB are not accepted")
        return sniff_mime(data)

    async def read(self, data: bytes, *, tags: dict[str, str] | None = None) -> ReadDocument:
        mime = self.validate(data)
        digest = hashlib.sha256(data).hexdigest()
        if mime == "application/pdf":
            pages = await self._read_pdf(data, tags or {})
        else:
            text, source = await self._ocr.read_image(Image(data=data, mime_type=mime), tags or {})
            pages = [Page(number=1, text=text, source=source)]
        if not any(page.text.strip() for page in pages):
            raise DocumentError("no readable text was found in the file")
        return ReadDocument(sha256=digest, mime_type=mime, pages=pages)

    async def _read_pdf(self, data: bytes, tags: dict[str, str]) -> list[Page]:
        try:
            # PyMuPDF ships only partial type hints.
            pdf: Any = pymupdf.open(stream=data, filetype="pdf")
        except (RuntimeError, ValueError) as exc:
            raise DocumentError("the PDF could not be opened") from exc
        with pdf:
            if pdf.needs_pass:
                raise DocumentError("password-protected PDFs are not supported")
            if pdf.page_count > self._storage.max_pdf_pages:
                limit = self._storage.max_pdf_pages
                raise DocumentError(f"PDFs above {limit} pages are not accepted")
            pages: list[Page] = []
            for index, page in enumerate(pdf, start=1):
                text = page.get_text("text").strip()
                if len(text) >= self._agents.ocr_min_text_chars:
                    pages.append(Page(number=index, text=text, source="text_layer"))
                    continue
                png = page.get_pixmap(dpi=self._agents.ocr_render_dpi).tobytes("png")
                scanned, source = await self._ocr.read_image(
                    Image(data=png, mime_type="image/png"), {**tags, "page": str(index)}
                )
                pages.append(Page(number=index, text=scanned, source=source))
            return pages
