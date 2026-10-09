"""Сборка clickmimic.exe (onedir) через PyInstaller.

Запуск из корня репозитория, после `pip install -e ".[vision]" pyinstaller` и
`python packaging/prepare_models.py build/models`:
    python packaging/build_exe.py build/models
Результат: dist/clickmimic/clickmimic.exe и dist/clickmimic/models (ONNX-модели внутри сборки).
"""
import importlib.metadata
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
MODELS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "models"
DIST = ROOT / "dist" / "clickmimic"

HIDDEN = ["clickmimic.detect.omniparser", "clickmimic.input.sendinput", "clickmimic.gui", "PIL.ImageTk",
          "mss", "truststore", "certifi"]
# rapidocr читает свои config.yaml/default_models.yaml с диска.
COLLECT_ALL = ["clickmimic", "rapidocr", "certifi"]
# Подготовка моделей ставит torch/ultralytics в то же окружение; в exe они не нужны.
EXCLUDE = ["torch", "torchvision", "ultralytics", "paddle", "openvino", "tensorrt", "MNN",
           "easyocr", "matplotlib", "pandas", "scipy", "IPython"]
METADATA = ["rapidocr", "numpy", "pyyaml", "pillow", "requests", "tqdm"]
# onnxruntime стоит либо обычный, либо с DirectML (тот же модуль, другое имя пакета).
for dist in ("onnxruntime-directml", "onnxruntime"):
    try:
        importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        continue
    METADATA.append(dist)
    break

args = [
    str(ROOT / "packaging" / "launcher.py"),
    "--name", "clickmimic",
    "--onedir",
    "--console",
    "--noconfirm",
    "--clean",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build" / "pyinstaller"),
    "--specpath", str(ROOT / "build"),
    # Пакет установлен в editable-режиме, такой импорт PyInstaller не видит: без --paths он не
    # анализирует clickmimic и теряет его зависимости (например, mss для снимка экрана).
    "--paths", str(ROOT),
]
args += [f"--hidden-import={m}" for m in HIDDEN]
args += [f"--collect-all={m}" for m in COLLECT_ALL]
args += [f"--exclude-module={m}" for m in EXCLUDE]
args += [f"--copy-metadata={m}" for m in METADATA]

PyInstaller.__main__.run(args)

# Встроенные в пакет rapidocr модели по умолчанию (PP-OCRv6, без кириллицы) не используются.
for f in (DIST / "_internal" / "rapidocr" / "models").glob("*.onnx"):
    f.unlink()
shutil.copytree(MODELS, DIST / "models", dirs_exist_ok=True)
