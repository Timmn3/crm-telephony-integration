"""Базовые команды: /start, /help, /me."""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.types import User as TgUser
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.commands import set_commands_for_user
from app.bot.keyboards.inline import registration_keyboard
from app.bot.keyboards.reply import phone_request_keyboard, remove_keyboard
from app.config import get_settings
from app.db.models import User, UserRole
from app.db.repositories import group_repo, pending_repo

logger = logging.getLogger(__name__)

router = Router(name="start")

_settings = get_settings()

NOT_REGISTERED = (
    "👋 Здравствуйте!\n\n"
    "Вы пока не зарегистрированы в системе. "
    "Обратитесь к администратору для получения доступа."
)


def _unregistered_text(tg_user: TgUser | None) -> str:
    """Текст для незнакомца: показываем его ID для передачи администратору."""
    if tg_user is None:
        return NOT_REGISTERED
    return (
        "👋 Здравствуйте!\n\n"
        "Вы пока не зарегистрированы в системе.\n"
        f"Ваш Telegram ID: <code>{tg_user.id}</code>\n\n"
        "Передайте этот ID администратору для получения доступа. "
        "Администратор уже уведомлён о вашем обращении."
    )


async def _notify_admins_new_user(
    bot: Bot, session: AsyncSession, tg_user: TgUser
) -> None:
    """Шлёт админам карточку нового обращения с кнопками назначения роли.

    Дедуп через БД: если запись об обращении уже есть (pending_users), значит
    админы уже уведомлены — повторно не шлём (переживает рестарт бота).
    """
    if await pending_repo.get(session, tg_user.id) is not None:
        return
    await pending_repo.add(
        session,
        tg_id=tg_user.id,
        full_name=tg_user.full_name or "",
        tg_username=tg_user.username,
    )

    name = html.escape(tg_user.full_name or "—")
    uname = f"@{html.escape(tg_user.username)}" if tg_user.username else "—"
    text = (
        "🆕 <b>Новое обращение к боту</b>\n\n"
        f"Имя: {name}\n"
        f"Username: {uname}\n"
        f"Telegram ID: <code>{tg_user.id}</code>\n\n"
        "Назначить роль?"
    )
    keyboard = registration_keyboard(tg_user.id)
    for admin_id in _settings.admin_tg_ids:
        try:
            await bot.send_message(admin_id, text, reply_markup=keyboard)
        except (TelegramForbiddenError, TelegramBadRequest) as exc:
            logger.warning("Не удалось уведомить админа %s о новом обращении: %s", admin_id, exc)

ROLE_TITLES = {
    UserRole.ADMIN: "администратор",
    UserRole.OPERATOR: "оператор отдела продаж",
    UserRole.MANAGER: "выездной менеджер",
}

HELP_BY_ROLE = {
    UserRole.ADMIN: (
        "<b>Команды администратора</b>\n"
        "/add_operator — добавить оператора\n"
        "/add_manager — добавить менеджера (с привязкой к группе)\n"
        "/add_group — создать группу\n"
        "/edit_group — редактировать группу\n"
        "/delete_group — удалить группу\n"
        "/groups — список групп и их менеджеров\n"
        "/pending — необработанные обращения\n"
        "/users — список пользователей\n"
        "/remove_user — деактивировать пользователя\n"
        "/amo_fields — показать поля сделки/контакта amoCRM\n"
        "/set_amo_code — обновить код авторизации amoCRM\n"
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
async def cmd_start(
    message: Message, user: User | None, bot: Bot, session: AsyncSession
) -> None:
    """Приветствие в зависимости от роли (или подсказка для незнакомца)."""
    if user is None or not user.is_active:
        await message.answer(_unregistered_text(message.from_user))
        # Уведомляем админов только о действительно новых (не о деактивированных).
        if user is None and message.from_user is not None:
            await _notify_admins_new_user(bot, session, message.from_user)
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

    await set_commands_for_user(bot, user.tg_id, user.role)
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
    if user.group_id:
        group = await group_repo.get_by_id(session, user.group_id)
        if group:
            group_label = f"город: {html.escape(group.city)}, группа: {group.id}" if group.city else f"группа: {group.id}"
            lines.append(f"Группа: {group_label}")
        else:
            lines.append("Группа: —")
    if user.role == UserRole.MANAGER:
        lines.append(f"Телефон: {'сохранён ✓' if user.phone else 'не указан ✗'}")

    await message.answer("\n".join(lines))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отмена текущего пошагового диалога."""
    if await state.get_state() is None:
        await message.answer("Сейчас нечего отменять.")
        return
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=remove_keyboard())


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена по кнопке «Отмена»."""
    await state.clear()
    if callback.message:
        try:
            await callback.message.edit_text("Действие отменено.")
        except Exception:  # noqa: BLE001 — сообщение могло устареть
            pass
    await callback.answer()
