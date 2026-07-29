"""Бизнес-логика заявок: рендер карточки, отправка администратору, обновление.

КРИТИЧНО (безопасность): функция render_card НИКОГДА не включает client_phone.
Телефон клиента не попадает ни в текст карточки, ни в кнопки, ни в логи.

В v2 одна заявка = одно сообщение одному выездному админу (рассылки нет): менеджер
выбирает получателя при создании, поэтому координаты сообщения хранятся прямо на
Order (tg_chat_id/tg_message_id).
"""
from __future__ import annotations

import html
import logging
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import manager_card_keyboard
from app.db.models import Order, OrderStatus
from app.db.repositories import order_repo
from app.utils.phone import strip_phones

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Рендер карточки
# --------------------------------------------------------------------------- #

def status_note(order: Order) -> str | None:
    """Текстовая пометка статуса на карточке администратора."""
    return {
        OrderStatus.CALL_REQUESTED: "⏳ Звонок запрошен, ожидайте одобрения менеджера.",
        OrderStatus.CALL_APPROVED: "✅ Звонок одобрен.",
        OrderStatus.CALL_IN_PROGRESS: "📞 Звонок инициирован. Ожидайте — Mango перезвонит вам.",
        OrderStatus.COMPLETED: "✔️ Заявка закрыта.",
        OrderStatus.CANCELLED: "🚫 Заявка отменена.",
    }.get(order.status)


def render_card(order: Order, *, note: str | None = None) -> str:
    """Формирует HTML-текст карточки заявки. Без номера телефона клиента!

    Каждое поле дополнительно прогоняется через strip_phones (defense-in-depth):
    даже если телефон просочится в имя/адрес/комментарий — в текст он не попадёт.
    """
    name = strip_phones(order.client_name) or "—"
    lines = [
        f"📋 <b>Заявка #{order.amo_lead_id}</b>",
        "",
        f"<b>Клиент:</b> {html.escape(name)}",
    ]
    address = strip_phones(order.client_address)
    if address:
        lines.append(f"<b>Адрес:</b> {html.escape(address)}")
    comment = strip_phones(order.comment)
    if comment:
        lines.append(f"<b>Комментарий:</b> {html.escape(comment)}")
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Отправка и обновление карточки
# --------------------------------------------------------------------------- #

class DeliveryResult(str, Enum):
    """Причина результата отправки карточки — нужна, чтобы показать администратору
    и менеджеру точный текст, а не обобщённое «не доставлено».
    """

    DELIVERED = "delivered"
    # aiogram TelegramBadRequest("chat not found") — админ ни разу не писал боту /start.
    NOT_STARTED = "not_started"
    # aiogram TelegramForbiddenError("bot was blocked by the user") — писал раньше, но заблокировал бота.
    BLOCKED = "blocked"
    NO_MANAGER = "no_manager"


async def send_to_manager(bot: Bot, session: AsyncSession, order: Order) -> DeliveryResult:
    """Отправляет карточку заявки назначенному администратору в ЛС."""
    if order.manager_tg_id is None:
        logger.warning("Заявка #%s без администратора — отправка невозможна", order.id)
        return DeliveryResult.NO_MANAGER
    text = render_card(order, note=status_note(order))
    kb = manager_card_keyboard(order)
    try:
        msg = await bot.send_message(order.manager_tg_id, text, reply_markup=kb)
    except TelegramForbiddenError as exc:
        logger.warning(
            "Администратор tg_id=%s заблокировал бота — заявка #%s не доставлена: %s",
            order.manager_tg_id, order.id, exc,
        )
        return DeliveryResult.BLOCKED
    except TelegramBadRequest as exc:
        logger.warning(
            "Администратор tg_id=%s ещё не начинал диалог с ботом — заявка #%s не доставлена: %s",
            order.manager_tg_id, order.id, exc,
        )
        return DeliveryResult.NOT_STARTED
    await order_repo.set_message(session, order, msg.chat.id, msg.message_id)
    logger.info("Заявка #%s отправлена администратору tg_id=%s", order.id, order.manager_tg_id)
    return DeliveryResult.DELIVERED


async def refresh_card(bot: Bot, order: Order) -> None:
    """Обновляет карточку заявки под текущий статус (текст + кнопки)."""
    if order.tg_chat_id is None or order.tg_message_id is None:
        return
    text = render_card(order, note=status_note(order))
    kb = manager_card_keyboard(order)
    try:
        await bot.edit_message_text(
            text,
            chat_id=order.tg_chat_id,
            message_id=order.tg_message_id,
            reply_markup=kb,
        )
    except TelegramBadRequest as exc:
        # «message is not modified» / устаревшее сообщение — не критично.
        logger.debug("Не удалось обновить карточку заявки #%s: %s", order.id, exc)
