"""Распознавание экрана по схеме OmniParser v2: YOLO-детектор интерактивных элементов + OCR.

Повторяет пайплайн из microsoft/OmniParser (util/utils.py) в упрощённом виде, без подписей
иконок Florence-2. Обе модели работают через onnxruntime, без torch:
1. YOLO (icon_detect, экспорт в ONNX) находит интерактивные области.
2. RapidOCR (PP-OCRv5) находит и распознаёт текст.
3. Текст, лежащий внутри области, становится её содержимым; остальной текст — отдельные элементы.
   Области без текста остаются с пустым content: на них ссылаются по id или координатам.
"""
from __future__ import annotations

import time
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


class OmniParser:
    def __init__(self, config: OmniParserConfig | None = None):
        from .ocr import TextReader
        from .yolo_onnx import IconDetector

        self.cfg = config or OmniParserConfig()
        root = self.cfg.models_dir or models_dir()
        t = time.perf_counter()
        self.icons = IconDetector(
            require(root / ICON_DETECT), self.cfg.imgsz, self.cfg.box_threshold, self.cfg.iou_threshold
        )
        self.ocr = TextReader(require(root / OCR_DIR), self.cfg.ocr_min_score)
        self.load_time = time.perf_counter() - t
        self.last_timings: dict[str, float] = {}

    def parse(self, image: Image.Image) -> list[UIElement]:
        t0 = time.perf_counter()
        rgb = np.asarray(image.convert("RGB"))
        icons = self.icons.detect(rgb)
        t1 = time.perf_counter()
        texts = self.ocr.read(rgb)
        t2 = time.perf_counter()
        elements = merge(icons, texts)
        t3 = time.perf_counter()
        self.last_timings = {"yolo": t1 - t0, **self.ocr.last_stats, "merge": t3 - t2, "total": t3 - t0}
        return elements


def format_timings(t: dict[str, float]) -> str:
    return (
        f"YOLO {t['yolo'] * 1000:.0f} мс, OCR поиск {t['ocr_det'] * 1000:.0f} мс, "
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
