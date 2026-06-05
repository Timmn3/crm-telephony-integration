"""Бизнес-логика заявок: рендер карточки, отправка менеджеру, обновление.

КРИТИЧНО (безопасность): функция render_card НИКОГДА не включает client_phone.
Телефон клиента не попадает ни в текст карточки, ни в кнопки, ни в логи.

В v2 одна заявка = одно сообщение одному выездному админу (рассылки нет): менеджер
выбирает получателя при создании, поэтому координаты сообщения хранятся прямо на
Order (tg_chat_id/tg_message_id).
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import manager_card_keyboard
from app.db.models import Order, OrderStatus
from app.db.repositories import order_repo

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Рендер карточки
# --------------------------------------------------------------------------- #

def status_note(order: Order) -> str | None:
    """Текстовая пометка статуса на карточке менеджера."""
    return {
        OrderStatus.CALL_REQUESTED: "⏳ Звонок запрошен, ожидайте одобрения оператора.",
        OrderStatus.CALL_APPROVED: "✅ Звонок одобрен.",
        OrderStatus.CALL_IN_PROGRESS: "📞 Звонок инициирован. Ожидайте — Mango перезвонит вам.",
        OrderStatus.COMPLETED: "✔️ Заявка закрыта.",
        OrderStatus.CANCELLED: "🚫 Заявка отменена.",
    }.get(order.status)


def render_card(order: Order, *, note: str | None = None) -> str:
    """Формирует HTML-текст карточки заявки. Без номера телефона клиента!"""
    lines = [
        f"📋 <b>Заявка #{order.amo_lead_id}</b>",
        "",
        f"<b>Клиент:</b> {html.escape(order.client_name or '—')}",
    ]
    if order.client_address:
        lines.append(f"<b>Адрес:</b> {html.escape(order.client_address)}")
    if order.comment:
        lines.append(f"<b>Комментарий:</b> {html.escape(order.comment)}")
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Отправка и обновление карточки
# --------------------------------------------------------------------------- #

async def send_to_manager(bot: Bot, session: AsyncSession, order: Order) -> bool:
    """Отправляет карточку заявки назначенному менеджеру в ЛС. True при успехе."""
    if order.manager_tg_id is None:
        logger.warning("Заявка #%s без менеджера — отправка невозможна", order.id)
        return False
    text = render_card(order, note=status_note(order))
    kb = manager_card_keyboard(order)
    try:
        msg = await bot.send_message(order.manager_tg_id, text, reply_markup=kb)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось отправить заявку #%s менеджеру tg_id=%s: %s",
                       order.id, order.manager_tg_id, exc)
        return False
    await order_repo.set_message(session, order, msg.chat.id, msg.message_id)
    logger.info("Заявка #%s отправлена менеджеру tg_id=%s", order.id, order.manager_tg_id)
    return True


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
