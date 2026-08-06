"""Репозиторий заявок."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import case, func, select, update
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


# Статусы, при которых звонок админа на служебную линию что-то значит: по одобренной
# заявке он соединяет, по остальным двум — работает как запрос одобрения. Заявку в
# CALL_IN_PROGRESS сюда не включаем: звонок по ней уже идёт.
SIGNAL_STATUSES = (
    OrderStatus.SENT,
    OrderStatus.CALL_REQUESTED,
    OrderStatus.CALL_APPROVED,
)


async def count_awaiting_signal(session: AsyncSession) -> int:
    """Сколько заявок, по которым звонок админа имеет смысл.

    Воркер «звонка-сигнала» опрашивает API Mango, только если это число больше нуля:
    нет таких заявок — реагировать не на что, значит и дёргать статистику незачем.
    """
    from sqlalchemy import func as sqlfunc
    result = await session.execute(
        select(sqlfunc.count()).select_from(Order).where(
            Order.status.in_(SIGNAL_STATUSES)
        )
    )
    return result.scalar_one()


async def get_approved_by_manager(
    session: AsyncSession, manager_tg_id: int
) -> Order | None:
    """Самая свежеодобренная заявка администратора, ожидающая звонка.

    ВНИМАНИЕ: `manager_tg_id` — это tg_id выездного АДМИНИСТРАТОРА (получателя
    заявки), а не менеджера; исторические имена полей в модели сбивают с толку.

    Если у администратора одобрено несколько заявок, из одного звонка невозможно
    понять, какая нужна — берём последнюю одобренную. Это осознанное ограничение,
    оно озвучено заказчику.
    """
    result = await session.execute(
        select(Order)
        .where(
            Order.manager_tg_id == manager_tg_id,
            Order.status == OrderStatus.CALL_APPROVED,
        )
        .order_by(Order.call_approved_at.desc().nullslast())
    )
    return result.scalars().first()


async def get_active_for_signal(
    session: AsyncSession, manager_tg_id: int
) -> Order | None:
    """Заявка, к которой относится звонок админа на служебную линию.

    Из голосового звонка невозможно понять, какая заявка нужна, поэтому берём одну
    по приоритету статусов: готовую к звонку → ждущую одобрения → только что
    полученную → уже звонящую. Внутри статуса — самая свежая.

    `manager_tg_id` — это tg_id выездного АДМИНИСТРАТОРА (историческое имя поля).
    """
    priority = case(
        (Order.status == OrderStatus.CALL_APPROVED, 1),
        (Order.status == OrderStatus.CALL_REQUESTED, 2),
        (Order.status == OrderStatus.SENT, 3),
        (Order.status == OrderStatus.CALL_IN_PROGRESS, 4),
        else_=99,
    )
    result = await session.execute(
        select(Order)
        .where(
            Order.manager_tg_id == manager_tg_id,
            Order.status.in_(ACTIVE_STATUSES),
        )
        .order_by(priority, Order.created_at.desc())
    )
    return result.scalars().first()


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


async def count_stale(session: AsyncSession, cutoff: datetime) -> int:
    """Сколько активных заявок не обновлялись с момента cutoff."""
    result = await session.execute(
        select(func.count()).select_from(Order).where(
            Order.status.in_(ACTIVE_STATUSES),
            Order.updated_at < cutoff,
        )
    )
    return result.scalar_one()


async def close_stale(session: AsyncSession, cutoff: datetime) -> int:
    """Закрывает активные заявки, не обновлявшиеся с cutoff. Возвращает количество.

    Карточки в Telegram сознательно не трогаем — кнопки на них остаются, заявка
    просто уходит из активных выборок и перестаёт блокировать дубли по лиду.
    updated_at проставляем явно: при bulk-обновлении onupdate из модели не срабатывает.
    """
    result = await session.execute(
        update(Order)
        .where(
            Order.status.in_(ACTIVE_STATUSES),
            Order.updated_at < cutoff,
        )
        .values(status=OrderStatus.COMPLETED, updated_at=func.now())
    )
    closed = result.rowcount or 0
    if closed:
        logger.info("Автозакрытие: закрыто %d заявок (не обновлялись с %s)", closed, cutoff)
    return closed
