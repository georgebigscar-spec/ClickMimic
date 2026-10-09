"""Распознавание текста встроенным OCR Windows 10/11 (Windows.Media.Ocr).

Работает в системе, без своих моделей, и обычно быстрее RapidOCR на первом проходе. Хуже читает
мелкий и бледный текст и не даёт уверенности по словам. Русский язык нужно установить:
    Add-WindowsCapability -Online -Name "Language.OCR~~~ru-RU~0.0.1.0"
"""
from __future__ import annotations

import asyncio
import logging
import time

import numpy as np

from ..elements import BBox

log = logging.getLogger("clickmimic")

GAP = 1.5  # разрыв между словами (в высотах строки), после которого строка делится на две


def available_languages() -> list[str]:
    from winrt.windows.media.ocr import OcrEngine

    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]


class WindowsTextReader:
    """Тот же интерфейс, что у ocr.TextReader: read(rgb) -> [(BBox, текст, уверенность)]."""

    def __init__(self, languages: tuple[str, ...] = ("ru", "en")):
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        self.engine = None
        for tag in languages:
            if OcrEngine.is_language_supported(Language(tag)):
                self.engine = OcrEngine.try_create_from_language(Language(tag))
                break
        if self.engine is None:
            self.engine = OcrEngine.try_create_from_user_profile_languages()
        if self.engine is None:
            raise RuntimeError("OCR Windows недоступен: не установлен ни один язык распознавания")
        self.language = self.engine.recognizer_language.language_tag
        if not self.language.lower().startswith("ru"):
            log.warning("OCR Windows: русский не установлен, распознаю на %s. Установка: "
                        'Add-WindowsCapability -Online -Name "Language.OCR~~~ru-RU~0.0.1.0"', self.language)
        self.max_side = int(OcrEngine.max_image_dimension)
        self.last_stats: dict[str, float] = {}

    def read(self, rgb: np.ndarray, upscale: bool = True) -> list[tuple[BBox, str, float]]:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        t0 = time.perf_counter()
        h, w = rgb.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        if scale < 1.0:
            from PIL import Image

            rgb = np.asarray(Image.fromarray(rgb).resize((int(w * scale), int(h * scale))))
            h, w = rgb.shape[:2]
        bgra = np.empty((h, w, 4), np.uint8)
        bgra[..., :3] = rgb[..., ::-1]
        bgra[..., 3] = 255
        writer = DataWriter()
        writer.write_bytes(bgra.tobytes())
        # перегрузка с BitmapAlphaMode в pywinrt не проецируется; без неё альфа и так premultiplied
        bitmap = SoftwareBitmap.create_copy_from_buffer(writer.detach_buffer(), BitmapPixelFormat.BGRA8, w, h)
        result = asyncio.run(self._recognize(bitmap))
        out = []
        for line in result.lines:
            for words in _split(list(line.words)):
                rects = [wd.bounding_rect for wd in words]
                x1 = min(r.x for r in rects) / scale
                y1 = min(r.y for r in rects) / scale
                x2 = max(r.x + r.width for r in rects) / scale
                y2 = max(r.y + r.height for r in rects) / scale
                text = " ".join(wd.text for wd in words).strip()
                if text:
                    out.append((BBox(int(x1), int(y1), int(np.ceil(x2)), int(np.ceil(y2))), text, 1.0))
        elapsed = time.perf_counter() - t0
        self.last_stats = {"ocr_det": elapsed, "ocr_rec": 0.0, "lines": len(out), "lines_recognized": len(out),
                           "ocr_engine": "windows"}
        return out

    async def _recognize(self, bitmap):
        return await self.engine.recognize_async(bitmap)


def _split(words: list) -> list[list]:
    """Windows объединяет в строку слова на одной линии, даже далеко друг от друга (меню, колонки)."""
    groups: list[list] = []
    for wd in words:
        r = wd.bounding_rect
        if groups:
            prev = groups[-1][-1].bounding_rect
            if r.x - (prev.x + prev.width) <= GAP * max(r.height, prev.height, 1):
                groups[-1].append(wd)
                continue
        groups.append([wd])
    return groups
