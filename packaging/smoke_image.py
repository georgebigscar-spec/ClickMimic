"""Рисует «окно» с кнопками и терминалом для smoke-теста и замера скорости собранного exe."""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def font(size):
    for path in (r"C:\Windows\Fonts\arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


img = Image.new("RGB", (1440, 900), (240, 240, 240))
d = ImageDraw.Draw(img)
ui, mono = font(18), font(14)
d.rectangle((0, 0, 1440, 36), fill=(255, 255, 255))
d.text((15, 8), "Файл    Правка    Вид    Справка    File    Edit    Help", fill=(0, 0, 0), font=ui)
d.text((40, 120), "Сохранить изменения в документе?", fill=(0, 0, 0), font=font(24))
for x, label in ((420, "OK"), (600, "Отмена"), (780, "Cancel")):
    d.rounded_rectangle((x, 200, x + 150, 245), radius=6, fill=(225, 225, 225), outline=(120, 120, 120), width=2)
    d.text((x + 30, 210), label, fill=(0, 0, 0), font=ui)
# Плотный «терминал», как на реальном экране: много строк текста.
d.rectangle((40, 300, 1400, 860), fill=(12, 36, 86))
for i in range(30):
    line = f"{i:02d}  File \"C:\\Users\\user\\clickmimic\\cli.py\", line {i * 7}, in main  Загрузка модели {i}%"
    d.text((50, 305 + i * 18), line, fill=(230, 230, 230), font=mono)
img.save(sys.argv[1])
