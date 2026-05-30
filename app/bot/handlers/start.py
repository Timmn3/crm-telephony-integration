"""Базовые команды: /start, /help, /me."""
from __future__ import annotations

import html
import logging

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.keyboards.reply import phone_request_keyboard
from app.db.models import User, UserRole
from app.db.repositories import region_repo
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = Router(name="start")

NOT_REGISTERED = (
    "👋 Здравствуйте!\n\n"
    "Вы пока не зарегистрированы в системе. "
    "Обратитесь к администратору для получения доступа."
)

ROLE_TITLES = {
    UserRole.ADMIN: "администратор",
    UserRole.OPERATOR: "оператор отдела продаж",
    UserRole.MANAGER: "выездной менеджер",
}

HELP_BY_ROLE = {
    UserRole.ADMIN: (
        "<b>Команды администратора</b>\n"
        "/add_operator — добавить оператора\n"
        "/add_manager — добавить менеджера (с привязкой к региону)\n"
        "/add_region — создать регион\n"
        "/regions — список регионов\n"
        "/users — список пользователей\n"
        "/remove_user — деактивировать пользователя\n"
        "/amo_fields — показать поля сделки/контакта amoCRM\n"
        "/me — информация о себе"
    ),
    UserRole.OPERATOR: (
        "<b>Команды оператора</b>\n"
        "/order — создать новую заявку\n"
        "/my_orders — мои активные заявки\n"
        "/me — информация о себе"
    ),
    UserRole.MANAGER: (
        "<b>Команды менеджера</b>\n"
        "/my_tasks — мои заявки\n"
        "/me — информация о себе"
    ),
}


@router.message(CommandStart())
async def cmd_start(message: Message, user: User | None) -> None:
    """Приветствие в зависимости от роли (или подсказка для незнакомца)."""
    if user is None or not user.is_active:
        await message.answer(NOT_REGISTERED)
        return

    name = html.escape(user.full_name or (message.from_user.full_name if message.from_user else ""))
    title = ROLE_TITLES.get(user.role, "пользователь")

    # Менеджер без телефона — просим поделиться контактом.
    if user.role == UserRole.MANAGER and not user.phone:
        await message.answer(
            f"👋 Здравствуйте, {name}!\n\n"
            "Чтобы завершить регистрацию, поделитесь, пожалуйста, "
            "номером телефона — он нужен для связи через систему звонков.",
            reply_markup=phone_request_keyboard(),
        )
        return

    await message.answer(
        f"👋 Здравствуйте, {name}!\n"
        f"Ваша роль: <b>{title}</b>.\n\n"
        f"{HELP_BY_ROLE.get(user.role, '')}"
    )


@router.message(Command("help"))
async def cmd_help(message: Message, user: User | None) -> None:
    """Справка по доступным командам."""
    if user is None or not user.is_active:
        await message.answer(NOT_REGISTERED)
        return
    await message.answer(HELP_BY_ROLE.get(user.role, "Нет доступных команд."))


@router.message(Command("me"))
async def cmd_me(message: Message, user: User | None, session: AsyncSession) -> None:
    """Информация о текущем пользователе."""
    if user is None or not user.is_active:
        await message.answer(NOT_REGISTERED)
        return

    title = ROLE_TITLES.get(user.role, "пользователь")
    lines = [
        "<b>Профиль</b>",
        f"Имя: {html.escape(user.full_name or '—')}",
        f"Роль: {title}",
    ]
    if user.tg_username:
        lines.append(f"Username: @{html.escape(user.tg_username)}")
    if user.region_id:
        region = await region_repo.get_by_id(session, user.region_id)
        lines.append(f"Регион: {html.escape(region.name) if region else '—'}")
    if user.role == UserRole.MANAGER:
        lines.append(f"Телефон: {'сохранён ✓' if user.phone else 'не указан ✗'}")

    await message.answer("\n".join(lines))
