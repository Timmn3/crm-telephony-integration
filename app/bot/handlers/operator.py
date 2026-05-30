"""Обработчики оператора: создание заявки (/order) и список (/my_orders).

Блок 9 добавит сюда же одобрение/отклонение звонка.
"""
from __future__ import annotations

import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.role import IsOperator
from app.bot.keyboards.inline import (
    cancel_keyboard,
    managers_keyboard,
    regions_keyboard,
    send_target_keyboard,
)
from app.bot.states import CreateOrder
from app.db.models import OrderStatus
from app.db.repositories import order_repo, region_repo, user_repo
from app.services import order_service
from app.services.amocrm import AmoCRMClient, AmoCRMError, LeadNotFound, PhoneNotFound

logger = logging.getLogger(__name__)

router = Router(name="operator")

STATUS_RU = {
    OrderStatus.NEW: "черновик",
    OrderStatus.SENT: "отправлена",
    OrderStatus.TAKEN: "взята",
    OrderStatus.CALL_REQUESTED: "запрошен звонок",
    OrderStatus.CALL_APPROVED: "звонок одобрен",
    OrderStatus.CALL_IN_PROGRESS: "звонок идёт",
    OrderStatus.COMPLETED: "закрыта",
    OrderStatus.CANCELLED: "отменена",
}


# --------------------------------------------------------------------------- #
# /order — создание заявки
# --------------------------------------------------------------------------- #

@router.message(Command("order"), IsOperator)
async def order_start(message: Message, state: FSMContext) -> None:
    await state.set_state(CreateOrder.waiting_lead_id)
    await message.answer(
        "Введите номер сделки из amoCRM:", reply_markup=cancel_keyboard()
    )


