from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .net import use_system_certs


def _detector(imgsz: int | None = None):
    from .detect.omniparser import OmniParser, OmniParserConfig

    cfg = OmniParserConfig()
    if imgsz:
        cfg.imgsz = imgsz
    parser = OmniParser(cfg)
    logging.info("Модели загружены за %.0f мс", parser.load_time * 1000)
    return parser


def cmd_parse(args) -> int:
    from PIL import Image

    from . import annotate, capture
    from .detect.omniparser import format_timings

    parser = _detector(args.imgsz)
    for n in range(args.repeat):
        t = time.perf_counter()
        if args.image:
            image, offset = Image.open(args.image).convert("RGB"), (0, 0)
        else:
            image, offset = capture.grab(args.monitor)
        grab_ms = (time.perf_counter() - t) * 1000
        elements = parser.parse(image)
        print(f"Проход {n + 1}: снимок {grab_ms:.0f} мс, {format_timings(parser.last_timings)}", file=sys.stderr)
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
        _detector(args.imgsz),
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
    pp.add_argument("--repeat", type=int, default=1, help="распознать N раз подряд (замер скорости; со 2-го раза работает кэш OCR)")
    pp.add_argument("--imgsz", type=int, help="размер входа YOLO (по умолчанию 1280; 960/640 быстрее, но мелкие иконки теряются)")
    pp.set_defaults(func=cmd_parse)

    pr = sub.add_parser("run", help="выполнить сценарий")
    pr.add_argument("script")
    pr.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="подстановка ${KEY}")
    pr.add_argument("--dry-run", action="store_true", help="распознавать, но не нажимать")
    pr.add_argument("--imgsz", type=int, help="размер входа YOLO")
    pr.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    use_system_certs()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
