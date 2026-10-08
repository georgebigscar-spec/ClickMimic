"""Сборка clickmimic.exe (onedir) через PyInstaller.

Запуск из корня репозитория, после `pip install -e ".[vision]" pyinstaller`:
    python packaging/build_exe.py
Результат: dist/clickmimic/clickmimic.exe. Веса модели не вшиваются, скачиваются при первом запуске.
"""
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent

HIDDEN = ["clickmimic.detect.omniparser", "clickmimic.detect.weights", "clickmimic.input.sendinput", "huggingface_hub", "truststore", "certifi"]
# torchvision собираем целиком вместе с _C*.pyd, иначе exe падает с "torchvision::nms does not exist".
# huggingface_hub раньше попадал в сборку транзитивно через transformers; теперь собираем явно.
COLLECT_ALL = ["clickmimic", "ultralytics", "easyocr", "torchvision", "huggingface_hub", "certifi"]
METADATA = ["torch", "torchvision", "huggingface_hub", "ultralytics", "easyocr", "tqdm", "requests", "packaging", "filelock",
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
