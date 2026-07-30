"""Управление меню команд (кнопка «Меню» в Telegram) по ролям пользователей."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.repositories import user_repo

logger = logging.getLogger(__name__)

_COMMANDS: dict[UserRole, list[BotCommand]] = {
    UserRole.DIRECTOR: [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="add_manager", description="Добавить менеджера"),
        BotCommand(command="add_admin", description="Добавить администратора"),
        BotCommand(command="add_group", description="Создать группу"),
        BotCommand(command="groups", description="Список групп и администраторов"),
        BotCommand(command="edit_group", description="Редактировать группу"),
        BotCommand(command="set_extension", description="Задать extension Mango для группы"),
        BotCommand(command="delete_group", description="Удалить группу"),
        BotCommand(command="pending", description="Необработанные обращения"),
        BotCommand(command="users", description="Список пользователей"),
        BotCommand(command="remove_user", description="Деактивировать пользователя"),
        BotCommand(command="amo_fields", description="Поля сделки/контакта amoCRM"),
        BotCommand(command="set_amo_code", description="Обновить код авторизации amoCRM"),
        BotCommand(command="me", description="Информация о себе"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ],
    UserRole.MANAGER: [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="order", description="Создать заявку"),
        BotCommand(command="my_orders", description="Мои активные заявки"),
        BotCommand(command="me", description="Информация о себе"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ],
    UserRole.ADMIN: [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="my_tasks", description="Мои заявки"),
        BotCommand(command="me", description="Информация о себе"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
    ],
}


async def set_commands_for_user(bot: Bot, tg_id: int, role: UserRole) -> None:
    """Устанавливает меню команд для конкретного пользователя по его роли."""
    commands = _COMMANDS.get(role)
    if not commands:
        return
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeChat(chat_id=tg_id))
    except Exception as exc:
        logger.warning("Не удалось установить команды для tg_id=%s: %s", tg_id, exc)


async def setup_all_commands(bot: Bot, session: AsyncSession) -> None:
    """Устанавливает меню команд для всех активных пользователей при старте бота."""
    users = await user_repo.list_all(session)
    count = 0
    for user in users:
        if not user.is_active:
            continue
        await set_commands_for_user(bot, user.tg_id, user.role)
        count += 1
    logger.info("Меню команд установлено для %d пользователей", count)
