"""Репозиторий заявок и связанных сообщений."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderMessage, OrderStatus

logger = logging.getLogger(__name__)

# Статусы, которые считаем «активными» (заявка в работе).
ACTIVE_STATUSES = (
    OrderStatus.SENT,
    OrderStatus.TAKEN,
    OrderStatus.CALL_REQUESTED,
    OrderStatus.CALL_APPROVED,
    OrderStatus.CALL_IN_PROGRESS,
)


async def create(
    session: AsyncSession,
    *,
    amo_lead_id: int,
    client_name: str,
    client_phone: str,
    region_id: int,
    operator_tg_id: int,
    client_address: str | None = None,
    comment: str | None = None,
    status: OrderStatus = OrderStatus.NEW,
) -> Order:
    """Создаёт заявку."""
    order = Order(
        amo_lead_id=amo_lead_id,
        client_name=client_name,
        client_phone=client_phone,
        client_address=client_address,
        comment=comment,
        region_id=region_id,
        operator_tg_id=operator_tg_id,
        status=status,
    )
    session.add(order)
    await session.flush()
    logger.info(
        "Создана заявка id=%s amo=%s region=%s operator=%s",
        order.id, amo_lead_id, region_id, operator_tg_id,
    )
    return order


async def get_by_id(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


async def try_take(
    session: AsyncSession, order_id: int, manager_tg_id: int
) -> Order | None:
    """Атомарно «забирает» заявку менеджером.

    Выполняет один UPDATE с условием status=SENT. Если заявку уже взял другой
    менеджер (status != SENT), UPDATE затронет 0 строк и вернётся None —
    так решается гонка при рассылке в несколько ЛС одновременно.
    """
    result = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.status == OrderStatus.SENT)
        .values(status=OrderStatus.TAKEN, manager_tg_id=manager_tg_id)
        .returning(Order.id)
    )
    taken_id = result.scalar_one_or_none()
    if taken_id is None:
        logger.info("Заявка id=%s уже занята/не в статусе SENT (manager=%s)",
                    order_id, manager_tg_id)
        return None
    await session.flush()
    order = await session.get(Order, order_id)
    logger.info("Заявка id=%s взята менеджером tg_id=%s", order_id, manager_tg_id)
    return order


async def set_status(
    session: AsyncSession,
    order: Order,
    status: OrderStatus,
    *,
    set_call_requested_at: bool = False,
    set_call_approved_at: bool = False,
) -> None:
    """Меняет статус заявки и при необходимости проставляет таймстампы."""
    order.status = status
    now = datetime.now(timezone.utc)
    if set_call_requested_at:
        order.call_requested_at = now
    if set_call_approved_at:
        order.call_approved_at = now
    await session.flush()
    logger.info("Заявка id=%s -> status=%s", order.id, status.value)


async def set_manager(session: AsyncSession, order: Order, manager_tg_id: int | None) -> None:
    order.manager_tg_id = manager_tg_id
    await session.flush()


async def add_message(
    session: AsyncSession,
    order_id: int,
    chat_id: int,
    message_id: int,
    recipient_tg_id: int | None = None,
) -> OrderMessage:
    """Сохраняет ссылку на отправленную карточку заявки."""
    msg = OrderMessage(
        order_id=order_id,
        chat_id=chat_id,
        message_id=message_id,
        recipient_tg_id=recipient_tg_id,
    )
    session.add(msg)
    await session.flush()
    return msg


async def list_messages(session: AsyncSession, order_id: int) -> list[OrderMessage]:
    result = await session.execute(
        select(OrderMessage).where(OrderMessage.order_id == order_id)
    )
    return list(result.scalars().all())


async def set_primary_message(
    session: AsyncSession, order: Order, chat_id: int, message_id: int
) -> None:
    """Запоминает основное сообщение карточки (поля на самой заявке)."""
    order.tg_chat_id = chat_id
    order.tg_message_id = message_id
    await session.flush()


async def list_active_by_operator(
    session: AsyncSession, operator_tg_id: int
) -> list[Order]:
    result = await session.execute(
        select(Order)
        .where(
            Order.operator_tg_id == operator_tg_id,
            Order.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())


async def list_active_by_manager(
    session: AsyncSession, manager_tg_id: int
) -> list[Order]:
    result = await session.execute(
        select(Order)
        .where(
            Order.manager_tg_id == manager_tg_id,
            Order.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())
