"""Создание Bot и Dispatcher, подключение middleware и роутеров."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot.handlers import register_handlers
from app.bot.middlewares.auth import AuthMiddleware
from app.bot.middlewares.db import DbSessionMiddleware
from app.config import get_settings

logger = logging.getLogger(__name__)


def create_bot() -> Bot:
    """Создаёт экземпляр бота с HTML-разметкой по умолчанию."""
    settings = get_settings()
    return Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher() -> Dispatcher:
    """Создаёт диспетчер, подключает middleware и роутеры."""
    dp = Dispatcher(storage=MemoryStorage())

    # Порядок важен: сначала открываем сессию БД, затем грузим пользователя.
    dp.update.outer_middleware(DbSessionMiddleware())
    dp.update.outer_middleware(AuthMiddleware())

    register_handlers(dp)
    logger.info("Диспетчер сконфигурирован")
    return dp
