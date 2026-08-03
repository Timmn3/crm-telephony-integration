"""Репозиторий настроек времени выполнения (таблица app_settings, key/value)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting

logger = logging.getLogger(__name__)

# Ключ: через сколько часов бездействия закрывать заявку ("0" — выключено).
AUTOCLOSE_HOURS = "order_autoclose_hours"


async def get(session: AsyncSession, key: str) -> str | None:
    """Значение настройки или None, если её ещё не задавали."""
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    return setting.value if setting else None


async def set_value(session: AsyncSession, key: str, value: str) -> None:
    """Создаёт или перезаписывает настройку."""
    result = await session.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is None:
        session.add(AppSetting(key=key, value=value))
    else:
        setting.value = value
    await session.flush()
    logger.info("Настройка %s = %s", key, value)


async def get_int(session: AsyncSession, key: str, default: int) -> int:
    """Числовая настройка с запасным значением.

    Если записи нет или в ней мусор (правили руками в БД) — возвращаем default,
    чтобы кривое значение не отключило механизм молча.
    """
    raw = await get(session, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("Настройка %s содержит не число: %r — берём %s", key, raw, default)
        return default
