from __future__ import annotations

from typing import Protocol

from PIL import Image

from ..elements import UIElement


class Detector(Protocol):
    def parse(self, image: Image.Image) -> list[UIElement]:
        """Найти элементы на изображении; координаты относительно изображения."""
        ...
