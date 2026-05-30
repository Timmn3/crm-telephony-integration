"""Обработчики действий менеджера.

Блок 5: регистрация через «Поделиться контактом».
Блоки 8/10: взятие заявки, запрос звонка, инициация звонка.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.role import IsManager
from app.bot.keyboards.inline import operator_call_request_keyboard
from app.bot.keyboards.reply import phone_request_keyboard, remove_keyboard
from app.db.models import Order, OrderStatus, User, UserRole
from app.db.repositories import call_log_repo, order_repo, region_repo, user_repo
from app.services import order_service
from app.services.mango import MangoClient, MangoError
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

router = Router(name="manager")

STATUS_RU = {
    OrderStatus.SENT: "ожидает взятия",
    OrderStatus.TAKEN: "взята",
    OrderStatus.CALL_REQUESTED: "запрошен звонок",
    OrderStatus.CALL_APPROVED: "звонок одобрен",
    OrderStatus.CALL_IN_PROGRESS: "звонок идёт",
    OrderStatus.COMPLETED: "закрыта",
    OrderStatus.CANCELLED: "отменена",
}


async def _manager_name(session: AsyncSession, order: Order) -> str | None:
    """Имя менеджера заявки (для карточки)."""
    if order.manager_tg_id is None:
        return None
    manager = await user_repo.get_by_tg_id(session, order.manager_tg_id)
    if manager is None:
        return None
    return manager.full_name or (f"@{manager.tg_username}" if manager.tg_username else str(manager.tg_id))


async def _notify(bot: Bot, tg_id: int, text: str, **kwargs) -> None:
    """Безопасная отправка уведомления (без падения при закрытом ЛС)."""
    try:
        await bot.send_message(tg_id, text, **kwargs)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось уведомить tg_id=%s: %s", tg_id, exc)


@router.message(F.contact)
async def on_contact(message: Message, user: User | None, session: AsyncSession) -> None:
    """Сохраняет номер менеджера из пересланного контакта."""
    if user is None or not user.is_active or user.role != UserRole.MANAGER:
        # Контакт от не-менеджера нам не нужен.
        await message.answer("Спасибо, но номер телефона сейчас не требуется.",
                             reply_markup=remove_keyboard())
        return

    contact = message.contact
    # Безопасность: принимаем только собственный контакт пользователя.
    if contact.user_id is not None and message.from_user is not None \
            and contact.user_id != message.from_user.id:
        await message.answer(
            "Пожалуйста, поделитесь именно своим номером — нажмите кнопку ниже.",
            reply_markup=phone_request_keyboard(),
        )
        return

    phone = normalize_phone(contact.phone_number)
    if phone is None:
        await message.answer(
            "Не удалось распознать номер телефона. Попробуйте ещё раз.",
            reply_markup=phone_request_keyboard(),
        )
        return

    await user_repo.set_phone(session, user, phone)

    region_name = "—"
    if user.region_id:
        region = await region_repo.get_by_id(session, user.region_id)
        if region:
            region_name = html.escape(region.name)

    await message.answer(
        "✅ Готово! Вы зарегистрированы как выездной менеджер.\n"
        f"Регион: <b>{region_name}</b>.\n\n"
        "Когда поступит заявка — вы получите карточку с кнопкой «Беру заявку».",
        reply_markup=remove_keyboard(),
    )
    logger.info("Менеджер tg_id=%s завершил регистрацию (телефон сохранён)", user.tg_id)


# --------------------------------------------------------------------------- #
# Беру заявку (атомарно)
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("take_order:"), IsManager)
async def take_order(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    if user.region_id != order.region_id:
        await callback.answer("Эта заявка не для вашего региона.", show_alert=True)
        return

    taken = await order_repo.try_take(session, order_id, user.tg_id)
    if taken is None:
        # Заявку уже забрал другой менеджер — обновим карточки и сообщим.
        current = await order_repo.get_by_id(session, order_id)
        await order_service.refresh_cards(
            bot, session, current, manager_name=await _manager_name(session, current)
        )
        await callback.answer("Заявку уже взял другой менеджер.", show_alert=True)
        return

    await order_service.refresh_cards(bot, session, taken, manager_name=user.full_name)
    await callback.answer("✅ Вы взяли заявку.")

    name = user.full_name or (f"@{user.tg_username}" if user.tg_username else str(user.tg_id))
    await _notify(
        bot, taken.operator_tg_id,
        f"📥 Менеджер {html.escape(name)} взял заявку #{taken.amo_lead_id}.",
    )
    logger.info("Заявка #%s взята менеджером tg_id=%s", order_id, user.tg_id)


# --------------------------------------------------------------------------- #
# Запросить звонок
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("request_call:"), IsManager)
async def request_call(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if order.manager_tg_id != user.tg_id:
        await callback.answer("Это не ваша заявка.", show_alert=True)
        return
    if order.status not in (OrderStatus.TAKEN, OrderStatus.CALL_IN_PROGRESS):
        await callback.answer("Сейчас нельзя запросить звонок.", show_alert=True)
        return

    await order_repo.set_status(
        session, order, OrderStatus.CALL_REQUESTED, set_call_requested_at=True
    )
    await order_service.refresh_cards(bot, session, order, manager_name=user.full_name)
    await callback.answer("Запрос на звонок отправлен оператору.")

    name = user.full_name or (f"@{user.tg_username}" if user.tg_username else str(user.tg_id))
    await _notify(
        bot, order.operator_tg_id,
        f"🔔 Менеджер {html.escape(name)} запрашивает звонок клиенту.\n"
        f"Заявка #{order.amo_lead_id}\n"
        f"Клиент: {html.escape(order.client_name)}",
        reply_markup=operator_call_request_keyboard(order.id),
    )
    logger.info("Менеджер tg_id=%s запросил звонок по заявке #%s", user.tg_id, order_id)


# --------------------------------------------------------------------------- #
# Позвонить клиенту (через Mango)
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("make_call:"), IsManager)
async def make_call(
    callback: CallbackQuery, user: User, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if order.manager_tg_id != user.tg_id:
        await callback.answer("Это не ваша заявка.", show_alert=True)
        return
    if order.status != OrderStatus.CALL_APPROVED:
        await callback.answer("Звонок сейчас недоступен. Запросите одобрение.", show_alert=True)
        return
    if not user.phone:
        await callback.answer(
            "У вас не сохранён номер телефона. Поделитесь контактом через /start.",
            show_alert=True,
        )
        return

    # Инициируем звонок. order.client_phone передаётся только в Mango и не логируется.
    mango = MangoClient()
    try:
        command_id, _result = await mango.initiate_callback(
            manager_phone=user.phone,
            client_phone=order.client_phone,
            order_id=order.id,
        )
    except MangoError as exc:
        await call_log_repo.create(
            session, order_id=order.id, manager_tg_id=user.tg_id,
            mango_command_id="", status="error",
        )
        logger.error("Ошибка инициации звонка по заявке #%s: %s", order.id, exc)
        await callback.answer("Не удалось инициировать звонок, попробуйте позже.", show_alert=True)
        return

    await order_repo.set_status(session, order, OrderStatus.CALL_IN_PROGRESS)
    await call_log_repo.create(
        session, order_id=order.id, manager_tg_id=user.tg_id,
        mango_command_id=command_id, status="initiated",
    )
    await order_service.refresh_cards(bot, session, order, manager_name=user.full_name)
    await callback.answer()
    await _notify(
        bot, user.tg_id,
        "📞 Звонок инициирован. Ожидайте — Mango перезвонит вам на ваш номер, "
        "а затем соединит с клиентом.",
    )
    logger.info("Звонок по заявке #%s инициирован менеджером tg_id=%s", order.id, user.tg_id)


# --------------------------------------------------------------------------- #
# Закрыть заявку (менеджер, оператор или админ)
# --------------------------------------------------------------------------- #

@router.callback_query(F.data.startswith("complete_order:"))
async def complete_order(
    callback: CallbackQuery, user: User | None, session: AsyncSession, bot: Bot
) -> None:
    if user is None or not user.is_active:
        await callback.answer("Нет доступа.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return

    allowed = (
        user.role == UserRole.ADMIN
        or (user.role == UserRole.MANAGER and order.manager_tg_id == user.tg_id)
        or (user.role == UserRole.OPERATOR and order.operator_tg_id == user.tg_id)
    )
    if not allowed:
        await callback.answer("Нет доступа к этой заявке.", show_alert=True)
        return

    await order_repo.set_status(session, order, OrderStatus.COMPLETED)
    await order_service.refresh_cards(
        bot, session, order, manager_name=await _manager_name(session, order)
    )
    await callback.answer("✔️ Заявка закрыта.")
    logger.info("Заявка #%s закрыта пользователем tg_id=%s", order_id, user.tg_id)


# --------------------------------------------------------------------------- #
# /my_tasks
# --------------------------------------------------------------------------- #

@router.message(Command("my_tasks"), IsManager)
async def my_tasks(message: Message, user: User, session: AsyncSession) -> None:
    orders = await order_repo.list_active_by_manager(session, user.tg_id)
    if not orders:
        await message.answer("У вас пока нет активных заявок.")
        return
    lines = ["<b>Ваши заявки:</b>"]
    for o in orders:
        status = STATUS_RU.get(o.status, o.status.value)
        lines.append(f"#{o.amo_lead_id} — {html.escape(o.client_name)} — <i>{status}</i>")
    await message.answer("\n".join(lines))
