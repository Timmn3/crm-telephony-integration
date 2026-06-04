"""Обработчики команд администратора.

Команды: /add_group, /groups, /add_operator, /add_manager, /users,
/remove_user, /amo_fields, /set_amo_code.

Примечание про Telegram: бот не может узнать tg_id по @username и не может
написать пользователю первым, пока тот сам не обратится к боту. Способы добавить
менеджера: переслать боту его сообщение (из пересылки берём tg_id) либо ввести
числовой Telegram ID. @username работает только для уже известных боту.
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
from app.bot.keyboards.inline import (
    cancel_keyboard,
    confirm_replace_keyboard,
    groups_keyboard,
    registration_keyboard,
)
from app.bot.keyboards.reply import phone_request_keyboard
from app.bot.states import AddGroup, AddManager, AddOperator, RemoveUser, SetAmoCode
from app.db.models import User, UserRole
from app.db.repositories import group_repo, pending_repo, user_repo
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

    Возвращает (existing_user, tg_id, error). existing_user может быть None при
    числовом id незнакомого пользователя.
    """
    identifier = identifier.strip()
    if not identifier:
        return None, None, "Пустой идентификатор."

    if identifier.startswith("@"):
        user = await user_repo.get_by_username(session, identifier)
        if user is None:
            return None, None, (
                "Пользователь с таким @username боту неизвестен. Перешлите его "
                "сообщение боту или укажите числовой Telegram ID."
            )
        return user, user.tg_id, None

    if identifier.lstrip("-").isdigit():
        tg_id = int(identifier)
        user = await user_repo.get_by_tg_id(session, tg_id)
        return user, tg_id, None

    return None, None, "Не похоже на Telegram ID (число) или @username."


async def _notify_new_user(bot: Bot, tg_id: int, text: str, **kwargs) -> bool:
    """Пытается отправить уведомление пользователю. True при успехе."""
    try:
        await bot.send_message(tg_id, text, **kwargs)
        return True
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось уведомить tg_id=%s: %s", tg_id, exc)
        return False


# --------------------------------------------------------------------------- #
# Группы
# --------------------------------------------------------------------------- #

