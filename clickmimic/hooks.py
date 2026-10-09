"""Глобальные хуки мыши и клавиатуры Windows (WH_MOUSE_LL, WH_KEYBOARD_LL) для записи действий.

Хуки работают в отдельном потоке с собственным циклом сообщений. Обработчик должен возвращаться
быстро (иначе Windows снимет хук), поэтому он только передаёт событие дальше. Нажатия, которые
программа сама отправляет через SendInput, пропускаются по метке в dwExtraInfo. Флаг injected для
этого не годится: через удалённый рабочий стол, виртуальную машину или программы для мыши
Windows помечает так и настоящий ввод пользователя.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from typing import Callable

from .input.sendinput import OWN_INPUT_MARK

log = logging.getLogger("clickmimic")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
WM_KEYDOWN, WM_SYSKEYDOWN = 0x0100, 0x0104
WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN, WM_MOUSEWHEEL = 0x0201, 0x0204, 0x0207, 0x020A
LLMHF_INJECTED, LLKHF_INJECTED = 0x01, 0x10

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetKeyState.restype = ctypes.c_short
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.ToUnicodeEx.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ubyte), wintypes.LPWSTR,
                               ctypes.c_int, wintypes.UINT, wintypes.HKL]
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

# Имена клавиш как в сценариях (см. input/sendinput.py).
KEY_NAMES = {
    0x08: "backspace", 0x09: "tab", 0x0D: "enter", 0x13: "pause", 0x1B: "esc", 0x20: "space",
    0x21: "pageup", 0x22: "pagedown", 0x23: "end", 0x24: "home", 0x25: "left", 0x26: "up", 0x27: "right",
    0x28: "down", 0x2C: "printscreen", 0x2D: "insert", 0x2E: "delete", 0x5D: "apps",
    **{0x70 + i: f"f{i + 1}" for i in range(24)},
    **{0x30 + i: str(i) for i in range(10)},
    **{0x41 + i: chr(ord("a") + i) for i in range(26)},
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\",
    0xDD: "]", 0xDE: "'",
}
MODIFIERS = {0x10, 0x11, 0x12, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C, 0x14, 0x90, 0x91}


def _down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _modifiers() -> set[str]:
    mods = set()
    if _down(0x11):
        mods.add("ctrl")
    if _down(0x12):
        mods.add("alt")
    if _down(0x10):
        mods.add("shift")
    if _down(0x5B) or _down(0x5C):
        mods.add("win")
    if {"ctrl", "alt"} <= mods:  # AltGr в европейских раскладках = Ctrl+Alt: это ввод символа
        mods -= {"ctrl", "alt"}
        mods.add("altgr")
    return mods


def _char(vk: int, scan: int, mods: set[str]) -> str | None:
    """Символ, который напечатает клавиша в раскладке активного окна."""
    state = (ctypes.c_ubyte * 256)()
    if "shift" in mods:
        state[0x10] = 0x80
    if "altgr" in mods:
        state[0x11] = state[0x12] = 0x80
    if user32.GetKeyState(0x14) & 1:
        state[0x14] = 0x01
    thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    layout = user32.GetKeyboardLayout(thread)
    buf = ctypes.create_unicode_buffer(8)
    # Флаг 4: не менять состояние клавиатуры (иначе ломаются «мёртвые» клавиши в приложении).
    n = user32.ToUnicodeEx(vk, scan, state, buf, len(buf), 4, layout)
    if n == 1 and buf.value and buf.value.isprintable():
        return buf.value
    return None


def foreground_pid() -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
    return pid.value


class InputHooks:
    """on_mouse(button, x, y), on_wheel(x, y, delta), on_key(name, char, mods)."""

    def __init__(self, on_mouse: Callable, on_wheel: Callable, on_key: Callable,
                 key_filter: Callable[[], bool] = lambda: True, skip_own: bool = True):
        self.on_mouse, self.on_wheel, self.on_key = on_mouse, on_wheel, on_key
        self.key_filter = key_filter  # False = нажатие не для записи (например, фокус в окне программы)
        self.skip_own = skip_own  # False только в тестах: там «пользователь» кликает через наш SendInput
        self.received = 0  # сколько нажатий дошло до хуков (для диагностики)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: Exception | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="input-hooks")
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread:
            self._thread.join(2)

    def _mouse_proc(self, code, wparam, lparam):
        if code == 0:
            info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            if not (self.skip_own and info.dwExtraInfo == OWN_INPUT_MARK):
                try:
                    if wparam == WM_MOUSEWHEEL:
                        delta = ctypes.c_short(info.mouseData >> 16).value
                        self.on_wheel(info.pt.x, info.pt.y, delta)
                    elif wparam in (WM_LBUTTONDOWN, WM_RBUTTONDOWN, WM_MBUTTONDOWN):
                        button = {WM_LBUTTONDOWN: "left", WM_RBUTTONDOWN: "right"}.get(wparam, "middle")
                        self.received += 1
                        self.on_mouse(button, info.pt.x, info.pt.y)
                except Exception:
                    log.exception("Ошибка в хуке мыши")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _key_proc(self, code, wparam, lparam):
        if code == 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not (self.skip_own and info.dwExtraInfo == OWN_INPUT_MARK) and info.vkCode not in MODIFIERS:
                try:
                    name = KEY_NAMES.get(info.vkCode)
                    if name == "pause" or self.key_filter():
                        mods = _modifiers()
                        char = _char(info.vkCode, info.scanCode, mods) if not mods & {"ctrl", "alt", "win"} else None
                        name = name or char  # цифровой блок и прочие клавиши, печатающие символ
                        if name:
                            self.on_key(name, char, mods - {"altgr"})
                except Exception:
                    log.exception("Ошибка в хуке клавиатуры")
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        # Ссылки на колбэки обязательны: иначе сборщик мусора удалит их, пока хук активен.
        self._mouse_cb = HOOKPROC(self._mouse_proc)
        self._key_cb = HOOKPROC(self._key_proc)
        module = kernel32.GetModuleHandleW(None)
        mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_cb, module, 0)
        keys = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._key_cb, module, 0)
        if not mouse or not keys:
            self._error = ctypes.WinError(ctypes.get_last_error())
            self._ready.set()
            return
        self._ready.set()
        log.debug("Хуки мыши и клавиатуры установлены (поток %d)", self._thread_id)
        msg = wintypes.MSG()
        try:
            while (r := user32.GetMessageW(ctypes.byref(msg), None, 0, 0)) > 0:
                pass
            if r < 0:
                log.warning("Цикл сообщений хуков прервался: ошибка %d", ctypes.get_last_error())
        finally:
            user32.UnhookWindowsHookEx(mouse)
            user32.UnhookWindowsHookEx(keys)
            log.debug("Хуки сняты, поймано кликов: %d", self.received)


OWN_PID = os.getpid()
