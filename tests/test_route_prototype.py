"""Тесты прототипа «звонок без интернета»: команда route и фильтрация событий.

Главное, что здесь проверяется, — что мы НЕ переводим чужой звонок. Mango шлёт
события по всем входящим ВАТС, включая звонки пациентов в колл-центр, поэтому
условия срабатывания проверяются по отдельности.
"""
import hashlib
import json

import pytest
from aioresponses import aioresponses

from app.api import webhooks
from app.config import get_settings
from app.services.mango import MangoError, build_route_payload

ROUTE_URL = "https://app.mango-office.ru/vpbx/commands/route"

SERVICE_LINE = "74950000001"   # служебная линия, куда звонит админ
ALLOWED_CALLER = "79990000011"  # тестовый телефон админа
TARGET = "79990000022"          # куда переводим


@pytest.fixture
def proto_settings(monkeypatch):
    """Полностью настроенный прототип."""
    s = get_settings()
    monkeypatch.setattr(s, "mango_api_key", "KEY")
    monkeypatch.setattr(s, "mango_api_salt", "SALT")
    monkeypatch.setattr(s, "mango_service_line", SERVICE_LINE)
    monkeypatch.setattr(s, "mango_test_caller", ALLOWED_CALLER)
    monkeypatch.setattr(s, "mango_test_target", TARGET)
    return s


@pytest.fixture(autouse=True)
def _clear_routed():
    """Сбрасываем память обработанных call_id — она глобальна на процесс."""
    webhooks._routed_calls.clear()
    yield
    webhooks._routed_calls.clear()


@pytest.fixture
def routed(monkeypatch):
    """Перехватывает route_call: список (call_id, to_number) вместо реального вызова."""
    calls: list[tuple[str, str]] = []

    async def fake_route(self, call_id, to_number, command_id=None):
        calls.append((call_id, to_number))
        return "rt_test", {"result": 1000}

    monkeypatch.setattr(webhooks.MangoClient, "route_call", fake_route)
    return calls


class FakeRequest:
    """Минимальная замена fastapi.Request — только form() с полем json."""

    def __init__(self, payload: dict | str):
        self._raw = payload if isinstance(payload, str) else json.dumps(payload)

    async def form(self):
        return {"json": self._raw}


def event(**over) -> dict:
    """Событие events/call с нужными нам полями; over переопределяет любое."""
    payload = {
        "call_id": "call-1",
        "call_state": "Appeared",
        "location": "abonent",
        "from": {"number": ALLOWED_CALLER},
        "to": {"line_number": SERVICE_LINE},
    }
    payload.update(over)
    return payload


def test_multipart_dependency_installed():
    """Страж зависимости: без python-multipart Starlette роняет ЛЮБОЙ request.form()
    — включая urlencoded. Webhook-и Mango при этом молча перестают работать и
    выглядят как «пришёл кривой json». Проверено на проде 29.07.2026.
    """
    import multipart  # noqa: F401


# --------------------------------------------------------------------------- #
# build_route_payload
# --------------------------------------------------------------------------- #

def test_route_signature_matches_payload():
    _, form = build_route_payload(
        api_key="KEY", api_salt="SALT", call_id="abc", to_number=TARGET,
        command_id="rt_1",
    )
    expected = hashlib.sha256(("KEY" + form["json"] + "SALT").encode()).hexdigest()
    assert form["sign"] == expected
    data = json.loads(form["json"])
    assert data == {"command_id": "rt_1", "call_id": "abc", "to_number": TARGET}
    # У route нет line_number — свой АОН здесь задать нельзя (офиц. дока).
    assert "line_number" not in data


@pytest.mark.parametrize("call_id,to_number", [("", TARGET), ("abc", "")])
def test_route_payload_requires_both(call_id, to_number):
    with pytest.raises(MangoError):
        build_route_payload(
            api_key="KEY", api_salt="SALT", call_id=call_id, to_number=to_number,
        )


async def test_route_call_ok(proto_settings):
    with aioresponses() as m:
        m.post(ROUTE_URL, status=200, payload={"result": 1000})
        command_id, result = await webhooks.MangoClient().route_call("abc", TARGET)
    assert command_id.startswith("rt_")
    assert result == {"result": 1000}


async def test_route_call_http_error(proto_settings):
    with aioresponses() as m:
        m.post(ROUTE_URL, status=500, body="boom")
        with pytest.raises(MangoError):
            await webhooks.MangoClient().route_call("abc", TARGET)


async def test_route_call_error_code(proto_settings):
    """Код результата не 1000/0 — считаем ошибкой."""
    with aioresponses() as m:
        m.post(ROUTE_URL, status=200, payload={"result": 4101})
        with pytest.raises(MangoError):
            await webhooks.MangoClient().route_call("abc", TARGET)


# --------------------------------------------------------------------------- #
# Фильтрация событий — что НЕ должно приводить к переводу
# --------------------------------------------------------------------------- #

async def test_event_routes_when_all_match(proto_settings, routed):
    resp = await webhooks.mango_events_call(FakeRequest(event()))
    assert resp == {"status": "ok"}
    assert routed == [("call-1", TARGET)]


async def test_event_ignored_when_prototype_disabled(proto_settings, routed, monkeypatch):
    """Не задан хотя бы один номер — прототип выключен целиком."""
    monkeypatch.setattr(proto_settings, "mango_test_target", "")
    await webhooks.mango_events_call(FakeRequest(event()))
    assert routed == []


async def test_event_ignored_for_other_line(proto_settings, routed):
    """Звонок пациента на номер колл-центра не должен быть переведён."""
    await webhooks.mango_events_call(
        FakeRequest(event(to={"line_number": "74959999999"}))
    )
    assert routed == []


async def test_event_ignored_for_other_caller(proto_settings, routed):
    """На служебную линию позвонил посторонний — игнорируем."""
    await webhooks.mango_events_call(
        FakeRequest(event(**{"from": {"number": "79995550000"}}))
    )
    assert routed == []


@pytest.mark.parametrize("state", ["Connected", "Disconnected", "OnHold", ""])
async def test_event_ignored_for_other_states(proto_settings, routed, state):
    """route применим только пока трубку не сняли (Appeared)."""
    await webhooks.mango_events_call(FakeRequest(event(call_state=state)))
    assert routed == []


async def test_event_routes_once_per_call(proto_settings, routed):
    """Mango шлёт несколько уведомлений по одному вызову — route уходит один раз."""
    for _ in range(3):
        await webhooks.mango_events_call(FakeRequest(event()))
    assert routed == [("call-1", TARGET)]


async def test_broken_payload_does_not_crash(proto_settings, routed):
    """Кривой json не должен ронять эндпоинт — Mango ждёт HTTP 200."""
    resp = await webhooks.mango_events_call(FakeRequest("{не json"))
    assert resp["status"] in {"ok", "ignored"}
    assert routed == []


async def test_route_failure_does_not_break_response(proto_settings, monkeypatch):
    """Ошибка Mango логируется, но эндпоинт всё равно отвечает 200."""
    async def boom(self, call_id, to_number, command_id=None):
        raise MangoError("Сервис Mango недоступен.")

    monkeypatch.setattr(webhooks.MangoClient, "route_call", boom)
    resp = await webhooks.mango_events_call(FakeRequest(event()))
    assert resp == {"status": "ok"}
