"""Reading text from page images: a vision model first, local OCR when none is available.

Vision models keep table layout far better than line-based OCR, which matters
for hospital bills. RapidOCR runs on CPU with no network, so a page is never lost.
"""

import asyncio
import logging
from functools import cached_property
from typing import TYPE_CHECKING, Literal

from app.errors import ModelUnavailableError
from app.llm.router import ModelRouter
from app.llm.types import Image, Message, Task

if TYPE_CHECKING:
    from rapidocr import RapidOCR

log = logging.getLogger(__name__)

_TRANSCRIBE = (
    "You transcribe scanned Indian hospital and insurance documents. Copy every piece of "
    "text exactly as printed: names, numbers, dates, amounts. Write each table row on one "
    "line with cells separated by ' | '. Do not summarise, translate, correct or explain. "
    "If the page is blank, reply with an empty string."
)

# Rows whose box centres are this close (as a share of box height) sit on one line.
_SAME_LINE = 0.6


class OcrEngine:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    @cached_property
    def _local(self) -> "RapidOCR":
        from rapidocr import RapidOCR

        return RapidOCR()

    async def read_image(
        self, image: Image, tags: dict[str, str]
    ) -> tuple[str, Literal["vision_model", "local_ocr"]]:
        if self._router.chain(Task.VISION):
            messages = [
                Message(role="system", content=_TRANSCRIBE),
                Message(role="user", content="Transcribe this page.", images=(image,)),
            ]
            try:
                completion = await self._router.complete(Task.VISION, messages, tags=tags)
            except ModelUnavailableError:
                log.warning("vision models unavailable, using local OCR", extra=tags)
            else:
                return completion.text, "vision_model"
        return await asyncio.to_thread(self._read_locally, image.data), "local_ocr"

    def _read_locally(self, data: bytes) -> str:
        from rapidocr.utils.output import RapidOCROutput

        result = self._local(data)
        if not isinstance(result, RapidOCROutput) or result.boxes is None or result.txts is None:
            return ""
        cells = []
        for box, text in zip(result.boxes, result.txts, strict=True):
            ys = [point[1] for point in box]
            xs = [point[0] for point in box]
            cells.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text)))
        cells.sort(key=lambda cell: (cell[0], cell[2]))

        lines: list[list[tuple[float, str]]] = []
        last_centre = last_height = None
        for centre, height, left, text in cells:
            if (
                last_centre is not None
                and last_height is not None
                and (abs(centre - last_centre) <= _SAME_LINE * max(height, last_height))
            ):
                lines[-1].append((left, text))
            else:
                lines.append([(left, text)])
                last_centre, last_height = centre, height
        return "\n".join(" | ".join(t for _, t in sorted(line)) for line in lines)