@router.message(CreateOrder.waiting_lead_id)
async def order_lead_id(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Номер сделки — это число. Попробуйте ещё раз или /cancel.")
        return
    lead_id = int(text)

    client = AmoCRMClient(session)
    try:
        data = await client.get_order_data(lead_id)
    except LeadNotFound:
        await message.answer("Сделка не найдена, проверьте номер.")
        return
    except PhoneNotFound:
        await message.answer(
            "У контакта сделки не найден телефон клиента. "
            "Проверьте карточку в amoCRM и попробуйте снова."
        )
        return
    except AmoCRMError as exc:
        await message.answer(f"Ошибка amoCRM: {html.escape(str(exc))}")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Непредвиденная ошибка при запросе сделки %s", lead_id)
        await message.answer("Не удалось получить данные сделки (см. логи).")
        return

    # Телефон храним только в FSM (память процесса) до создания заявки в БД.
    await state.update_data(
        amo_lead_id=data.amo_lead_id,
        client_name=data.client_name,
        client_phone=data.client_phone,
        client_address=data.client_address,
        comment=data.comment,
    )

    regions = await region_repo.list_active(session)
    if not regions:
        await state.clear()
        await message.answer("Нет активных регионов. Обратитесь к администратору.")
        return

    preview_lines = [
        f"<b>Сделка #{data.amo_lead_id}</b>",
        f"Клиент: {html.escape(data.client_name)}",
    ]
    if data.client_address:
        preview_lines.append(f"Адрес: {html.escape(data.client_address)}")
    if data.comment:
        preview_lines.append(f"Комментарий: {html.escape(data.comment)}")
    preview_lines.append("")
    preview_lines.append("Телефон клиента найден ✓ (скрыт)")
    preview_lines.append("")
    preview_lines.append("Выберите регион:")

    await state.set_state(CreateOrder.waiting_region)
    await message.answer(
        "\n".join(preview_lines),
        reply_markup=regions_keyboard(regions, prefix="select_region"),
    )


@router.callback_query(CreateOrder.waiting_region, F.data.startswith("select_region:"), IsOperator)
async def order_region(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    region_id = int(callback.data.split(":")[1])
    region = await region_repo.get_by_id(session, region_id)
    if region is None:
        await callback.answer("Регион не найден", show_alert=True)
        return

    data = await state.get_data()
    order = await order_repo.create(
        session,
        amo_lead_id=data["amo_lead_id"],
        client_name=data["client_name"],
        client_phone=data["client_phone"],
        client_address=data.get("client_address"),
        comment=data.get("comment"),
        region_id=region_id,
        operator_tg_id=callback.from_user.id,
        status=OrderStatus.NEW,
    )
    await state.update_data(order_id=order.id)
    await state.set_state(CreateOrder.waiting_target)
    await callback.message.edit_text(
        f"Регион: <b>{html.escape(region.name)}</b>.\n"
        "Отправить всем менеджерам региона или выбрать конкретного?",
        reply_markup=send_target_keyboard(order.id),
    )
    await callback.answer()


@router.callback_query(CreateOrder.waiting_target, F.data.startswith("send_all:"), IsOperator)
async def order_send_all(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await state.clear()
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    managers = await user_repo.list_active_managers_by_region(session, order.region_id)
    if not managers:
        await state.clear()
        await callback.message.edit_text("В этом регионе нет активных менеджеров.")
        await callback.answer()
        return

    await order_repo.set_status(session, order, OrderStatus.SENT)
    delivered = await order_service.broadcast_to_managers(bot, session, order, managers)
    await state.clear()
    await callback.message.edit_text(
        f"✅ Заявка #{order.amo_lead_id} разослана менеджерам региона "
        f"(доставлено: {delivered} из {len(managers)})."
    )
    await callback.answer()


@router.callback_query(CreateOrder.waiting_target, F.data.startswith("send_pick:"), IsOperator)
async def order_send_pick(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await state.clear()
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    managers = await user_repo.list_active_managers_by_region(session, order.region_id)
    if not managers:
        await state.clear()
        await callback.message.edit_text("В этом регионе нет активных менеджеров.")
        await callback.answer()
        return

    await callback.message.edit_text(
        "Выберите менеджера:",
        reply_markup=managers_keyboard(managers, order_id, prefix="select_manager"),
    )
    await callback.answer()


@router.callback_query(CreateOrder.waiting_target, F.data.startswith("select_manager:"), IsOperator)
async def order_select_manager(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    _, user_id, order_id = callback.data.split(":")
    order = await order_repo.get_by_id(session, int(order_id))
    manager = await user_repo.get_by_id(session, int(user_id))
    if order is None or manager is None:
        await state.clear()
        await callback.answer("Заявка или менеджер не найдены", show_alert=True)
        return

    await order_repo.set_status(session, order, OrderStatus.SENT)
    delivered = await order_service.send_to_manager(bot, session, order, manager)
    await state.clear()

    name = html.escape(manager.full_name or str(manager.tg_id))
    if delivered:
        await callback.message.edit_text(
            f"✅ Заявка #{order.amo_lead_id} отправлена менеджеру {name}."
        )
    else:
        await callback.message.edit_text(
            f"⚠️ Не удалось отправить заявку менеджеру {name} — "
            "он ещё не начинал диалог с ботом."
        )
    await callback.answer()


# --------------------------------------------------------------------------- #
# /my_orders
# --------------------------------------------------------------------------- #

@router.message(Command("my_orders"), IsOperator)
async def my_orders(message: Message, session: AsyncSession) -> None:
    orders = await order_repo.list_active_by_operator(session, message.from_user.id)
    if not orders:
        await message.answer("У вас нет активных заявок. Создать: /order")
        return
    lines = ["<b>Ваши активные заявки:</b>"]
    for o in orders:
        status = STATUS_RU.get(o.status, o.status.value)
        lines.append(
            f"#{o.amo_lead_id} — {html.escape(o.client_name)} — <i>{status}</i>"
        )
    await message.answer("\n".join(lines))
