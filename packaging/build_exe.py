"""Сборка clickmimic.exe (onedir) через PyInstaller.

Запуск из корня репозитория, после `pip install -e ".[vision]" pyinstaller`:
    python packaging/build_exe.py
Результат: dist/clickmimic/clickmimic.exe. Веса модели не вшиваются, скачиваются при первом запуске.
"""
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent

# Florence-2 (remote code) импортирует timm и einops динамически, статический анализ их не видит.
HIDDEN = ["timm", "einops", "clickmimic.detect.omniparser", "clickmimic.input.sendinput"]
COLLECT_ALL = ["clickmimic", "ultralytics", "easyocr", "timm"]
METADATA = ["torch", "transformers", "tokenizers", "huggingface_hub", "safetensors", "ultralytics",
            "timm", "einops", "easyocr", "tqdm", "regex", "requests", "packaging", "filelock",
            "numpy", "pyyaml", "pillow"]

args = [
    str(ROOT / "packaging" / "launcher.py"),
    "--name", "clickmimic",
    "--onedir",
    "--console",
    "--noconfirm",
    "--clean",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
]
args += [f"--hidden-import={m}" for m in HIDDEN]
args += [f"--collect-all={m}" for m in COLLECT_ALL]
args += [f"--copy-metadata={m}" for m in METADATA]

PyInstaller.__main__.run(args)
