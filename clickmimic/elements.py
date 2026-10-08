from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class BBox:
    """Прямоугольник в физических пикселях экрана (left, top, right, bottom)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center(self) -> tuple[int, int]:
        return int((self.x1 + self.x2) / 2), int((self.y1 + self.y2) / 2)

    @property
    def area(self) -> float:
        return max(0.0, self.x2 - self.x1) * max(0.0, self.y2 - self.y1)

    def offset(self, dx: float, dy: float) -> "BBox":
        return BBox(self.x1 + dx, self.y1 + dy, self.x2 + dx, self.y2 + dy)

    def intersection(self, other: "BBox") -> float:
        w = min(self.x2, other.x2) - max(self.x1, other.x1)
        h = min(self.y2, other.y2) - max(self.y1, other.y1)
        return max(0.0, w) * max(0.0, h)

    def iou(self, other: "BBox") -> float:
        inter = self.intersection(other)
        union = self.area + other.area - inter
        return inter / union if union else 0.0


@dataclass
class UIElement:
    """Элемент интерфейса, найденный на скриншоте."""

    id: int
    kind: str  # "text" (OCR) или "icon" (YOLO + подпись Florence-2)
    bbox: BBox
    content: str
    interactable: bool
    score: float = 1.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "bbox": [round(v) for v in (self.bbox.x1, self.bbox.y1, self.bbox.x2, self.bbox.y2)],
            "center": list(self.bbox.center),
            "content": self.content,
            "interactable": self.interactable,
            "score": round(self.score, 3),
        }
