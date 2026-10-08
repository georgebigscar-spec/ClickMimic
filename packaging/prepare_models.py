"""Готовит ONNX-модели для ClickMimic.

1. Скачивает YOLO icon_detect из microsoft/OmniParser-v2.0 и экспортирует его в ONNX.
2. Скачивает модели RapidOCR (PP-OCRv5: детектор + распознаватель eslav).

Нужны зависимости из extra "export" (ultralytics, torch, onnx): pip install -e ".[export]"
Запуск:  python packaging/prepare_models.py [папка]   (по умолчанию ~/.clickmimic/models)
"""
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from clickmimic.detect.models import ICON_DETECT, OCR_DIR, models_dir  # noqa: E402
from clickmimic.detect.ocr import build_engine  # noqa: E402


def export_yolo(target: Path) -> None:
    from huggingface_hub import hf_hub_download
    from ultralytics import YOLO

    with tempfile.TemporaryDirectory() as tmp:
        pt = hf_hub_download("microsoft/OmniParser-v2.0", "icon_detect/model.pt", local_dir=tmp)
        onnx = YOLO(pt).export(format="onnx", imgsz=1280, dynamic=True, simplify=True)
        shutil.copy(onnx, target)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else models_dir()
    out.mkdir(parents=True, exist_ok=True)
    if not (out / ICON_DETECT).exists():
        export_yolo(out / ICON_DETECT)
    # RapidOCR сам скачивает недостающие модели в model_root_dir при создании движка.
    (out / OCR_DIR).mkdir(exist_ok=True)
    build_engine(out / OCR_DIR)
    for f in sorted(out.rglob("*")):
        if f.is_file():
            print(f"{f.relative_to(out)}  {f.stat().st_size / 1e6:.1f} МБ")


if __name__ == "__main__":
    main()
