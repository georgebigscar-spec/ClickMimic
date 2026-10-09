"""Захват экрана через mss (работает с несколькими мониторами)."""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass

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


class WindowNotFound(RuntimeError):
    pass


@dataclass
class Source:
    """Что снимать: монитор (0 = все мониторы) или окно приложения."""

    monitor: int = 1
    window: str = ""  # часть заголовка окна; пусто = снимать монитор
    process: str = ""  # имя exe для уточнения, например "notepad.exe"
    hwnd: int | None = None  # конкретное окно, если оно уже выбрано (иначе ищется по заголовку)

    def describe(self) -> str:
        if self.window:
            return f"окно «{self.window}»" + (f" ({self.process})" if self.process else "")
        return "все мониторы" if self.monitor == 0 else f"монитор {self.monitor}"


def resolve_window(src: Source) -> int:
    from . import windows

    if src.hwnd and windows.exists(src.hwnd):
        return src.hwnd
    w = windows.find_window(src.window, src.process or None)
    if w is None:
        raise WindowNotFound(f"Не найдено {src.describe()}")
    src.hwnd = w.hwnd
    return w.hwnd


def grab_source(src: Source, activate: bool = False) -> tuple[Image.Image, tuple[int, int]]:
    """Снимок монитора или окна и смещение его левого верхнего угла в экранных координатах.

    activate=True выводит окно на передний план и снимает его с экрана: так снимок совпадает с тем,
    куда попадут клики. Без активации окно снимается через PrintWindow, даже если его перекрывают
    другие окна (если приложение это не поддерживает, снимается область экрана).
    """
    if not src.window:
        return grab(src.monitor)
    from . import windows

    make_dpi_aware()
    hwnd = resolve_window(src)
    if activate:
        windows.activate(hwnd)
        time.sleep(0.15)  # окну нужно время, чтобы перерисоваться поверх остальных
    left, top, width, height = windows.rect(hwnd)
    if not activate:
        img = windows.print_window(hwnd)
        if img is not None:
            return img, (left, top)
    if windows.is_minimized(hwnd):
        raise WindowNotFound(f"{src.describe()} свёрнуто")
    return grab(region=(left, top, width, height))
