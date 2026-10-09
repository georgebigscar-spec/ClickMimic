"""Формат сценария (YAML) и его разбор.

name: Открыть блокнот
settings: {monitor: 1, timeout: 10, move_duration: 0.25, typing_interval: 0.03}
# или окно приложения вместо монитора: settings: {window: "Блокнот"}
steps:
  - hotkey: [win, r]
  - type: "notepad\n"
  - wait_for: {text: "Untitled"}
  - click: {text: "File"}
  - type: "Привет, ${name}!"
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .locator import Target

ACTIONS = {
    "click", "double_click", "right_click", "move", "type", "hotkey", "press",
    "scroll", "wait", "wait_for", "wait_gone", "screenshot", "log",
}


@dataclass
class Settings:
    monitor: int = 1
    window: str = ""  # часть заголовка окна: снимать и активировать только его
    process: str = ""  # имя exe окна, если заголовок неоднозначен
    timeout: float = 10.0
    poll_interval: float = 0.5
    move_duration: float = 0.25
    typing_interval: float = 0.02
    step_delay: float = 0.3


@dataclass
class Step:
    action: str
    target: Target | None = None
    args: dict = field(default_factory=dict)
    optional: bool = False
    line: int | None = None


@dataclass
class Script:
    name: str
    settings: Settings
    steps: list[Step]


_VAR = re.compile(r"\$\{(\w+)\}")


def _substitute(value, variables: dict[str, str]):
    if isinstance(value, str):
        def repl(m):
            if m.group(1) not in variables:
                raise KeyError(f"Переменная не задана: {m.group(1)} (передайте --var {m.group(1)}=...)")
            return variables[m.group(1)]
        return _VAR.sub(repl, value)
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    return value


def _parse_step(raw: dict | str, n: int) -> Step:
    if isinstance(raw, str):
        raw = {raw: None}
    raw = dict(raw)
    optional = bool(raw.pop("optional", False))
    actions = [k for k in raw if k in ACTIONS]
    if len(actions) != 1:
        raise ValueError(f"Шаг {n}: нужно ровно одно действие из {sorted(ACTIONS)}, получено {list(raw)}")
    action = actions[0]
    value = raw.pop(action)
    extra = raw  # прочие ключи на уровне шага, например button: right

    if action in {"click", "double_click", "right_click", "move", "wait_for", "wait_gone"}:
        spec = value if isinstance(value, (dict, str)) else {}
        spec = {**extra, **spec} if isinstance(spec, dict) else spec
        target = Target.from_spec(spec)
        args = {k: v for k, v in (spec.items() if isinstance(spec, dict) else []) if k not in Target.__dataclass_fields__}
        return Step(action, target, args, optional, n)
    if action == "scroll":
        spec = value if isinstance(value, dict) else {"amount": value}
        amount = int(spec.pop("amount", -3))
        target = Target.from_spec(spec) if any(k in spec for k in ("text", "id", "at", "rel")) else None
        return Step(action, target, {"amount": amount}, optional, n)
    if action == "type":
        return Step(action, None, {"text": str(value), **extra}, optional, n)
    if action in {"hotkey", "press"}:
        keys = value if isinstance(value, list) else str(value).split("+")
        return Step("hotkey", None, {"keys": [k.strip() for k in keys]}, optional, n)
    if action == "wait":
        return Step(action, None, {"seconds": float(value)}, optional, n)
    if action == "screenshot":
        return Step(action, None, {"path": str(value or "screenshot.png")}, optional, n)
    return Step(action, None, {"message": str(value)}, optional, n)


def load(source: str | Path, variables: dict[str, str] | None = None) -> Script:
    is_text = isinstance(source, str) and "\n" in source  # текст сценария, а не путь к файлу
    path = Path("script.yaml" if is_text else source)
    data = yaml.safe_load(str(source)) if is_text or not path.exists() else yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "steps" not in data:
        raise ValueError("Сценарий должен быть YAML-словарём с ключом steps")
    data = _substitute(data, variables or {})
    settings = Settings(**(data.get("settings") or {}))
    steps = [_parse_step(s, i + 1) for i, s in enumerate(data["steps"])]
    return Script(data.get("name", path.stem), settings, steps)
