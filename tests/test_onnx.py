"""Пост-обработка YOLO и кэш OCR на подменённых моделях (без настоящих ONNX-файлов)."""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("cv2")

from clickmimic.detect.ocr import TextReader  # noqa: E402
from clickmimic.detect.yolo_onnx import IconDetector  # noqa: E402


def make_detector(raw):
    det = IconDetector.__new__(IconDetector)
    det.input_name = "images"
    det.imgsz, det.conf, det.iou = 640, 0.05, 0.1
    det.session = SimpleNamespace(run=lambda _out, feed: [raw(feed["images"])])
    return det


def test_yolo_boxes_are_mapped_back_to_image_coords():
    # Картинка 1280x640 -> масштаб 0.5, вход 640x320 без отступов.
    def raw(blob):
        assert blob.shape == (1, 3, 320, 640)
        # Два бокса (cx, cy, w, h, score): уверенный и почти пустой.
        return np.array([[[100.0, 300.0], [50.0, 50.0], [40.0, 10.0], [20.0, 10.0], [0.9, 0.01]]])

    out = make_detector(raw).detect(np.zeros((640, 1280, 3), np.uint8))
    assert len(out) == 1
    box, score = out[0]
    assert (box.x1, box.y1, box.x2, box.y2) == (160.0, 80.0, 240.0, 120.0)
    assert score == pytest.approx(0.9)


def test_yolo_nms_drops_overlapping_box():
    def raw(blob):
        return np.array([[[100.0, 102.0], [100.0, 100.0], [40.0, 40.0], [40.0, 40.0], [0.8, 0.7]]])

    out = make_detector(raw).detect(np.zeros((640, 640, 3), np.uint8))
    assert [round(s, 1) for _, s in out] == [0.8]


class FakeEngine:
    def __init__(self):
        self.rec_calls = []

    def text_det(self, img):
        quads = np.array([[[10, 10], [60, 10], [60, 30], [10, 30]], [[10, 50], [90, 50], [90, 70], [10, 70]]], float)
        return SimpleNamespace(boxes=quads)

    def text_rec(self, inp):
        self.rec_calls.append(len(inp.img))
        return SimpleNamespace(txts=tuple(f"t{int(c.mean())}" for c in inp.img), scores=[0.9] * len(inp.img))


def make_reader():
    r = TextReader.__new__(TextReader)
    r._rec_input = lambda img: SimpleNamespace(img=img)
    r.engine = FakeEngine()
    r.min_score, r.cache_size, r.last_stats = 0.5, 100, {}
    from collections import OrderedDict

    r.cache = OrderedDict()
    return r


def test_ocr_cache_skips_unchanged_lines():
    reader = make_reader()
    img = np.zeros((100, 100, 3), np.uint8)
    first = reader.read(img)
    assert len(first) == 2 and reader.engine.rec_calls == [2]

    reader.read(img)
    assert reader.engine.rec_calls == [2]  # всё из кэша
    assert reader.last_stats["lines_recognized"] == 0

    img[55:65, 20:80] = 200  # изменилась только вторая строка
    reader.read(img)
    assert reader.engine.rec_calls == [2, 1]
