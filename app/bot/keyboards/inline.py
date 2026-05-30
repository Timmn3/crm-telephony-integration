"""Inline-клавиатуры и формат callback_data.

Формат callback_data: ``action:order_id`` или ``action:order_id:extra``.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.db.models import Order, OrderStatus, Region, User


def regions_keyboard(regions: list[Region], prefix: str) -> InlineKeyboardMarkup:
    """Кнопки выбора региона. callback_data: ``{prefix}:{region_id}``."""
    builder = InlineKeyboardBuilder()
    for region in regions:
        builder.button(text=region.name, callback_data=f"{prefix}:{region.id}")
    builder.adjust(2)
    return builder.as_markup()


def managers_keyboard(
    managers: list[User], order_id: int, prefix: str = "select_manager"
) -> InlineKeyboardMarkup:
    """Кнопки выбора менеджера. callback_data: ``{prefix}:{user_id}:{order_id}``."""
    builder = InlineKeyboardBuilder()
    for manager in managers:
        title = manager.full_name or (f"@{manager.tg_username}" if manager.tg_username else str(manager.tg_id))
        builder.button(
            text=title,
            callback_data=f"{prefix}:{manager.id}:{order_id}",
        )
    builder.adjust(1)
    return builder.as_markup()


def send_target_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Выбор: отправить всем региона или выбрать конкретного менеджера."""
    builder = InlineKeyboardBuilder()
    builder.button(text="📢 Всем в регион", callback_data=f"send_all:{order_id}")
    builder.button(text="👤 Выбрать менеджера", callback_data=f"send_pick:{order_id}")
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
    """Кнопки на карточке заявки у менеджера в зависимости от статуса."""
    builder = InlineKeyboardBuilder()
    status = order.status

    if status == OrderStatus.SENT:
        builder.button(text="✅ Беру заявку", callback_data=f"take_order:{order.id}")
    elif status == OrderStatus.TAKEN:
        builder.button(text="📞 Запросить звонок клиенту", callback_data=f"request_call:{order.id}")
        builder.button(text="✔️ Закрыть заявку", callback_data=f"complete_order:{order.id}")
    elif status == OrderStatus.CALL_REQUESTED:
        # Ожидание одобрения — кнопок действий нет.
        return None
    elif status == OrderStatus.CALL_APPROVED:
        builder.button(text="📞 Позвонить клиенту", callback_data=f"make_call:{order.id}")
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


def skip_reason_keyboard(order_id: int) -> InlineKeyboardMarkup:
    """Кнопка «Без причины» при отклонении звонка."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="Без причины", callback_data=f"reject_noreason:{order_id}"
            )
        ]]
    )
