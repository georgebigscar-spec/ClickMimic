import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="хуки только в Windows")


def test_hooks_install_and_remove():
    from clickmimic.hooks import InputHooks

    events = []
    h = InputHooks(lambda *a: events.append(a), lambda *a: events.append(a), lambda *a: events.append(a))
    h.start()
    assert h._thread.is_alive()
    h.stop()
    assert not h._thread.is_alive()


def test_key_names_and_chars():
    from clickmimic.hooks import KEY_NAMES, _char

    assert KEY_NAMES[0x41] == "a" and KEY_NAMES[0x0D] == "enter" and KEY_NAMES[0x74] == "f5"
    assert _char(0x41, 0x1E, {"shift"}) in ("A", "Ф")  # зависит от раскладки раннера
