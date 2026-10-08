"""YOLO-детектор OmniParser v2 (icon_detect), экспортированный в ONNX и запускаемый через onnxruntime."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..elements import BBox


class IconDetector:
    def __init__(self, model_path: Path, imgsz: int = 1280, conf: float = 0.05, iou: float = 0.1):
        import onnxruntime as ort

        self.session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou

    def _letterbox(self, rgb: np.ndarray) -> tuple[np.ndarray, float, tuple[int, int]]:
        h, w = rgb.shape[:2]
        scale = self.imgsz / max(h, w)
        nh, nw = round(h * scale), round(w * scale)
        resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
        # Добиваем до кратного 32 (модель экспортирована с динамическим размером входа).
        th, tw = -(-nh // 32) * 32, -(-nw // 32) * 32
        top, left = (th - nh) // 2, (tw - nw) // 2
        canvas = np.full((th, tw, 3), 114, dtype=np.uint8)
        canvas[top : top + nh, left : left + nw] = resized
        return canvas, scale, (left, top)

    def detect(self, rgb: np.ndarray) -> list[tuple[BBox, float]]:
        canvas, scale, (pad_x, pad_y) = self._letterbox(rgb)
        blob = canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
        out = self.session.run(None, {self.input_name: blob})[0][0]  # (4 + классы, N)
        boxes, scores = out[:4].T, out[4:].max(axis=0)
        keep = scores >= self.conf
        boxes, scores = boxes[keep], scores[keep]
        if not len(boxes):
            return []
        cx, cy, bw, bh = boxes.T
        x1 = (cx - bw / 2 - pad_x) / scale
        y1 = (cy - bh / 2 - pad_y) / scale
        w, h = bw / scale, bh / scale
        rects = np.stack([x1, y1, w, h], axis=1)
        idx = cv2.dnn.NMSBoxes(rects.tolist(), scores.tolist(), self.conf, self.iou)
        H, W = rgb.shape[:2]
        result = []
        for i in np.array(idx).flatten():
            x, y, ww, hh = rects[i]
            box = BBox(max(0.0, x), max(0.0, y), min(float(W), x + ww), min(float(H), y + hh))
            result.append((box, float(scores[i])))
        return result
