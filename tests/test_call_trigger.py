"""Тесты сценария «звонок-сигнал».

Главное, что здесь проверяется, — что бот НЕ звонит, когда не должен. Статистика
Mango отдаёт все входящие ВАТС, включая звонки пациентов в колл-центр, поэтому
каждое условие срабатывания проверяется отдельно.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.db.models import OrderStatus, UserRole
from app.db.repositories import group_repo, order_repo, user_repo
from app.services import call_trigger
from app.services.call_service import CallStartResult
from app.services.mango import (
    MangoError,
    build_stats_request_payload,
    build_stats_result_payload,
    parse_stats_calls,
)

SERVICE_LINE = "74950000001"
ADMIN_PHONE = "79990000011"
CLIENT_PHONE = "79990000022"


# --------------------------------------------------------------------------- #
# Формирование запроса статистики
# --------------------------------------------------------------------------- #

def test_stats_payload_signature_and_body():
    form = build_stats_request_payload(
        "KEY", "SALT", window_minutes=15,
        now=datetime(2026, 7, 30, 8, 0, 0), limit=100,
    )
    expected = hashlib.sha256(("KEY" + form["json"] + "SALT").encode()).hexdigest()
    assert form["sign"] == expected

    body = json.loads(form["json"])
    assert body["context_type"] == 1        # только входящие
    assert body["limit"] == 100
    assert body["offset"] == 0


def test_stats_payload_shifts_window_to_moscow():
    """Mango считает даты московскими, сервер живёт в UTC — иначе окно срезает свежее."""
    form = build_stats_request_payload(
        "KEY", "SALT", window_minutes=15, now=datetime(2026, 7, 30, 8, 0, 0),
    )
    body = json.loads(form["json"])
    assert body["end_date"] == "30.07.2026 11:00:00"
    assert body["start_date"] == "30.07.2026 10:45:00"


@pytest.mark.parametrize("bad_limit", [15, 99, 3, 0])
def test_stats_payload_rejects_bad_limit(bad_limit):
    """У Mango закрытый список значений limit; произвольное = неверный запрос,
    а за них блокируют доступ к API на 2 минуты."""
    with pytest.raises(MangoError):
        build_stats_request_payload("KEY", "SALT", window_minutes=15, limit=bad_limit)


def test_stats_result_payload_signature():
    form = build_stats_result_payload("KEY", "SALT", "abc")
    assert json.loads(form["json"]) == {"key": "abc"}
    assert form["sign"] == hashlib.sha256(
        ("KEY" + form["json"] + "SALT").encode()
    ).hexdigest()


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{}]}, {"data": ["x"]}])
def test_parse_stats_survives_empty(payload):
    assert parse_stats_calls(payload) == []


def test_parse_stats_extracts_list():
    calls = parse_stats_calls({"data": [{"list": [{"entry_id": 1}, {"entry_id": 2}]}]})
    assert [c["entry_id"] for c in calls] == [1, 2]


# --------------------------------------------------------------------------- #
# Сравнение номеров и отсечка по времени
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("left,right,expected", [
    ("79883376699", "89883376699", True),      # 7 и 8 — один номер
    ("+7 988 337-66-99", "89883376699", True),
    ("79883376699", "79051244561", False),
    ("", "79051244561", False),
    ("123", "79051244561", False),
])
def test_same_number(left, right, expected):
    assert call_trigger._same_number(left, right) is expected


def test_call_before_approval_rejected():
    """Вчерашний звонок не должен поднимать сегодняшнюю заявку."""
    approved = datetime(2026, 7, 30, 10, 0, 0, tzinfo=timezone.utc)
    assert call_trigger._is_after_approval(approved.timestamp() + 60, approved) is True
    assert call_trigger._is_after_approval(approved.timestamp() - 60, approved) is False
    assert call_trigger._is_after_approval("не число", approved) is False


# --------------------------------------------------------------------------- #
# Обработка входящего: фикстуры
# --------------------------------------------------------------------------- #

class _SessionProxy:
    """Отдаёт воркеру тестовую сессию вместо реальной фабрики (и не закрывает её)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _clear_handled():
    call_trigger._handled_calls.clear()
    call_trigger._nudged_orders.clear()
    yield
    call_trigger._handled_calls.clear()
    call_trigger._nudged_orders.clear()


@pytest.fixture
def trigger_settings(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "call_trigger_enabled", True)
    monkeypatch.setattr(s, "mango_service_line", SERVICE_LINE)
    return s


