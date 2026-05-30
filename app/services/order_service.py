"""Бизнес-логика заявок: рендер карточки, рассылка, обновление сообщений.

КРИТИЧНО (безопасность): функция render_card НИКОГДА не включает client_phone.
Телефон клиента не попадает ни в текст карточки, ни в кнопки, ни в логи.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import manager_card_keyboard
from app.db.models import Order, OrderStatus, User
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


def render_card(
    order: Order,
    *,
    manager_name: str | None = None,
    note: str | None = None,
) -> str:
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
    if manager_name:
        lines.append(f"<b>Менеджер:</b> {html.escape(manager_name)}")
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Рассылка
# --------------------------------------------------------------------------- #

async def send_to_manager(
    bot: Bot, session: AsyncSession, order: Order, manager: User
) -> bool:
    """Отправляет карточку заявки одному менеджеру в ЛС. True при успехе."""
    text = render_card(order)
    kb = manager_card_keyboard(order)
    try:
        msg = await bot.send_message(manager.tg_id, text, reply_markup=kb)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось отправить заявку #%s менеджеру tg_id=%s: %s",
                       order.id, manager.tg_id, exc)
        return False
    await order_repo.add_message(
        session, order.id, msg.chat.id, msg.message_id, recipient_tg_id=manager.tg_id
    )
    if order.tg_message_id is None:
        await order_repo.set_primary_message(session, order, msg.chat.id, msg.message_id)
    logger.info("Заявка #%s отправлена менеджеру tg_id=%s", order.id, manager.tg_id)
    return True


async def broadcast_to_managers(
    bot: Bot, session: AsyncSession, order: Order, managers: list[User]
) -> int:
    """Рассылает карточку всем менеджерам в ЛС. Возвращает число доставленных."""
    delivered = 0
    for manager in managers:
        if await send_to_manager(bot, session, order, manager):
            delivered += 1
    logger.info("Заявка #%s разослана: доставлено %s из %s",
                order.id, delivered, len(managers))
    return delivered


# --------------------------------------------------------------------------- #
# Обновление карточек при смене статуса
# --------------------------------------------------------------------------- #

async def refresh_cards(
    bot: Bot, session: AsyncSession, order: Order, *, manager_name: str | None = None
) -> None:
    """Обновляет все разосланные карточки заявки под текущий статус.

    - адресату-владельцу (взявшему заявку) показываются актуальные кнопки;
    - остальным получателям рассылки показывается «заявку взял другой менеджер»
      без кнопок.
    """
    messages = await order_repo.list_messages(session, order.id)
    note = status_note(order)

    for om in messages:
        is_owner = (
            order.manager_tg_id is not None
            and om.recipient_tg_id == order.manager_tg_id
        )
        if order.manager_tg_id is None or is_owner:
            text = render_card(order, manager_name=manager_name, note=note)
            kb = manager_card_keyboard(order)
        else:
            text = render_card(
                order, note="🚫 Заявку взял другой менеджер."
            )
            kb = None

        try:
            await bot.edit_message_text(
                text,
                chat_id=om.chat_id,
                message_id=om.message_id,
                reply_markup=kb,
            )
        except TelegramBadRequest as exc:
            # «message is not modified» / устаревшее сообщение — не критично.
            logger.debug("Не удалось обновить карточку (chat=%s msg=%s): %s",
                         om.chat_id, om.message_id, exc)
