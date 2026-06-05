"""Фильтры доступа по ролям пользователя."""
from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import TelegramObject

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


# Готовые фильтры для удобства.
IsDirector = RoleFilter(UserRole.DIRECTOR)
IsManager = RoleFilter(UserRole.MANAGER)
IsAdmin = RoleFilter(UserRole.ADMIN)
IsManagerOrDirector = RoleFilter(UserRole.MANAGER, UserRole.DIRECTOR)
