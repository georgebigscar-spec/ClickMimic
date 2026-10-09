import yaml
from PIL import Image

from clickmimic import script
from clickmimic.capture import Source
from clickmimic.elements import BBox, UIElement
from clickmimic.input.dryrun import DryRunBackend
from clickmimic.recorder import ClickRecord, Recorder, ScriptBuilder, target_for_click
from clickmimic.runner import Runner


def els():
    return [
        UIElement(0, "text", BBox(10, 10, 50, 30), "Файл", False),
        UIElement(1, "icon", BBox(100, 50, 180, 80), "Сохранить", True),
        UIElement(2, "icon", BBox(200, 50, 220, 70), "", True),
        UIElement(3, "text", BBox(225, 52, 300, 68), "Показать всё", False),
        UIElement(4, "icon", BBox(100, 150, 180, 180), "Сохранить", True),
        UIElement(5, "icon", BBox(10, 300, 600, 330), "Поиск", True),
        UIElement(6, "icon", BBox(700, 700, 720, 720), "", True),
    ]


def test_click_on_text_element():
    assert target_for_click(els(), 30, 20) == {"text": "Файл"}


def test_click_on_second_duplicate_gets_index():
    assert target_for_click(els(), 140, 160) == {"text": "Сохранить", "index": 1}


def test_click_on_wide_field_keeps_offset():
    assert target_for_click(els(), 500, 315) == {"text": "Поиск", "offset": [195, 0]}


def test_icon_without_text_uses_nearby_label():
    assert target_for_click(els(), 210, 60) == {"text": "Показать всё", "offset": [-52, 0]}


def test_lonely_icon_falls_back_to_window_coords():
    assert target_for_click(els(), 710, 710) == {"rel": [710, 710]}


def test_builder_keys_and_double_click():
    b = ScriptBuilder(Source(window="Блокнот", process="notepad.exe"))
    first = b.click(ClickRecord(110, 60, "left", None, (100, 0)), t=1.0)
    assert first is not None
    assert b.click(ClickRecord(111, 61, "left", None, (100, 0)), t=1.2) is None  # второй клик двойного
    first.spec = {"text": "Сохранить"}
    for ch in "Привет":
        b.key(ch.lower(), ch, set())
    b.key("backspace", None, set())
    b.key("enter", None, set())
    b.key("s", None, {"ctrl", "shift"})
    b.key("tab", None, set())
    b.scroll(5, 5, -1)
    b.scroll(5, 5, -2)
    b.key("backspace", None, set())

    assert b.steps() == [
        {"double_click": {"text": "Сохранить"}},
        {"type": "Приве\n"},
        {"hotkey": "ctrl+shift+s"},
        {"press": "tab"},
        {"scroll": {"amount": -3, "rel": [5, 5]}},
        {"press": "backspace"},
    ]


def test_yaml_is_a_valid_script():
    b = ScriptBuilder(Source(window="Блокнот", process="notepad.exe"))
    rec = b.click(ClickRecord(130, 20, "right", None, (100, 0)), t=0)
    rec.spec = {"text": 'Файл "А"', "index": 1}
    b.key("a", "a", set())
    b.click(ClickRecord(300, 400, "left", None, (100, 0)), t=5)  # не распознан: координаты в окне
    text = b.to_yaml()
    data = yaml.safe_load(text)
    assert data["settings"]["window"] == "Блокнот"
    sc = script.load(text)
    assert [s.action for s in sc.steps] == ["right_click", "type", "click"]
    assert sc.steps[0].target.text == 'Файл "А"' and sc.steps[0].target.index == 1
    assert sc.steps[2].target.rel == (200, 400)


class FakeDetector:
    def parse(self, image):
        return els()


def test_recorder_end_to_end_and_replay():
    shots = []

    def grab():
        shots.append(1)
        return Image.new("RGB", (800, 800)), (1000, 0)

    rec = Recorder(Source(window="Окно"), FakeDetector(), grab, region=lambda: (1000, 0, 800, 800),
                   ignore=lambda x, y: y < 0)
    rec.on_mouse("left", 1030, 20)  # «Файл»
    rec.on_mouse("left", 1030, -5)  # по панели записи: пропускается
    rec.on_mouse("left", 50, 50)  # вне окна
    for ch in "hi":
        rec.on_key(ch, ch, set())
    rec.on_key("enter", None, set())
    rec.on_mouse("right", 1140, 160)  # второй «Сохранить»
    rec.on_wheel(1300, 400, -240)
    rec.on_key("pause", None, set())
    rec.stop()
    assert rec.wait(5)
    assert rec.ignored == 1 and rec.stop_requested and len(shots) == 2

    text = rec.builder.to_yaml()
    sc = script.load(text)
    backend = DryRunBackend()
    Runner(sc, FakeDetector(), backend, grabber=lambda: (Image.new("RGB", (800, 800)), (1000, 0)),
           sleep=lambda s: None).run()
    assert [e for e in backend.events if e[0] != "move"] == [
        ("click", 1030, 20, "left", 1),
        ("type", "hi\n"),
        ("click", 1140, 165, "right", 1),
        ("scroll", -2, 1300, 400),
    ]
