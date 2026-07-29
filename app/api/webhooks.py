"""FastAPI-приложение: healthcheck и приём webhook-ов Mango.

Mango шлёт уведомления о событиях звонков (настраивается в ЛК Mango).
При неуспешном завершении звонка (клиент недоступен/не взял трубку) —
откатываем заявку в CALL_APPROVED и уведомляем администратора.

IP Mango для whitelist (на уровне reverse-proxy/firewall) — их ПЯТЬ:
81.88.80.132, 81.88.80.133, 81.88.82.36, 81.88.82.44, 81.88.82.45
(офиц. дока, разделы 1.2 и 3.1.1 — см. docs/mango/README.md)
"""
from __future__ import annotations

import json
import logging
from collections import OrderedDict

from aiogram import Bot
from fastapi import FastAPI, Request

from app.config import get_settings
from app.db.database import async_session_factory
from app.db.models import OrderStatus
from app.db.repositories import call_log_repo, order_repo
from app.services.mango import MangoClient, MangoError
from app.services.order_service import refresh_card
from app.utils.phone import mask_phone

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


# --------------------------------------------------------------------------- #
# ПРОТОТИП: «звонок без интернета» — админ звонит на служебную линию, мы
# переводим его вызов на нужный номер командой route.
# Пока это только проверка механики: цель перевода берётся из MANGO_TEST_TARGET,
# телефон клиента здесь НЕ используется.
# --------------------------------------------------------------------------- #

# Уже обработанные call_id: Mango шлёт по одному вызову несколько уведомлений
# (Appeared -> Connected -> ...), а route должен уйти ровно один раз.
# Для прототипа хватает памяти процесса; ограничиваем размер, чтобы не рос вечно.
_routed_calls: OrderedDict[str, None] = OrderedDict()
_ROUTED_MAX = 500


def _already_routed(call_id: str) -> bool:
    """Отмечает call_id обработанным. True — если его уже переводили раньше."""
    if call_id in _routed_calls:
        return True
    _routed_calls[call_id] = None
    while len(_routed_calls) > _ROUTED_MAX:
        _routed_calls.popitem(last=False)
    return False


@app.post("/webhooks/mango/events/call")
async def mango_events_call(request: Request) -> dict[str, str]:
    """Приём событий Mango о вызовах (API Realtime).

    Путь фиксирован: в ЛК Mango указывается базовый адрес
    `https://<домен>/webhooks/mango/`, а `/events/call` система дописывает сама.

    ВАЖНО: события приходят по ВСЕМ входящим звонкам ВАТС, включая звонки
    пациентов в колл-центр. Поэтому вызов переводится только при совпадении всех
    условий: прототип настроен, звонок пришёл на служебную линию, звонит
    разрешённый номер, состояние — Appeared (трубку ещё не сняли). Во всех
    остальных случаях событие только логируется.
    """
    try:
        form = await request.form()
        raw = form.get("json", "{}")
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except Exception as exc:
        # Причину пишем обязательно: отсутствие python-multipart выглядит здесь
        # ровно как «кривой json», и без текста ошибки это не отличить.
        logger.warning(
            "Не удалось разобрать событие Mango events/call: %s: %s",
            type(exc).__name__, exc,
        )
        return {"status": "ignored"}

    call_id = str(payload.get("call_id") or "")
    call_state = str(payload.get("call_state") or "")
    from_number = str((payload.get("from") or {}).get("number") or "")
    to_line = str((payload.get("to") or {}).get("line_number") or "")

    # Телефоны в логи — только маскированно (номер сотрудника это тоже PII).
    logger.info(
        "Mango events/call: state=%s call_id=%s from=%s line=%s location=%s",
        call_state, call_id, mask_phone(from_number), mask_phone(to_line),
        payload.get("location"),
    )

    settings = get_settings()
    if not settings.route_prototype_enabled:
        return {"status": "ok"}

    if call_state != "Appeared" or not call_id:
        return {"status": "ok"}
    if to_line != settings.mango_service_line:
        return {"status": "ok"}
    if from_number != settings.mango_test_caller:
        logger.info("Звонок на служебную линию с неразрешённого номера — игнор")
        return {"status": "ok"}
    if _already_routed(call_id):
        return {"status": "ok"}

    try:
        command_id, _result = await MangoClient().route_call(
            call_id=call_id, to_number=settings.mango_test_target
        )
        logger.info("Прототип: вызов call_id=%s переведён (command_id=%s)",
                    call_id, command_id)
    except MangoError as exc:
        logger.error("Прототип: route не удался (call_id=%s): %s", call_id, exc)
    except Exception:
        logger.exception("Прототип: непредвиденная ошибка route (call_id=%s)", call_id)

    return {"status": "ok"}


@app.post("/webhooks/mango/call")
async def mango_call_event(request: Request) -> dict[str, str]:
    """Принимает уведомления Mango о событиях звонков."""
    try:
        form = await request.form()
        raw = form.get("json", "{}")
        payload = json.loads(raw) if isinstance(raw, str) else {}
    except Exception as exc:
        logger.warning("Не удалось разобрать webhook Mango: %s: %s",
                       type(exc).__name__, exc)
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
