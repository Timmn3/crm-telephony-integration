"""Инициация звонка администратор ↔ клиент через Mango.

Один и тот же звонок запускается из двух мест: кнопкой «Позвонить клиенту» в
Telegram и фоновым воркером сценария «звонок-сигнал» (админ без интернета звонит
на служебную линию). Чтобы эти пути не разъезжались, вся работа с Mango и БД
живёт здесь, а вызывающий код отвечает только за проверку прав и за то, как
сообщить результат человеку.

Телефон клиента (`Order.client_phone`) уходит ТОЛЬКО в Mango и не попадает ни в
логи, ни в Telegram — инвариант проекта.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import Bot
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Order, OrderStatus, User
from app.db.repositories import call_log_repo, order_repo
from app.services import order_service
from app.services.mango import (
    MangoClient,
    MangoConfigError,
    MangoError,
    MangoExtensionMissingError,
)

logger = logging.getLogger(__name__)

# Тексты для администратора. Вынесены сюда, чтобы кнопка и воркер говорили одно и
# то же; формулировки сохранены дословно из прежнего обработчика make_call.
MSG_NO_EXTENSION = "У вас не настроен персональный номер для звонков. Сообщите разработчику."
MSG_NOT_CONFIGURED = (
    "Звонки временно недоступны (не настроен маскирующий номер). Сообщите разработчику."
)
MSG_MANGO_FAILED = "Не удалось инициировать звонок, попробуйте позже."
MSG_CALL_STARTED = (
    "📞 Звонок инициирован. Ожидайте — Mango перезвонит вам на ваш номер, "
    "а затем соединит с клиентом."
)


@dataclass(slots=True)
class CallStartResult:
    """Итог попытки инициировать звонок."""

    ok: bool
    command_id: str | None = None
    message: str = ""


async def start_call(
    session: AsyncSession,
    bot: Bot,
    order: Order,
    user: User,
    *,
    source: str = "button",
) -> CallStartResult:
    """Инициирует звонок по заявке и переводит её в CALL_IN_PROGRESS.

    `source` попадает только в лог — им отличаем звонок по кнопке от звонка,
    поднятого сигналом с телефона. Проверки прав и статуса заявки остаются на
    вызывающем: у кнопки и у воркера они разные.
    """
    mango = MangoClient()
    try:
        command_id, _result = await mango.initiate_callback(
            manager_phone=user.phone,
            client_phone=order.client_phone,
            order_id=order.id,
            extension=user.mango_extension,
        )
    except MangoError as exc:
        await call_log_repo.create(
            session, order_id=order.id, manager_tg_id=user.tg_id,
            mango_command_id="", status="error",
        )
        logger.error("Ошибка инициации звонка по заявке #%s (%s): %s",
                     order.id, source, exc)
        # Конфиг не настроен — «позже» не поможет, нужна правка на сервере/в БД.
        # Различаем причины, чтобы не вводить админа в заблуждение.
        if isinstance(exc, MangoExtensionMissingError):
            message = MSG_NO_EXTENSION
        elif isinstance(exc, MangoConfigError):
            message = MSG_NOT_CONFIGURED
        else:
            message = MSG_MANGO_FAILED
        return CallStartResult(ok=False, message=message)

    await order_repo.set_status(session, order, OrderStatus.CALL_IN_PROGRESS)
    await call_log_repo.create(
        session, order_id=order.id, manager_tg_id=user.tg_id,
        mango_command_id=command_id, status="initiated",
    )
    await order_service.refresh_card(bot, order)
    logger.info("Звонок по заявке #%s инициирован администратором tg_id=%s (%s)",
                order.id, user.tg_id, source)
    return CallStartResult(ok=True, command_id=command_id, message=MSG_CALL_STARTED)
