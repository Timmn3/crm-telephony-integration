"""FastAPI-приложение: healthcheck и приём webhook-ов Mango.

Mango шлёт уведомления о событиях звонков (настраивается в ЛК Mango).
При неуспешном завершении звонка (клиент недоступен/не взял трубку) —
откатываем заявку в CALL_APPROVED и уведомляем менеджера.

IP Mango для whitelist (на уровне reverse-proxy/firewall):
81.88.80.132, 81.88.80.133, 81.88.82.36
"""
from __future__ import annotations

import json
import logging

from aiogram import Bot
from fastapi import FastAPI, Request

from app.db.database import async_session_factory
from app.db.models import OrderStatus
from app.db.repositories import call_log_repo, order_repo
from app.services.order_service import refresh_card

logger = logging.getLogger(__name__)

app = FastAPI(title="Call Masking Bot API", docs_url=None, redoc_url=None)

# Причины завершения звонка, которые означают «клиент недоступен / не взял».
_FAIL_REASONS = {
    "noanswer", "no_answer", "busy", "rejected", "failed",
    "unavailable", "error", "cancel", "congestion",
}


def _is_call_failed(payload: dict) -> bool:
    """Возвращает True если webhook сигнализирует о неуспешном звонке."""
    reason = str(payload.get("disconnect_reason") or "").lower()
    if reason and reason in _FAIL_REASONS:
        return True
    # Отбой с нулевой длительностью — клиент не взял трубку
    state = str(payload.get("call_state") or payload.get("state") or "").lower()
    duration = payload.get("duration")
    if state == "disconnected" and duration is not None and int(duration) == 0:
        return True
    return False


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/mango/call")
async def mango_call_event(request: Request) -> dict[str, str]:
    """Принимает уведомления Mango о событиях звонков."""
    try:
        form = await request.form()
        raw = form.get("json", "{}")
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:
        logger.warning("Не удалось разобрать webhook Mango")
        return {"status": "ignored"}

    command_id = payload.get("command_id")
    call_state = payload.get("call_state") or payload.get("state") or "event"
    duration = payload.get("duration")

    # Полный payload для диагностики
    logger.info("Webhook Mango: command_id=%s state=%s duration=%s payload=%s",
                command_id, call_state, duration, payload)

    if not command_id:
        return {"status": "ok"}

    try:
        async with async_session_factory() as session:
            # Обновляем call_log
            log_entry = await call_log_repo.update_status(
                session,
                str(command_id),
                status=str(call_state),
                duration=int(duration) if duration is not None else None,
            )
            await session.commit()

            if log_entry is None:
                return {"status": "ok"}

            if not _is_call_failed(payload):
                return {"status": "ok"}

            # Откатываем заявку — клиент недоступен
            async with async_session_factory() as session2:
                order = await order_repo.get_by_id(session2, log_entry.order_id)
                if order is None or order.status != OrderStatus.CALL_IN_PROGRESS:
                    return {"status": "ok"}

                await order_repo.set_status(session2, order, OrderStatus.CALL_APPROVED)
                await session2.commit()

                logger.info(
                    "Заявка #%s откатана в CALL_APPROVED (клиент недоступен, command_id=%s)",
                    order.id, command_id,
                )

                bot: Bot = request.app.state.bot
                await refresh_card(bot, order)
                await bot.send_message(
                    order.manager_tg_id,
                    "📵 Клиент недоступен или не взял трубку.\n"
                    "Нажмите «Позвонить клиенту», чтобы попробовать ещё раз.",
                )

    except Exception:
        logger.exception("Ошибка обработки webhook Mango (command_id=%s)", command_id)

    return {"status": "ok"}
