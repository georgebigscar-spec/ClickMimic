"""OCR на RapidOCR (модели PP-OCRv5 в ONNX): поиск строк + распознавание с кэшем.

Распознавание каждой строки — самая дорогая часть. Между соседними снимками экрана почти весь
текст тот же, поэтому результат распознавания кэшируется по пикселям вырезанной строки:
на повторных кадрах распознаются только изменившиеся строки.
"""
from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from ..elements import BBox

# Модели: детектор PP-OCRv5 mobile + распознаватель eslav (русский, украинский, белорусский и английский).
OCR_PARAMS = {
    "Det.ocr_version": "PP-OCRv5",
    "Det.lang_type": "ch",
    "Det.model_type": "mobile",
    "Rec.ocr_version": "PP-OCRv5",
    "Rec.lang_type": "eslav",
    "Rec.model_type": "mobile",
    "Global.use_cls": False,
    "Global.log_level": "error",
}


def build_engine(model_root: Path, gpu: bool = False):
    from rapidocr import LangDet, LangRec, ModelType, OCRVersion, RapidOCR

    params = dict(OCR_PARAMS)
    params["Det.ocr_version"] = OCRVersion(params["Det.ocr_version"])
    params["Rec.ocr_version"] = OCRVersion(params["Rec.ocr_version"])
    params["Det.lang_type"] = LangDet(params["Det.lang_type"])
    params["Rec.lang_type"] = LangRec(params["Rec.lang_type"])
    params["Det.model_type"] = ModelType(params["Det.model_type"])
    params["Rec.model_type"] = ModelType(params["Rec.model_type"])
    params["Global.model_root_dir"] = str(model_root)
    if gpu:
        params["EngineConfig.onnxruntime.use_dml"] = True
    engine = RapidOCR(params=params)
    # С rapidocr 3.10 модели загружаются лениво, при первом полном вызове движка, а text_det и
    # text_rec до этого равны None. Мы зовём их напрямую, поэтому загружаем модели сразу
    # (заодно prepare_models скачивает их на этапе сборки, а не при первом запуске).
    for loader in ("_load_det_model", "_load_rec_model"):
        if hasattr(engine, loader):
            getattr(engine, loader)()
    return engine


class TextReader:
    def __init__(self, model_root: Path, min_score: float = 0.5, cache_size: int = 5000, gpu: bool = False):
        from rapidocr.ch_ppocr_rec import TextRecInput

        self._rec_input = TextRecInput
        self.engine = build_engine(model_root, gpu)
        self._lock = threading.Lock()
        self.min_score = min_score
        self.cache: OrderedDict[bytes, tuple[str, float]] = OrderedDict()
        self.cache_size = cache_size
        self.last_stats: dict[str, float] = {}

    def read(self, rgb: np.ndarray, upscale: bool = True) -> list[tuple[BBox, str, float]]:
        """upscale=False: поиск строк в исходном масштабе. По умолчанию RapidOCR растягивает картинку,
        пока меньшая сторона не станет 736 px: для небольшого фрагмента экрана это лишняя работа."""
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        t0 = time.perf_counter()
        with self._lock:
            det_model = self.engine.text_det
            saved = det_model.limit_type, det_model.limit_side_len
            if not upscale:
                det_model.limit_type, det_model.limit_side_len = "max", 4096
            try:
                det = det_model(bgr)
            finally:
                det_model.limit_type, det_model.limit_side_len = saved
        t1 = time.perf_counter()
        quads = det.boxes if det.boxes is not None else []

        boxes, crops, keys = [], [], []
        H, W = bgr.shape[:2]
        for quad in quads:
            xs, ys = quad[:, 0], quad[:, 1]
            x1, y1 = max(0, int(xs.min())), max(0, int(ys.min()))
            x2, y2 = min(W, int(np.ceil(xs.max()))), min(H, int(np.ceil(ys.max())))
            if x2 - x1 < 2 or y2 - y1 < 2:
                continue
            crop = bgr[y1:y2, x1:x2]
            boxes.append(BBox(x1, y1, x2, y2))
            crops.append(crop)
            keys.append(hashlib.blake2b(f"{y2 - y1}x{x2 - x1}".encode() + crop.tobytes(), digest_size=16).digest())

        missing = [i for i, k in enumerate(keys) if k not in self.cache]
        if missing:
            rec = self.engine.text_rec(self._rec_input(img=[crops[i] for i in missing]))
            for i, txt, score in zip(missing, rec.txts or (), rec.scores):
                self._remember(keys[i], (txt.strip(), float(score)))
        t2 = time.perf_counter()

        out = []
        for box, key in zip(boxes, keys):
            if key not in self.cache:
                continue
            self.cache.move_to_end(key)
            txt, score = self.cache[key]
            if txt and score >= self.min_score:
                out.append((box, txt, score))
        self.last_stats = {
            "ocr_det": t1 - t0,
            "ocr_rec": t2 - t1,
            "lines": len(boxes),
            "lines_recognized": len(missing),
        }
        return out

    def _remember(self, key: bytes, value: tuple[str, float]) -> None:
        self.cache[key] = value
        if len(self.cache) > self.cache_size:
            self.cache.popitem(last=False)
