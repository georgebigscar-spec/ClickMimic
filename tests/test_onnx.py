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


def test_yolo_does_not_upscale_small_windows():
    def raw(blob):
        assert blob.shape == (1, 3, 224, 320)  # 300x200 -> 320x213, добито до кратного 32
        return np.zeros((1, 5, 0), np.float32)

    assert make_detector(raw).detect(np.zeros((200, 300, 3), np.uint8)) == []


def make_parser(texts, boxes):
    from concurrent.futures import ThreadPoolExecutor

    from clickmimic.detect.omniparser import OmniParser, OmniParserConfig

    p = OmniParser.__new__(OmniParser)
    p.cfg = OmniParserConfig()
    calls = {"yolo": 0, "ocr": 0}

    def detect(rgb):
        calls["yolo"] += 1
        return boxes

    def read(rgb, upscale=True):
        calls["ocr"] += 1
        return texts

    p.icons = SimpleNamespace(detect=detect)
    p.ocr = SimpleNamespace(read=read, last_stats={"ocr_det": 0.0, "ocr_rec": 0.0, "lines": 1, "lines_recognized": 1})
    p._pool = ThreadPoolExecutor(max_workers=1)
    p._last = None
    return p, calls


def test_parser_skips_models_for_unchanged_frame_and_yolo_on_request():
    from PIL import Image

    from clickmimic.detect.omniparser import format_timings
    from clickmimic.elements import BBox

    texts = [(BBox(10, 10, 50, 30), "OK", 0.9)]
    boxes = [(BBox(5, 5, 60, 35), 0.8)]
    p, calls = make_parser(texts, boxes)
    img = Image.new("RGB", (100, 50), "white")

    full = p.parse(img)
    assert calls == {"yolo": 1, "ocr": 1}
    assert [(e.kind, e.content) for e in full] == [("icon", "OK")]

    again = p.parse(img)  # тот же кадр: модели не запускаются
    assert calls == {"yolo": 1, "ocr": 1}
    assert "кадр не изменился" in format_timings(p.last_timings)
    assert [(e.kind, e.content) for e in again] == [("icon", "OK")]
    again[0].bbox = again[0].bbox.offset(100, 100)  # изменения копии не портят кэш
    assert p.parse(img)[0].bbox == BBox(5, 5, 60, 35)

    other = Image.new("RGB", (100, 50), "black")
    text_only = p.parse(other, icons=False)
    assert calls == {"yolo": 1, "ocr": 2}
    assert [(e.kind, e.content) for e in text_only] == [("text", "OK")]
    assert "YOLO пропущен" in format_timings(p.last_timings)
    p.parse(other)  # кадр тот же, но теперь нужны области YOLO
    assert calls == {"yolo": 2, "ocr": 3}

    p.forget_frame()
    p.parse(other)
    assert calls == {"yolo": 3, "ocr": 4}
