from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .net import use_system_certs


def _detector(args):
    from .detect.omniparser import OmniParser, OmniParserConfig

    cfg = OmniParserConfig(device=args.device, parallel=not getattr(args, "sequential", False), ocr=args.ocr)
    if args.imgsz:
        cfg.imgsz = args.imgsz
    parser = OmniParser(cfg)
    device = "видеокарта (DirectML)" if parser.device == "gpu" else "процессор"
    logging.info("Модели загружены за %.0f мс, устройство: %s", parser.load_time * 1000, device)
    return parser


def cmd_parse(args) -> int:
    from PIL import Image

    from . import annotate, capture
    from .detect.omniparser import format_timings

    parser = _detector(args)
    for n in range(args.repeat):
        t = time.perf_counter()
        if args.image:
            image, offset = Image.open(args.image).convert("RGB"), (0, 0)
        else:
            image, offset = capture.grab_source(capture.Source(args.monitor, args.window or ""))
        grab_ms = (time.perf_counter() - t) * 1000
        if not args.same_image:
            parser.forget_frame()  # иначе повтор на той же картинке не запускает модели
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
    if args.window:
        sc.settings.window = args.window
    source = capture.Source(sc.settings.monitor, sc.settings.window, sc.settings.process)
    runner = Runner(
        sc,
        _detector(args),
        get_backend(args.dry_run),
        lambda: capture.grab_source(source, activate=True),
    )
    try:
        runner.run()
    except (ElementNotFound, Aborted, capture.WindowNotFound) as exc:
        logging.error("%s", exc)
        if runner.last_image is not None:
            runner.last_image.save("clickmimic_failure.png")
            logging.error("Последний снимок экрана: clickmimic_failure.png")
        return 1
    return 0


def cmd_gui(args) -> int:
    from .gui import run_gui

    return run_gui(smoke_image=args.smoke)


def cmd_hooks_test(args) -> int:
    """Проверка записи: печатает клики и нажатия, которые ловят хуки Windows."""
    from . import hooks

    def show(*a):
        print(time.strftime("%H:%M:%S"), *a, flush=True)

    h = hooks.InputHooks(lambda b, x, y: show("клик", b, x, y), lambda x, y, d: show("колесо", x, y, d),
                         lambda n, c, m: show("клавиша", n, repr(c), "+".join(sorted(m))))
    h.start()
    print(f"Хуки установлены. Кликайте и нажимайте клавиши {args.seconds} с...", flush=True)
    try:
        time.sleep(args.seconds)
    finally:
        h.stop()
    print(f"Поймано кликов: {h.received}", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # Кириллица в выводе (включая --help) не должна падать в консоли/пайпе с cp1251/cp866.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    p = argparse.ArgumentParser(prog="clickmimic", description="Распознавание UI (OmniParser v2) и имитация действий пользователя")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    pp = sub.add_parser("parse", help="распознать экран или картинку и вывести элементы")
    pp.add_argument("--image", help="файл вместо снимка экрана")
    pp.add_argument("--monitor", type=int, default=1, help="номер монитора, 0 = все мониторы")
    pp.add_argument("--window", help="снимать окно, в заголовке которого есть этот текст")
    pp.add_argument("--out", help="сохранить картинку с разметкой")
    pp.add_argument("--json", help="сохранить элементы в JSON")
    pp.add_argument("--repeat", type=int, default=1, help="распознать N раз подряд (замер скорости; со 2-го раза работает кэш OCR)")
    pp.add_argument("--imgsz", type=int, help="размер входа YOLO (по умолчанию 1280; 960/640 быстрее, но мелкие иконки теряются)")
    pp.add_argument("--ocr", choices=["rapid", "windows"], default="rapid",
                    help="распознавание текста: RapidOCR или встроенный OCR Windows 10/11")
    pp.add_argument("--device", choices=["cpu", "gpu", "auto"], default="cpu",
                    help="gpu = видеокарта через DirectML (если есть), auto = видеокарта, иначе процессор")
    pp.add_argument("--sequential", action="store_true", help="YOLO и OCR по очереди, а не параллельно (для сравнения)")
    pp.add_argument("--same-image", action="store_true",
                    help="с --repeat: не сбрасывать кэш кадра (иначе повторы считают модели заново)")
    pp.set_defaults(func=cmd_parse)

    pr = sub.add_parser("run", help="выполнить сценарий")
    pr.add_argument("script")
    pr.add_argument("--var", action="append", default=[], metavar="KEY=VALUE", help="подстановка ${KEY}")
    pr.add_argument("--dry-run", action="store_true", help="распознавать, но не нажимать")
    pr.add_argument("--imgsz", type=int, help="размер входа YOLO")
    pr.add_argument("--ocr", choices=["rapid", "windows"], default="rapid",
                    help="распознавание текста: RapidOCR или встроенный OCR Windows 10/11")
    pr.add_argument("--device", choices=["cpu", "gpu", "auto"], default="cpu",
                    help="gpu = видеокарта через DirectML (если есть), auto = видеокарта, иначе процессор")
    pr.add_argument("--window", help="работать с окном, в заголовке которого есть этот текст (перекрывает settings.window)")
    pr.set_defaults(func=cmd_run)

    pg = sub.add_parser("gui", help="окно программы (запускается и без команды)")
    pg.add_argument("--smoke", metavar="IMAGE", help="проверка сборки: распознать картинку в окне и выйти")
    pg.set_defaults(func=cmd_gui)

    ph = sub.add_parser("hooks-test", help="проверить, что запись видит клики и нажатия")
    ph.add_argument("--seconds", type=float, default=15)
    ph.set_defaults(func=cmd_hooks_test)

    args = p.parse_args(argv)
    if args.cmd is None:  # двойной щелчок по exe открывает окно программы
        args = p.parse_args(["gui"])
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    use_system_certs()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
