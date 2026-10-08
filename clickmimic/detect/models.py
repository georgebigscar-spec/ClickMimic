"""Где лежат ONNX-модели.

В собранном exe модели лежат в папке models рядом с clickmimic.exe и ничего не скачивается.
При запуске из исходников их готовит `python packaging/prepare_models.py` в ~/.clickmimic/models
(путь можно переопределить переменной CLICKMIMIC_MODELS).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ICON_DETECT = "icon_detect.onnx"
OCR_DIR = "ocr"


def models_dir() -> Path:
    if os.environ.get("CLICKMIMIC_MODELS"):
        return Path(os.environ["CLICKMIMIC_MODELS"])
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "models"
    return Path.home() / ".clickmimic" / "models"


def require(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(
            f"Не найдена модель {path}. Подготовьте модели: python packaging/prepare_models.py"
        )
    return path
