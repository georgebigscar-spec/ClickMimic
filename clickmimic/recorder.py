"""Запись действий пользователя в сценарий.

Хуки мыши и клавиатуры (hooks.py) сообщают о нажатиях. На каждый клик снимается окно, кадр
распознаётся в фоне, и клик превращается в шаг по тексту элемента под курсором
(`click: {text: "Сохранить"}`), а если текста нет — в шаг по ближайшей подписи со смещением или
по координатам внутри окна. Нажатия клавиш собираются в `type`, сочетания — в `hotkey`.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import yaml
from PIL import Image

from .capture import Source
from .elements import UIElement
from .locator import Target, find

log = logging.getLogger("clickmimic")

DOUBLE_CLICK_TIME = 0.5  # с
DOUBLE_CLICK_DIST = 4  # px
LABEL_DIST = 40  # px: насколько далеко от клика может быть подпись для элемента без текста
STOP_KEY = "pause"  # клавиша Pause/Break останавливает запись

# Клавиши, которые записываются отдельным шагом press, а не текстом.
SPECIAL_KEYS = {
    "tab", "esc", "delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down",
    "apps", "printscreen", *(f"f{i}" for i in range(1, 25)),
}


@dataclass
class ClickRecord:
    x: int  # экранные координаты
    y: int
    button: str
    image: Image.Image | None
    offset: tuple[int, int]
    double: bool = False
    spec: dict | None = None  # цель шага после распознавания
    resolved: threading.Event = field(default_factory=threading.Event)

    @property
    def action(self) -> str:
        if self.double:
            return "double_click"
        return "right_click" if self.button == "right" else "click"


def _unique_index(elements: list[UIElement], target: Target, el: UIElement) -> int | None:
    for i in range(20):
        found = find(elements, Target(**{**target.__dict__, "index": i}))
        if found is None:
            return None
        if found.id == el.id:
            return i
    return None


def target_for_click(elements: list[UIElement], x: int, y: int) -> dict:
    """Цель шага для клика в точке (x, y) кадра. Координаты elements — в том же кадре."""
    inside = [e for e in elements if e.bbox.x1 <= x <= e.bbox.x2 and e.bbox.y1 <= y <= e.bbox.y2]
    labelled = [e for e in inside if e.content.strip()]
    if labelled:
        el = min(labelled, key=lambda e: e.bbox.area)
        spec: dict = {"text": el.content.strip()}
        index = _unique_index(elements, Target(text=spec["text"]), el)
        if index is None:  # нечёткое совпадение уводит к другому элементу: требуем точное
            spec["exact"] = True
            index = _unique_index(elements, Target(text=spec["text"], exact=True), el) or 0
        if index:
            spec["index"] = index
        # Для широких элементов (поле ввода, строка списка) важно, куда именно кликнули.
        cx, cy = el.bbox.center
        if el.bbox.x2 - el.bbox.x1 > 200 and abs(x - cx) > 40:
            spec["offset"] = [int(x - cx), int(y - cy)]
        return spec

    # Элемент без текста: ищем ближайшую подпись и кликаем со смещением от неё.
    def dist(e: UIElement) -> float:
        dx = max(e.bbox.x1 - x, 0, x - e.bbox.x2)
        dy = max(e.bbox.y1 - y, 0, y - e.bbox.y2)
        return (dx * dx + dy * dy) ** 0.5

    labels = [e for e in elements if e.content.strip() and dist(e) <= LABEL_DIST]
    if labels:
        el = min(labels, key=dist)
        spec = {"text": el.content.strip()}
        index = _unique_index(elements, Target(text=spec["text"]), el)
        if index is not None:
            if index:
                spec["index"] = index
            cx, cy = el.bbox.center
            spec["offset"] = [int(x - cx), int(y - cy)]
            return spec
    return {"rel": [int(x), int(y)]}


def _flow(value) -> str:
    return yaml.safe_dump(value, default_flow_style=True, allow_unicode=True, width=10_000).strip().removesuffix("...").strip()


class ScriptBuilder:
    """Собирает шаги из событий. Клики разрешаются позже, когда готово распознавание."""

    def __init__(self, source: Source):
        self.source = source
        self.items: list = []  # ClickRecord или dict шага
        self._text = ""
        self._last_click: tuple[float, ClickRecord] | None = None

    # --- события ---

    def click(self, rec: ClickRecord, t: float) -> ClickRecord | None:
        """Добавляет клик. Возвращает запись, если её нужно распознать (у второго клика двойного — нет)."""
        self._flush_text()
        if self._last_click and rec.button == "left":
            t0, prev = self._last_click
            if (prev.button == "left" and not prev.double and t - t0 <= DOUBLE_CLICK_TIME
                    and abs(prev.x - rec.x) <= DOUBLE_CLICK_DIST and abs(prev.y - rec.y) <= DOUBLE_CLICK_DIST
                    and self.items and self.items[-1] is prev):
                prev.double = True
                self._last_click = None
                return None
        self.items.append(rec)
        self._last_click = (t, rec)
        return rec

    def scroll(self, x: int, y: int, amount: int) -> None:
        """amount в «щелчках» колеса; соседние прокрутки в одной точке складываются."""
        self._flush_text()
        self._last_click = None
        last = self.items[-1] if self.items else None
        if isinstance(last, dict) and "scroll" in last and last["scroll"].get("rel") == [x, y]:
            last["scroll"]["amount"] += amount
            if last["scroll"]["amount"] == 0:
                self.items.pop()
            return
        self.items.append({"scroll": {"amount": amount, "rel": [x, y]}})

    def key(self, name: str, char: str | None, mods: set[str]) -> None:
        """name — имя клавиши для hotkey ('a', 'enter', 'f5'); char — напечатанный символ или None."""
        self._last_click = None
        command = mods & {"ctrl", "alt", "win"}
        if command:
            self._flush_text()
            keys = [m for m in ("ctrl", "alt", "shift", "win") if m in mods] + [name]
            self.items.append({"hotkey": "+".join(keys)})
        elif name == "enter":
            self._text += "\n"
        elif name == "backspace":
            if self._text:
                self._text = self._text[:-1]
            else:
                self.items.append({"press": "backspace"})
        elif name in SPECIAL_KEYS:
            self._flush_text()
            self.items.append({"press": name})
        elif char:
            self._text += char

    def _flush_text(self) -> None:
        if self._text:
            self.items.append({"type": self._text})
            self._text = ""

    # --- результат ---

    def steps(self) -> list[dict]:
        self._flush_text()
        out = []
        for item in self.items:
            if isinstance(item, ClickRecord):
                out.append({item.action: item.spec or {"rel": [item.x - item.offset[0], item.y - item.offset[1]]}})
            else:
                out.append(item)
        return out

    def to_yaml(self, name: str = "Записанный сценарий") -> str:
        settings: dict = {"timeout": 10, "step_delay": 0.5}
        if self.source.window:
            settings = {"window": self.source.window, **({"process": self.source.process} if self.source.process else {}),
                        **settings}
        else:
            settings = {"monitor": self.source.monitor, **settings}
        lines = [
            f"name: {_flow(name)}",
            f"# Записано ClickMimic {time.strftime('%Y-%m-%d %H:%M')}. Клики ищут элементы по тексту;",
            "# rel — координаты внутри окна, если у элемента нет текста.",
            f"settings: {_flow(settings)}",
            "steps:",
        ]
        for step in self.steps():
            (action, value), = step.items()
            lines.append(f"  - {action}: {_flow(value)}")
        return "\n".join(lines) + "\n"


class Recorder:
    """Связывает хуки, снимки и распознавание. Методы on_* вызываются из потока хуков."""

    def __init__(self, source: Source, detector, grab: Callable[[], tuple[Image.Image, tuple[int, int]]],
                 region: Callable[[], tuple[int, int, int, int] | None] = lambda: None,
                 ignore: Callable[[int, int], bool] = lambda x, y: False,
                 on_change: Callable[[], None] = lambda: None, clock: Callable[[], float] = time.monotonic):
        self.builder = ScriptBuilder(source)
        self.detector = detector
        self.grab = grab
        self.region = region  # (left, top, width, height) области записи на экране или None
        self.ignore = ignore  # клики по панели записи
        self.on_change = on_change
        self.clock = clock
        self.paused = False
        self.stop_requested = False  # нажата клавиша остановки; окно записи проверяет флаг
        self.stopped = threading.Event()
        self.ignored = 0
        self._events: queue.Queue = queue.Queue()
        self._parse_queue: queue.Queue = queue.Queue()
        self._pending = 0
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._event_loop, daemon=True, name="rec-events"),
                         threading.Thread(target=self._parse_loop, daemon=True, name="rec-parse")]
        for t in self._threads:
            t.start()

    # --- из потока хуков: только складываем в очередь ---

    def on_mouse(self, button: str, x: int, y: int) -> None:
        if not self.paused and not self.stopped.is_set():
            self._events.put(("mouse", button, x, y, self.clock()))

    def on_wheel(self, x: int, y: int, delta: int) -> None:
        if not self.paused and not self.stopped.is_set():
            self._events.put(("wheel", x, y, delta, self.clock()))

    def on_key(self, name: str, char: str | None, mods: set[str]) -> None:
        if self.stopped.is_set():
            return
        if name == STOP_KEY:
            self._events.put(("stop",))
        elif not self.paused:
            self._events.put(("key", name, char, set(mods), self.clock()))

    # --- обработка ---

    @property
    def actions(self) -> int:
        return len(self.builder.items)

    @property
    def pending(self) -> int:
        return self._pending

    def _inside(self, x: int, y: int) -> bool:
        r = self.region()
        return r is None or (r[0] <= x < r[0] + r[2] and r[1] <= y < r[1] + r[3])

    def _event_loop(self) -> None:
        while True:
            ev = self._events.get()
            if ev is None:
                return
            try:
                self._handle(ev)
            except Exception:
                log.exception("Ошибка записи")
            self.on_change()

    def _handle(self, ev) -> None:
        kind = ev[0]
        if kind == "stop":
            self.stop_requested = True
            return
        if kind == "mouse":
            _, button, x, y, t = ev
            if self.ignore(x, y):
                return
            if not self._inside(x, y):
                self.ignored += 1
                return
            image, offset = self.grab()  # кадр до того, как клик что-то изменит
            rec = self.builder.click(ClickRecord(x, y, button, image, offset), t)
            if rec is not None:
                with self._lock:
                    self._pending += 1
                self._parse_queue.put(rec)
        elif kind == "wheel":
            _, x, y, delta, t = ev
            if self.ignore(x, y) or not self._inside(x, y):
                return
            r = self.region()
            ox, oy = (r[0], r[1]) if r else (0, 0)
            self.builder.scroll(x - ox, y - oy, int(round(delta / 120)))
        elif kind == "key":
            _, name, char, mods, t = ev
            self.builder.key(name, char, mods)

    def _parse_loop(self) -> None:
        while True:
            rec = self._parse_queue.get()
            if rec is None:
                return
            try:
                elements = self.detector.parse(rec.image)
                rec.spec = target_for_click(elements, rec.x - rec.offset[0], rec.y - rec.offset[1])
            except Exception:
                log.exception("Не удалось распознать кадр клика")
            finally:
                rec.image = None  # кадры больше не нужны, а память нужна
                rec.resolved.set()
                with self._lock:
                    self._pending -= 1
                self.on_change()

    def stop(self) -> None:
        """Прекращает приём событий; распознавание оставшихся кликов продолжается (см. pending)."""
        self.stopped.set()
        self._events.put(None)

    def wait(self, timeout: float | None = None) -> bool:
        """Ждёт, пока распознаны все клики."""
        self._threads[0].join(timeout)
        end = None if timeout is None else time.monotonic() + timeout
        for item in list(self.builder.items):
            if isinstance(item, ClickRecord):
                left = None if end is None else max(0.0, end - time.monotonic())
                if not item.resolved.wait(left):
                    return False
        self._parse_queue.put(None)
        return True
