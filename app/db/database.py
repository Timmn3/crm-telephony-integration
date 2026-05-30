"""Async engine, фабрика сессий и утилиты работы с БД."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Контекстный менеджер сессии с автокоммитом/откатом.

    Использование::

        async with get_session() as session:
            ...
    """
    session = async_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def init_db() -> None:
    """Проверяет соединение с БД при старте приложения.

    Сами таблицы создаются миграциями Alembic (`alembic upgrade head`),
    поэтому здесь только пинг, чтобы упасть рано и понятно, если БД недоступна.
    """
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Соединение с БД установлено: %s:%s/%s",
                    _settings.db_host, _settings.db_port, _settings.db_name)
    except Exception:
        logger.exception("Не удалось подключиться к БД")
        raise


async def dispose_engine() -> None:
    """Закрывает пул соединений (вызывать при остановке приложения)."""
    await engine.dispose()
    logger.info("Пул соединений с БД закрыт")
