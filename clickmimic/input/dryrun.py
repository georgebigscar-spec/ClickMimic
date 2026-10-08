from __future__ import annotations

import logging

log = logging.getLogger("clickmimic.input")


class DryRunBackend:
    """Ничего не нажимает, только пишет в лог. Используется вне Windows и с --dry-run."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self._pos = (1, 1)  # не (0, 0), иначе сработает failsafe

    def _record(self, *event) -> None:
        self.events.append(event)
        log.info("[dry-run] %s", " ".join(map(str, event)))

    def move(self, x, y, duration=0.0):
        self._pos = (x, y)
        self._record("move", x, y)

    def click(self, x, y, button="left", clicks=1):
        self._pos = (x, y)
        self._record("click", x, y, button, clicks)

    def scroll(self, amount, x=None, y=None):
        self._record("scroll", amount, x, y)

    def type_text(self, text, interval=0.0):
        self._record("type", text)

    def hotkey(self, *keys):
        self._record("hotkey", "+".join(keys))

    def position(self):
        return self._pos
