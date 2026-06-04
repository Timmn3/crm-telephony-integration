"""Репозиторий рабочих групп (слотов менеджеров)."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Group

logger = logging.getLogger(__name__)


async def create(session: AsyncSession, name: str, city: str = "") -> Group:
    """Создаёт группу."""
    group = Group(name=name.strip(), city=city.strip())
    session.add(group)
    await session.flush()
    logger.info("Создана группа id=%s name=%r city=%r", group.id, group.name, group.city)
    return group


async def get_by_id(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def get_by_name(session: AsyncSession, name: str) -> Group | None:
    result = await session.execute(
        select(Group).where(Group.name == name.strip())
    )
    return result.scalar_one_or_none()


async def list_active(session: AsyncSession) -> list[Group]:
    result = await session.execute(
        select(Group).where(Group.is_active.is_(True)).order_by(Group.city, Group.name)
    )
    return list(result.scalars().all())


async def list_all(session: AsyncSession) -> list[Group]:
    result = await session.execute(
        select(Group).order_by(Group.city, Group.name)
    )
    return list(result.scalars().all())


async def set_active(session: AsyncSession, group: Group, active: bool) -> None:
    group.is_active = active
    await session.flush()
    logger.info("Группа id=%s is_active=%s", group.id, active)


async def count(session: AsyncSession) -> int:
    result = await session.execute(select(Group))
    return len(list(result.scalars().all()))
