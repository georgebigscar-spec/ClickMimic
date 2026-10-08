from __future__ import annotations

import sys
from typing import Protocol


class InputBackend(Protocol):
    def move(self, x: int, y: int, duration: float = 0.0) -> None: ...
    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None: ...
    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None: ...
    def type_text(self, text: str, interval: float = 0.0) -> None: ...
    def hotkey(self, *keys: str) -> None: ...
    def position(self) -> tuple[int, int]: ...


def get_backend(dry_run: bool = False) -> InputBackend:
    if dry_run or sys.platform != "win32":
        from .dryrun import DryRunBackend

        return DryRunBackend()
    from .sendinput import SendInputBackend

    return SendInputBackend()
