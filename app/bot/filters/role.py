"""Фильтры доступа по ролям пользователя."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

from app.config import get_settings
from app.db.models import User, UserRole


class RoleFilter(BaseFilter):
    """Пропускает только активных пользователей с одной из разрешённых ролей.

    Значение data['user'] прокидывается AuthMiddleware. Фильтр получает его
    как именованный аргумент (aiogram передаёт данные контекста в фильтры).
    """

    def __init__(self, *roles: UserRole) -> None:
        self.roles = set(roles)

    async def __call__(
        self, event: TelegramObject, user: User | None = None
    ) -> bool:
        return user is not None and user.is_active and user.role in self.roles


class DirectorOrCoderFilter(BaseFilter):
    """Директор или разработчик (CODER из .env) — для служебных команд.

    Разработчик проходит по tg_id даже без роли в БД: страховка на случай, если
    он перестанет быть директором, а служебную команду выполнить надо.
    """

    async def __call__(
        self, event: TelegramObject, user: User | None = None
    ) -> bool:
        if user is not None and user.is_active and user.role == UserRole.DIRECTOR:
            return True
        coder_tg_id = get_settings().coder_tg_id
        from_user = getattr(event, "from_user", None)
        return bool(coder_tg_id and from_user and from_user.id == coder_tg_id)


# Готовые фильтры для удобства.
IsDirector = RoleFilter(UserRole.DIRECTOR)
IsManager = RoleFilter(UserRole.MANAGER)
IsAdmin = RoleFilter(UserRole.ADMIN)
IsManagerOrDirector = RoleFilter(UserRole.MANAGER, UserRole.DIRECTOR)
IsDirectorOrCoder = DirectorOrCoderFilter()
