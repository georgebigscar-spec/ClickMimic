"""Окно программы: выбор источника (монитор или окно приложения), распознавание с разметкой,
список элементов, настройки и запуск сценариев.

Модели и снимки работают в фоновом потоке; результаты передаются в поток Tk через очередь.
"""
from __future__ import annotations

import json
import logging
import queue
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable

from PIL import Image

from . import annotate, capture
from .capture import Source
from .elements import UIElement

log = logging.getLogger("clickmimic")

SETTINGS_PATH = Path.home() / ".clickmimic" / "gui.json"
COLORS = {"icon": "#e63c3c", "text": "#2878e6"}
HIGHLIGHT = "#ffd400"


@dataclass
class GuiSettings:
    monitor: int = 1
    window: str = ""
    process: str = ""
    imgsz: int = 1280
    box_threshold: float = 0.05
    ocr_min_score: float = 0.5
    device: str = "cpu"  # "cpu" или "gpu" (DirectML)
    live_interval: float = 0.5
    show_ids: bool = True
    activate_window: bool = False
    minimize_during_run: bool = True
    dry_run: bool = False
    script_path: str = ""
    variables: str = ""
    geometry: str = "1400x900"

    @classmethod
    def load(cls, path: Path = SETTINGS_PATH) -> "GuiSettings":
        try:
            data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})

    def save(self, path: Path = SETTINGS_PATH) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), "utf-8")
        except OSError:
            log.warning("Не удалось сохранить настройки в %s", path)


@dataclass
class SourceItem:
    label: str
    source: Source | None = None  # None = картинка из файла
    image: Path | None = None


@dataclass
class ParseResult:
    image: Image.Image
    offset: tuple[int, int]
    elements: list[UIElement]  # координаты на изображении
    timings: dict
    grab_ms: float


def parse_variables(text: str) -> dict[str, str]:
    """'name=Георгий; file=C:\\temp\\a.txt' или по строке на переменную."""
    out = {}
    for part in text.replace("\n", ";").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def step_for(e: UIElement, offset: tuple[int, int]) -> str:
    """Шаг сценария, который кликнет по элементу."""
    if e.content:
        text = e.content.replace("\\", "\\\\").replace('"', '\\"')
        extra = ", interactable: true" if e.interactable else ""
        return f'- click: {{text: "{text}"{extra}}}'
    x, y = e.bbox.center
    return f"- click: {{at: [{x + offset[0]}, {y + offset[1]}]}}  # элемент #{e.id} без текста"


def default_detector_factory(s: GuiSettings):
    from .detect.omniparser import OmniParser, OmniParserConfig

    return OmniParser(OmniParserConfig(box_threshold=s.box_threshold, imgsz=s.imgsz, ocr_min_score=s.ocr_min_score,
                                       device=s.device))


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.q.put(("log", self.format(record)))


