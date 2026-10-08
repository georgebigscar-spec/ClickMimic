"""Обёртка над OmniParser v2: YOLO-детектор интерактивных элементов + OCR.

Повторяет пайплайн из microsoft/OmniParser (util/utils.py) в упрощённом виде и без подписей
иконок Florence-2 (icon_caption), чтобы не тянуть ~1 ГБ весов и transformers:
1. YOLO (icon_detect) находит интерактивные области.
2. EasyOCR находит текст.
3. Текст, лежащий внутри области, становится её содержимым; остальной текст — отдельные элементы.
   Области без текста остаются с пустым content: на них ссылаются по id или координатам.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from ..elements import BBox, UIElement
from .weights import ensure_weights


@dataclass
class OmniParserConfig:
    weights_dir: Path | None = None
    box_threshold: float = 0.05
    iou_threshold: float = 0.1
    imgsz: int = 1280
    ocr_languages: tuple[str, ...] = ("en", "ru")
    ocr_text_threshold: float = 0.8
    device: str | None = None  # None = cuda, если доступна


class OmniParser:
    def __init__(self, config: OmniParserConfig | None = None):
        import torch
        from ultralytics import YOLO

        self.cfg = config or OmniParserConfig()
        weights = ensure_weights(self.cfg.weights_dir) if self.cfg.weights_dir else ensure_weights()
        self.device = self.cfg.device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.yolo = YOLO(str(weights / "icon_detect" / "model.pt"))

        import easyocr

        self.ocr = easyocr.Reader(list(self.cfg.ocr_languages), gpu=self.device == "cuda")

    def _detect_icons(self, image: Image.Image) -> list[tuple[BBox, float]]:
        result = self.yolo.predict(
            image,
            conf=self.cfg.box_threshold,
            iou=self.cfg.iou_threshold,
            imgsz=self.cfg.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        return [(BBox(*map(float, b)), float(s)) for b, s in zip(boxes, scores)]

    def _detect_text(self, image: Image.Image) -> list[tuple[BBox, str, float]]:
        out = []
        for quad, text, conf in self.ocr.readtext(np.asarray(image), text_threshold=self.cfg.ocr_text_threshold):
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            out.append((BBox(min(xs), min(ys), max(xs), max(ys)), text.strip(), float(conf)))
        return [t for t in out if t[1]]

    def parse(self, image: Image.Image) -> list[UIElement]:
        image = image.convert("RGB")
        icons = self._detect_icons(image)
        texts = self._detect_text(image)
        return merge(icons, texts)


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
