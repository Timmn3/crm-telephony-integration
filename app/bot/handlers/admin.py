"""Обработчики команд администратора.

Команды: /add_region, /regions, /add_operator, /add_manager, /users, /remove_user.

Примечание про Telegram: бот не может узнать tg_id по @username и не может
написать пользователю первым, пока тот сам не обратится к боту. Поэтому
основной способ добавления — по числовому Telegram ID (пользователь сначала
пишет боту /start и сообщает свой ID администратору). @username поддерживается
только для пользователей, уже известных боту.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.role import IsAdmin
from app.bot.keyboards.inline import cancel_keyboard, regions_keyboard
from app.bot.keyboards.reply import phone_request_keyboard
from app.bot.states import AddManager, AddOperator, AddRegion, RemoveUser
from app.db.models import User, UserRole
from app.db.repositories import region_repo, user_repo
from app.services.amocrm import AmoCRMClient, AmoCRMError

logger = logging.getLogger(__name__)

router = Router(name="admin")

ROLE_RU = {
    UserRole.ADMIN: "администратор",
    UserRole.OPERATOR: "оператор",
    UserRole.MANAGER: "менеджер",
}


# --------------------------------------------------------------------------- #
# Вспомогательные функции
# --------------------------------------------------------------------------- #

async def _resolve_target(
    session: AsyncSession, identifier: str
) -> tuple[User | None, int | None, str | None]:
    """Разрешает идентификатор (@username или числовой tg_id).

    Возвращает (existing_user, tg_id, error). Если ошибка — error заполнен.
    existing_user может быть None при числовом id незнакомого пользователя.
    """
    identifier = identifier.strip()
    if not identifier:
        return None, None, "Пустой идентификатор."

    if identifier.startswith("@"):
        user = await user_repo.get_by_username(session, identifier)
        if user is None:
            return None, None, (
                "Пользователь с таким @username боту неизвестен. "
                "Пусть он сначала напишет боту /start, либо укажите числовой Telegram ID."
            )
        return user, user.tg_id, None

    if identifier.lstrip("-").isdigit():
        tg_id = int(identifier)
        user = await user_repo.get_by_tg_id(session, tg_id)
        return user, tg_id, None

    return None, None, "Не похоже на Telegram ID (число) или @username."


async def _notify_new_user(bot: Bot, tg_id: int, text: str, **kwargs) -> bool:
    """Пытается отправить уведомление новому пользователю. True при успехе."""
    try:
        await bot.send_message(tg_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось уведомить tg_id=%s: %s", tg_id, exc)
        return False


# --------------------------------------------------------------------------- #
# Регионы
# --------------------------------------------------------------------------- #

@router.message(Command("add_region"), IsAdmin)
async def add_region_start(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    if command.args:
        await _create_region(message, command.args.strip(), session)
        return
    await state.set_state(AddRegion.waiting_name)
    await message.answer("Введите название региона:", reply_markup=cancel_keyboard())


@router.message(AddRegion.waiting_name)
async def add_region_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _create_region(message, (message.text or "").strip(), session)


async def _create_region(message: Message, name: str, session: AsyncSession) -> None:
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    existing = await region_repo.get_by_name(session, name)
    if existing is not None:
        await message.answer(f"Регион «{html.escape(name)}» уже существует.")
        return
    region = await region_repo.create(session, name)
    await message.answer(f"✅ Регион «{html.escape(region.name)}» создан (id={region.id}).")


@router.message(Command("regions"), IsAdmin)
async def list_regions(message: Message, session: AsyncSession) -> None:
    regions = await region_repo.list_all(session)
    if not regions:
        await message.answer("Регионов пока нет. Создайте: /add_region <название>")
        return
    lines = ["<b>Регионы:</b>"]
    for r in regions:
        mark = "✅" if r.is_active else "🚫"
        lines.append(f"{mark} #{r.id} {html.escape(r.name)}")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# Операторы
# --------------------------------------------------------------------------- #

@router.message(Command("add_operator"), IsAdmin)
async def add_operator_start(
    message: Message, command: CommandObject, state: FSMContext,
    session: AsyncSession, bot: Bot,
) -> None:
    if command.args:
        await state.clear()
        await _create_operator(message, command.args.strip(), session, bot)
        return
    await state.set_state(AddOperator.waiting_tg_id)
    await message.answer(
        "Введите Telegram ID нового оператора (число) или @username, "
        "если он уже писал боту.\n\n"
        "ℹ️ Узнать свой ID пользователь может, написав боту /start.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddOperator.waiting_tg_id)
async def add_operator_id(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    await state.clear()
    await _create_operator(message, (message.text or "").strip(), session, bot)


async def _create_operator(
    message: Message, identifier: str, session: AsyncSession, bot: Bot
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        return

    if user is not None:
        await user_repo.set_role(session, user, UserRole.OPERATOR)
        await user_repo.set_active(session, user, True)
    else:
        user = await user_repo.create(
            session, tg_id=tg_id, role=UserRole.OPERATOR, full_name=""
        )

    await message.answer(f"✅ Оператор добавлен (tg_id={tg_id}).")
    delivered = await _notify_new_user(
        bot, tg_id,
        "✅ Вам выдан доступ <b>оператора</b>. Отправьте /start, чтобы начать работу.",
    )
    if not delivered:
        await message.answer(
            "⚠️ Не удалось отправить уведомление пользователю — "
            "он ещё не начинал диалог с ботом. Попросите его написать боту /start."
        )


# --------------------------------------------------------------------------- #
# Менеджеры
# --------------------------------------------------------------------------- #

@router.message(Command("add_manager"), IsAdmin)
async def add_manager_start(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    if command.args:
        await _ask_manager_region(message, command.args.strip(), state, session)
        return
    await state.set_state(AddManager.waiting_tg_id)
    await message.answer(
        "Введите Telegram ID нового менеджера (число) или @username, "
        "если он уже писал боту.\n\n"
        "ℹ️ Узнать свой ID пользователь может, написав боту /start.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddManager.waiting_tg_id)
async def add_manager_id(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    await _ask_manager_region(message, (message.text or "").strip(), state, session)


async def _ask_manager_region(
    message: Message, identifier: str, state: FSMContext, session: AsyncSession
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        await state.clear()
        return

    regions = await region_repo.list_active(session)
    if not regions:
        await message.answer("Сначала создайте хотя бы один регион: /add_region <название>")
        await state.clear()
        return

    await state.set_state(AddManager.waiting_region)
    await state.update_data(manager_tg_id=tg_id)
    await message.answer(
        "Выберите регион для менеджера:",
        reply_markup=regions_keyboard(regions, prefix="admin_region"),
    )


@router.callback_query(AddManager.waiting_region, F.data.startswith("admin_region:"))
async def add_manager_region(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    region_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    tg_id = data.get("manager_tg_id")
    await state.clear()

    region = await region_repo.get_by_id(session, region_id)
    if region is None or tg_id is None:
        await callback.message.edit_text("❌ Ошибка: регион не найден.")
        await callback.answer()
        return

    user = await user_repo.get_by_tg_id(session, tg_id)
    if user is not None:
        await user_repo.set_role(session, user, UserRole.MANAGER)
        await user_repo.set_region(session, user, region_id)
        await user_repo.set_active(session, user, True)
    else:
        user = await user_repo.create(
            session, tg_id=tg_id, role=UserRole.MANAGER, full_name="", region_id=region_id
        )

    await callback.message.edit_text(
        f"✅ Менеджер добавлен (tg_id={tg_id}), регион: {html.escape(region.name)}."
    )
    await callback.answer()

    delivered = await _notify_new_user(
        bot, tg_id,
        f"✅ Вас добавили как выездного менеджера, регион: <b>{html.escape(region.name)}</b>.\n\n"
        "Пожалуйста, поделитесь номером телефона для связи через систему звонков.",
        reply_markup=phone_request_keyboard(),
    )
    if not delivered:
        await callback.message.answer(
            "⚠️ Не удалось отправить уведомление менеджеру — он ещё не писал боту. "
            "Попросите его написать /start: после этого бот попросит номер телефона."
        )


# --------------------------------------------------------------------------- #
# Список пользователей и деактивация
# --------------------------------------------------------------------------- #

@router.message(Command("users"), IsAdmin)
async def list_users(message: Message, session: AsyncSession) -> None:
    users = await user_repo.list_all(session)
    if not users:
        await message.answer("Пользователей пока нет.")
        return
    lines = ["<b>Пользователи:</b>"]
    for u in users:
        mark = "✅" if u.is_active else "🚫"
        role = ROLE_RU.get(u.role, u.role.value)
        name = html.escape(u.full_name or "—")
        uname = f" @{html.escape(u.tg_username)}" if u.tg_username else ""
        region = f", регион #{u.region_id}" if u.region_id else ""
        lines.append(f"{mark} {name}{uname} — {role}{region} (tg_id={u.tg_id})")
    await message.answer("\n".join(lines))


@router.message(Command("remove_user"), IsAdmin)
async def remove_user_start(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    if command.args:
        await state.clear()
        await _deactivate_user(message, command.args.strip(), session)
        return
    await state.set_state(RemoveUser.waiting_identifier)
    await message.answer(
        "Введите Telegram ID или @username пользователя для деактивации:",
        reply_markup=cancel_keyboard(),
    )


@router.message(RemoveUser.waiting_identifier)
async def remove_user_id(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _deactivate_user(message, (message.text or "").strip(), session)


async def _deactivate_user(message: Message, identifier: str, session: AsyncSession) -> None:
    user, _tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        return
    if user is None:
        await message.answer("Пользователь не найден в системе.")
        return
    if user.role == UserRole.ADMIN:
        await message.answer("Нельзя деактивировать администратора через эту команду.")
        return
    await user_repo.set_active(session, user, False)
    await message.answer(
        f"🚫 Пользователь {html.escape(user.full_name or str(user.tg_id))} деактивирован."
    )


# --------------------------------------------------------------------------- #
# Поля amoCRM
# --------------------------------------------------------------------------- #

@router.message(Command("amo_fields"), IsAdmin)
async def amo_fields(message: Message, session: AsyncSession) -> None:
    """Показывает кастомные поля сделок и контактов amoCRM с их ID.

    Помогает настроить AMO_ADDRESS_FIELD_ID / AMO_PHONE_FIELD_ID в .env.
    """
    client = AmoCRMClient(session)
    try:
        fields = await client.list_custom_fields()
    except AmoCRMError as exc:
        await message.answer(f"❌ Ошибка amoCRM: {html.escape(str(exc))}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка при получении полей amoCRM")
        await message.answer("❌ Не удалось получить поля amoCRM (см. логи).")
        return

    lines: list[str] = []
    titles = {"leads": "Поля сделки", "contacts": "Поля контакта"}
    for entity, title in titles.items():
        lines.append(f"<b>{title}:</b>")
        items = fields.get(entity, [])
        if not items:
            lines.append("  (нет полей или нет доступа)")
        for f in items:
            code = f" code={html.escape(f.code)}" if f.code else ""
            lines.append(f"  #{f.id} {html.escape(f.name)} [{html.escape(f.field_type)}]{code}")
        lines.append("")

    text = "\n".join(lines).strip()
    # Telegram ограничивает длину сообщения ~4096 символов.
    for chunk_start in range(0, len(text), 3500):
        await message.answer(text[chunk_start:chunk_start + 3500])