@router.message(Command("add_group"), IsAdmin)
async def add_group_start(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    if command.args:
        # «/add_group Группа-4 Москва» — первое слово название, остальное город.
        parts = command.args.strip().split(maxsplit=1)
        name = parts[0]
        city = parts[1] if len(parts) > 1 else ""
        await _create_group(message, name, city, session)
        return
    await state.set_state(AddGroup.waiting_name)
    await message.answer("Введите название группы (например «Группа-4»):",
                         reply_markup=cancel_keyboard())


@router.message(AddGroup.waiting_name)
async def add_group_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Название не может быть пустым. Попробуйте снова или /cancel.")
        return
    await state.update_data(group_name=name)
    await state.set_state(AddGroup.waiting_city)
    await message.answer("Введите город группы (например «Москва»):",
                         reply_markup=cancel_keyboard())


@router.message(AddGroup.waiting_city)
async def add_group_city(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    await state.clear()
    await _create_group(message, data.get("group_name", ""), (message.text or "").strip(), session)


async def _create_group(message: Message, name: str, city: str, session: AsyncSession) -> None:
    if not name:
        await message.answer("Название не может быть пустым.")
        return
    existing = await group_repo.get_by_name(session, name)
    if existing is not None:
        await message.answer(f"Группа «{html.escape(name)}» уже существует.")
        return
    group = await group_repo.create(session, name, city)
    await message.answer(
        f"✅ Группа «{html.escape(group.name)}» создана "
        f"(город: {html.escape(group.city or '—')}, id={group.id})."
    )


@router.message(Command("groups"), IsAdmin)
async def list_groups(message: Message, session: AsyncSession) -> None:
    groups = await group_repo.list_all(session)
    if not groups:
        await message.answer("Групп пока нет. Создайте: /add_group <название> <город>")
        return
    lines = ["<b>Группы:</b>"]
    for g in groups:
        mark = "✅" if g.is_active else "🚫"
        manager = await user_repo.get_active_manager_by_group(session, g.id)
        who = "свободна"
        if manager is not None:
            who = manager.full_name or (
                f"@{manager.tg_username}" if manager.tg_username else str(manager.tg_id)
            )
        lines.append(
            f"{mark} #{g.id} <b>{html.escape(g.name)}</b> "
            f"({html.escape(g.city or '—')}) — {html.escape(who)}"
        )
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
        await _resolve_operator_target(message, command.args.strip(), state, session)
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
    await _resolve_operator_target(message, (message.text or "").strip(), state, session)


async def _resolve_operator_target(
    message: Message, identifier: str, state: FSMContext, session: AsyncSession
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        await state.clear()
        return
    await _offer_operator_groups(message, state, session, tg_id=tg_id)


async def _offer_operator_groups(
    message: Message, state: FSMContext, session: AsyncSession, *, tg_id: int
) -> None:
    groups = await group_repo.list_active(session)
    if not groups:
        await message.answer("Сначала создайте хотя бы одну группу: /add_group <название> <город>")
        await state.clear()
        return
    await state.update_data(operator_tg_id=tg_id)
    await state.set_state(AddOperator.waiting_group)
    await message.answer(
        "Выберите группу для оператора:",
        reply_markup=groups_keyboard(groups, prefix="admin_op_group"),
    )


@router.callback_query(AddOperator.waiting_group, F.data.startswith("admin_op_group:"))
async def add_operator_group(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    data = await state.get_data()
    tg_id = data.get("operator_tg_id")
    await state.clear()

    if group is None or tg_id is None:
        await callback.message.edit_text("❌ Ошибка: данные не найдены.")
        await callback.answer()
        return

    await _assign_operator(callback.message, session, bot, tg_id, group)
    await callback.answer()


async def _assign_operator(
    message: Message, session: AsyncSession, bot: Bot, tg_id: int, group
) -> None:
    user = await user_repo.get_by_tg_id(session, tg_id)
    if user is not None:
        await user_repo.set_role(session, user, UserRole.OPERATOR)
        await user_repo.set_group(session, user, group.id)
        await user_repo.set_active(session, user, True)
    else:
        await user_repo.create(
            session, tg_id=tg_id, role=UserRole.OPERATOR, full_name="", group_id=group.id
        )
    await pending_repo.delete(session, tg_id)

    gname = html.escape(group.name)
    await message.edit_text(f"✅ Оператор добавлен (tg_id={tg_id}), группа: {gname}.")
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
        await _resolve_manager_target(message, command.args.strip(), state, session)
        return
    await state.set_state(AddManager.waiting_target)
    await message.answer(
        "Перешлите сообщение от нового менеджера или введите его @username / "
        "числовой Telegram ID.\n\n"
        "ℹ️ Пересланное сообщение позволяет боту узнать его ID автоматически.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddManager.waiting_target)
async def add_manager_target(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    # 1) Пересланное сообщение — берём отправителя.
    fwd = message.forward_from
    if fwd is not None:
        full_name = fwd.full_name or ""
        username = fwd.username
        existing = await user_repo.get_by_tg_id(session, fwd.id)
        await _offer_groups(
            message, state, session,
            tg_id=fwd.id, full_name=full_name, username=username,
            existing=existing,
        )
        return
    # 2) Текст: @username или tg_id.
    await _resolve_manager_target(message, (message.text or "").strip(), state, session)


async def _resolve_manager_target(
    message: Message, identifier: str, state: FSMContext, session: AsyncSession
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        await state.clear()
        return
    await _offer_groups(
        message, state, session,
        tg_id=tg_id,
        full_name=user.full_name if user else "",
        username=user.tg_username if user else None,
        existing=user,
    )


async def _offer_groups(
    message: Message, state: FSMContext, session: AsyncSession,
    *, tg_id: int, full_name: str, username: str | None, existing: User | None,
) -> None:
    groups = await group_repo.list_active(session)
    if not groups:
        await message.answer("Сначала создайте хотя бы одну группу: /add_group <название> <город>")
        await state.clear()
        return
    await state.update_data(
        manager_tg_id=tg_id, manager_full_name=full_name, manager_username=username
    )
    await state.set_state(AddManager.waiting_group)
    await message.answer(
        "Выберите группу для менеджера:",
        reply_markup=groups_keyboard(groups, prefix="admin_group"),
    )


@router.callback_query(AddManager.waiting_group, F.data.startswith("admin_group:"))
async def add_manager_group(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    data = await state.get_data()
    tg_id = data.get("manager_tg_id")
    if group is None or tg_id is None:
        await state.clear()
        await callback.message.edit_text("❌ Ошибка: группа не найдена.")
        await callback.answer()
        return

    current = await user_repo.get_active_manager_by_group(session, group_id)
    if current is not None and current.tg_id != tg_id:
        # Группа занята — спрашиваем подтверждение замены.
        await state.update_data(group_id=group_id)
        await state.set_state(AddManager.waiting_replace)
        cur_name = current.full_name or (
            f"@{current.tg_username}" if current.tg_username else str(current.tg_id)
        )
        await callback.message.edit_text(
            f"В группе «{html.escape(group.name)}» сейчас работает "
            f"<b>{html.escape(cur_name)}</b>. Заменить его на нового менеджера?",
            reply_markup=confirm_replace_keyboard(tg_id, group_id),
        )
        await callback.answer()
        return

    await _assign_manager(callback.message, state, session, bot, group_id)
    await callback.answer()


@router.callback_query(AddManager.waiting_replace, F.data.startswith("confirm_replace:"))
async def add_manager_replace(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    data = await state.get_data()
    group_id = data.get("group_id")
    if group_id is None:
        await state.clear()
        await callback.message.edit_text("❌ Ошибка: группа не определена.")
        await callback.answer()
        return
    # Отвязываем прежнего менеджера (group_id=NULL), оставляя активным.
    previous = await user_repo.unassign_from_group(session, group_id)
    if previous is not None:
        await _notify_new_user(
            bot, previous.tg_id,
            "ℹ️ Вы откреплены от группы. Новые заявки по ней приходить не будут.",
        )
    await _assign_manager(callback.message, state, session, bot, group_id)
    await callback.answer()


async def _assign_manager(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, group_id: int
) -> None:
    data = await state.get_data()
    tg_id = data["manager_tg_id"]
    full_name = data.get("manager_full_name") or ""
    username = data.get("manager_username")
    await state.clear()

    group = await group_repo.get_by_id(session, group_id)
    user = await user_repo.get_by_tg_id(session, tg_id)
    if user is not None:
        await user_repo.set_role(session, user, UserRole.MANAGER)
        await user_repo.set_group(session, user, group_id)
        await user_repo.set_active(session, user, True)
    else:
        user = await user_repo.create(
            session, tg_id=tg_id, role=UserRole.MANAGER,
            full_name=full_name, tg_username=username, group_id=group_id,
        )
    await pending_repo.delete(session, tg_id)

    has_phone = bool(user.phone)
    if group:
        group_label = f"город: {html.escape(group.city)}, группа: {group.id}" if group.city else f"группа: {group.id}"
    else:
        group_label = f"группа: {group_id}"
    await message.edit_text(f"✅ Менеджер добавлен (tg_id={tg_id}), {group_label}.")

    text = (
        f"✅ Вас назначили выездным менеджером, {group_label}.\n\n"
    )
    if has_phone:
        text += "Телефон уже сохранён — можно принимать заявки."
        delivered = await _notify_new_user(bot, tg_id, text)
    else:
        text += "Пожалуйста, поделитесь номером телефона для связи через систему звонков."
        delivered = await _notify_new_user(bot, tg_id, text, reply_markup=phone_request_keyboard())

    if not delivered:
        await message.answer(
            "⚠️ Не удалось отправить уведомление менеджеру — он ещё не писал боту. "
            "Попросите его написать /start: после этого бот попросит номер телефона."
        )


# --------------------------------------------------------------------------- #
# Быстрое назначение роли из уведомления о новом обращении (/start незнакомца)
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("reg_op:"), IsAdmin)
async def reg_assign_operator(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Кнопка «Оператор» в карточке нового обращения — запускаем выбор группы."""
    tg_id = int(callback.data.split(":")[1])
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _offer_operator_groups(callback.message, state, session, tg_id=tg_id)
    await callback.answer()


@router.callback_query(F.data.startswith("reg_mgr:"), IsAdmin)
async def reg_assign_manager(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Кнопка «Менеджер» — запускаем выбор группы (далее штатный флоу AddManager)."""
    tg_id = int(callback.data.split(":")[1])
    existing = await user_repo.get_by_tg_id(session, tg_id)
    if callback.message:
        # Убираем кнопки уведомления, чтобы не нажали повторно.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _offer_groups(
            callback.message, state, session,
            tg_id=tg_id,
            full_name=existing.full_name if existing else "",
            username=existing.tg_username if existing else None,
            existing=existing,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_ignore:"), IsAdmin)
async def reg_ignore(callback: CallbackQuery, session: AsyncSession) -> None:
    """Кнопка «Пропустить» — убираем карточку и обращение из очереди."""
    tg_id = int(callback.data.split(":")[1])
    await pending_repo.delete(session, tg_id)
    if callback.message:
        await callback.message.edit_text(
            "Обращение пропущено. Назначить роль позже: /add_operator или /add_manager."
        )
    await callback.answer()


@router.message(Command("pending"), IsAdmin)
async def list_pending(message: Message, session: AsyncSession) -> None:
    """Список необработанных обращений с кнопками назначения роли."""
    pending = await pending_repo.list_all(session)
    if not pending:
        await message.answer("Очередь обращений пуста.")
        return
    await message.answer(f"<b>Ожидают назначения роли: {len(pending)}</b>")
    for p in pending:
        name = html.escape(p.full_name or "—")
        uname = f"@{html.escape(p.tg_username)}" if p.tg_username else "—"
        text = (
            "🆕 <b>Обращение</b>\n"
            f"Имя: {name}\n"
            f"Username: {uname}\n"
            f"Telegram ID: <code>{p.tg_id}</code>"
        )
        await message.answer(text, reply_markup=registration_keyboard(p.tg_id))


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
        group = ""
        if u.group_id:
            g = await group_repo.get_by_id(session, u.group_id)
            group = f", группа {html.escape(g.name)}" if g else f", группа #{u.group_id}"
        lines.append(f"{mark} {name}{uname} — {role}{group} (tg_id={u.tg_id})")
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
    # Деактивированный менеджер освобождает группу.
    if user.group_id is not None:
        await user_repo.set_group(session, user, None)
    await message.answer(
        f"🚫 Пользователь {html.escape(user.full_name or str(user.tg_id))} деактивирован."
    )


# --------------------------------------------------------------------------- #
# amoCRM
# --------------------------------------------------------------------------- #

@router.message(Command("amo_fields"), IsAdmin)
async def amo_fields(message: Message, session: AsyncSession) -> None:
    """Показывает кастомные поля сделок и контактов amoCRM с их ID."""
    client = AmoCRMClient(session)
    try:
        fields = await client.list_custom_fields()
    except AmoCRMError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
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
    for chunk_start in range(0, len(text), 3500):
        await message.answer(text[chunk_start:chunk_start + 3500])


@router.message(Command("set_amo_code"), IsAdmin)
async def set_amo_code(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    """Обновляет авторизацию amoCRM по новому коду (если refresh истёк)."""
    if command.args:
        await state.clear()
        await _apply_amo_code(message, command.args.strip(), session)
        return
    await state.set_state(SetAmoCode.waiting_code)
    await message.answer(
        "Введите новый код авторизации amoCRM (из настроек интеграции):",
        reply_markup=cancel_keyboard(),
    )


@router.message(SetAmoCode.waiting_code)
async def set_amo_code_value(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    await _apply_amo_code(message, (message.text or "").strip(), session)


async def _apply_amo_code(message: Message, code: str, session: AsyncSession) -> None:
    if not code:
        await message.answer("Код не может быть пустым.")
        return
    client = AmoCRMClient(session)
    if client.is_stub:
        await message.answer(
            "❌ Реквизиты интеграции amoCRM не заданы (subdomain/client_id/secret). "
            "Заполните их в .env и перезапустите бота."
        )
        return
    try:
        await client.exchange_code(code)
    except AmoCRMError as exc:
        await message.answer(f"❌ {html.escape(str(exc))}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка обмена кода авторизации amoCRM")
        await message.answer("❌ Не удалось обменять код (см. логи).")
        return
    await message.answer("✅ Авторизация amoCRM обновлена.")
