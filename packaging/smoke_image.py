"""Рисует простое «окно» с кнопками для smoke-теста собранного exe."""
import sys

from PIL import Image, ImageDraw, ImageFont

img = Image.new("RGB", (800, 500), (240, 240, 240))
d = ImageDraw.Draw(img)
font = ImageFont.load_default(size=28)
d.rectangle((0, 0, 800, 40), fill=(255, 255, 255))
d.text((15, 5), "File   Edit   View   Help", fill=(0, 0, 0), font=font)
for x, label in ((420, "OK"), (600, "Cancel")):
    d.rounded_rectangle((x, 400, x + 150, 450), radius=6, fill=(225, 225, 225), outline=(120, 120, 120), width=2)
    d.text((x + 30, 408), label, fill=(0, 0, 0), font=font)
d.text((40, 150), "Save changes to document?", fill=(0, 0, 0), font=font)
img.save(sys.argv[1])
