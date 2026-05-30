"""Тесты amoCRM-клиента с моками HTTP (aioresponses)."""
import pytest
from aioresponses import aioresponses

from app.config import get_settings
from app.db.repositories import settings_repo
from app.services.amocrm import AmoCRMClient, LeadNotFound, PhoneNotFound

BASE = "https://test.amocrm.ru"


async def test_get_order_data_parses_lead_and_contact(session):
    await settings_repo.set_value(session, settings_repo.AMOCRM_ACCESS_TOKEN, "tok")
    settings = get_settings()
    settings.amo_address_field_id = 777
    try:
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
    finally:
        settings.amo_address_field_id = None

    assert data.client_phone == "79991234567"
    assert data.client_name == "Иванов"
    assert data.client_address == "ул. Ленина, 10"
    assert data.comment == "Замер окон"


async def test_lead_not_found(session):
    await settings_repo.set_value(session, settings_repo.AMOCRM_ACCESS_TOKEN, "tok")
    with aioresponses() as m:
        m.get(f"{BASE}/api/v4/leads/404?with=contacts", status=404)
        with pytest.raises(LeadNotFound):
            await AmoCRMClient(session).get_order_data(404)


async def test_phone_not_found(session):
    await settings_repo.set_value(session, settings_repo.AMOCRM_ACCESS_TOKEN, "tok")
    with aioresponses() as m:
        m.get(
            f"{BASE}/api/v4/leads/500?with=contacts",
            payload={"id": 500, "name": "X", "_embedded": {"contacts": [{"id": 7}]}},
        )
        m.get(f"{BASE}/api/v4/contacts/7", payload={"id": 7, "name": "Без телефона"})
        with pytest.raises(PhoneNotFound):
            await AmoCRMClient(session).get_order_data(500)


async def test_token_refresh_on_401(session):
    await settings_repo.set_value(session, settings_repo.AMOCRM_ACCESS_TOKEN, "old")
    await settings_repo.set_value(session, settings_repo.AMOCRM_REFRESH_TOKEN, "oldref")
    settings = get_settings()
    settings.amocrm_client_id = "cid"
    settings.amocrm_client_secret = "csec"
    try:
        with aioresponses() as m:
            m.get(f"{BASE}/api/v4/leads/999?with=contacts", status=401)
            m.post(
                f"{BASE}/oauth2/access_token",
                payload={"access_token": "newtok", "refresh_token": "newref"},
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
    finally:
        settings.amocrm_client_id = ""
        settings.amocrm_client_secret = ""

    assert data.client_phone == "79990000000"
    assert await settings_repo.get(session, settings_repo.AMOCRM_ACCESS_TOKEN) == "newtok"
