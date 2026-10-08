from __future__ import annotations

from PIL import Image, ImageDraw

from .elements import UIElement

COLORS = {"icon": (230, 60, 60), "text": (40, 120, 230)}


def draw(image: Image.Image, elements: list[UIElement]) -> Image.Image:
    """Рисует рамки и id элементов — удобно для отладки сценариев."""
    out = image.convert("RGB").copy()
    d = ImageDraw.Draw(out)
    for e in elements:
        color = COLORS.get(e.kind, (0, 160, 0))
        b = e.bbox
        d.rectangle((b.x1, b.y1, b.x2, b.y2), outline=color, width=2)
        label = str(e.id)
        tw = d.textlength(label) + 4
        d.rectangle((b.x1, b.y1 - 12, b.x1 + tw, b.y1), fill=color)
        d.text((b.x1 + 2, b.y1 - 12), label, fill=(255, 255, 255))
    return out
