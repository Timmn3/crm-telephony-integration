"""Репозиторий key-value настроек (токены amoCRM и т.п.)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Setting

logger = logging.getLogger(__name__)

# Ключи настроек.
AMOCRM_ACCESS_TOKEN = "amocrm_access_token"
AMOCRM_REFRESH_TOKEN = "amocrm_refresh_token"


async def get(session: AsyncSession, key: str) -> str | None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_value(session: AsyncSession, key: str, value: str | None) -> None:
    """Создаёт или обновляет настройку."""
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        setting = Setting(key=key, value=value)
        session.add(setting)
    else:
        setting.value = value
    await session.flush()
    logger.debug("Настройка %r обновлена", key)


async def delete(session: AsyncSession, key: str) -> None:
    result = await session.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting is not None:
        await session.delete(setting)
        await session.flush()
