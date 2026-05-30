"""Репозиторий регионов."""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Region

logger = logging.getLogger(__name__)


async def create(session: AsyncSession, name: str) -> Region:
    """Создаёт регион."""
    region = Region(name=name.strip())
    session.add(region)
    await session.flush()
    logger.info("Создан регион id=%s name=%r", region.id, region.name)
    return region


async def get_by_id(session: AsyncSession, region_id: int) -> Region | None:
    return await session.get(Region, region_id)


async def get_by_name(session: AsyncSession, name: str) -> Region | None:
    result = await session.execute(
        select(Region).where(Region.name == name.strip())
    )
    return result.scalar_one_or_none()


async def list_active(session: AsyncSession) -> list[Region]:
    result = await session.execute(
        select(Region).where(Region.is_active.is_(True)).order_by(Region.name)
    )
    return list(result.scalars().all())


async def list_all(session: AsyncSession) -> list[Region]:
    result = await session.execute(select(Region).order_by(Region.name))
    return list(result.scalars().all())


async def set_active(session: AsyncSession, region: Region, active: bool) -> None:
    region.is_active = active
    await session.flush()
    logger.info("Регион id=%s is_active=%s", region.id, active)
