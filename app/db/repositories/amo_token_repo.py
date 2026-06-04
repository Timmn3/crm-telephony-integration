"""Репозиторий OAuth-токенов amoCRM (таблица amo_tokens, всегда одна запись)."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AmoToken

logger = logging.getLogger(__name__)


async def get(session: AsyncSession) -> AmoToken | None:
    """Возвращает текущую запись токенов (или None, если ещё не было обмена)."""
    result = await session.execute(select(AmoToken).order_by(AmoToken.id).limit(1))
    return result.scalar_one_or_none()


async def save(
    session: AsyncSession,
    *,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
) -> AmoToken:
    """Создаёт или обновляет единственную запись токенов amoCRM."""
    token = await get(session)
    if token is None:
        token = AmoToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        session.add(token)
    else:
        token.access_token = access_token
        token.refresh_token = refresh_token
        token.expires_at = expires_at
    await session.flush()
    logger.info("Токены amoCRM сохранены (действуют до %s)", expires_at)
    return token
