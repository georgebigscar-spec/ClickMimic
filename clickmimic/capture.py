"""Захват экрана через mss (работает с несколькими мониторами)."""
from __future__ import annotations

import sys

from PIL import Image

_dpi_set = False


def make_dpi_aware() -> None:
    """Без DPI-awareness Windows масштабирует координаты, и клики промахиваются на 125%/150%."""
    global _dpi_set
    if _dpi_set or sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()
    _dpi_set = True


def grab(monitor: int = 1, region: tuple[int, int, int, int] | None = None) -> tuple[Image.Image, tuple[int, int]]:
    """Возвращает (скриншот, смещение левого верхнего угла в экранных координатах).

    monitor: 0 = весь виртуальный экран, 1.. = конкретный монитор.
    region: (left, top, width, height) в экранных координатах, перекрывает monitor.
    """
    import mss

    make_dpi_aware()
    with mss.mss() as sct:
        if region:
            left, top, width, height = region
            box = {"left": left, "top": top, "width": width, "height": height}
        else:
            box = sct.monitors[monitor]
        shot = sct.grab(box)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img, (box["left"], box["top"])
