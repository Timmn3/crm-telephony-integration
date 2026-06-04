"""Репозиторий обращений незарегистрированных пользователей (pending_users).

Наличие записи = «админы уже уведомлены об этом обращении». Запись создаётся
при первом /start незнакомца и удаляется при назначении роли / пропуске.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PendingUser

logger = logging.getLogger(__name__)


async def get(session: AsyncSession, tg_id: int) -> PendingUser | None:
    result = await session.execute(
        select(PendingUser).where(PendingUser.tg_id == tg_id)
    )
    return result.scalar_one_or_none()


async def add(
    session: AsyncSession,
    *,
    tg_id: int,
    full_name: str = "",
    tg_username: str | None = None,
) -> PendingUser:
    """Создаёт запись об обращении (или обновляет профиль, если уже есть)."""
    existing = await get(session, tg_id)
    if existing is not None:
        existing.full_name = full_name or existing.full_name
        existing.tg_username = tg_username
        await session.flush()
        return existing
    pending = PendingUser(tg_id=tg_id, full_name=full_name, tg_username=tg_username)
    session.add(pending)
    await session.flush()
    logger.info("Новое обращение в очереди: tg_id=%s", tg_id)
    return pending


async def delete(session: AsyncSession, tg_id: int) -> None:
    """Удаляет обращение из очереди (роль назначена или пропущено)."""
    existing = await get(session, tg_id)
    if existing is not None:
        await session.delete(existing)
        await session.flush()
        logger.info("Обращение убрано из очереди: tg_id=%s", tg_id)


async def list_all(session: AsyncSession) -> list[PendingUser]:
    result = await session.execute(
        select(PendingUser).order_by(PendingUser.created_at)
    )
    return list(result.scalars().all())
