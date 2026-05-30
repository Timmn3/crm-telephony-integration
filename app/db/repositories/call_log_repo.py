"""Репозиторий журнала звонков."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CallLog

logger = logging.getLogger(__name__)


async def create(
    session: AsyncSession,
    *,
    order_id: int,
    manager_tg_id: int,
    mango_command_id: str,
    status: str,
    duration: int | None = None,
) -> CallLog:
    """Создаёт запись о звонке."""
    entry = CallLog(
        order_id=order_id,
        manager_tg_id=manager_tg_id,
        mango_command_id=mango_command_id,
        status=status,
        duration=duration,
    )
    session.add(entry)
    await session.flush()
    logger.info("CallLog #%s order=%s status=%s", entry.id, order_id, status)
    return entry


async def get_by_command_id(session: AsyncSession, command_id: str) -> CallLog | None:
    result = await session.execute(
        select(CallLog).where(CallLog.mango_command_id == command_id)
    )
    return result.scalars().first()


async def update_status(
    session: AsyncSession,
    command_id: str,
    *,
    status: str,
    duration: int | None = None,
) -> CallLog | None:
    """Обновляет статус/длительность звонка по command_id (для webhook Mango)."""
    entry = await get_by_command_id(session, command_id)
    if entry is None:
        return None
    entry.status = status
    if duration is not None:
        entry.duration = duration
    await session.flush()
    logger.info("CallLog command_id=%s -> status=%s duration=%s",
                command_id, status, duration)
    return entry
