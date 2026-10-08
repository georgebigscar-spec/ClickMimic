"""Загрузка весов microsoft/OmniParser-v2.0 с Hugging Face."""
from __future__ import annotations

import os
from pathlib import Path

REPO_ID = "microsoft/OmniParser-v2.0"
DEFAULT_DIR = Path(os.environ.get("CLICKMIMIC_WEIGHTS", Path.home() / ".clickmimic" / "weights"))


def ensure_weights(target: Path = DEFAULT_DIR) -> Path:
    """Скачивает icon_detect (YOLO, ~40 МБ), если его ещё нет."""
    if (target / "icon_detect" / "model.pt").exists():
        return target
    from huggingface_hub import snapshot_download

    snapshot_download(
        REPO_ID,
        local_dir=target,
        allow_patterns=["icon_detect/*"],
    )
    return target
