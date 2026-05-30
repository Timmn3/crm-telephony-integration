"""Настройка логирования приложения.

Логи пишутся одновременно в консоль (stdout) и в файл logs/bot.log
с ротацией. Формат включает время, уровень, имя логгера и сообщение,
чтобы было легко отследить работу бота post-factum.
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_LOG_DIR = Path("logs")
_LOG_FILE = _LOG_DIR / "bot.log"


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер: консоль + файл с ротацией.

    :param level: уровень логирования (DEBUG/INFO/WARNING/ERROR).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Чистим существующие хендлеры, чтобы избежать дублей при повторном вызове.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # Консоль
    console = logging.StreamHandler(stream=sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    # Файл с ротацией (5 файлов по 5 МБ)
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # Если файловая система недоступна (например, read-only контейнер) —
        # продолжаем работать только с консольным логом.
        root.warning("Не удалось создать файловый лог, используется только консоль")

    # Понижаем «болтливость» сторонних библиотек.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    root.info("Логирование настроено, уровень=%s", level.upper())
