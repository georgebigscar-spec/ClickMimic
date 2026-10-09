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


def providers(device: str) -> list[str]:
    """Провайдеры onnxruntime для устройства: "cpu", "gpu" (DirectML) или "auto".

    DirectML работает на любой видеокарте с DirectX 12 (NVIDIA, AMD, Intel), но есть только
    в сборке onnxruntime-directml; без неё или без видеокарты остаётся процессор.
    """
    import onnxruntime as ort

    if device != "cpu" and "DmlExecutionProvider" in ort.get_available_providers():
        return ["DmlExecutionProvider", "CPUExecutionProvider"]
    return ["CPUExecutionProvider"]
