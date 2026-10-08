from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def _detector(captions: bool):
    from .detect.omniparser import OmniParser, OmniParserConfig

    return OmniParser(OmniParserConfig(captions=captions))


def cmd_parse(args) -> int:
    from PIL import Image

    from . import annotate, capture

    if args.image:
        image, offset = Image.open(args.image).convert("RGB"), (0, 0)
    else:
        image, offset = capture.grab(args.monitor)
    elements = _detector(not args.no_captions).parse(image)
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
    if args.no_captions:
        sc.settings.captions = False
    runner = Runner(
        sc,
        _detector(sc.settings.captions),
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
    from .detect.weights import ensure_weights

    print(ensure_weights())
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clickmimic", description="Распознавание UI (OmniParser v2) и имитация действий пользователя")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("parse", help="распознать экран или картинку и вывести элементы")
    pp.add_argument("--image", help="файл вместо снимка экрана")
    pp.add_argument("--monitor", type=int, default=1)
    pp.add_argument("--out", help="сохранить картинку с разметкой")
    pp.add_argument("--json", help="сохранить элементы в JSON")
    pp.add_argument("--no-captions", action="store_true", help="не подписывать иконки (быстрее на CPU)")
    pp.set_defaults(func=cmd_parse)

    pr = sub.add_parser("run", help="выполнить сценарий")
    pr.add_argument("script")
    pr.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="подстановка ${KEY}")
    pr.add_argument("--dry-run", action="store_true", help="распознавать, но не нажимать")
    pr.add_argument("--no-captions", action="store_true")
    pr.set_defaults(func=cmd_run)

    pd = sub.add_parser("download-weights", help="скачать веса OmniParser v2")
    pd.set_defaults(func=cmd_download)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
