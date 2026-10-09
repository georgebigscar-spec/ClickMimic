"""Поиск элемента по описанию цели из скрипта."""
from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from .elements import BBox, UIElement


@dataclass
class Target:
    text: str | None = None  # текст элемента (OCR)
    interactable: bool | None = None  # True = только области YOLO (кнопки, поля и т.п.)
    id: int | None = None  # id из вывода `clickmimic parse`
    exact: bool = False
    index: int = 0  # какое по счёту совпадение в порядке чтения
    min_score: float = 80.0
    region: tuple[int, int, int, int] | None = None  # left, top, width, height (экранные координаты)
    offset: tuple[int, int] = (0, 0)
    at: tuple[int, int] | None = None  # абсолютные координаты, без распознавания
    rel: tuple[int, int] | None = None  # координаты внутри окна/монитора сценария, без распознавания
    near: tuple[int, int] | None = None  # из нескольких одинаковых совпадений берётся ближайшее к точке в окне

    @classmethod
    def from_spec(cls, spec: dict | str) -> "Target":
        if isinstance(spec, str):
            return cls(text=spec)
        known = {k: v for k, v in spec.items() if k in cls.__dataclass_fields__}
        for key in ("region", "offset", "at", "rel", "near"):
            if known.get(key) is not None:
                known[key] = tuple(known[key])
        return cls(**known)

    def describe(self) -> str:
        if self.at:
            return f"точка {self.at}"
        if self.rel:
            return f"точка {self.rel} в окне"
        parts = [f"{k}={v!r}" for k, v in (("text", self.text), ("id", self.id)) if v is not None]
        return ", ".join(parts) + (f" #{self.index}" if self.index else "")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def score(query: str, content: str, exact: bool) -> float:
    q, c = _norm(query), _norm(content)
    if not c:
        return 0.0
    if q == c:
        return 100.0
    if exact:
        return 0.0
    if re.search(rf"(?<!\w){re.escape(q)}(?!\w)", c):
        return 95.0
    return float(fuzz.ratio(q, c))


def find(elements: list[UIElement], target: Target, origin: tuple[int, int] = (0, 0)) -> UIElement | None:
    """origin — экранные координаты левого верхнего угла окна сценария (для near)."""
    candidates = elements
    if target.region:
        l, t, w, h = target.region
        area = BBox(l, t, l + w, t + h)
        candidates = [e for e in candidates if area.intersection(e.bbox) >= 0.5 * e.bbox.area]

    if target.id is not None:
        matches = [e for e in candidates if e.id == target.id]
    else:
        if target.text is None:
            raise ValueError("Цель должна содержать text, id или at")
        if target.interactable is not None:
            candidates = [e for e in candidates if e.interactable == target.interactable]
        scored = [(score(target.text, e.content, target.exact), e) for e in candidates]
        matches = [e for s, e in scored if s >= target.min_score]
        # Лучшие совпадения первыми; при равенстве — порядок чтения (id).
        best = {e.id: s for s, e in scored}
        matches.sort(key=lambda e: (-best[e.id], e.id))
        if target.near and not target.index and len(matches) > 1:
            # Одинаковые надписи: та, что ближе к месту, где кликнули при записи.
            nx, ny = origin[0] + target.near[0], origin[1] + target.near[1]
            top = best[matches[0].id]

            def dist(e: UIElement) -> float:
                cx, cy = e.bbox.center
                return (cx - nx) ** 2 + (cy - ny) ** 2

            return min((e for e in matches if best[e.id] == top), key=dist)
    return matches[target.index] if len(matches) > target.index else None


def point_for(element: UIElement, target: Target) -> tuple[int, int]:
    cx, cy = element.bbox.center
    return cx + target.offset[0], cy + target.offset[1]
