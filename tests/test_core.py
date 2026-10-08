from PIL import Image

from clickmimic import script
from clickmimic.detect.omniparser import merge
from clickmimic.elements import BBox, UIElement
from clickmimic.input.dryrun import DryRunBackend
from clickmimic.locator import Target, find
from clickmimic.runner import ElementNotFound, Runner


def els():
    return [
        UIElement(0, "text", BBox(10, 10, 50, 30), "File", False),
        UIElement(1, "text", BBox(60, 10, 100, 30), "Edit", False),
        UIElement(2, "icon", BBox(200, 10, 220, 30), "", True),
        UIElement(3, "icon", BBox(10, 100, 90, 130), "Save", True),
        UIElement(4, "icon", BBox(100, 100, 190, 130), "Save as", True),
    ]


def test_find_exact_beats_partial():
    assert find(els(), Target(text="Save")).id == 3
    assert find(els(), Target(text="save as")).id == 4


def test_find_icon_and_index_and_region():
    assert find(els(), Target(id=2)).id == 2
    assert find(els(), Target(text="File", interactable=True)) is None
    assert find(els(), Target(text="Save", interactable=False)) is None
    assert find(els(), Target(text="Save", index=1)).id == 4
    assert find(els(), Target(text="Save", region=(95, 90, 200, 50))).id == 4


def test_find_fuzzy_threshold():
    assert find(els(), Target(text="Fiel")) is None
    assert find(els(), Target(text="Edti", min_score=70)).id == 1


def test_merge_attaches_inner_text_to_boxes():
    icons = [(BBox(0, 0, 100, 40), 0.9), (BBox(1, 1, 99, 39), 0.5), (BBox(200, 0, 230, 30), 0.8)]
    texts = [(BBox(10, 10, 60, 30), "OK", 0.99), (BBox(0, 100, 80, 120), "Label", 0.95)]
    out = merge(icons, texts)
    assert [(e.content, e.kind) for e in out] == [("OK", "icon"), ("", "icon"), ("Label", "text")]
    assert [e.id for e in out] == list(range(3))


class FakeDetector:
    def __init__(self, frames):
        self.frames = frames
        self.n = 0

    def parse(self, image):
        f = self.frames[min(self.n, len(self.frames) - 1)]
        self.n += 1
        return [UIElement(e.id, e.kind, e.bbox, e.content, e.interactable) for e in f]


def make_runner(yaml_text, frames, **variables):
    sc = script.load(yaml_text, variables)
    backend = DryRunBackend()
    clock = [0.0]
    runner = Runner(
        sc, FakeDetector(frames), backend,
        grabber=lambda: (Image.new("RGB", (10, 10)), (1000, 0)),  # второй монитор справа
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        clock=lambda: clock[0],
    )
    return runner, backend


def test_run_script_end_to_end():
    yaml_text = """
name: t
settings: {timeout: 2, step_delay: 0}
steps:
  - wait_for: {text: Edit}
  - click: {text: "Save as"}
  - right_click: {id: 2, offset: [5, 0]}
  - type: "Привет, ${who}"
  - hotkey: ctrl+s
  - click: {text: Nope}
    optional: true
"""
    runner, backend = make_runner(yaml_text, [[], els()], who="мир")
    runner.run()
    actions = [e for e in backend.events if e[0] != "move"]
    assert actions == [
        ("click", 1145, 115, "left", 1),
        ("click", 1215, 20, "right", 1),
        ("type", "Привет, мир"),
        ("hotkey", "ctrl+s"),
    ]


def test_missing_element_raises():
    runner, _ = make_runner("steps: [{click: {text: Nope}}]\nsettings: {timeout: 1}", [els()])
    try:
        runner.run()
    except ElementNotFound as exc:
        assert "Nope" in str(exc)
    else:
        raise AssertionError("ожидалось ElementNotFound")


def test_example_script_parses():
    sc = script.load("scripts/notepad_demo.yaml", {"name": "Георгий", "file": "a.txt"})
    assert sc.steps[0].args == {"keys": ["win", "r"]}
    assert sc.steps[10].optional
