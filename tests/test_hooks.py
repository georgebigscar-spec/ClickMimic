import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="хуки только в Windows")


def test_hooks_install_and_remove():
    from clickmimic.hooks import InputHooks

    events = []
    h = InputHooks(lambda *a: events.append(a), lambda *a: events.append(a), lambda *a: events.append(a))
    h.start()
    assert h._thread.is_alive()
    h.stop()
    assert not h._thread.is_alive()


def test_key_names_and_chars():
    from clickmimic.hooks import KEY_NAMES, _char

    assert KEY_NAMES[0x41] == "a" and KEY_NAMES[0x0D] == "enter" and KEY_NAMES[0x74] == "f5"
    assert _char(0x41, 0x1E, {"shift"}) in ("A", "Ф")  # зависит от раскладки раннера


def _send_click(x, y):
    from clickmimic.input.sendinput import SendInputBackend

    backend = SendInputBackend()
    backend.move(x, y, 0)
    backend.click(x, y, "left", 1)
    return backend


def _pump_until(cond, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return cond()


def test_hooks_receive_real_events():
    """Клик и нажатие клавиши через SendInput доходят до обработчиков (injected разрешён только здесь)."""
    from clickmimic.hooks import InputHooks

    mouse, keys = [], []
    h = InputHooks(lambda *a: mouse.append(a), lambda *a: None, lambda *a: keys.append(a), skip_injected=False)
    h.start()
    try:
        backend = _send_click(200, 200)
        backend.hotkey("a")
        assert _pump_until(lambda: mouse and keys), (mouse, keys)
    finally:
        h.stop()
    assert mouse[0][0] == "left" and abs(mouse[0][1] - 200) <= 2
    assert keys[0][0] == "a"


def test_recorder_with_real_hooks_counts_steps():
    from PIL import Image

    from clickmimic.capture import Source
    from clickmimic.elements import BBox, UIElement
    from clickmimic.hooks import InputHooks
    from clickmimic.recorder import Recorder

    class Det:
        def parse(self, image, icons=True):
            return [UIElement(0, "text", BBox(150, 150, 250, 250), "Кнопка", False)]

    rec = Recorder(Source(monitor=1), Det(), lambda: (Image.new("RGB", (800, 600)), (0, 0)),
                   region=lambda: (0, 0, 800, 600))
    h = InputHooks(rec.on_mouse, rec.on_wheel, rec.on_key, skip_injected=False)
    h.start()
    try:
        _send_click(200, 200)
        assert _pump_until(lambda: rec.actions >= 1), "клик не записан"
    finally:
        h.stop()
        rec.stop()
    assert rec.wait(5)
    assert rec.builder.steps()[0] == {"click": {"text": "Кнопка", "near": [200, 200]}}


def test_gui_recording_with_real_hooks(tmp_path):
    """Как в программе: панель записи, настоящие хуки, клик по экрану через SendInput."""
    import tkinter as tk
    import time

    from clickmimic.elements import BBox, UIElement
    from clickmimic.gui import App, GuiSettings
    from clickmimic.hooks import InputHooks

    class Det:
        last_timings = None

        def __init__(self, settings):
            pass

        def parse(self, image, icons=True):
            return [UIElement(0, "text", BBox(150, 250, 250, 350), "Кнопка", False)]

    root = tk.Tk()
    try:
        app = App(root, GuiSettings(), Det, save_settings=False)
        app.hooks_factory = lambda r: InputHooks(r.on_mouse, r.on_wheel, r.on_key, skip_injected=False)
        end = time.monotonic() + 10
        while app.detector is None and time.monotonic() < end:
            root.update()
            time.sleep(0.01)
        app.source_box.current(0)  # монитор 1
        app.start_recording()
        session = app.recording
        assert session is not None
        for _ in range(20):
            root.update()
            time.sleep(0.02)
        _send_click(200, 300)
        end = time.monotonic() + 5
        while session.recorder.actions < 1 and time.monotonic() < end:
            root.update()
            time.sleep(0.02)
        assert session.recorder.actions == 1, (session.hooks.received, session.recorder.ignored, session.label.get())
        session.finish()
        end = time.monotonic() + 10
        while app.recording is not None and time.monotonic() < end:
            root.update()
            time.sleep(0.02)
        assert "Кнопка" in app.editor.text.get("1.0", "end")
        app.close()
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_runs_as_admin_for_own_and_shell_window():
    import ctypes

    from clickmimic import windows

    assert isinstance(windows.runs_as_admin(), bool)
    shell = ctypes.windll.user32.GetShellWindow()
    if shell:
        assert isinstance(windows.runs_as_admin(shell), bool)
