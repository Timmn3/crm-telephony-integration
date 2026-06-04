"""Обработчики оператора: создание заявки (/order), список (/my_orders),
одобрение/отклонение звонка.
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
    groups_select_keyboard,
    skip_comment_keyboard,
)
from app.bot.states import CreateOrder
from app.db.models import Order, OrderStatus, User, UserRole
from app.db.repositories import group_repo, order_repo, user_repo
from app.services import order_service
from app.services.amocrm import AmoCRMClient, AmoCRMError, LeadNotFound, PhoneNotFound

logger = logging.getLogger(__name__)

router = Router(name="operator")

STATUS_RU = {
    OrderStatus.SENT: "отправлена",
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
            "У контакта в сделке не указан телефон. "
            "Добавьте номер в amoCRM и попробуйте снова."
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

    groups = await group_repo.list_active(session)
    if not groups:
        await state.clear()
        await message.answer("Нет активных групп. Обратитесь к администратору.")
        return

    # Имена текущих менеджеров для подписи кнопок.
    manager_names: dict[int, str | None] = {}
    for g in groups:
        m = await user_repo.get_active_manager_by_group(session, g.id)
        manager_names[g.id] = (
            m.full_name or (f"@{m.tg_username}" if m.tg_username else str(m.tg_id))
        ) if m else None

    preview_lines = [
        f"<b>Сделка #{data.amo_lead_id}</b>",
        f"Клиент: {html.escape(data.client_name)}",
    ]
    if data.client_address:
        preview_lines.append(f"Адрес: {html.escape(data.client_address)}")
    preview_lines += ["", "Телефон клиента найден ✓ (скрыт)", "", "Выберите группу:"]

    await state.set_state(CreateOrder.waiting_group)
    await message.answer(
        "\n".join(preview_lines),
        reply_markup=groups_select_keyboard(groups, manager_names),
    )


@router.callback_query(CreateOrder.waiting_group, F.data.startswith("select_group:"), IsOperator)
async def order_group(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[1])
    group = await group_repo.get_by_id(session, group_id)
    if group is None:
        await callback.answer("Группа не найдена", show_alert=True)
        return

    manager = await user_repo.get_active_manager_by_group(session, group_id)
    if manager is None:
        await callback.answer(
            "В этой группе нет менеджера. Выберите другую или попросите админа назначить.",
            show_alert=True,
        )
        return

    await state.update_data(group_id=group_id, manager_tg_id=manager.tg_id)
    await state.set_state(CreateOrder.waiting_comment)
    await callback.message.edit_text(
        f"Группа: <b>{html.escape(group.name)}</b>.\n"
        "Добавить комментарий к заявке? Введите текст или нажмите «Без комментария».",
        reply_markup=skip_comment_keyboard(),
    )
    await callback.answer()


@router.message(CreateOrder.waiting_comment)
async def order_comment_text(
    callback_or_message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    comment = (callback_or_message.text or "").strip() or None
    await _finalize_order(callback_or_message, state, session, bot, comment_override=comment)


@router.callback_query(CreateOrder.waiting_comment, F.data == "skip_comment", IsOperator)
async def order_comment_skip(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    await _finalize_order(callback.message, state, session, bot, comment_override=None)
    await callback.answer()


async def _finalize_order(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot,
    *, comment_override: str | None,
) -> None:
    data = await state.get_data()
    await state.clear()

    group_id = data.get("group_id")
    manager_tg_id = data.get("manager_tg_id")
    if group_id is None or manager_tg_id is None:
        await message.answer("Сессия создания заявки потеряна. Начните заново: /order")
        return

    comment = comment_override if comment_override is not None else data.get("comment")

    order = await order_repo.create(
        session,
        amo_lead_id=data["amo_lead_id"],
        client_name=data["client_name"],
        client_phone=data["client_phone"],
        client_address=data.get("client_address"),
        comment=comment,
        group_id=group_id,
        operator_tg_id=message.chat.id,
        manager_tg_id=manager_tg_id,
        status=OrderStatus.SENT,
    )

    delivered = await order_service.send_to_manager(bot, session, order)
    group = await group_repo.get_by_id(session, group_id)
    manager = await user_repo.get_by_tg_id(session, manager_tg_id)
    mname = html.escape(
        (manager.full_name or str(manager.tg_id)) if manager else str(manager_tg_id)
    )
    gname = html.escape(group.name) if group else str(group_id)

    if delivered:
        await message.answer(
            f"✅ Заявка #{order.amo_lead_id} отправлена менеджеру {mname} (группа {gname})."
        )
    else:
        await message.answer(
            f"⚠️ Заявка #{order.amo_lead_id} создана, но менеджер {mname} ещё не "
            "начинал диалог с ботом — карточка не доставлена."
        )


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
        lines.append(f"#{o.amo_lead_id} — {html.escape(o.client_name)} — <i>{status}</i>")
    await message.answer("\n".join(lines))


# --------------------------------------------------------------------------- #
# Одобрение / отклонение звонка
# --------------------------------------------------------------------------- #

def _can_manage_call(user: User | None, order: Order) -> bool:
    """Может ли пользователь одобрять/отклонять звонок по этой заявке."""
    if user is None or not user.is_active:
        return False
    if user.role == UserRole.ADMIN:
        return True
    return user.role == UserRole.OPERATOR and order.operator_tg_id == user.tg_id


@router.callback_query(F.data.startswith("approve_call:"))
async def approve_call(
    callback: CallbackQuery, user: User | None, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if not _can_manage_call(user, order):
        await callback.answer("Нет доступа к этой заявке.", show_alert=True)
        return
    if order.status != OrderStatus.CALL_REQUESTED:
        await callback.answer("Запрос уже обработан.", show_alert=True)
        return

    await order_repo.set_status(
        session, order, OrderStatus.CALL_APPROVED, set_call_approved_at=True
    )
    await order_service.refresh_card(bot, order)
    try:
        await callback.message.edit_text(f"✅ Звонок по заявке #{order.amo_lead_id} одобрен.")
    except Exception:  # noqa: BLE001
        pass
    await callback.answer("Звонок одобрен.")
    logger.info("Оператор tg_id=%s одобрил звонок по заявке #%s",
                callback.from_user.id, order_id)


@router.callback_query(F.data.startswith("reject_call:"))
async def reject_call(
    callback: CallbackQuery, user: User | None, session: AsyncSession, bot: Bot
) -> None:
    order_id = int(callback.data.split(":")[1])
    order = await order_repo.get_by_id(session, order_id)
    if order is None:
        await callback.answer("Заявка не найдена.", show_alert=True)
        return
    if not _can_manage_call(user, order):
        await callback.answer("Нет доступа к этой заявке.", show_alert=True)
        return
    if order.status != OrderStatus.CALL_REQUESTED:
        await callback.answer("Запрос уже обработан.", show_alert=True)
        return

    # Возврат к SENT — кнопка «Запросить звонок» снова доступна у менеджера.
    await order_repo.set_status(session, order, OrderStatus.SENT)
    await order_service.refresh_card(bot, order)
    try:
        await callback.message.edit_text(f"❌ Звонок по заявке #{order.amo_lead_id} отклонён.")
    except Exception:  # noqa: BLE001
        pass
    await callback.answer("Звонок отклонён.")

    if order.manager_tg_id:
        try:
            await bot.send_message(
                order.manager_tg_id,
                f"❌ Запрос на звонок по заявке #{order.amo_lead_id} отклонён. "
                "При необходимости запросите звонок повторно.",
            )
        except Exception:  # noqa: BLE001
            logger.warning("Не удалось уведомить менеджера об отклонении звонка")
    logger.info("Звонок по заявке #%s отклонён оператором tg_id=%s",
                order_id, callback.from_user.id)
