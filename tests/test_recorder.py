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
    assert target_for_click(els(), 30, 20) == {"text": "Файл", "near": [30, 20]}


def test_click_on_second_duplicate_keeps_place():
    spec = target_for_click(els(), 140, 160)
    assert spec == {"text": "Сохранить", "near": [140, 160]}
    # при воспроизведении из двух «Сохранить» выбирается ближайший к месту клика
    from clickmimic.locator import Target, find

    assert find(els(), Target.from_spec(spec)).id == 4
    assert find(els(), Target(text="Сохранить", near=(140, 60))).id == 1


def test_click_on_wide_field_keeps_offset():
    assert target_for_click(els(), 500, 315) == {"text": "Поиск", "offset": [195, 0], "near": [500, 315]}


def test_icon_without_text_uses_nearby_label():
    assert target_for_click(els(), 210, 60) == {"text": "Показать всё", "offset": [-52, 0], "near": [210, 60]}


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


class CropDetector:
    """Видит els() в координатах полного кадра 800x800 и отдаёт то, что попало в картинку."""

    def __init__(self):
        self.calls = []

    def parse(self, image, icons=True, upscale=True):
        self.calls.append((image.size, icons, upscale))
        ox, oy = image.info.get("origin", (0, 0))
        out = []
        for e in els():
            b = e.bbox
            x1, y1, x2, y2 = max(b.x1 - ox, 0), max(b.y1 - oy, 0), min(b.x2 - ox, image.width), min(b.y2 - oy, image.height)
            if x2 > x1 and y2 > y1:
                out.append(UIElement(e.id, e.kind, BBox(x1, y1, x2, y2), e.content, e.interactable))
        return out


def test_recorder_reads_only_text_around_click(monkeypatch):
    det = CropDetector()
    frame = Image.new("RGB", (800, 800))
    rec = Recorder(Source(window="Окно"), det, lambda: (frame, (0, 0)), crop=(200, 64))
    orig_crop = Image.Image.crop

    def crop(img, box):
        out = orig_crop(img, box)
        out.info["origin"] = box[:2]
        return out

    monkeypatch.setattr(Image.Image, "crop", crop)
    near = rec.elements_near(frame, 140, 160)
    assert det.calls == [((200, 64), False, False)]
    assert {e.content for e in near} == {"Сохранить"} and near[0].bbox.x1 == 100
    # «Поиск» шириной 590 px обрезан краем полосы: распознаётся весь кадр
    det.calls.clear()
    assert target_for_click(rec.elements_near(frame, 500, 315), 500, 315)["offset"] == [195, 0]
    assert [c[0] for c in det.calls] == [(200, 64), (800, 800)]
    rec.stop()


def test_process_only_source_in_yaml():
    b = ScriptBuilder(Source(window="", process="explorer.exe"))
    data = yaml.safe_load(b.to_yaml())
    assert data["settings"]["process"] == "explorer.exe" and "window" not in data["settings"]
    assert Source(process="explorer.exe").is_window and not Source().is_window
    assert script.load(b.to_yaml()).settings.process == "explorer.exe"