@pytest.fixture
def started(monkeypatch, session):
    """Перехватывает call_service.start_call: список order_id вместо звонка в Mango."""
    calls: list[int] = []

    async def fake_start(sess, bot, order, user, *, source="button"):
        calls.append(order.id)
        return CallStartResult(ok=True, command_id="cb_test", message="ok")

    monkeypatch.setattr(call_trigger.call_service, "start_call", fake_start)
    monkeypatch.setattr(
        call_trigger, "async_session_factory", lambda: _SessionProxy(session)
    )
    return calls


async def _make_order(session, *, phone=ADMIN_PHONE, status=OrderStatus.CALL_APPROVED,
                      approved_shift_sec=-60):
    """Админ с телефоном + его заявка. approved_shift_sec — когда её одобрили."""
    group = await group_repo.create(session, name=f"G-{phone}", city="Тест")
    admin = await user_repo.create(
        session, tg_id=int(phone[-9:]), role=UserRole.ADMIN,
        full_name="Тестовый админ", group_id=group.id, phone=phone,
    )
    await user_repo.set_mango_extension(session, admin, "101")
    order = await order_repo.create(
        session, amo_lead_id=int(phone[-6:]), client_name="Клиент",
        client_phone=CLIENT_PHONE, group_id=group.id,
        operator_tg_id=999999, manager_tg_id=admin.tg_id, status=status,
    )
    if status == OrderStatus.CALL_APPROVED:
        order.call_approved_at = datetime.now(timezone.utc) + timedelta(
            seconds=approved_shift_sec
        )
    await session.flush()
    return admin, order


def _event(**over) -> dict:
    payload = {
        "entry_id": 555,
        "caller_number": ADMIN_PHONE,
        "called_number": SERVICE_LINE,
        "context_start_time": datetime.now(timezone.utc).timestamp(),
        "duration": 12,
        "context_status": 0,
    }
    payload.update(over)
    return payload


# --------------------------------------------------------------------------- #
# Обработка входящего: сценарии
# --------------------------------------------------------------------------- #

async def test_signal_starts_call(session, trigger_settings, started, fake_bot):
    admin, order = await _make_order(session)
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())
    assert started == [order.id]


async def test_ignores_other_line(session, trigger_settings, started, fake_bot):
    """Звонок пациента на номер колл-центра не должен ничего запускать."""
    await _make_order(session)
    await call_trigger._handle_incoming(
        fake_bot, trigger_settings, _event(called_number="74959999999")
    )
    assert started == []


async def test_ignores_unknown_caller(session, trigger_settings, started, fake_bot):
    await _make_order(session)
    await call_trigger._handle_incoming(
        fake_bot, trigger_settings, _event(caller_number="79995550000")
    )
    assert started == []


async def test_no_active_orders_only_notifies_admin(session, trigger_settings,
                                                    started, fake_bot):
    """Активных заявок нет — звонить некуда, менеджера дёргать незачем."""
    group = await group_repo.create(session, name="G-empty", city="Тест")
    admin = await user_repo.create(
        session, tg_id=555000111, role=UserRole.ADMIN,
        full_name="Без заявок", group_id=group.id, phone=ADMIN_PHONE,
    )
    await session.flush()

    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())
    assert started == []
    assert any(m["chat_id"] == admin.tg_id and "активных заявок" in m["text"]
               for m in fake_bot.sent)


# --------------------------------------------------------------------------- #
# Эскалация: звонок вместо кнопки «Запросить звонок»
# --------------------------------------------------------------------------- #

MANAGER_TG_ID = 999999   # operator_tg_id, который проставляет _make_order


async def test_sent_order_becomes_call_request(session, trigger_settings,
                                               started, fake_bot):
    """Админ без интернета не может нажать кнопку — звонок делает это за него."""
    admin, order = await _make_order(session, status=OrderStatus.SENT)

    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())

    assert started == []                       # звонить пока рано, нужно одобрение
    assert order.status == OrderStatus.CALL_REQUESTED
    assert order.call_requested_at is not None

    to_manager = [m for m in fake_bot.sent if m["chat_id"] == MANAGER_TG_ID]
    assert len(to_manager) == 1
    assert "Запрос на звонок" in to_manager[0]["text"]
    assert to_manager[0]["reply_markup"] is not None   # кнопки Одобрить/Отклонить


async def test_requested_order_nudges_manager(session, trigger_settings,
                                              started, fake_bot):
    """Заявка уже ждёт одобрения — статус не трогаем, менеджеру напоминаем."""
    admin, order = await _make_order(session, status=OrderStatus.CALL_REQUESTED)

    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())

    assert started == []
    assert order.status == OrderStatus.CALL_REQUESTED

    to_manager = [m for m in fake_bot.sent if m["chat_id"] == MANAGER_TG_ID]
    assert len(to_manager) == 1
    assert "Напоминание" in to_manager[0]["text"]
    assert "звонил на служебный номер" in to_manager[0]["text"]


