"""Inline-клавиатуры и формат callback_data.

Формат callback_data: ``action:id`` или ``action:id:extra``.

Примечание: при создании заявки select_group / skip_comment не несут order_id —
заявка создаётся в БД только в конце диалога (после комментария), id заранее нет.
Данные сделки до этого момента живут в FSM оператора.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Group, Order, OrderStatus


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
    groups: list[Group], manager_names: dict[int, str | None]
) -> InlineKeyboardMarkup:
    """Выбор группы оператором при создании заявки.

    Рядом с названием — имя текущего менеджера или «свободна».
    callback_data: ``select_group:{group_id}``.
    """
    builder = InlineKeyboardBuilder()
    for group in groups:
        who = manager_names.get(group.id)
        label = f"{group.name} ({who})" if who else f"{group.name} (свободна)"
        builder.button(text=label, callback_data=f"select_group:{group.id}")
    builder.adjust(2)
    return builder.as_markup()


def registration_keyboard(tg_id: int) -> InlineKeyboardMarkup:
    """Кнопки назначения роли в уведомлении админу о новом обращении.

    callback_data: ``reg_op:{tg_id}`` / ``reg_mgr:{tg_id}`` / ``reg_ignore:{tg_id}``.
    """
    builder = InlineKeyboardBuilder()
    builder.button(text="👔 Оператор", callback_data=f"reg_op:{tg_id}")
    builder.button(text="🚗 Менеджер", callback_data=f"reg_mgr:{tg_id}")
    builder.button(text="🚫 Пропустить", callback_data=f"reg_ignore:{tg_id}")
    builder.adjust(2, 1)
    return builder.as_markup()


def confirm_replace_keyboard(user_id: int, group_id: int) -> InlineKeyboardMarkup:
    """Подтверждение замены менеджера в занятой группе."""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, заменить", callback_data=f"confirm_replace:{user_id}:{group_id}")
    builder.button(text="❌ Отмена", callback_data="cancel")
    builder.adjust(2)
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
        builder.button(text="📞 Запросить звонок ещё раз", callback_data=f"request_call:{order.id}")
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
