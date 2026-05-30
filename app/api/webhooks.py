"""FastAPI-приложение: healthcheck и приём webhook-ов Mango.

Mango может слать уведомления о событиях звонков (настраивается в ЛК Mango).
Здесь они логируются и при возможности обновляют запись в call_log по command_id.

IP Mango для whitelist (на уровне reverse-proxy/firewall):
81.88.80.132, 81.88.80.133, 81.88.82.36
"""
from __future__ import annotations

import json
import logging

from fastapi import FastAPI, Request

from app.db.database import async_session_factory
from app.db.repositories import call_log_repo

logger = logging.getLogger(__name__)

app = FastAPI(title="Call Masking Bot API", docs_url=None, redoc_url=None)


@app.get("/health")
async def health() -> dict[str, str]:
    """Проверка живости сервиса."""
    return {"status": "ok"}


@app.post("/webhooks/mango/call")
async def mango_call_event(request: Request) -> dict[str, str]:
    """Принимает уведомления Mango о событиях звонков.

    Тело приходит как form-data с полем `json`. Best-effort: логируем событие
    и, если есть command_id, обновляем статус/длительность в call_log.
    """
    try:
        form = await request.form()
        raw = form.get("json", "{}")
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except Exception:  # noqa: BLE001
        logger.warning("Не удалось разобрать webhook Mango")
        return {"status": "ignored"}

    command_id = payload.get("command_id")
    call_state = payload.get("call_state") or payload.get("state") or "event"
    duration = payload.get("duration")
    logger.info("Webhook Mango: command_id=%s state=%s", command_id, call_state)

    if command_id:
        try:
            async with async_session_factory() as session:
                await call_log_repo.update_status(
                    session,
                    str(command_id),
                    status=str(call_state),
                    duration=int(duration) if duration is not None else None,
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Ошибка обновления call_log из webhook Mango")

    return {"status": "ok"}
