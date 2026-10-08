# Точка входа для PyInstaller: __main__.py пакета использует относительные импорты.
import sys

from clickmimic.cli import main

if __name__ == "__main__":
    sys.exit(main())
