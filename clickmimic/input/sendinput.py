"""Ввод через WinAPI SendInput: события идут в системную очередь как от реальной мыши/клавиатуры."""
from __future__ import annotations

import ctypes
import random
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_EXTENDEDKEY = 0x0002, 0x0004, 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "shift": 0x10, "ctrl": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "space": 0x20, "pageup": 0x21, "pagedown": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "win": 0x5B, "apps": 0x5D,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
VK |= {"control": VK["ctrl"], "return": VK["enter"], "escape": VK["esc"], "del": VK["delete"]}
EXTENDED = {"insert", "delete", "del", "home", "end", "pageup", "pagedown",
            "left", "up", "right", "down", "win", "apps"}


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
        raise ctypes.WinError(ctypes.get_last_error())


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


def _mouse(flags: int, data: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(0, 0, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, 0)
    return i


def _vk_for(key: str) -> tuple[int, int]:
    k = key.lower()
    if k in VK:
        return VK[k], KEYEVENTF_EXTENDEDKEY if k in EXTENDED else 0
    if len(k) == 1:
        res = user32.VkKeyScanW(ord(k))
        if res == -1:
            raise ValueError(f"Неизвестная клавиша: {key}")
        return res & 0xFF, 0
    raise ValueError(f"Неизвестная клавиша: {key}")


BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class SendInputBackend:
    def position(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y

    def move(self, x: int, y: int, duration: float = 0.0) -> None:
        if duration > 0:
            # Плавное движение с ease-in-out, похожее на руку человека.
            sx, sy = self.position()
            steps = max(2, int(duration * 100))
            for n in range(1, steps):
                t = n / steps
                e = t * t * (3 - 2 * t)
                user32.SetCursorPos(int(sx + (x - sx) * e), int(sy + (y - sy) * e))
                time.sleep(duration / steps)
        user32.SetCursorPos(int(x), int(y))

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        down, up = BUTTONS[button]
        self.move(x, y)
        for _ in range(clicks):
            _send(_mouse(down))
            time.sleep(random.uniform(0.03, 0.07))
            _send(_mouse(up))
            time.sleep(random.uniform(0.05, 0.1))

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        _send(_mouse(MOUSEEVENTF_WHEEL, amount * WHEEL_DELTA))

    def type_text(self, text: str, interval: float = 0.0) -> None:
        # KEYEVENTF_UNICODE не зависит от раскладки: кириллица печатается при английской раскладке.
        for ch in text:
            if ch == "\n":
                self.hotkey("enter")
            else:
                for unit in _utf16_units(ch):
                    _send(_key(scan=unit, flags=KEYEVENTF_UNICODE),
                          _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
            if interval:
                time.sleep(interval * random.uniform(0.6, 1.4))

    def hotkey(self, *keys: str) -> None:
        codes = [_vk_for(k) for k in keys]
        _send(*[_key(vk, flags=f) for vk, f in codes])
        time.sleep(0.03)
        _send(*[_key(vk, flags=f | KEYEVENTF_KEYUP) for vk, f in reversed(codes)])


def _utf16_units(ch: str) -> list[int]:
    data = ch.encode("utf-16-le")
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]
