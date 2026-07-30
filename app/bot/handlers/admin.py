"""Обработчики команд директора.

Команды: /add_group, /groups, /edit_group, /delete_group, /add_manager,
/add_admin, /set_extension, /users, /remove_user, /amo_fields, /set_amo_code.

Примечание про Telegram: бот не может узнать tg_id по @username и не может
написать пользователю первым, пока тот сам не обратится к боту. Способы добавить
администратора: переслать боту его сообщение (из пересылки берём tg_id) либо ввести
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

from app.bot.commands import set_commands_for_user
from app.bot.utils import format_group_lines
from app.bot.filters.role import IsDirector
from app.bot.keyboards.inline import (
    cancel_keyboard,
    confirm_delete_group_keyboard,
    edit_group_field_keyboard,
    groups_keyboard,
    registration_keyboard,
)
from app.bot.keyboards.reply import phone_request_keyboard
from app.bot.states import (
    AddAdmin,
    AddGroup,
    AddManager,
    DeleteGroup,
    EditGroup,
    RemoveUser,
    SetAmoCode,
    SetExtension,
)
from app.db.models import User, UserRole
from app.db.repositories import group_repo, order_repo, pending_repo, user_repo
from app.services.amocrm import AmoCRMClient, AmoCRMError

logger = logging.getLogger(__name__)

router = Router(name="admin")

ROLE_RU = {
    UserRole.DIRECTOR: "директор",
    UserRole.MANAGER: "менеджер",
    UserRole.ADMIN: "администратор",
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

@router.message(Command("add_group"), IsDirector)
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


@router.message(Command("groups"), IsDirector)
async def list_groups(message: Message, session: AsyncSession) -> None:
    groups = await group_repo.list_all(session)
    if not groups:
        await message.answer("Групп пока нет. Создайте: /add_group <название> <город>")
        return
    lines = ["<b>Группы:</b>"]
    for g in sorted(groups, key=lambda x: x.id):
        mark = "✅" if g.is_active else "🚫"
        admins = await user_repo.list_active_admins_by_group(session, g.id)
        if admins:
            who = ", ".join(
                a.full_name or (f"@{a.tg_username}" if a.tg_username else str(a.tg_id))
                for a in admins
            )
        else:
            who = "нет админов"
        lines.append(
            f"{mark} #{g.id} <b>{html.escape(g.name)}</b> "
            f"({html.escape(g.city or '—')}) — {html.escape(who)}"
        )
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# Редактирование группы
# --------------------------------------------------------------------------- #

@router.message(Command("edit_group"), IsDirector)
async def edit_group_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    groups = await group_repo.list_all(session)
    if not groups:
        await message.answer("Групп пока нет. Создайте: /add_group")
        return
    await state.set_state(EditGroup.waiting_group)
    await message.answer(
        "Выберите группу для редактирования:",
        reply_markup=groups_keyboard(sorted(groups, key=lambda g: g.id), prefix="admin_edit_group"),
    )


@router.callback_query(EditGroup.waiting_group, F.data.startswith("admin_edit_group:"))
async def edit_group_pick(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await state.clear()
        await callback.message.edit_text("❌ Группа не найдена.")
        await callback.answer()
        return
    await state.update_data(edit_group_id=group_id)
    await state.set_state(EditGroup.waiting_field)
    await callback.message.edit_text(
        f"Группа <b>{html.escape(group.name)}</b> (город: {html.escape(group.city or '—')}).\n"
        "Что изменить?",
        reply_markup=edit_group_field_keyboard(),
    )
    await callback.answer()


@router.callback_query(EditGroup.waiting_field, F.data.startswith("egfield:"))
async def edit_group_field(callback: CallbackQuery, state: FSMContext) -> None:
    field = callback.data.split(":")[1]
    if field == "cancel":
        await state.clear()
        await callback.message.edit_text("Отменено.")
        await callback.answer()
        return
    await state.update_data(edit_field=field)
    await state.set_state(EditGroup.waiting_value)
    prompt = "Введите новое название группы:" if field == "name" else "Введите новый город:"
    await callback.message.edit_text(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(EditGroup.waiting_value)
async def edit_group_value(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    group_id = data.get("edit_group_id")
    field = data.get("edit_field")
    new_value = (message.text or "").strip()

    if not new_value:
        await message.answer("Значение не может быть пустым. Введите снова или /cancel.")
        return

    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await state.clear()
        await message.answer("❌ Группа не найдена.")
        return

    if field == "name":
        existing = await group_repo.get_by_name(session, new_value)
        if existing is not None and existing.id != group.id:
            await message.answer(f"❌ Группа с названием «{html.escape(new_value)}» уже существует.")
            return
        await group_repo.update(session, group, name=new_value)
        await state.clear()
        await message.answer(f"✅ Название группы #{group.id} изменено на «{html.escape(group.name)}».")
    else:
        await group_repo.update(session, group, city=new_value)
        await state.clear()
        await message.answer(
            f"✅ Город группы «{html.escape(group.name)}» изменён на «{html.escape(group.city)}»."
        )


# --------------------------------------------------------------------------- #
# Удаление группы
# --------------------------------------------------------------------------- #

@router.message(Command("delete_group"), IsDirector)
async def delete_group_start(message: Message, state: FSMContext, session: AsyncSession) -> None:
    groups = await group_repo.list_all(session)
    if not groups:
        await message.answer("Групп нет — удалять нечего.")
        return
    await state.set_state(DeleteGroup.waiting_group)
    await message.answer(
        "Выберите группу для удаления:",
        reply_markup=groups_keyboard(sorted(groups, key=lambda g: g.id), prefix="admin_del_group"),
    )


@router.callback_query(DeleteGroup.waiting_group, F.data.startswith("admin_del_group:"))
async def delete_group_pick(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await callback.answer("Группа не найдена.", show_alert=True)
        await state.clear()
        return

    bound_users = await user_repo.list_by_group(session, group_id)
    active_orders = await order_repo.count_active_by_group(session, group_id)
    total_orders = await order_repo.count_total_by_group(session, group_id)

    lines = [
        f"⚠️ <b>Удаление группы «{html.escape(group.name)}»</b> (id={group.id})",
        f"Город: {html.escape(group.city or '—')}",
        "",
    ]
    if bound_users:
        names = ", ".join(
            html.escape(u.full_name or str(u.tg_id)) for u in bound_users
        )
        lines.append(f"👤 Привязанные пользователи: {names} — будут отвязаны")
    else:
        lines.append("👤 Привязанных пользователей нет")

    lines += [
        "",
        f"📋 Незакрытых заявок: <b>{active_orders}</b>",
        f"📋 Всего заявок в истории: {total_orders} (будут удалены)",
        "",
        "Это действие <b>необратимо</b>. Подтвердить?",
    ]

    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=confirm_delete_group_keyboard(group_id),
    )
    await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("confirm_del_group:"))
async def delete_group_confirm(callback: CallbackQuery, session: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await callback.answer("Группа уже удалена.", show_alert=True)
        await callback.message.edit_reply_markup(reply_markup=None)
        return

    unassigned = await user_repo.unassign_all_from_group(session, group_id)
    group_name = group.name
    await group_repo.delete(session, group)

    lines = [f"🗑 Группа «{html.escape(group_name)}» удалена."]
    if unassigned:
        names = ", ".join(html.escape(u.full_name or str(u.tg_id)) for u in unassigned)
        lines.append(f"Отвязаны пользователи: {names}.")
    await callback.message.edit_text("\n".join(lines), reply_markup=None)
    await callback.answer()


@router.callback_query(F.data == "cancel_del_group")
async def delete_group_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Удаление отменено.", reply_markup=None)
    await callback.answer()


# --------------------------------------------------------------------------- #
# Менеджеры
# --------------------------------------------------------------------------- #

@router.message(Command("add_manager"), IsDirector)
async def add_manager_start(
    message: Message, command: CommandObject, state: FSMContext,
    session: AsyncSession, bot: Bot,
) -> None:
    if command.args:
        await _resolve_manager_target(message, command.args.strip(), state, session, bot)
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
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    await state.clear()
    await _resolve_manager_target(message, (message.text or "").strip(), state, session, bot)


async def _resolve_manager_target(
    message: Message, identifier: str, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        await state.clear()
        return
    await state.clear()
    await _assign_manager_role(message, session, bot, tg_id)


async def _assign_manager_role(
    message: Message, session: AsyncSession, bot: Bot, tg_id: int
) -> None:
    """Выдаёт роль менеджера (создателя заявок). К группе менеджер не привязан."""
    user = await user_repo.get_by_tg_id(session, tg_id)
    if user is not None:
        await user_repo.set_role(session, user, UserRole.MANAGER)
        await user_repo.set_active(session, user, True)
    else:
        await user_repo.create(session, tg_id=tg_id, role=UserRole.MANAGER, full_name="")
    await pending_repo.delete(session, tg_id)

    await message.answer(f"✅ Менеджер добавлен (tg_id={tg_id}).")
    await set_commands_for_user(bot, tg_id, UserRole.MANAGER)
    delivered = await _notify_new_user(
        bot, tg_id,
        "✅ Вам выдан доступ <b>менеджера</b>. Отправьте /start, чтобы начать работу.",
    )
    if not delivered:
        await message.answer(
            "⚠️ Не удалось отправить уведомление пользователю — "
            "он ещё не начинал диалог с ботом. Попросите его написать боту /start."
        )


# --------------------------------------------------------------------------- #
# Администраторы
# --------------------------------------------------------------------------- #

@router.message(Command("add_admin"), IsDirector)
async def add_admin_start(
    message: Message, command: CommandObject, state: FSMContext, session: AsyncSession
) -> None:
    if command.args:
        await _resolve_admin_target(message, command.args.strip(), state, session)
        return
    await state.set_state(AddAdmin.waiting_target)
    await message.answer(
        "Перешлите сообщение от нового администратора или введите его @username / "
        "числовой Telegram ID.\n\n"
        "ℹ️ Пересланное сообщение позволяет боту узнать его ID автоматически.",
        reply_markup=cancel_keyboard(),
    )


@router.message(AddAdmin.waiting_target)
async def add_admin_target(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    # 1) Пересланное сообщение — берём отправителя.
    fwd = message.forward_from
    if fwd is not None:
        full_name = fwd.full_name or ""
        username = fwd.username
        existing = await user_repo.get_by_tg_id(session, fwd.id)
        await _offer_admin_groups(
            message, state, session,
            tg_id=fwd.id, full_name=full_name, username=username,
            existing=existing,
        )
        return
    # 2) Текст: @username или tg_id.
    await _resolve_admin_target(message, (message.text or "").strip(), state, session)


async def _resolve_admin_target(
    message: Message, identifier: str, state: FSMContext, session: AsyncSession
) -> None:
    user, tg_id, error = await _resolve_target(session, identifier)
    if error:
        await message.answer(f"❌ {error}")
        await state.clear()
        return
    await _offer_admin_groups(
        message, state, session,
        tg_id=tg_id,
        full_name=user.full_name if user else "",
        username=user.tg_username if user else None,
        existing=user,
    )


async def _offer_admin_groups(
    message: Message, state: FSMContext, session: AsyncSession,
    *, tg_id: int, full_name: str, username: str | None, existing: User | None,
) -> None:
    groups = await group_repo.list_active(session)
    if not groups:
        await message.answer("Сначала создайте хотя бы одну группу: /add_group <название> <город>")
        await state.clear()
        return
    await state.update_data(
        admin_tg_id=tg_id, admin_full_name=full_name, admin_username=username
    )
    await state.set_state(AddAdmin.waiting_group)
    await message.answer(
        "Выберите группу для администратора:",
        reply_markup=groups_keyboard(groups, prefix="admin_adm_group"),
    )


@router.callback_query(AddAdmin.waiting_group, F.data.startswith("admin_adm_group:"))
async def add_admin_group(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    data = await state.get_data()
    tg_id = data.get("admin_tg_id")
    if group is None or tg_id is None:
        await state.clear()
        await callback.message.edit_text("❌ Ошибка: группа не найдена.")
        await callback.answer()
        return

    # В группе может быть несколько выездных админов — просто добавляем нового.
    await _assign_admin(callback.message, state, session, bot, group_id)
    await callback.answer()


async def _assign_admin(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot, group_id: int
) -> None:
    data = await state.get_data()
    tg_id = data["admin_tg_id"]
    full_name = data.get("admin_full_name") or ""
    username = data.get("admin_username")
    await state.clear()

    group = await group_repo.get_by_id(session, group_id)
    user = await user_repo.get_by_tg_id(session, tg_id)
    existing_group_id = user.group_id if user is not None else None
    group_changed = existing_group_id != group_id

    if user is not None:
        await user_repo.set_role(session, user, UserRole.ADMIN)
        await user_repo.set_group(session, user, group_id)
        await user_repo.set_active(session, user, True)
    else:
        user = await user_repo.create(
            session, tg_id=tg_id, role=UserRole.ADMIN,
            full_name=full_name, tg_username=username, group_id=group_id,
        )
    await pending_repo.delete(session, tg_id)

    # Автоподстановка extension по группе (все админы группы делят один физический
    # SIM/добавочный). exclude_self только при РЕАЛЬНОЙ смене группы: пользователь
    # уже попадёт в list_active_admins_by_group(group_id) (set_group выше уже
    # выполнен), но его текущее значение принадлежит старой группе. Без этого флага
    # повторный /add_admin на том же человеке в той же группе стирал бы его же
    # корректный extension.
    known_extension = await user_repo.get_group_mango_extension(
        session, group_id,
        exclude_user_id=user.id if group_changed else None,
    )
    await user_repo.set_mango_extension(session, user, known_extension)
    extension_note = (
        f"ℹ️ Extension Mango подставлен автоматически по группе: "
        f"<code>{html.escape(known_extension)}</code>."
        if known_extension else
        "⚠️ Extension Mango для этой группы ещё не задан ни у кого — звонки "
        "не заработают, пока не выполните /set_extension для этой группы."
    )

    has_phone = bool(user.phone)
    group_info = format_group_lines(group) if group else f"Группа: #{group_id}"
    await message.edit_text(
        f"✅ Администратор добавлен (tg_id={tg_id}).\n{group_info}\n{extension_note}"
    )

    await set_commands_for_user(bot, tg_id, UserRole.ADMIN)
    text = f"✅ Вас назначили администратором.\n{group_info}\n\n"
    if has_phone:
        text += "Телефон уже сохранён — можно принимать заявки."
        delivered = await _notify_new_user(bot, tg_id, text)
    else:
        text += "Пожалуйста, поделитесь номером телефона для связи через систему звонков."
        delivered = await _notify_new_user(bot, tg_id, text, reply_markup=phone_request_keyboard())

    if not delivered:
        await message.answer(
            "⚠️ Не удалось отправить уведомление администратору — он ещё не писал боту. "
            "Попросите его написать /start: после этого бот попросит номер телефона."
        )


# --------------------------------------------------------------------------- #
# Extension Mango для группы
# --------------------------------------------------------------------------- #

@router.message(Command("set_extension"), IsDirector)
async def set_extension_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    groups = await group_repo.list_active(session)
    if not groups:
        await message.answer("Групп пока нет. Создайте: /add_group <название> <город>")
        return
    await state.set_state(SetExtension.waiting_group)
    await message.answer(
        "Выберите группу, для которой нужно задать/изменить extension Mango "
        "(будет применён ко всем активным администраторам группы):",
        reply_markup=groups_keyboard(groups, prefix="admin_set_ext_group"),
    )


@router.callback_query(SetExtension.waiting_group, F.data.startswith("admin_set_ext_group:"))
async def set_extension_pick_group(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await state.clear()
        await callback.message.edit_text("❌ Группа не найдена.")
        await callback.answer()
        return
    admins = await user_repo.list_active_admins_by_group(session, group_id)
    if not admins:
        await state.clear()
        await callback.message.edit_text(
            f"❌ В группе «{html.escape(group.name)}» нет активных администраторов.\n"
            "Сначала добавьте администратора: /add_admin, затем повторите /set_extension."
        )
        await callback.answer()
        return
    current = await user_repo.get_group_mango_extension(session, group_id)
    current_line = (
        f"Текущий extension: <code>{html.escape(current)}</code>."
        if current else "Extension пока не задан."
    )
    await state.update_data(set_ext_group_id=group_id)
    await state.set_state(SetExtension.waiting_value)
    await callback.message.edit_text(
        f"Группа <b>{html.escape(group.name)}</b> ({len(admins)} админ.).\n"
        f"{current_line}\n\nВведите новый extension (например 502):",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(SetExtension.waiting_value)
async def set_extension_value(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    group_id = data.get("set_ext_group_id")
    new_value = (message.text or "").strip()
    await state.clear()

    if not new_value or len(new_value) > 32:
        await message.answer(
            "Значение должно быть непустым и не длиннее 32 символов. "
            "Повторите: /set_extension"
        )
        return

    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await message.answer("❌ Группа не найдена.")
        return
    admins = await user_repo.list_active_admins_by_group(session, group_id)
    if not admins:
        await message.answer(f"❌ В группе «{html.escape(group.name)}» больше нет активных администраторов.")
        return

    for admin in admins:
        await user_repo.set_mango_extension(session, admin, new_value)

    names = ", ".join(a.full_name or str(a.tg_id) for a in admins)
    await message.answer(
        f"✅ Extension <code>{html.escape(new_value)}</code> установлен для группы "
        f"«{html.escape(group.name)}» ({len(admins)} чел.: {html.escape(names)})."
    )


# --------------------------------------------------------------------------- #
# Быстрое назначение роли из уведомления о новом обращении (/start незнакомца)
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("reg_mgr:"), IsDirector)
async def reg_assign_manager(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    """Кнопка «Менеджер» в карточке нового обращения — выдаём роль сразу."""
    tg_id = int(callback.data.split(":")[1])
    if callback.message:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await state.clear()
        await _assign_manager_role(callback.message, session, bot, tg_id)
    await callback.answer()


@router.callback_query(F.data.startswith("reg_adm:"), IsDirector)
async def reg_assign_admin(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    """Кнопка «Администратор» — запускаем выбор группы (далее штатный флоу AddAdmin)."""
    tg_id = int(callback.data.split(":")[1])
    existing = await user_repo.get_by_tg_id(session, tg_id)
    if callback.message:
        # Убираем кнопки уведомления, чтобы не нажали повторно.
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass
        await _offer_admin_groups(
            callback.message, state, session,
            tg_id=tg_id,
            full_name=existing.full_name if existing else "",
            username=existing.tg_username if existing else None,
            existing=existing,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("reg_ignore:"), IsDirector)
async def reg_ignore(callback: CallbackQuery, session: AsyncSession) -> None:
    """Кнопка «Пропустить» — убираем карточку и обращение из очереди."""
    tg_id = int(callback.data.split(":")[1])
    await pending_repo.delete(session, tg_id)
    if callback.message:
        await callback.message.edit_text(
            "Обращение пропущено. Назначить роль позже: /add_manager или /add_admin."
        )
    await callback.answer()


@router.message(Command("pending"), IsDirector)
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

@router.message(Command("users"), IsDirector)
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


@router.message(Command("remove_user"), IsDirector)
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
    if user.role == UserRole.DIRECTOR:
        await message.answer("Нельзя деактивировать директора через эту команду.")
        return
    await user_repo.set_active(session, user, False)
    # Деактивированный пользователь открепляется от группы.
    if user.group_id is not None:
        await user_repo.set_group(session, user, None)
    await message.answer(
        f"🚫 Пользователь {html.escape(user.full_name or str(user.tg_id))} деактивирован."
    )


# --------------------------------------------------------------------------- #
# amoCRM
# --------------------------------------------------------------------------- #

@router.message(Command("amo_fields"), IsDirector)
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


@router.message(Command("set_amo_code"), IsDirector)
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