class App:
    def __init__(self, root, settings: GuiSettings | None = None,
                 detector_factory: Callable[[GuiSettings], object] = default_detector_factory,
                 save_settings: bool = True):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.root = root
        self.s = settings or GuiSettings()
        self.detector_factory = detector_factory
        self.save_settings = save_settings
        self.detector = None
        self.events: queue.Queue = queue.Queue()
        self.lock = threading.Lock()  # один разбор или сценарий за раз
        self.live = tk.BooleanVar(value=False)
        self.live_flag = threading.Event()  # Tk-переменные нельзя читать из фонового потока
        self.live_item: SourceItem | None = None
        self.stop_run = threading.Event()
        self.running_script = False
        self.recording: RecordSession | None = None
        self.hooks_factory = default_hooks_factory  # подменяется в тестах
        self.result: ParseResult | None = None
        self.sources: list[SourceItem] = []
        self.image_item: SourceItem | None = None
        self.photo = None
        self.scale, self.pad = 1.0, (0, 0)
        self.on_result: Callable[[ParseResult], None] | None = None  # для проверки сборки

        root.title("ClickMimic")
        root.geometry(self.s.geometry)
        root.minsize(900, 600)
        self._build()
        self._log_handler = QueueLogHandler(self.events)
        logging.getLogger().addHandler(self._log_handler)
        if logging.getLogger().getEffectiveLevel() > logging.INFO:
            logging.getLogger().setLevel(logging.INFO)
        self.refresh_sources()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<F5>", lambda _: self.parse_once())
        root.after(50, self._poll)
        self.load_detector()

    # ---------- интерфейс ----------

    def _build(self) -> None:
        tk, ttk, root = self.tk, self.ttk, self.root

        bar = ttk.Frame(root, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Label(bar, text="Источник:").pack(side="left")
        self.source_var = tk.StringVar()
        self.source_box = ttk.Combobox(bar, textvariable=self.source_var, state="readonly", width=60)
        self.source_box.pack(side="left", padx=(4, 2))
        self.source_box.bind("<<ComboboxSelected>>", lambda _: self._source_changed())
        ttk.Button(bar, text="↻", width=3, command=self.refresh_sources).pack(side="left")
        ttk.Button(bar, text="Картинка…", command=self.open_image).pack(side="left", padx=(6, 0))
        self.parse_btn = ttk.Button(bar, text="Распознать (F5)", command=self.parse_once)
        self.parse_btn.pack(side="left", padx=(12, 4))
        ttk.Checkbutton(bar, text="Непрерывно", variable=self.live, command=self._live_toggled).pack(side="left")
        self.rec_btn = ttk.Button(bar, text="● Запись", command=self.start_recording)
        self.rec_btn.pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="Настройки…", command=self.open_settings).pack(side="right")
        ttk.Button(bar, text="Сохранить…", command=self.save_result).pack(side="right", padx=4)

        main = ttk.PanedWindow(root, orient="horizontal")
        main.pack(fill="both", expand=True, padx=8)

        self.canvas = tk.Canvas(main, background="#2b2b2b", highlightthickness=0)
        self.canvas.bind("<Configure>", lambda _: self._render())
        self.canvas.bind("<Button-1>", self._canvas_click)
        main.add(self.canvas, weight=3)

        side = ttk.Frame(main)
        main.add(side, weight=2)
        top = ttk.Frame(side)
        top.pack(fill="x", pady=(0, 4))
        ttk.Label(top, text="Поиск:").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *_: self._fill_tree())
        ttk.Entry(top, textvariable=self.filter_var).pack(side="left", fill="x", expand=True, padx=4)
        self.count_label = ttk.Label(top, text="")
        self.count_label.pack(side="left")

        cols = ("id", "kind", "content", "x", "y")
        tree_frame = ttk.Frame(side)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", selectmode="browse")
        for c, title, w, anchor in (("id", "id", 45, "e"), ("kind", "тип", 50, "w"), ("content", "текст", 260, "w"),
                                    ("x", "x", 55, "e"), ("y", "y", 55, "e")):
            self.tree.heading(c, text=title)
            self.tree.column(c, width=w, anchor=anchor, stretch=(c == "content"))
        sb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _: self._highlight())
        self.tree.bind("<Double-1>", lambda _: self.copy_step())

        actions = ttk.Frame(side)
        actions.pack(fill="x", pady=4)
        ttk.Button(actions, text="Копировать шаг", command=self.copy_step).pack(side="left")
        ttk.Button(actions, text="Кликнуть", command=self.click_selected).pack(side="left", padx=4)

        run = ttk.LabelFrame(root, text="Сценарий", padding=(8, 4))
        run.pack(fill="x", padx=8, pady=(6, 0))
        row = ttk.Frame(run)
        row.pack(fill="x")
        self.script_var = tk.StringVar(value=self.s.script_path)
        ttk.Entry(row, textvariable=self.script_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Открыть…", command=self.choose_script).pack(side="left", padx=4)
        ttk.Label(row, text="Переменные:").pack(side="left", padx=(8, 2))
        self.vars_var = tk.StringVar(value=self.s.variables)
        ttk.Entry(row, textvariable=self.vars_var, width=30).pack(side="left")
        self.dry_var = tk.BooleanVar(value=self.s.dry_run)
        ttk.Checkbutton(row, text="Без нажатий", variable=self.dry_var).pack(side="left", padx=8)
        self.run_btn = ttk.Button(row, text="▶ Запустить", command=self.run_script)
        self.run_btn.pack(side="left")
        self.stop_btn = ttk.Button(row, text="■ Стоп", command=self.stop_run.set, state="disabled")
        self.stop_btn.pack(side="left", padx=4)

        log_frame = ttk.Frame(run)
        log_frame.pack(fill="both", expand=True, pady=(4, 0))
        self.log_text = tk.Text(log_frame, height=7, wrap="word", state="disabled", font=("Consolas", 9))
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        lsb.pack(side="left", fill="y")

        self.status = tk.StringVar(value="Загрузка моделей…")
        ttk.Label(root, textvariable=self.status, anchor="w", padding=(8, 3)).pack(fill="x")

    # ---------- источники ----------

    def refresh_sources(self) -> None:
        from . import windows

        items: list[SourceItem] = []
        try:
            import mss

            with mss.mss() as sct:
                mons = sct.monitors
            for i, m in enumerate(mons[1:], start=1):
                items.append(SourceItem(f"Монитор {i} ({m['width']}×{m['height']})", Source(monitor=i)))
            if len(mons) > 2:
                items.append(SourceItem("Все мониторы", Source(monitor=0)))
        except Exception as exc:  # нет дисплея (например, в тестах)
            log.debug("Мониторы недоступны: %s", exc)
            items.append(SourceItem("Монитор 1", Source(monitor=1)))
        own = self.root.winfo_id()
        for w in windows.list_windows():
            if w.title == "ClickMimic" or w.hwnd == own:
                continue
            items.append(SourceItem(f"Окно: {w.label}", Source(window=w.title, process=w.process, hwnd=w.hwnd)))
        if self.image_item:
            items.append(self.image_item)
        self.sources = items
        self.source_box["values"] = [i.label for i in items]
        self.source_box.current(self._preferred_index())
        self._source_changed(save=False)

    def _preferred_index(self) -> int:
        current = self.sources[self.source_box.current()] if self.source_box.current() >= 0 and self.source_box.current() < len(self.sources) else None
        for i, item in enumerate(self.sources):
            if current is not None and item.label == current.label:
                return i
        for i, item in enumerate(self.sources):
            src = item.source
            if src is None:
                continue
            if self.s.window and src.window == self.s.window and src.process == self.s.process:
                return i
            if not self.s.window and not src.window and src.monitor == self.s.monitor:
                return i
        return 0

    def current_item(self) -> SourceItem:
        i = self.source_box.current()
        return self.sources[i if 0 <= i < len(self.sources) else 0]

    def _source_changed(self, save: bool = True) -> None:
        self.live_item = self.current_item()
        src = self.live_item.source
        if src is not None and save:
            self.s.monitor, self.s.window, self.s.process = src.monitor, src.window, src.process
            self._persist()

    def open_image(self, path: str | None = None) -> None:
        from tkinter import filedialog

        path = path or filedialog.askopenfilename(
            title="Картинка для распознавания", filetypes=[("Изображения", "*.png *.jpg *.jpeg *.bmp"), ("Все файлы", "*.*")])
        if not path:
            return
        self.image_item = SourceItem(f"Картинка: {Path(path).name}", image=Path(path))
        self.sources = [i for i in self.sources if i.image is None] + [self.image_item]
        self.source_box["values"] = [i.label for i in self.sources]
        self.source_box.current(len(self.sources) - 1)
        self._source_changed()
        self.parse_once()

    # ---------- модели и распознавание ----------

    def load_detector(self) -> None:
        self.detector = None
        self.parse_btn.state(["disabled"])
        self.status.set("Загрузка моделей…")
        settings = GuiSettings(**asdict(self.s))

        def work():
            with self.lock:
                try:
                    t = time.perf_counter()
                    det = self.detector_factory(settings)
                    self.events.put(("detector", det, time.perf_counter() - t))
                except Exception as exc:
                    self.events.put(("error", f"Не удалось загрузить модели: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def _grab(self, item: SourceItem) -> tuple[Image.Image, tuple[int, int]]:
        if item.image is not None:
            return Image.open(item.image).convert("RGB"), (0, 0)
        return capture.grab_source(item.source, activate=self.s.activate_window)

    def _parse(self, item: SourceItem) -> ParseResult:
        t = time.perf_counter()
        image, offset = self._grab(item)
        grab_ms = (time.perf_counter() - t) * 1000
        elements = self.detector.parse(image)
        return ParseResult(image, offset, elements, dict(getattr(self.detector, "last_timings", {})), grab_ms)

    def parse_once(self) -> None:
        if self.detector is None or self.running_script or self.recording is not None:
            return
        item = self.current_item()

        def work():
            if not self.lock.acquire(blocking=False):
                return
            try:
                self.events.put(("result", self._parse(item)))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.lock.release()

        self.status.set("Распознавание…")
        threading.Thread(target=work, daemon=True).start()

    def _live_toggled(self) -> None:
        if not self.live.get():
            self.live_flag.clear()
            return
        if self.detector is None or self.running_script:
            self.live.set(False)
            return
        self.live_item = self.current_item()
        self.live_flag.set()

        def loop():
            while self.live_flag.is_set():
                item = self.live_item
                with self.lock:
                    try:
                        self.events.put(("result", self._parse(item)))
                    except Exception as exc:
                        self.events.put(("error", str(exc)))
                        self.events.put(("live_off",))
                        return
                time.sleep(max(0.0, self.s.live_interval))

        threading.Thread(target=loop, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                kind = ev[0]
                if kind == "result":
                    self._show_result(ev[1])
                elif kind == "detector":
                    self.detector = ev[1]
                    self.parse_btn.state(["!disabled"])
                    device = "видеокарта" if getattr(self.detector, "device", "cpu") == "gpu" else "процессор"
                    if self.s.device == "gpu" and device == "процессор":
                        device += " (видеокарта с DirectML не найдена)"
                    self.status.set(f"Модели загружены за {ev[2] * 1000:.0f} мс, устройство: {device}. F5 — распознать.")
                    if self.on_result is not None and self.image_item is not None:
                        self.parse_once()
                elif kind == "error":
                    self.status.set(ev[1])
                    self._append_log(ev[1])
                elif kind == "live_off":
                    self._stop_live()
                elif kind == "log":
                    self._append_log(ev[1])
                elif kind == "run_done":
                    self._run_finished()
        except queue.Empty:
            pass
        self.root.after(50, self._poll)

    def _show_result(self, r: ParseResult) -> None:
        from .detect.omniparser import format_timings

        self.result = r
        timing = format_timings(r.timings) if r.timings else ""
        self.status.set(f"{len(r.elements)} элементов. Снимок {r.grab_ms:.0f} мс, {timing}")
        self._fill_tree()
        self._render()
        if self.on_result is not None:
            self.on_result(r)

    # ---------- отображение ----------

    def _visible_elements(self) -> list[UIElement]:
        if self.result is None:
            return []
        q = self.filter_var.get().strip().casefold()
        return [e for e in self.result.elements if not q or q in e.content.casefold() or q == str(e.id)]

    def _fill_tree(self) -> None:
        selected = self.selected()
        self.tree.delete(*self.tree.get_children())
        if self.result is None:
            return
        ox, oy = self.result.offset
        shown = self._visible_elements()
        for e in shown:
            x, y = e.bbox.center
            self.tree.insert("", "end", iid=str(e.id), values=(e.id, e.kind, e.content, x + ox, y + oy))
        self.count_label.configure(text=f"{len(shown)} из {len(self.result.elements)}")
        if selected is not None and self.tree.exists(str(selected.id)):
            self.tree.selection_set(str(selected.id))
        else:
            self._highlight()

    def _render(self) -> None:
        from PIL import ImageTk

        c = self.canvas
        c.delete("all")
        if self.result is None:
            return
        img = self.result.image
        cw, ch = max(c.winfo_width(), 1), max(c.winfo_height(), 1)
        self.scale = min(cw / img.width, ch / img.height, 2.0)
        w, h = max(1, int(img.width * self.scale)), max(1, int(img.height * self.scale))
        self.pad = ((cw - w) // 2, (ch - h) // 2)
        self.photo = ImageTk.PhotoImage(img.resize((w, h), Image.BILINEAR))
        c.create_image(*self.pad, image=self.photo, anchor="nw")
        for e in self._visible_elements():
            x1, y1, x2, y2 = self._to_canvas(e)
            color = COLORS.get(e.kind, "#00a000")
            c.create_rectangle(x1, y1, x2, y2, outline=color, width=1, tags=("box",))
            if self.s.show_ids:
                c.create_text(x1 + 1, y1, text=str(e.id), anchor="sw", fill=color, font=("Segoe UI", 7))
        self._highlight()

    def _to_canvas(self, e: UIElement) -> tuple[float, float, float, float]:
        b, s, (px, py) = e.bbox, self.scale, self.pad
        return px + b.x1 * s, py + b.y1 * s, px + b.x2 * s, py + b.y2 * s

    def _highlight(self) -> None:
        self.canvas.delete("hl")
        e = self.selected()
        if e is not None:
            x1, y1, x2, y2 = self._to_canvas(e)
            self.canvas.create_rectangle(x1 - 2, y1 - 2, x2 + 2, y2 + 2, outline=HIGHLIGHT, width=3, tags=("hl",))

    def _canvas_click(self, event) -> None:
        if self.result is None:
            return
        x = (event.x - self.pad[0]) / self.scale
        y = (event.y - self.pad[1]) / self.scale
        hits = [e for e in self._visible_elements() if e.bbox.x1 <= x <= e.bbox.x2 and e.bbox.y1 <= y <= e.bbox.y2]
        if hits:
            e = min(hits, key=lambda e: e.bbox.area)
            self.tree.selection_set(str(e.id))
            self.tree.see(str(e.id))

    def selected(self) -> UIElement | None:
        sel = self.tree.selection()
        if not sel or self.result is None:
            return None
        return next((e for e in self.result.elements if str(e.id) == sel[0]), None)

    # ---------- действия с элементом ----------

    def copy_step(self) -> None:
        e = self.selected()
        if e is None:
            return
        text = step_for(e, self.result.offset)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status.set(f"Скопировано: {text}")

    def click_selected(self) -> None:
        e = self.selected()
        item = self.current_item()
        if e is None or item.source is None:
            return
        x, y = e.bbox.center
        x, y = x + self.result.offset[0], y + self.result.offset[1]
        dry = self.dry_var.get()

        def work():
            from .input import get_backend

            try:
                if item.source.window and not dry:
                    from . import windows

                    windows.activate(capture.resolve_window(item.source))
                    time.sleep(0.15)
                get_backend(dry).click(x, y)
                log.info("Клик по #%d %r в (%d, %d)%s", e.id, e.content, x, y, " (без нажатия)" if dry else "")
            except Exception as exc:
                self.events.put(("error", f"Клик не выполнен: {exc}"))

        threading.Thread(target=work, daemon=True).start()

    def save_result(self) -> None:
        from tkinter import filedialog

        if self.result is None:
            return
        path = filedialog.asksaveasfilename(title="Сохранить разметку", defaultextension=".png",
                                            initialfile="parsed.png", filetypes=[("PNG", "*.png")])
        if not path:
            return
        r = self.result
        annotate.draw(r.image, r.elements).save(path)
        ox, oy = r.offset
        data = []
        for e in r.elements:
            d = e.to_dict()
            d["bbox"] = [d["bbox"][0] + ox, d["bbox"][1] + oy, d["bbox"][2] + ox, d["bbox"][3] + oy]
            d["center"] = [d["center"][0] + ox, d["center"][1] + oy]
            data.append(d)
        json_path = Path(path).with_suffix(".json")
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        self.status.set(f"Сохранено: {path} и {json_path.name}")

    # ---------- сценарий ----------

    def choose_script(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(title="Сценарий", filetypes=[("YAML", "*.yaml *.yml"), ("Все файлы", "*.*")])
        if path:
            self.script_var.set(path)
            self._persist()

    def run_script(self) -> None:
        from tkinter import messagebox

        from . import script
        from .input import get_backend
        from .runner import Runner

        if self.detector is None or self.running_script:
            return
        self._persist()
        try:
            sc = script.load(self.script_var.get(), parse_variables(self.vars_var.get()))
        except Exception as exc:
            messagebox.showerror("Сценарий", f"Не удалось загрузить сценарий:\n{exc}")
            return
        item = self.current_item()
        if sc.settings.window:
            source = Source(sc.settings.monitor, sc.settings.window, sc.settings.process)
        elif item.source is not None:
            source = Source(**{**asdict(item.source)})
        else:
            source = Source(sc.settings.monitor)
        self._stop_live()
        self.running_script = True
        self.stop_run.clear()
        self.run_btn.state(["disabled"])
        self.parse_btn.state(["disabled"])
        self.stop_btn.state(["!disabled"])
        minimize = self.s.minimize_during_run and not source.window and not self.dry_var.get()
        if minimize:
            self.root.iconify()
        log.info("Запуск: %s, %s%s", sc.name, source.describe(), " (без нажатий)" if self.dry_var.get() else "")
        backend = get_backend(self.dry_var.get())

        def work():
            with self.lock:
                runner = Runner(sc, self.detector, backend, lambda: capture.grab_source(source, activate=True),
                                stop=self.stop_run.is_set)
                try:
                    runner.run()
                except Exception as exc:
                    log.error("Сценарий остановлен: %s", exc)
                    if runner.last_image is not None:
                        path = Path.cwd() / "clickmimic_failure.png"
                        try:
                            runner.last_image.save(path)
                            log.error("Последний снимок: %s", path)
                        except OSError:
                            pass
                finally:
                    self.events.put(("run_done", minimize))

        threading.Thread(target=work, daemon=True).start()

    def _run_finished(self) -> None:
        self.running_script = False
        self.run_btn.state(["!disabled"])
        self.parse_btn.state(["!disabled"])
        self.stop_btn.state(["disabled"])
        if self.root.state() == "iconic":
            self.root.deiconify()

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ---------- настройки ----------

    def open_settings(self) -> None:
        SettingsDialog(self)

    def apply_settings(self, new: GuiSettings) -> None:
        model_keys = ("imgsz", "box_threshold", "ocr_min_score", "device")
        reload = any(getattr(new, k) != getattr(self.s, k) for k in model_keys)
        self.s = new
        self._persist()
        self._render()
        if reload:
            self.load_detector()

    def _persist(self) -> None:
        self.s.script_path = self.script_var.get()
        self.s.variables = self.vars_var.get()
        self.s.dry_run = self.dry_var.get()
        if self.root.state() == "normal":
            self.s.geometry = self.root.geometry()
        if self.save_settings:
            self.s.save()

    # ---------- запись ----------

    def start_recording(self) -> None:
        from tkinter import messagebox

        item = self.current_item()
        if self.detector is None or self.running_script or self.recording is not None:
            return
        if item.source is None:
            messagebox.showinfo("Запись", "Выберите монитор или окно приложения: запись идёт с экрана, а не с картинки.")
            return
        self._stop_live()
        try:
            self.recording = RecordSession(self, Source(**asdict(item.source)))
        except Exception as exc:
            self.recording = None
            messagebox.showerror("Запись", f"Не удалось начать запись:\n{exc}")

    def _recording_done(self, yaml_text: str) -> None:
        self.recording = None
        if self.root.state() == "iconic":
            self.root.deiconify()
        self.editor = ScriptEditor(self, yaml_text)

    def _stop_live(self) -> None:
        self.live.set(False)
        self.live_flag.clear()

    def close(self) -> None:
        self._stop_live()
        if self.recording is not None:
            self.recording.abort()
        self.stop_run.set()
        self._persist()
        logging.getLogger().removeHandler(self._log_handler)
        self.root.destroy()


def default_hooks_factory(recorder):
    """Глобальные хуки Windows; вне Windows записи нет."""
    if sys.platform != "win32":
        raise RuntimeError("Запись действий работает только в Windows")
    from . import hooks

    return hooks.InputHooks(recorder.on_mouse, recorder.on_wheel, recorder.on_key,
                            key_filter=lambda: hooks.foreground_pid() != hooks.OWN_PID)


class RecordSession:
    """Запись: целевое окно впереди, главное окно свёрнуто, маленькая панель поверх всех окон."""

    def __init__(self, app: App, source: Source):
        import tkinter as tk
        from tkinter import ttk

        from .recorder import Recorder

        self.app = app
        self.source = source
        self.hwnd = capture.resolve_window(source) if source.window else None
        self.started = time.monotonic()
        self.finishing = False
        self.panel_rect = (0, 0, 0, 0)
        self.recorder = Recorder(source, app.detector, self._grab, region=self._region, ignore=self._on_panel)
        self.hooks = app.hooks_factory(self.recorder)

        top = self.top = tk.Toplevel(app.root)
        top.title("ClickMimic — запись")
        top.attributes("-topmost", True)
        if sys.platform == "win32":
            top.attributes("-toolwindow", True)
        top.resizable(False, False)
        top.protocol("WM_DELETE_WINDOW", self.finish)
        frm = ttk.Frame(top, padding=(8, 4))
        frm.pack()
        self.label = tk.StringVar(value="● Запись 00:00")
        ttk.Label(frm, textvariable=self.label, foreground="#d22", width=44).pack(side="left")
        self.pause_btn = ttk.Button(frm, text="Пауза", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=4)
        ttk.Button(frm, text="■ Стоп", command=self.finish).pack(side="left")
        top.update_idletasks()
        top.geometry(f"+{max(0, (top.winfo_screenwidth() - top.winfo_width()) // 2)}+8")

        self.hooks.start()
        self._warn_admin()
        app.root.iconify()
        self._focus_target()
        top.after(200, self._tick)
        log.info("Запись: %s. Остановить: «Стоп» на панели или клавиша Pause.", source.describe())

    def _warn_admin(self) -> None:
        from . import windows

        try:
            blocked = self.hwnd and windows.runs_as_admin(self.hwnd) and not windows.runs_as_admin()
        except Exception:
            return
        if blocked:
            from tkinter import messagebox

            log.warning("Окно запущено от имени администратора, а ClickMimic нет")
            messagebox.showwarning(
                "Запись", "Это окно запущено от имени администратора. Windows не передаёт его клики и нажатия "
                "программам с обычными правами, поэтому шаги не запишутся и сценарий не сможет кликать.\n\n"
                "Запустите ClickMimic от имени администратора (правый клик по clickmimic.exe).", parent=self.top)

    # --- снимки и область записи (вызываются из потоков записи) ---

    def _region(self) -> tuple[int, int, int, int] | None:
        from . import windows

        if self.hwnd:
            return windows.rect(self.hwnd)
        try:
            import mss

            with mss.mss() as sct:
                m = sct.monitors[self.source.monitor]
            return m["left"], m["top"], m["width"], m["height"]
        except Exception:
            return None

    def _grab(self):
        if self.hwnd:
            # Окно впереди, поэтому снимаем экран: так в кадр попадают и открытые меню.
            return capture.grab(region=self._region())
        return capture.grab(self.source.monitor)

    def _on_panel(self, x: int, y: int) -> bool:
        px, py, pw, ph = self.panel_rect
        return px <= x < px + pw and py <= y < py + ph

    def _focus_target(self) -> None:
        if self.hwnd:
            from . import windows

            try:
                windows.activate(self.hwnd)
            except Exception:
                log.warning("Не удалось вывести окно на передний план")

    # --- панель ---

    def _tick(self) -> None:
        if not self.top.winfo_exists():
            return
        t = self.top
        self.panel_rect = (t.winfo_rootx(), t.winfo_rooty(), t.winfo_width(), t.winfo_height())
        rec = self.recorder
        if self.finishing:
            if rec.pending:
                self.label.set(f"Обработка кликов: осталось {rec.pending}")
            else:
                self._done()
                return
        else:
            if rec.stop_requested:
                self.finish()
            secs = int(time.monotonic() - self.started)
            state = "⏸ Пауза" if rec.paused else "● Запись"
            clicks = getattr(self.hooks, "received", None)
            extra = f"  кликов: {clicks}" if clicks is not None else ""
            extra += f", вне окна: {rec.ignored}" if rec.ignored else ""
            self.label.set(f"{state} {secs // 60:02d}:{secs % 60:02d}  шагов: {rec.actions}{extra}")
        t.after(200, self._tick)

    def toggle_pause(self) -> None:
        rec = self.recorder
        rec.paused = not rec.paused
        self.pause_btn.configure(text="Продолжить" if rec.paused else "Пауза")
        if not rec.paused:
            self._focus_target()

    def finish(self) -> None:
        if self.finishing:
            return
        self.finishing = True
        self.hooks.stop()
        self.recorder.stop()
        self.pause_btn.state(["disabled"])

    def _done(self) -> None:
        self.recorder.wait(5)
        text = self.recorder.builder.to_yaml()
        self.top.destroy()
        log.info("Запись остановлена: %d шагов (кликов поймано: %s, вне окна: %d)", len(self.recorder.builder.steps()),
                 getattr(self.hooks, "received", "?"), self.recorder.ignored)
        self.app._recording_done(text)

    def abort(self) -> None:
        self.hooks.stop()
        self.recorder.stop()


class ScriptEditor:
    """Записанный сценарий: можно поправить, сохранить и запустить."""

    def __init__(self, app: App, text: str):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        top = self.top = tk.Toplevel(app.root)
        top.title("Записанный сценарий")
        top.geometry("760x520")
        frm = ttk.Frame(top, padding=8)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="Проверьте шаги: клики ищут элементы по тексту, rel — координаты внутри окна.").pack(anchor="w")
        body = ttk.Frame(frm)
        body.pack(fill="both", expand=True, pady=6)
        self.text = tk.Text(body, wrap="none", font=("Consolas", 10), undo=True)
        sb = ttk.Scrollbar(body, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        self.text.insert("1.0", text)
        btns = ttk.Frame(frm)
        btns.pack(fill="x")
        ttk.Button(btns, text="Сохранить…", command=self.save).pack(side="left")
        ttk.Button(btns, text="Сохранить и запустить", command=lambda: self.save(run=True)).pack(side="left", padx=6)
        ttk.Button(btns, text="Закрыть", command=top.destroy).pack(side="right")

    def save(self, run: bool = False, path: str | None = None) -> bool:
        from tkinter import filedialog, messagebox

        from . import script

        content = self.text.get("1.0", "end-1c")
        try:
            script.load(content)
        except Exception as exc:
            messagebox.showerror("Сценарий", f"Ошибка в сценарии:\n{exc}", parent=self.top)
            return False
        path = path or filedialog.asksaveasfilename(parent=self.top, title="Сохранить сценарий", defaultextension=".yaml",
                                                    initialfile="recorded.yaml", filetypes=[("YAML", "*.yaml *.yml")])
        if not path:
            return False
        Path(path).write_text(content, "utf-8")
        self.app.script_var.set(path)
        self.app._persist()
        self.app.status.set(f"Сценарий сохранён: {path}")
        self.top.destroy()
        if run:
            self.app.run_script()
        return True


class SettingsDialog:
    def __init__(self, app: App):
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        s = app.s
        top = self.top = tk.Toplevel(app.root)
        top.title("Настройки")
        top.transient(app.root)
        top.resizable(False, False)
        frm = ttk.Frame(top, padding=12)
        frm.pack(fill="both", expand=True)

        self.imgsz = tk.StringVar(value=str(s.imgsz))
        self.box = tk.StringVar(value=str(s.box_threshold))
        self.ocr = tk.StringVar(value=str(s.ocr_min_score))
        self.interval = tk.StringVar(value=str(s.live_interval))
        self.devices = {"Процессор": "cpu", "Видеокарта (DirectML)": "gpu"}
        self.device = tk.StringVar(value=next((k for k, v in self.devices.items() if v == s.device), "Процессор"))
        self.show_ids = tk.BooleanVar(value=s.show_ids)
        self.activate = tk.BooleanVar(value=s.activate_window)
        self.minimize = tk.BooleanVar(value=s.minimize_during_run)

        rows = [
            ("Устройство", ttk.Combobox(frm, textvariable=self.device, values=list(self.devices), state="readonly", width=22),
             "видеокарта ускоряет YOLO; без неё работает процессор"),
            ("Размер входа YOLO", ttk.Combobox(frm, textvariable=self.imgsz, values=["640", "960", "1280", "1600"], width=8),
             "меньше — быстрее, но мелкие иконки теряются"),
            ("Порог уверенности YOLO", ttk.Spinbox(frm, textvariable=self.box, from_=0.01, to=0.9, increment=0.01, width=8),
             "выше — меньше ложных рамок"),
            ("Мин. уверенность OCR", ttk.Spinbox(frm, textvariable=self.ocr, from_=0.0, to=1.0, increment=0.05, width=8),
             "строки с меньшей уверенностью отбрасываются"),
            ("Интервал непрерывного режима, с", ttk.Spinbox(frm, textvariable=self.interval, from_=0.0, to=60, increment=0.5, width=8),
             "пауза между распознаваниями"),
        ]
        for r, (label, widget, hint) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=3)
            widget.grid(row=r, column=1, sticky="w", padx=8)
            ttk.Label(frm, text=hint, foreground="#777").grid(row=r, column=2, sticky="w")
        r = len(rows)
        ttk.Checkbutton(frm, text="Показывать id на разметке", variable=self.show_ids).grid(row=r, column=0, columnspan=3, sticky="w", pady=(8, 2))
        ttk.Checkbutton(frm, text="Выводить окно на передний план перед снимком (иначе снимок в фоне через PrintWindow)",
                        variable=self.activate).grid(row=r + 1, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Checkbutton(frm, text="Сворачивать ClickMimic во время сценария на весь экран",
                        variable=self.minimize).grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=2)
        ttk.Label(frm, text=f"Настройки хранятся в {SETTINGS_PATH}", foreground="#777").grid(row=r + 3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        btns = ttk.Frame(frm)
        btns.grid(row=r + 4, column=0, columnspan=3, sticky="e", pady=(12, 0))
        ttk.Button(btns, text="OK", command=self.ok).pack(side="left", padx=4)
        ttk.Button(btns, text="Отмена", command=top.destroy).pack(side="left")
        top.bind("<Return>", lambda _: self.ok())
        top.bind("<Escape>", lambda _: top.destroy())
        top.grab_set()

    def ok(self) -> None:
        from tkinter import messagebox

        try:
            new = GuiSettings(**{
                **asdict(self.app.s),
                "imgsz": int(self.imgsz.get()),
                "device": self.devices[self.device.get()],
                "box_threshold": float(self.box.get().replace(",", ".")),
                "ocr_min_score": float(self.ocr.get().replace(",", ".")),
                "live_interval": float(self.interval.get().replace(",", ".")),
                "show_ids": self.show_ids.get(),
                "activate_window": self.activate.get(),
                "minimize_during_run": self.minimize.get(),
            })
        except ValueError:
            messagebox.showerror("Настройки", "Проверьте числовые значения", parent=self.top)
            return
        if new.imgsz % 32 or not 320 <= new.imgsz <= 2560:
            messagebox.showerror("Настройки", "Размер входа YOLO должен быть кратен 32 (320–2560)", parent=self.top)
            return
        self.top.destroy()
        self.app.apply_settings(new)


def _hide_own_console() -> None:
    """При запуске двойным щелчком у exe есть своя консоль; окну программы она не нужна."""
    if sys.platform != "win32":
        return
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32")
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return
    procs = (ctypes.c_uint32 * 4)()
    if kernel32.GetConsoleProcessList(procs, 4) <= 1:  # консоль только наша, а не cmd/PowerShell
        ctypes.WinDLL("user32").ShowWindow(hwnd, 0)


def run_gui(smoke_image: str | None = None, detector_factory=default_detector_factory) -> int:
    import tkinter as tk
    from tkinter import messagebox

    capture.make_dpi_aware()  # до создания окна, иначе Windows растянет его и координаты разъедутся
    if smoke_image is None:
        _hide_own_console()
    root = tk.Tk()

    def report(exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        log.error("%s", text)
        messagebox.showerror("ClickMimic", str(val))

    root.report_callback_exception = report
    if smoke_image is None:
        App(root, GuiSettings.load(), detector_factory)
        root.mainloop()
        return 0

    # Проверка сборки: окно открывается, модели грузятся, картинка распознаётся, окно закрывается.
    outcome = {"code": 2}
    app = App(root, GuiSettings(), detector_factory, save_settings=False)

    def done(r: ParseResult) -> None:
        texts = [e.content for e in r.elements if e.content]
        print(f"GUI: {len(r.elements)} элементов, тексты: {' | '.join(texts[:20])}")
        outcome["code"] = 0 if r.elements else 1
        root.after(300, app.close)

    def timeout() -> None:
        print(f"GUI: нет результата, статус: {app.status.get()}")
        app.close()

    app.on_result = done
    app.image_item = SourceItem(f"Картинка: {Path(smoke_image).name}", image=Path(smoke_image))
    app.refresh_sources()
    app.source_box.current(len(app.sources) - 1)
    app._source_changed(save=False)
    root.after(180_000, timeout)
    root.mainloop()
    return outcome["code"]
