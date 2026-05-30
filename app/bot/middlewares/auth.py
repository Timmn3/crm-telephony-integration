"""Middleware авторизации: подгружает пользователя из БД в data['user'].

Также авто-регистрирует администраторов из ADMIN_TG_IDS при первом обращении
и поддерживает актуальными имя/username из Telegram.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from aiogram.types import User as TgUser

from app.config import get_settings
from app.db.models import UserRole
from app.db.repositories import user_repo

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    """Кладёт ORM-объект User (или None) в data['user']."""

    def __init__(self) -> None:
        self._settings = get_settings()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        session = data.get("session")
        tg_user: TgUser | None = data.get("event_from_user")

        user = None
        if tg_user is not None and session is not None and not tg_user.is_bot:
            user = await user_repo.get_by_tg_id(session, tg_user.id)

            if user is None and tg_user.id in self._settings.admin_tg_ids:
                # Первый вход администратора из .env — создаём запись.
                user = await user_repo.create(
                    session,
                    tg_id=tg_user.id,
                    role=UserRole.ADMIN,
                    full_name=tg_user.full_name or "",
                    tg_username=tg_user.username,
                )
                logger.info("Авто-регистрация администратора tg_id=%s", tg_user.id)
            elif user is not None:
                await user_repo.update_tg_profile(
                    session,
                    user,
                    full_name=tg_user.full_name or "",
                    tg_username=tg_user.username,
                )

        data["user"] = user
        return await handler(event, data)
