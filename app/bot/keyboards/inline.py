"""Inline-клавиатуры и формат callback_data.

Формат callback_data: ``action:id`` или ``action:id:extra``.

Примечание: при создании заявки select_group / skip_comment не несут order_id —
заявка создаётся в БД только в конце диалога (после комментария), id заранее нет.
Данные сделки до этого момента живут в FSM оператора.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Group, Order, OrderStatus, User


def groups_keyboard(groups: list[Group], prefix: str) -> InlineKeyboardMarkup:
    """Простой выбор группы (для добавления менеджера админом).

    callback_data: ``{prefix}:{group_id}``.
    """
    builder = InlineKeyboardBuilder()
    for group in groups:
        builder.button(text=group.name, callback_data=f"{prefix}:{group.id}")
    builder.adjust(3)
    return builder.as_markup()


def groups_select_keyboard(
    groups: list[Group], admin_counts: dict[int, int]
) -> InlineKeyboardMarkup:
    """Выбор группы менеджером при создании заявки.

    Рядом с названием — число активных выездных администраторов группы.
    callback_data: ``select_group:{group_id}``.
    """
    builder = InlineKeyboardBuilder()
    for group in groups:
        count = admin_counts.get(group.id, 0)
        label = f"{group.name} ({count} адм.)" if count else f"{group.name} (нет админов)"
        builder.button(text=label, callback_data=f"select_group:{group.id}")
    builder.adjust(2)
    return builder.as_markup()


def admins_select_keyboard(admins: list[User]) -> InlineKeyboardMarkup:
    """Выбор конкретного выездного администратора группы при создании заявки.

    callback_data: ``select_admin:{tg_id}``.
    """
    builder = InlineKeyboardBuilder()
    for admin in admins:
        name = admin.full_name or (
            f"@{admin.tg_username}" if admin.tg_username else str(admin.tg_id)
        )
        builder.button(text=name, callback_data=f"select_admin:{admin.tg_id}")
    builder.adjust(1)
    return builder.as_markup()


def registration_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    """Кнопки назначения роли в уведомлении директору о новом обращении.

    callback_data: ``reg_mgr:{tg_id}`` / ``reg_adm:{tg_id}`` / ``reg_ignore:{tg_id}``.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="👔 Менеджер", callback_data=f"reg_mgr:{tg_id}")
    builder.button(text="🚗 Администратор", callback_data=f"reg_adm:{tg_id}")
    builder.button(text="🚫 Пропустить", callback_data=f"reg_ignore:{tg_id}")
    builder.adjust(2, 1)
    return builder.as_markup()


def skip_comment_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Без комментария» при создании заявки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="Без комментария", callback_data="skip_comment")
        ]]
    )


def edit_group_field_keyboard() -> InlineKeyboardMarkup:
    """Выбор поля для редактирования группы."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Название", callback_data="egfield:name")],
        [InlineKeyboardButton(text="🏙 Город",    callback_data="egfield:city")],
        [InlineKeyboardButton(text="❌ Отмена",   callback_data="egfield:cancel")],
    ])


def confirm_delete_group_keyboard(group_id: int) -> InlineKeyboardMarkup:
    """Подтверждение физического удаления группы."""
    builder = InlineKeyboardBuilder()
    builder.button(text="🗑 Подтвердить удаление", callback_data=f"confirm_del_group:{group_id}")
    builder.button(text="❌ Отмена", callback_data="cancel_del_group")
    builder.adjust(1)
    return builder.as_markup()


def cancel_keyboard(callback_data: str = "cancel") -> InlineKeyboardMarkup:
    """Кнопка отмены."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data)]]
    )


# --------------------------------------------------------------------------- #
# Клавиатуры карточки заявки (для менеджера и оператора)
# --------------------------------------------------------------------------- #

def manager_card_keyboard(order: Order) -> InlineKeyboardMarkup | None:
    """Кнопки на карточке заявки у менеджера в зависимости от статуса.

    Кнопка «Закрыть заявку» доступна на всех активных статусах.
    """
    builder = InlineKeyboardBuilder()
    status = order.status

    if status == OrderStatus.SENT:
        builder.button(text="📞 Запросить звонок клиенту", callback_data=f"request_call:{order.id}")
        builder.button(text="✔️ Закрыть заявку", callback_data=f"complete_order:{order.id}")
    elif status == OrderStatus.CALL_REQUESTED:
        # Ожидание одобрения — действий нет, только закрыть.
        builder.button(text="✔️ Закрыть заявку", callback_data=f"complete_order:{order.id}")
    elif status == OrderStatus.CALL_APPROVED:
        builder.button(text="📞 Позвонить клиенту", callback_data=f"make_call:{order.id}")
        builder.button(text="✔️ Закрыть заявку", callback_data=f"complete_order:{order.id}")
    elif status == OrderStatus.CALL_IN_PROGRESS:
        builder.button(text="📞 Позвонить клиенту", callback_data=f"make_call:{order.id}")
        builder.button(text="✔️ Закрыть заявку", callback_data=f"complete_order:{order.id}")
    else:
        return None

    builder.adjust(1)
    return builder.as_markup()


def operator_call_request_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Кнопки одобрить/отклонить звонок для оператора."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_call:{order_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_call:{order_id}")
    builder.adjust(2)
    return builder.as_markup()
