import os
import sys

import pytest
from PIL import Image

from clickmimic.elements import BBox, UIElement
from clickmimic.gui import GuiSettings, parse_variables, step_for

ELEMENTS = [
    UIElement(0, "text", BBox(10, 10, 60, 30), "Файл", False),
    UIElement(1, "icon", BBox(100, 50, 180, 80), 'Сохранить "всё"', True),
    UIElement(2, "icon", BBox(200, 50, 220, 70), "", True),
]


class FakeDetector:
    last_timings = {"yolo": 0.01, "ocr_det": 0.01, "ocr_rec": 0.02, "lines": 2, "lines_recognized": 2, "merge": 0, "total": 0.04}

    def __init__(self, settings):
        self.settings = settings
        self.calls = 0

    def parse(self, image):
        self.calls += 1
        return [UIElement(e.id, e.kind, e.bbox, e.content, e.interactable) for e in ELEMENTS]


def test_parse_variables():
    assert parse_variables("name=Георгий; file=C:\\a=b.txt\nx = 1") == {"name": "Георгий", "file": "C:\\a=b.txt", "x": "1"}
    assert parse_variables("") == {}


def test_step_for_text_and_icon():
    assert step_for(ELEMENTS[0], (0, 0)) == '- click: {text: "Файл"}'
    assert step_for(ELEMENTS[1], (0, 0)) == '- click: {text: "Сохранить \\"всё\\"", interactable: true}'
    assert step_for(ELEMENTS[2], (100, 200)) == "- click: {at: [310, 260]}  # элемент #2 без текста"


def test_settings_roundtrip(tmp_path):
    path = tmp_path / "gui.json"
    GuiSettings(window="Блокнот", imgsz=960).save(path)
    s = GuiSettings.load(path)
    assert (s.window, s.imgsz) == ("Блокнот", 960)
    path.write_text('{"imgsz": 640, "unknown": 1}', "utf-8")
    assert GuiSettings.load(path).imgsz == 640
    assert GuiSettings.load(tmp_path / "missing.json").imgsz == 1280


@pytest.fixture
def tk_root():
    tk = pytest.importorskip("tkinter")
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        pytest.skip("нет дисплея")
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk недоступен: {exc}")
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


def pump(root, until, timeout=10.0):
    import time

    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        root.update()
        time.sleep(0.01)
    assert until()


def test_gui_parses_image_and_selects(tk_root, tmp_path):
    from clickmimic.gui import App

    img = tmp_path / "shot.png"
    Image.new("RGB", (400, 200), "white").save(img)
    app = App(tk_root, GuiSettings(), FakeDetector, save_settings=False)
    pump(tk_root, lambda: app.detector is not None)
    app.open_image(str(img))
    pump(tk_root, lambda: app.result is not None)

    assert app.current_item().image == img
    assert len(app.tree.get_children()) == 3
    assert "3 элементов" in app.status.get()

    app.filter_var.set("сохр")
    tk_root.update()
    assert app.tree.get_children() == ("1",)

    app.filter_var.set("")
    tk_root.update()
    # щелчок по разметке выбирает самый маленький элемент под курсором
    x1, y1, x2, y2 = app._to_canvas(ELEMENTS[2])
    app._canvas_click(type("E", (), {"x": (x1 + x2) / 2, "y": (y1 + y2) / 2})())
    tk_root.update()
    assert app.selected().id == 2
    assert app.canvas.find_withtag("hl")

    app.copy_step()
    assert tk_root.clipboard_get().startswith("- click: {at:")

    # смена размера входа перезагружает модели
    app.apply_settings(GuiSettings(imgsz=960))
    pump(tk_root, lambda: app.detector is not None and app.detector.settings.imgsz == 960)
    app.close()


def test_gui_runs_script_dry(tk_root, tmp_path):
    from clickmimic.gui import App

    script = tmp_path / "s.yaml"
    script.write_text('name: t\nsettings: {step_delay: 0, timeout: 1}\nsteps:\n  - click: {text: "${what}"}\n  - log: готово\n', "utf-8")
    img = tmp_path / "shot.png"
    Image.new("RGB", (400, 200), "white").save(img)
    app = App(tk_root, GuiSettings(), FakeDetector, save_settings=False)
    pump(tk_root, lambda: app.detector is not None)
    app.open_image(str(img))
    pump(tk_root, lambda: app.result is not None)

    # сценарий снимает экран, а не картинку; подменяем источник снимка
    import clickmimic.capture as capture

    orig = capture.grab_source
    capture.grab_source = lambda src, activate=False: (Image.open(img).convert("RGB"), (0, 0))
    try:
        app.script_var.set(str(script))
        app.vars_var.set("what=Файл")
        app.dry_var.set(True)
        app.run_script()
        assert app.running_script
        pump(tk_root, lambda: not app.running_script)
    finally:
        capture.grab_source = orig
    log = app.log_text.get("1.0", "end")
    assert "найден #0" in log and "Готово" in log
    app.close()


def test_gui_records_actions_into_script(tk_root, tmp_path, monkeypatch):
    from clickmimic import gui
    from clickmimic.gui import App, RecordSession

    class FakeHooks:
        def __init__(self, recorder):
            self.recorder = recorder
            self.running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setattr(gui.capture, "grab", lambda monitor=1, region=None: (Image.new("RGB", (400, 200)), (0, 0)))
    monkeypatch.setattr(RecordSession, "_region", lambda self: (0, 0, 400, 200))
    app = App(tk_root, GuiSettings(), FakeDetector, save_settings=False)
    app.hooks_factory = FakeHooks
    pump(tk_root, lambda: app.detector is not None)
    app.source_box.current(0)  # монитор

    app.start_recording()
    session = app.recording
    assert session is not None and session.hooks.running
    rec = session.recorder
    rec.on_mouse("left", 30, 20)  # «Файл»
    for ch in "ok":
        rec.on_key(ch, ch, set())
    rec.on_key("pause", None, set())  # клавиша остановки
    pump(tk_root, lambda: app.recording is None)

    assert not session.hooks.running
    yaml_text = app.editor.text.get("1.0", "end-1c")
    assert "- click: {text: Файл}" in yaml_text and "- type: ok" in yaml_text
    path = tmp_path / "rec.yaml"
    assert app.editor.save(path=str(path))
    assert app.script_var.get() == str(path) and path.read_text("utf-8") == yaml_text
    app.close()
