"""Бизнес-логика заявок: рендер карточки, отправка администратору, обновление.

КРИТИЧНО (безопасность): функция render_card НИКОГДА не включает client_phone.
Телефон клиента не попадает ни в текст карточки, ни в кнопки, ни в логи.

В v2 одна заявка = одно сообщение одному выездному админу (рассылки нет): менеджер
выбирает получателя при создании, поэтому координаты сообщения хранятся прямо на
Order (tg_chat_id/tg_message_id).
"""
from __future__ import annotations

import asyncio
import html
import logging
from datetime import datetime, timedelta, timezone
from enum import Enum

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import manager_card_keyboard, operator_call_request_keyboard
from app.config import get_settings
from app.db.database import get_session
from app.db.models import Order, OrderStatus, User
from app.db.repositories import app_setting_repo, order_repo
from app.utils.phone import strip_phones

logger = logging.getLogger(__name__)

# Как часто воркер проверяет зависшие заявки (сам таймаут задаётся /autoclose).
_AUTOCLOSE_INTERVAL_SEC = 3600


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


# --------------------------------------------------------------------------- #
# Запрос звонка: уведомление менеджера
# --------------------------------------------------------------------------- #

async def notify_call_request(
    bot: Bot, order: Order, admin: User, *, repeated: bool = False
) -> None:
    """Просит менеджера-создателя одобрить звонок по заявке.

    Один и тот же текст уходит и по кнопке «Запросить звонок», и когда админ без
    интернета позвонил на служебную линию: у менеджера в обоих случаях должны быть
    кнопки «Одобрить / Отклонить».

    `repeated=True` — админ уже ждёт одобрения и позвонил ещё раз; добавляем строку,
    чтобы менеджер понимал, что это напоминание, а не новый запрос.
    """
    name = (
        admin.full_name
        or (f"@{admin.tg_username}" if admin.tg_username else str(admin.tg_id))
    )
    head = "🔔 <b>Запрос на звонок</b>" if not repeated else "⏰ <b>Напоминание: ждут одобрения</b>"
    lines = [
        head,
        f"Администратор {html.escape(name)} просит разрешение позвонить клиенту.",
        f"Заявка #{order.amo_lead_id}",
        f"Клиент: {html.escape(order.client_name)}",
    ]
    if repeated:
        lines.append("")
        lines.append("📲 Он звонил на служебный номер — значит ждёт прямо сейчас.")

    try:
        await bot.send_message(
            order.operator_tg_id,
            "\n".join(lines),
            reply_markup=operator_call_request_keyboard(order.id),
        )
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось уведомить менеджера tg_id=%s: %s",
                       order.operator_tg_id, exc)


# --------------------------------------------------------------------------- #
# Автозакрытие зависших заявок
# --------------------------------------------------------------------------- #

async def get_autoclose_hours(session: AsyncSession) -> int:
    """Актуальный таймаут автозакрытия: из app_settings, иначе дефолт из .env."""
    return await app_setting_repo.get_int(
        session,
        app_setting_repo.AUTOCLOSE_HOURS,
        get_settings().order_autoclose_hours,
    )


async def set_autoclose_hours(session: AsyncSession, hours: int) -> None:
    """Сохраняет таймаут автозакрытия (0 — выключить)."""
    await app_setting_repo.set_value(
        session, app_setting_repo.AUTOCLOSE_HOURS, str(hours)
    )


def autoclose_cutoff(hours: int) -> datetime:
    """Момент, старше которого заявка считается зависшей."""
    return datetime.now(timezone.utc) - timedelta(hours=hours)


async def close_stale_orders(session: AsyncSession, hours: int) -> int:
    """Закрывает заявки, с которыми не работали дольше hours. Возвращает количество.

    Карточки в Telegram не трогаем — кнопки на них остаются как были.
    """
    if hours <= 0:
        return 0
    return await order_repo.close_stale(session, autoclose_cutoff(hours))


async def run_autoclose(interval_sec: int = _AUTOCLOSE_INTERVAL_SEC) -> None:
    """Фоновый цикл автозакрытия. Запускается из app/main.py рядом с polling.

    Настройка перечитывается на каждой итерации, поэтому /autoclose действует без
    перезапуска бота. Ошибки внутри не должны ронять задачу: она живёт в общем
    `asyncio.gather`, и её падение остановило бы бота целиком.
    """
    logger.info("Воркер автозакрытия заявок запущен (проверка раз в %d сек)", interval_sec)
    while True:
        try:
            async with get_session() as session:
                hours = await get_autoclose_hours(session)
                if hours > 0:
                    await close_stale_orders(session, hours)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Ошибка автозакрытия заявок")
        await asyncio.sleep(interval_sec)
