"""Сценарий «звонок-сигнал»: админ без интернета звонит — бот соединяет.

Выездной администратор набирает служебный номер ВАТС и кладёт трубку. Бот видит
этот входящий в статистике Mango, находит по АОН администратора и его одобренную
заявку, после чего инициирует обычный `callback` — тот самый, что работает по
кнопке в Telegram. Соединяет именно `callback`, поэтому маскирующий номер задаём
мы сами и настройки ВАТС трогать не нужно.

Почему опрос, а не webhook: события `events/call` Mango шлёт только на адрес
внешней системы, а поле для него открывается лишь после платной активации API
коннектора. Опрос бесплатен, но данные отстают на 1-2 минуты — это ограничение
Mango, согласованное с заказчиком.

Чтобы не жечь лимит API (1 запрос в 2 секунды на всю ВАТС, делим с интеграцией
amoCRM), статистика запрашивается только когда есть заявки, ожидающие звонка.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from app.config import Settings, get_settings
from app.db.database import async_session_factory
from app.db.models import OrderStatus
from app.db.repositories import order_repo, user_repo
from app.services import call_service, order_service
from app.services.mango import MangoClient, MangoError, MangoRateLimited
from app.utils.phone import mask_phone

logger = logging.getLogger(__name__)

# Уже отработанные звонки: один и тот же входящий висит в окне выборки несколько
# минут, а соединять по нему нужно ровно один раз. Хватает памяти процесса —
# после перезапуска от повторов защищают статус заявки и отсечка по времени.
_handled_calls: OrderedDict[str, None] = OrderedDict()
_HANDLED_MAX = 500

# Когда по заявке последний раз просили менеджера одобрить звонок (monotonic).
# Нужен, чтобы настойчивые звонки админа не превратились в спам менеджеру.
_nudged_orders: OrderedDict[int, float] = OrderedDict()


def _digits(value: object) -> str:
    """Только цифры номера: сравнивать форматы 7XXX/8XXX/+7XXX напрямую нельзя."""
    return re.sub(r"\D", "", str(value or ""))


def _same_number(left: object, right: object) -> bool:
    """Совпадают ли номера по последним 10 цифрам (без учёта 7/8/+7)."""
    a, b = _digits(left), _digits(right)
    return len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:]


def _already_handled(entry_id: str) -> bool:
    """Помечает звонок обработанным. True — если его уже отрабатывали раньше."""
    if entry_id in _handled_calls:
        return True
    _handled_calls[entry_id] = None
    while len(_handled_calls) > _HANDLED_MAX:
        _handled_calls.popitem(last=False)
    return False


def _nudge_on_cooldown(order_id: int) -> bool:
    """True — напоминание по этой заявке отправляли только что, второе не шлём.

    Админ может звонить настойчиво (связь плохая, кажется что не сработало), а
    менеджеру от десятка одинаковых сообщений подряд толку нет.
    """
    cooldown = get_settings().call_trigger_nudge_cooldown_min * 60
    now = time.monotonic()
    last = _nudged_orders.get(order_id)
    if last is not None and now - last < cooldown:
        return True
    _nudged_orders[order_id] = now
    _nudged_orders.move_to_end(order_id)
    while len(_nudged_orders) > _HANDLED_MAX:
        _nudged_orders.popitem(last=False)
    return False


async def _notify(bot: Bot, tg_id: int, text: str) -> None:
    """Безопасная отправка: у админа может быть закрыт ЛС, и это не наша беда."""
    try:
        await bot.send_message(tg_id, text)
    except (TelegramForbiddenError, TelegramBadRequest) as exc:
        logger.warning("Не удалось уведомить tg_id=%s: %s", tg_id, exc)


async def _handle_incoming(bot: Bot, settings: Settings, call: dict) -> None:
    """Обрабатывает один входящий звонок из статистики."""
    if not _same_number(call.get("called_number"), settings.mango_service_line):
        return  # звонок на другой номер ВАТС — это пациент в колл-центр, не трогаем

    caller = _digits(call.get("caller_number"))
    entry_id = str(call.get("entry_id") or "")
    if not caller or not entry_id:
        return
    if _already_handled(entry_id):
        return

    logger.info("Сигнал на служебную линию: from=%s entry=%s",
                mask_phone(caller), entry_id)

    started = call.get("context_start_time")

    async with async_session_factory() as session:
        admin = await user_repo.get_admin_by_phone(session, caller)
        if admin is None:
            logger.info("Сигнал с неизвестного номера %s — игнорируем", mask_phone(caller))
            return

        order = await order_repo.get_active_for_signal(session, admin.tg_id)
        if order is None:
            logger.info("У администратора tg_id=%s нет активных заявок — игнорируем",
                        admin.tg_id)
            await _notify(
                bot, admin.tg_id,
                "☎️ Вы позвонили на служебный номер, но активных заявок за вами нет.",
            )
            return

        reply = await _act_on_order(bot, session, admin, order, started)
        await session.commit()

    if reply:
        await _notify(bot, admin.tg_id, reply)


async def _act_on_order(bot: Bot, session, admin, order, started) -> str | None:
    """Решает, что делать со звонком, исходя из статуса заявки.

    Возвращает текст для администратора (или None, если писать нечего). Админ
    звонит без интернета и увидит сообщение позже — но когда увидит, должен
    понять, что произошло.
    """
    if order.status == OrderStatus.CALL_IN_PROGRESS:
        logger.info("По заявке #%s звонок уже идёт — сигнал игнорируем", order.id)
        return None

    if order.status == OrderStatus.CALL_APPROVED:
        # Звонок должен быть ПОСЛЕ одобрения: иначе вчерашний сигнал поднимет
        # сегодняшнюю заявку, как только её одобрят.
        if not _is_after_approval(started, order.call_approved_at):
            logger.info("Сигнал старше одобрения заявки #%s — игнорируем", order.id)
            return None
        if not admin.phone:
            logger.warning("У администратора tg_id=%s нет телефона в профиле", admin.tg_id)
            return None
        result = await call_service.start_call(session, bot, order, admin, source="trigger")
        return result.message

    # Осталось SENT и CALL_REQUESTED: звонок работает как «запросить звонок» —
    # админу для этого не нужен Telegram, в чём и весь смысл сценария.
    if _nudge_on_cooldown(order.id):
        logger.info("Напоминание по заявке #%s недавно отправляли — пропускаем", order.id)
        return None

    was_requested = order.status == OrderStatus.CALL_REQUESTED
    if not was_requested:
        await order_repo.set_status(
            session, order, OrderStatus.CALL_REQUESTED, set_call_requested_at=True
        )
        await order_service.refresh_card(bot, order)

    await order_service.notify_call_request(bot, order, admin, repeated=was_requested)
    logger.info("Заявка #%s: менеджеру отправлен %s одобрения (сигнал с телефона)",
                order.id, "повторный запрос" if was_requested else "запрос")
    return (
        "📨 Менеджеру отправлено напоминание — ждём одобрения звонка."
        if was_requested else
        "📨 Запрос на звонок отправлен менеджеру. Как одобрит — позвоните ещё раз."
    )


def _is_after_approval(started: object, approved_at: datetime | None) -> bool:
    """True, если звонок случился позже одобрения заявки."""
    if approved_at is None:
        return True
    if not isinstance(started, (int, float)):
        return False
    started_at = datetime.fromtimestamp(float(started), tz=timezone.utc)
    if approved_at.tzinfo is None:
        approved_at = approved_at.replace(tzinfo=timezone.utc)
    return started_at >= approved_at


async def _tick(bot: Bot, settings: Settings) -> bool:
    """Одна итерация. Возвращает True, если опрашивали API (были одобренные заявки)."""
    async with async_session_factory() as session:
        pending = await order_repo.count_awaiting_signal(session)
    if not pending:
        return False

    calls = await MangoClient().fetch_recent_incoming(
        window_minutes=settings.call_trigger_window_min
    )
    for call in calls:
        await _handle_incoming(bot, settings, call)
    return True


async def run_call_trigger(bot: Bot) -> None:
    """Фоновый цикл опроса. Запускается из app/main.py рядом с polling, uvicorn
    и воркером автозакрытия заявок.

    Ни одна ошибка внутри не должна ронять задачу: она живёт в общем
    `asyncio.gather`, и её падение остановило бы бота целиком.
    """
    settings = get_settings()
    logger.info(
        "Воркер «звонок-сигнал»: %s",
        "запущен" if settings.call_trigger_active else "выключен (ждёт настройки)",
    )
    while True:
        delay = settings.call_trigger_idle_sec
        try:
            if settings.call_trigger_active:
                worked = await _tick(bot, settings)
                delay = settings.call_trigger_poll_sec if worked else settings.call_trigger_idle_sec
        except asyncio.CancelledError:
            raise
        except MangoRateLimited as exc:
            # Лимит общий на всю ВАТС — просто подождать подольше, это не поломка.
            logger.info("Опрос статистики: %s Пауза.", exc)
            delay = max(settings.call_trigger_poll_sec, 30)
        except MangoError as exc:
            logger.warning("Опрос статистики не удался: %s", exc)
        except Exception:
            logger.exception("Непредвиденная ошибка воркера «звонок-сигнал»")
        await asyncio.sleep(delay)
