"""Выполнение сценария: снимок экрана -> распознавание -> поиск цели -> ввод."""
from __future__ import annotations

import logging
import time
from typing import Callable

from PIL import Image

from .detect import Detector
from .elements import UIElement
from .input import InputBackend
from .locator import find, point_for
from .script import Script, Step

log = logging.getLogger("clickmimic")

Grabber = Callable[[], tuple[Image.Image, tuple[int, int]]]


class ElementNotFound(RuntimeError):
    pass


class Aborted(RuntimeError):
    pass


class Runner:
    def __init__(self, script: Script, detector: Detector, backend: InputBackend, grabber: Grabber,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 stop: Callable[[], bool] | None = None):
        self.script = script
        self.s = script.settings
        self.detector = detector
        self.input = backend
        self.grab = grabber
        self.sleep = sleep
        self.clock = clock
        self.last_image: Image.Image | None = None
        self.stop = stop or (lambda: False)

    def snapshot(self) -> list[UIElement]:
        """Распознаёт экран и переводит координаты элементов в экранные."""
        image, (ox, oy) = self.grab()
        self.last_image = image
        elements = self.detector.parse(image)
        timings = getattr(self.detector, "last_timings", None)
        if timings:
            from .detect.omniparser import format_timings

            log.info("  экран распознан: %s", format_timings(timings))
        for e in elements:
            e.bbox = e.bbox.offset(ox, oy)
        return elements

    def _check_failsafe(self) -> None:
        if self.stop():
            raise Aborted("Остановлено пользователем")
        # Как в pyautogui: курсор в левом верхнем углу экрана останавливает сценарий.
        if self.input.position() == (0, 0):
            raise Aborted("Остановлено: курсор в левом верхнем углу (failsafe)")

    def _wait(self, step: Step, present: bool) -> UIElement | None:
        deadline = self.clock() + float(step.args.get("timeout", self.s.timeout))
        while True:
            el = find(self.snapshot(), step.target)
            if (el is not None) == present:
                return el
            if self.clock() >= deadline:
                verb = "не найден" if present else "не исчез"
                raise ElementNotFound(f"Шаг {step.line}: элемент {step.target.describe()} {verb} за отведённое время")
            self._check_failsafe()
            self.sleep(self.s.poll_interval)

    def _resolve_point(self, step: Step) -> tuple[int, int]:
        if step.target.at:
            return step.target.at
        el = self._wait(step, present=True)
        log.info("  найден #%d %s %r @ %s", el.id, el.kind, el.content, el.bbox.center)
        return point_for(el, step.target)

    def run_step(self, step: Step) -> None:
        a = step.action
        log.info("Шаг %d: %s %s", step.line, a, step.target.describe() if step.target else step.args)
        if a in {"click", "double_click", "right_click", "move"}:
            x, y = self._resolve_point(step)
            self.input.move(x, y, self.s.move_duration)
            if a != "move":
                button = "right" if a == "right_click" else step.args.get("button", "left")
                clicks = 2 if a == "double_click" else int(step.args.get("clicks", 1))
                self.input.click(x, y, button, clicks)
        elif a == "wait_for":
            self._wait(step, present=True)
        elif a == "wait_gone":
            self._wait(step, present=False)
        elif a == "type":
            self.input.type_text(step.args["text"], float(step.args.get("interval", self.s.typing_interval)))
        elif a == "hotkey":
            self.input.hotkey(*step.args["keys"])
        elif a == "scroll":
            x = y = None
            if step.target:
                x, y = self._resolve_point(step)
            self.input.scroll(step.args["amount"], x, y)
        elif a == "wait":
            self.sleep(step.args["seconds"])
        elif a == "screenshot":
            image, _ = self.grab()
            image.save(step.args["path"])
        elif a == "log":
            log.info("  %s", step.args["message"])
        else:
            raise ValueError(f"Неизвестное действие {a}")

    def run(self) -> None:
        log.info("Сценарий: %s (%d шагов)", self.script.name, len(self.script.steps))
        for step in self.script.steps:
            self._check_failsafe()
            try:
                self.run_step(step)
            except ElementNotFound as exc:
                if not step.optional:
                    raise
                log.warning("  пропущен (optional): %s", exc)
            self.sleep(self.s.step_delay)
        log.info("Готово")
