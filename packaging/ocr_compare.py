"""Сравнение RapidOCR и встроенного OCR Windows: скорость и найденный текст.

python packaging/ocr_compare.py build/models smoke.png [ещё картинки...]
"""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from clickmimic.detect.models import OCR_DIR
from clickmimic.detect.ocr import TextReader
from clickmimic.detect.winocr import WindowsTextReader, available_languages

models = Path(sys.argv[1])
print("Языки OCR Windows:", ", ".join(available_languages()) or "нет")
readers = {"RapidOCR": TextReader(models / OCR_DIR), "OCR Windows": WindowsTextReader()}
print("OCR Windows распознаёт на:", readers["OCR Windows"].language)

for path in sys.argv[2:]:
    rgb = np.asarray(Image.open(path).convert("RGB"))
    print(f"\n=== {path} {rgb.shape[1]}x{rgb.shape[0]}")
    for name, reader in readers.items():
        times = []
        for i in range(3):
            if hasattr(reader, "cache"):
                reader.cache.clear()  # честное сравнение: без кэша строк
            t = time.perf_counter()
            lines = reader.read(rgb)
            times.append((time.perf_counter() - t) * 1000)
        texts = [t for _, t, _ in lines]
        print(f"{name}: {min(times):.0f}-{max(times):.0f} мс, строк {len(lines)}")
        print("   ", " | ".join(texts[:14]))
