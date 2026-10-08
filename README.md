# ClickMimic

Программа для Windows, которая распознаёт элементы интерфейса на экране моделью
[microsoft/OmniParser-v2.0](https://huggingface.co/microsoft/OmniParser-v2.0) и выполняет
YAML-сценарии, имитирующие работу пользователя: клики, ввод текста, горячие клавиши, прокрутку.

## Архитектура

```
 сценарий.yaml ──► script.py (разбор, ${переменные})
                        │
                        ▼
                   runner.py ──── цикл шага: снимок → распознавание → поиск цели → ввод
                   │    │    │
        capture.py │    │    │ input/
   (mss, DPI-aware)│    │    └─► sendinput.py  (WinAPI SendInput: мышь, Unicode-ввод, хоткеи)
                   │    │        dryrun.py     (только лог; вне Windows и с --dry-run)
                   ▼    ▼
       detect/omniparser.py ──► locator.py (нечёткий поиск по тексту/подписи, index, region, offset)
         ├─ YOLO icon_detect      — интерактивные области
         ├─ EasyOCR (en, ru)      — текст
         └─ Florence-2 icon_caption — подписи иконок без текста
```

| Модуль | Что делает |
|---|---|
| `capture.py` | Снимок монитора/региона через `mss`, включает per-monitor DPI awareness, чтобы координаты совпадали на 125–150% масштабе |
| `detect/omniparser.py` | Пайплайн OmniParser v2: YOLO + OCR, текст внутри иконки становится её содержимым, остальные иконки подписывает Florence-2 |
| `detect/weights.py` | Скачивает `icon_detect` и `icon_caption` с Hugging Face в `~/.clickmimic/weights` (путь меняется через `CLICKMIMIC_WEIGHTS`) |
| `locator.py` | Находит элемент: точное совпадение > целое слово > `rapidfuzz.ratio` (порог 80) |
| `input/sendinput.py` | Ввод через `SendInput`; текст печатается через `KEYEVENTF_UNICODE`, поэтому кириллица работает при любой раскладке; плавное движение мыши и случайные паузы между нажатиями |
| `runner.py` | Выполняет шаги, ждёт появления элемента с таймаутом, failsafe (курсор в левый верхний угол останавливает сценарий), при ошибке сохраняет последний снимок |
| `cli.py` | Команды `parse`, `run`, `download-weights` |

## Установка (Windows, Python 3.10+)

```powershell
python -m venv .venv; .venv\Scripts\activate
# для NVIDIA GPU сначала поставьте torch с CUDA: https://pytorch.org/get-started/locally/
pip install -e ".[vision,dev]"
clickmimic download-weights
```

На CPU распознавание одного экрана с подписями иконок занимает секунды–десятки секунд;
`--no-captions` (или `settings.captions: false`) отключает Florence-2 и сильно ускоряет работу,
тогда иконки без текста находятся только по `id` или координатам.

## Использование

```powershell
# посмотреть, что видит модель: список элементов и картинка с рамками и id
clickmimic parse --out parsed.png --json parsed.json

# выполнить сценарий (с --dry-run ничего не нажимается)
clickmimic run scripts\notepad_demo.yaml --var name=Георгий --var file=C:\temp\hello.txt
```

## Формат сценария

```yaml
name: Пример
settings: {monitor: 1, timeout: 10, move_duration: 0.25, typing_interval: 0.02, step_delay: 0.3, captions: true}
steps:
  - hotkey: [win, r]                     # или "ctrl+shift+esc"
  - type: "notepad\n"                    # \n = Enter; ${var} подставляется из --var
  - wait_for: {text: "File"}             # ждать появления (timeout из settings или свой)
  - click: {text: "Save", index: 1}      # второе совпадение в порядке чтения
  - click: {icon: "settings"}            # искать только среди иконок по подписи Florence-2
  - right_click: {text: "Файл", offset: [0, 20]}
  - double_click: {id: 42}               # id из вывода `clickmimic parse`
  - click: {at: [100, 200]}              # абсолютные координаты без распознавания
  - click: {text: "OK", region: [0, 0, 800, 600], exact: true, min_score: 90}
  - scroll: {amount: -5, text: "Список"}
  - wait_gone: {text: "Загрузка"}
  - click: {text: "Yes"}
    optional: true                       # не падать, если элемента нет
  - wait: 1.5
  - screenshot: done.png
  - log: "готово"
```

## Готовая сборка

GitHub Actions (`.github/workflows/build.yml`) на каждый push и PR гоняет тесты на Linux и Windows,
собирает `clickmimic.exe` через PyInstaller (CPU-версия torch) и проверяет его на тестовой картинке.
Архив `clickmimic-windows-x64.zip` лежит в артефактах запуска, а при пуше тега `v*` прикладывается к релизу.
Веса модели в архив не входят и скачиваются при первом запуске (`clickmimic download-weights`).

Локально: `pip install -e ".[vision]" pyinstaller` и `python packaging/build_exe.py`.

## Тесты

```bash
pip install -e ".[dev]"
pytest
```

Тесты не требуют модели и Windows: детектор подменяется фейком, ввод идёт в dry-run.

## Лицензии

Веса `icon_detect` распространяются под AGPL (они основаны на YOLO), `icon_caption` — под MIT.
Учитывайте это, если программа будет распространяться.

## Следующие шаги

- Проверить на реальной Windows-машине: точность на вашем софте и время распознавания на CPU/GPU.
- Кэширование: не запускать модель повторно, если экран не изменился.
- Привязка к окну (pywin32: найти окно по заголовку, активировать, снимать только его область).
- Условия и циклы в сценариях (`if_exists`, `repeat`), запись сценария по действиям пользователя.
