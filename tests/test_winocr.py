import sys
from types import SimpleNamespace

import pytest


def word(x, w, text, h=16):
    return SimpleNamespace(text=text, bounding_rect=SimpleNamespace(x=x, y=10, width=w, height=h))


def test_split_far_apart_words():
    from clickmimic.detect.winocr import _split

    words = [word(10, 40, "Файл"), word(60, 50, "Правка"), word(400, 60, "Справка")]
    assert [[w.text for w in g] for g in _split(words)] == [["Файл", "Правка"], ["Справка"]]


@pytest.mark.skipif(sys.platform != "win32", reason="OCR Windows есть только в Windows")
def test_windows_ocr_reads_text():
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    from clickmimic.detect.winocr import WindowsTextReader, available_languages

    if not available_languages():
        pytest.skip("на машине не установлен ни один язык OCR")
    img = Image.new("RGB", (600, 120), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 40), "Cancel", fill="black", font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 28))
    d.text((400, 40), "Save", fill="black", font=ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 28))
    lines = WindowsTextReader().read(np.asarray(img))
    texts = {t for _, t, _ in lines}
    assert "Cancel" in texts and "Save" in texts, lines
    box = next(b for b, t, _ in lines if t == "Cancel")
    assert 10 <= box.x1 <= 30 and 30 <= box.y1 <= 55
