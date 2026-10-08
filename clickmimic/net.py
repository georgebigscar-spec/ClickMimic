"""Проверка HTTPS-сертификатов через системное хранилище ОС.

Python в exe по умолчанию не видит корневые сертификаты, которые Windows подгружает по требованию,
поэтому urllib (через него EasyOCR качает свои модели) падает с CERTIFICATE_VERIFY_FAILED.
truststore проверяет сертификаты средствами самой Windows, как браузер.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("clickmimic")


def use_system_certs() -> None:
    try:
        import truststore

        truststore.inject_into_ssl()
        return
    except Exception as exc:  # noqa: BLE001 — любой сбой здесь не должен ломать запуск
        log.debug("truststore недоступен (%s), использую certifi", exc)
    try:
        import certifi

        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    except ImportError:
        pass
