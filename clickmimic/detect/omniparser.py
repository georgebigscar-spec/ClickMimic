"""Распознавание экрана по схеме OmniParser v2: YOLO-детектор интерактивных элементов + OCR.

Повторяет пайплайн из microsoft/OmniParser (util/utils.py) в упрощённом виде, без подписей
иконок Florence-2. Обе модели работают через onnxruntime, без torch:
1. YOLO (icon_detect, экспорт в ONNX) находит интерактивные области.
2. RapidOCR (PP-OCRv5) находит и распознаёт текст (параллельно с YOLO).
3. Текст, лежащий внутри области, становится её содержимым; остальной текст — отдельные элементы.
   Области без текста остаются с пустым content: на них ссылаются по id или координатам.
"""
from __future__ import annotations

import copy
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..elements import BBox, UIElement
from .models import ICON_DETECT, OCR_DIR, models_dir, require


@dataclass
class OmniParserConfig:
    models_dir: Path | None = None
    box_threshold: float = 0.05
    iou_threshold: float = 0.1
    imgsz: int = 1280  # меньше = быстрее, но мелкие иконки теряются
    ocr_min_score: float = 0.5
    device: str = "cpu"  # "cpu", "gpu" (DirectML) или "auto"
    parallel: bool = True  # YOLO и OCR одновременно, в двух потоках
    ocr: str = "rapid"  # "rapid" (RapidOCR) или "windows" (встроенный OCR Windows 10/11)


class OmniParser:
    def __init__(self, config: OmniParserConfig | None = None):
        from .models import providers
        from .ocr import TextReader
        from .yolo_onnx import IconDetector

        self.cfg = config or OmniParserConfig()
        root = self.cfg.models_dir or models_dir()
        t = time.perf_counter()
        prov = providers(self.cfg.device)
        self.icons = IconDetector(
            require(root / ICON_DETECT), self.cfg.imgsz, self.cfg.box_threshold, self.cfg.iou_threshold, prov
        )
        if self.cfg.ocr == "windows":
            from .winocr import WindowsTextReader

            self.ocr = WindowsTextReader()
        else:
            self.ocr = TextReader(require(root / OCR_DIR), self.cfg.ocr_min_score, gpu=prov[0] != "CPUExecutionProvider")
        self.device = self.icons.device
        self.load_time = time.perf_counter() - t
        self.last_timings: dict[str, float] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo")
        self._last: tuple[bytes, bool, list[UIElement]] | None = None

    def parse(self, image: Image.Image, icons: bool = True, upscale: bool = True) -> list[UIElement]:
        """icons=False пропускает YOLO: хватает, когда элемент ищется только по тексту.
        upscale=False ищет текст в исходном масштабе (для небольших фрагментов экрана)."""
        t0 = time.perf_counter()
        rgb = np.ascontiguousarray(np.asarray(image.convert("RGB")))
        key = hashlib.blake2b(f"{rgb.shape}{upscale}".encode() + rgb.tobytes(), digest_size=16).digest()
        if self._last and self._last[0] == key and (self._last[1] or not icons):
            # Экран не изменился с прошлого раза: модели не нужны.
            self.last_timings = {"same_frame": 1, "total": time.perf_counter() - t0}
            return [copy.copy(e) for e in self._last[2]]

        yolo_time = 0.0

        def run_yolo():
            nonlocal yolo_time
            t = time.perf_counter()
            boxes = self.icons.detect(rgb)
            yolo_time = time.perf_counter() - t
            return boxes

        if icons and self.cfg.parallel:
            future = self._pool.submit(run_yolo)
            texts = self.ocr.read(rgb, upscale=upscale)
            boxes = future.result()
        else:
            boxes = run_yolo() if icons else []
            texts = self.ocr.read(rgb, upscale=upscale)
        t2 = time.perf_counter()
        elements = merge(boxes, texts)
        t3 = time.perf_counter()
        self.last_timings = {"yolo": yolo_time, **self.ocr.last_stats, "icons": int(icons),
                             "merge": t3 - t2, "total": t3 - t0}
        self._last = (key, icons, [copy.copy(e) for e in elements])
        return elements

    def forget_frame(self) -> None:
        """Следующий parse выполнит модели, даже если кадр не изменился (кэш OCR остаётся)."""
        self._last = None


def format_timings(t: dict[str, float]) -> str:
    if t.get("same_frame"):
        return f"кадр не изменился, модели пропущены, всего {t['total'] * 1000:.0f} мс"
    yolo = f"YOLO {t['yolo'] * 1000:.0f} мс" if t.get("icons", 1) else "YOLO пропущен"
    if t.get("ocr_engine") == "windows":
        return f"{yolo}, OCR Windows {t['ocr_det'] * 1000:.0f} мс ({int(t['lines'])} строк), всего {t['total'] * 1000:.0f} мс"
    return (
        f"{yolo}, OCR поиск {t['ocr_det'] * 1000:.0f} мс, "
        f"OCR распознавание {t['ocr_rec'] * 1000:.0f} мс ({int(t['lines_recognized'])} из {int(t['lines'])} строк, "
        f"остальные из кэша), всего {t['total'] * 1000:.0f} мс"
    )


def merge(icons, texts) -> list[UIElement]:
    """Объединяет YOLO-боксы и OCR. Вынесено отдельно ради тестов без модели."""
    # Убираем почти полностью совпадающие иконки (оставляем более уверенную).
    icons = sorted(icons, key=lambda t: -t[1])
    kept: list[tuple[BBox, float]] = []
    for box, score in icons:
        if all(box.iou(k) < 0.7 for k, _ in kept):
            kept.append((box, score))

    used_text: set[int] = set()
    icon_text: list[str] = []
    for box, _ in kept:
        inside = [
            (i, t) for i, t in enumerate(texts)
            if t[0].area and t[0].intersection(box) / t[0].area > 0.8
        ]
        inside.sort(key=lambda it: (it[1][0].y1, it[1][0].x1))
        used_text.update(i for i, _ in inside)
        icon_text.append(" ".join(t[1] for _, t in inside))

    elements: list[UIElement] = []
    for (box, score), txt in zip(kept, icon_text):
        elements.append(UIElement(0, "icon", box, txt, True, score))
    for i, (box, txt, conf) in enumerate(texts):
        if i not in used_text:
            elements.append(UIElement(0, "text", box, txt, False, conf))

    # Порядок чтения: сверху вниз, слева направо — стабильные id между запусками.
    elements.sort(key=lambda e: (round(e.bbox.y1 / 10), e.bbox.x1))
    for n, e in enumerate(elements):
        e.id = n
    return elements