async def test_nudge_respects_cooldown(session, trigger_settings, started, fake_bot):
    """Настойчивый админ не должен завалить менеджера одинаковыми сообщениями."""
    await _make_order(session, status=OrderStatus.CALL_REQUESTED)

    # Разные entry_id — иначе сработает дедупликация звонков, а не кулдаун.
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event(entry_id=1))
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event(entry_id=2))
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event(entry_id=3))

    to_manager = [m for m in fake_bot.sent if m["chat_id"] == MANAGER_TG_ID]
    assert len(to_manager) == 1


async def test_call_in_progress_ignored(session, trigger_settings, started, fake_bot):
    """Звонок по заявке уже идёт — второй сигнал ничего не меняет."""
    admin, order = await _make_order(session, status=OrderStatus.CALL_IN_PROGRESS)

    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())

    assert started == []
    assert order.status == OrderStatus.CALL_IN_PROGRESS
    assert [m for m in fake_bot.sent if m["chat_id"] == MANAGER_TG_ID] == []


async def test_approved_order_wins_over_sent(session, trigger_settings,
                                             started, fake_bot):
    """Из нескольких заявок берём готовую к звонку, а не ждущую одобрения."""
    admin, sent_order = await _make_order(session, status=OrderStatus.SENT)
    approved = await order_repo.create(
        session, amo_lead_id=777777, client_name="Клиент-2",
        client_phone=CLIENT_PHONE, group_id=sent_order.group_id,
        operator_tg_id=MANAGER_TG_ID, manager_tg_id=admin.tg_id,
        status=OrderStatus.CALL_APPROVED,
    )
    approved.call_approved_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    await session.flush()

    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())

    assert started == [approved.id]
    assert sent_order.status == OrderStatus.SENT   # вторую заявку не тронули


async def test_ignores_call_made_before_approval(session, trigger_settings, started, fake_bot):
    """Заявку одобрили ПОСЛЕ звонка — значит звонок был не про неё."""
    await _make_order(session, approved_shift_sec=+300)
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())
    assert started == []


async def test_same_call_handled_once(session, trigger_settings, started, fake_bot):
    """Один и тот же входящий висит в окне выборки минутами — соединяем один раз."""
    admin, order = await _make_order(session)
    for _ in range(3):
        await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())
    assert started == [order.id]


async def test_admin_gets_notified(session, trigger_settings, started, fake_bot):
    admin, _ = await _make_order(session)
    await call_trigger._handle_incoming(fake_bot, trigger_settings, _event())
    assert any(m["chat_id"] == admin.tg_id for m in fake_bot.sent)


# --------------------------------------------------------------------------- #
# Цикл воркера
# --------------------------------------------------------------------------- #

async def test_tick_polls_for_sent_order(monkeypatch, session, trigger_settings,
                                         started, fake_bot):
    """Заявка ещё не одобрена — опрашивать всё равно надо: звонок работает как запрос.

    Этот случай уже ломался в бою: опрос запускался только при CALL_APPROVED, и
    эскалация не срабатывала вообще.
    """
    admin, order = await _make_order(session, status=OrderStatus.SENT)
    polled = False

    async def fake_fetch(self, **kwargs):
        nonlocal polled
        polled = True
        return [_event()]

    monkeypatch.setattr(call_trigger.MangoClient, "fetch_recent_incoming", fake_fetch)
    worked = await call_trigger._tick(fake_bot, trigger_settings)

    assert worked is True
    assert polled is True
    assert order.status == OrderStatus.CALL_REQUESTED
    assert any(m["chat_id"] == MANAGER_TG_ID for m in fake_bot.sent)


async def test_tick_skips_api_without_any_orders(monkeypatch, session,
                                                 trigger_settings, fake_bot):
    """Нет заявок вообще — в API не ходим (экономим общий лимит ВАТС)."""
    called = False

    async def fake_fetch(self, **kwargs):
        nonlocal called
        called = True
        return []

    monkeypatch.setattr(call_trigger.MangoClient, "fetch_recent_incoming", fake_fetch)
    monkeypatch.setattr(
        call_trigger, "async_session_factory", lambda: _SessionProxy(session)
    )
    worked = await call_trigger._tick(fake_bot, trigger_settings)
    assert worked is False
    assert called is False


async def test_tick_polls_when_orders_waiting(monkeypatch, session, trigger_settings,
                                              started, fake_bot):
    admin, order = await _make_order(session)

    async def fake_fetch(self, **kwargs):
        return [_event()]

    monkeypatch.setattr(call_trigger.MangoClient, "fetch_recent_incoming", fake_fetch)
    worked = await call_trigger._tick(fake_bot, trigger_settings)
    assert worked is True
    assert started == [order.id]
