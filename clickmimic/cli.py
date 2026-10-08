from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .net import use_system_certs


def _detector():
    from .detect.omniparser import OmniParser

    return OmniParser()


def cmd_parse(args) -> int:
    from PIL import Image

    from . import annotate, capture

    if args.image:
        image, offset = Image.open(args.image).convert("RGB"), (0, 0)
    else:
        image, offset = capture.grab(args.monitor)
    elements = _detector().parse(image)
    for e in elements:
        e.bbox = e.bbox.offset(*offset)
        print(f"{e.id:4d} {e.kind:5s} {str(e.bbox.center):>14s}  {e.content}")
    if args.json:
        Path(args.json).write_text(json.dumps([e.to_dict() for e in elements], ensure_ascii=False, indent=2), "utf-8")
    if args.out:
        for e in elements:  # для картинки нужны координаты изображения, а не экрана
            e.bbox = e.bbox.offset(-offset[0], -offset[1])
        annotate.draw(image, elements).save(args.out)
        print(f"Разметка сохранена: {args.out}")
    return 0


def cmd_run(args) -> int:
    from . import capture, script
    from .input import get_backend
    from .runner import Aborted, ElementNotFound, Runner

    variables = dict(v.split("=", 1) for v in args.var)
    sc = script.load(args.script, variables)
    runner = Runner(
        sc,
        _detector(),
        get_backend(args.dry_run),
        lambda: capture.grab(sc.settings.monitor),
    )
    try:
        runner.run()
    except (ElementNotFound, Aborted) as exc:
        logging.error("%s", exc)
        if runner.last_image is not None:
            runner.last_image.save("clickmimic_failure.png")
            logging.error("Последний снимок экрана: clickmimic_failure.png")
        return 1
    return 0


def cmd_download(args) -> int:
    import easyocr

    from .detect.omniparser import OmniParserConfig
    from .detect.weights import ensure_weights

    print(ensure_weights())
    # EasyOCR качает свои модели (~100 МБ) при первом создании Reader; делаем это здесь же.
    easyocr.Reader(list(OmniParserConfig().ocr_languages), gpu=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Кириллица в выводе (включая --help) не должна падать в консоли/пайпе с cp1251/cp866.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="clickmimic", description="Распознавание UI (OmniParser v2) и имитация действий пользователя")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="распознать экран или картинку и вывести элементы")
    pp.add_argument("--image", help="файл вместо снимка экрана")
    pp.add_argument("--monitor", type=int, default=1)
    pp.add_argument("--out", help="сохранить картинку с разметкой")
    pp.add_argument("--json", help="сохранить элементы в JSON")
    pp.set_defaults(func=cmd_parse)

    pr = sub.add_parser("run", help="выполнить сценарий")
    pr.add_argument("script")
    pr.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="подстановка ${KEY}")
    pr.add_argument("--dry-run", action="store_true", help="распознавать, но не нажимать")
    pr.set_defaults(func=cmd_run)

    pd = sub.add_parser("download-weights", help="скачать веса OmniParser v2 и модели EasyOCR")
    pd.set_defaults(func=cmd_download)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    use_system_certs()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
