"""Репозиторий заявок."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus

logger = logging.getLogger(__name__)

# Статусы, которые считаем «активными» (заявка в работе).
ACTIVE_STATUSES = (
    OrderStatus.SENT,
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
    group_id: int,
    operator_tg_id: int,
    manager_tg_id: int | None,
    client_address: str | None = None,
    comment: str | None = None,
    status: OrderStatus = OrderStatus.SENT,
) -> Order:
    """Создаёт заявку. Получатель-администратор задаётся менеджером при создании."""
    order = Order(
        amo_lead_id=amo_lead_id,
        client_name=client_name,
        client_phone=client_phone,
        client_address=client_address,
        comment=comment,
        group_id=group_id,
        operator_tg_id=operator_tg_id,
        manager_tg_id=manager_tg_id,
        status=status,
    )
    session.add(order)
    await session.flush()
    logger.info(
        "Создана заявка id=%s amo=%s group=%s operator=%s manager=%s",
        order.id, amo_lead_id, group_id, operator_tg_id, manager_tg_id,
    )
    return order


async def get_by_id(session: AsyncSession, order_id: int) -> Order | None:
    return await session.get(Order, order_id)


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


async def set_message(
    session: AsyncSession, order: Order, chat_id: int, message_id: int
) -> None:
    """Запоминает координаты сообщения-карточки в Telegram (для edit)."""
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


async def count_active_by_group(session: AsyncSession, group_id: int) -> int:
    """Количество незакрытых заявок группы."""
    from sqlalchemy import func as sqlfunc
    result = await session.execute(
        select(sqlfunc.count()).select_from(Order).where(
            Order.group_id == group_id,
            Order.status.in_(ACTIVE_STATUSES),
        )
    )
    return result.scalar_one()


async def count_total_by_group(session: AsyncSession, group_id: int) -> int:
    """Всего заявок группы (включая закрытые)."""
    from sqlalchemy import func as sqlfunc
    result = await session.execute(
        select(sqlfunc.count()).select_from(Order).where(Order.group_id == group_id)
    )
    return result.scalar_one()


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


async def get_active_by_amo_lead_id(
    session: AsyncSession, amo_lead_id: int
) -> Order | None:
    """Активная (не закрытая) заявка по номеру сделки amoCRM, если уже есть.

    Нужна для защиты от дублей: один и тот же лид не должен породить две
    параллельные заявки, если менеджер повторно вводит номер сделки.
    """
    result = await session.execute(
        select(Order)
        .where(
            Order.amo_lead_id == amo_lead_id,
            Order.status.in_(ACTIVE_STATUSES),
        )
        .order_by(Order.created_at.desc())
    )
    return result.scalars().first()
