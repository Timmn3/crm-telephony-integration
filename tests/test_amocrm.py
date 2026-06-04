"""Тесты amoCRM-клиента: режим-заглушка и реальный API с моками (aioresponses)."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from aioresponses import aioresponses

from app.config import get_settings
from app.db.repositories import amo_token_repo
from app.services.amocrm import AmoCRMClient, LeadNotFound, PhoneNotFound

BASE = "https://test.amocrm.ru"


@contextmanager
def configured_amo():
    """Временно делает amoCRM «настроенным» (выходим из режима-заглушки)."""
    s = get_settings()
    saved = (s.amocrm_subdomain, s.amocrm_client_id, s.amocrm_client_secret,
             s.amo_address_field_id)
    s.amocrm_subdomain = "test.amocrm.ru"
    s.amocrm_client_id = "cid"
    s.amocrm_client_secret = "csec"
    try:
        yield s
    finally:
        (s.amocrm_subdomain, s.amocrm_client_id, s.amocrm_client_secret,
         s.amo_address_field_id) = saved


async def _save_valid_token(session) -> None:
    await amo_token_repo.save(
        session, access_token="tok", refresh_token="ref",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )


async def test_stub_mode_returns_fake_client(session):
    """Без реквизитов интеграции — синтетический клиент, без обращения к сети."""
    client = AmoCRMClient(session)
    assert client.is_stub is True
    data = await client.get_order_data(424242)
    assert data.amo_lead_id == 424242
    # Телефон-заглушка берётся из настроек (AMOCRM_STUB_PHONE).
    assert data.client_phone == get_settings().amocrm_stub_phone
    assert data.client_phone  # не пустой
    assert "Тестовый" in data.client_name


async def test_get_order_data_parses_lead_and_contact(session):
    with configured_amo() as s:
        s.amo_address_field_id = 777
        await _save_valid_token(session)
        with aioresponses() as m:
            m.get(
                f"{BASE}/api/v4/leads/12345?with=contacts",
                payload={
                    "id": 12345, "name": "Замер окон",
                    "custom_fields_values": [
                        {"field_id": 777, "values": [{"value": "ул. Ленина, 10"}]}
                    ],
                    "_embedded": {"contacts": [{"id": 55, "is_main": True}]},
                },
            )
            m.get(
                f"{BASE}/api/v4/contacts/55",
                payload={
                    "id": 55, "name": "Иванов",
                    "custom_fields_values": [
                        {"field_code": "PHONE", "values": [{"value": "+7 (999) 123-45-67"}]}
                    ],
                },
            )
            data = await AmoCRMClient(session).get_order_data(12345)

    assert data.client_phone == "79991234567"
    assert data.client_name == "Иванов"
    assert data.client_address == "ул. Ленина, 10"
    assert data.comment == "Замер окон"


async def test_lead_not_found(session):
    with configured_amo():
        await _save_valid_token(session)
        with aioresponses() as m:
            m.get(f"{BASE}/api/v4/leads/404?with=contacts", status=404)
            with pytest.raises(LeadNotFound):
                await AmoCRMClient(session).get_order_data(404)


async def test_phone_not_found(session):
    with configured_amo():
        await _save_valid_token(session)
        with aioresponses() as m:
            m.get(
                f"{BASE}/api/v4/leads/500?with=contacts",
                payload={"id": 500, "name": "X", "_embedded": {"contacts": [{"id": 7}]}},
            )
            m.get(f"{BASE}/api/v4/contacts/7", payload={"id": 7, "name": "Без телефона"})
            with pytest.raises(PhoneNotFound):
                await AmoCRMClient(session).get_order_data(500)


async def test_token_refresh_on_401(session):
    with configured_amo():
        await _save_valid_token(session)
        with aioresponses() as m:
            m.get(f"{BASE}/api/v4/leads/999?with=contacts", status=401)
            m.post(
                f"{BASE}/oauth2/access_token",
                payload={"access_token": "newtok", "refresh_token": "newref",
                         "expires_in": 86400},
            )
            m.get(
                f"{BASE}/api/v4/leads/999?with=contacts",
                payload={"id": 999, "name": "Y", "_embedded": {"contacts": [{"id": 8}]}},
            )
            m.get(
                f"{BASE}/api/v4/contacts/8",
                payload={
                    "id": 8, "name": "Петров",
                    "custom_fields_values": [
                        {"field_code": "PHONE", "values": [{"value": "89990000000"}]}
                    ],
                },
            )
            data = await AmoCRMClient(session).get_order_data(999)

    assert data.client_phone == "79990000000"
    token = await amo_token_repo.get(session)
    assert token.access_token == "newtok"
    assert token.refresh_token == "newref"
