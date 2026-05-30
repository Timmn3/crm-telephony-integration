"""Регистрация роутеров обработчиков."""
from __future__ import annotations

import logging

from aiogram import Dispatcher

logger = logging.getLogger(__name__)


def register_handlers(dp: Dispatcher) -> None:
    """Подключает все роутеры обработчиков к диспетчеру.

    Порядок важен: более специфичные/приватные роутеры идут раньше.
    """
    from app.bot.handlers import admin, manager, operator, start

    dp.include_router(start.router)
    dp.include_router(admin.router)
    dp.include_router(operator.router)
    dp.include_router(manager.router)
    logger.info("Роутеры обработчиков подключены")
