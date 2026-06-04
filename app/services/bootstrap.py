"""Инициализация при старте: seed групп и первичная авторизация amoCRM.

Вызывается из main.py после применения миграций.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db.database import get_session
from app.db.repositories import amo_token_repo, group_repo
from app.services.amocrm import AmoCRMClient

logger = logging.getLogger(__name__)

# Предзаполненный список рабочих групп (из ТЗ).
# Примечание: текст ТЗ упоминает «11 групп», но предметный список содержит 10 —
# создаём ровно столько, сколько перечислено. Недостающую можно добавить /add_group.
INITIAL_GROUPS = [
    {"name": "Группа-1", "city": "Москва"},
    {"name": "Группа-2", "city": "Москва"},
    {"name": "Группа-3", "city": "Москва"},
    {"name": "Казань", "city": "Казань"},
    {"name": "Тверь", "city": "Тверь"},
    {"name": "Группа-5", "city": "Самара"},
    {"name": "Группа-6", "city": "Самара"},
    {"name": "Пермь", "city": "Пермь"},
    {"name": "Тула", "city": "Тула"},
    {"name": "Рязань", "city": "Рязань"},
]


async def seed_groups() -> None:
    """Создаёт предзаполненные группы, если таблица groups пустая."""
    async with get_session() as session:
        if await group_repo.count(session) > 0:
            logger.info("Группы уже существуют — seed пропущен")
            return
        for item in INITIAL_GROUPS:
            await group_repo.create(session, item["name"], item["city"])
        logger.info("Создано %s групп (seed)", len(INITIAL_GROUPS))


async def ensure_amo_token() -> None:
    """Обменивает AMOCRM_AUTH_CODE на токены при первом запуске.

    Ничего не делает в режиме-заглушке или если токены уже есть.
    """
    settings = get_settings()
    if not settings.amocrm_configured:
        logger.info("amoCRM не настроен — работаем в режиме-заглушке")
        return
    async with get_session() as session:
        existing = await amo_token_repo.get(session)
        if existing is not None:
            logger.info("Токены amoCRM уже есть — обмен кода не требуется")
            return
        if not settings.amocrm_auth_code:
            logger.warning(
                "amoCRM настроен, но нет токенов и AMOCRM_AUTH_CODE. "
                "Авторизуйтесь командой /set_amo_code <code>."
            )
            return
        client = AmoCRMClient(session)
        try:
            await client.exchange_code(settings.amocrm_auth_code)
            logger.info("Первичная авторизация amoCRM выполнена")
        except Exception:  # noqa: BLE001
            logger.exception(
                "Не удалось обменять AMOCRM_AUTH_CODE. Код мог истечь — "
                "получите новый и примените командой /set_amo_code."
            )
