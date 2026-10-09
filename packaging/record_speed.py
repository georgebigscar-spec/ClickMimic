"""Замер для записи: распознавание всего кадра против полосы вокруг клика (на настоящих моделях).

python packaging/record_speed.py build/models smoke.png
"""
import sys
import time
from pathlib import Path

from PIL import Image

from clickmimic.capture import Source
from clickmimic.detect.omniparser import OmniParser, OmniParserConfig
from clickmimic.recorder import Recorder, target_for_click

models, image_path = Path(sys.argv[1]), sys.argv[2]
parser = OmniParser(OmniParserConfig(models_dir=models))
frame = Image.open(image_path).convert("RGB")
rec = Recorder(Source(monitor=1), parser, lambda: (frame, (0, 0)))
x, y = 675, 222  # кнопка «Отмена» на smoke.png


def best(fn, n=3):
    times = []
    for _ in range(n):
        parser.forget_frame()
        t = time.perf_counter()
        out = fn()
        times.append(time.perf_counter() - t)
    return out, min(times) * 1000, max(times) * 1000


full, full_min, full_max = best(lambda: parser.parse(frame))
crop, crop_min, crop_max = best(lambda: rec.elements_near(frame, x, y))
print(f"весь кадр (YOLO + OCR): {full_min:.0f}-{full_max:.0f} мс")
print(f"полоса вокруг клика:   {crop_min:.0f}-{crop_max:.0f} мс")
spec = target_for_click(crop, x, y)
print("шаг:", spec)
rec.stop()
assert spec.get("text") == "Отмена", spec
