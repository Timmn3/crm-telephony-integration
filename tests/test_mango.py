"""Тесты Mango-клиента: подпись, обязательный маскирующий номер, инициация звонка."""
import hashlib
import json

import pytest
from aioresponses import aioresponses

from app.config import get_settings
from app.services.mango import (
    MangoClient,
    MangoError,
    MangoExtensionMissingError,
    build_callback_payload,
)

CALLBACK_URL = "https://app.mango-office.ru/vpbx/commands/callback"


@pytest.fixture
def mango_settings(monkeypatch):
    """Валидные Mango-настройки для теста (line_number обязателен после фикса маскирования)."""
    s = get_settings()
    monkeypatch.setattr(s, "mango_api_key", "KEY")
    monkeypatch.setattr(s, "mango_api_salt", "SALT")
    monkeypatch.setattr(s, "mango_line_number", "79917790890")
    return s


def test_signature_matches_payload():
    _, form = build_callback_payload(
        api_key="KEY", api_salt="SALT",
        manager_phone="79990000001", client_phone="79990000002",
        extension="15", line_number="79917790890", order_id=42, command_id="cb_42_100",
    )
    expected = hashlib.sha256(("KEY" + form["json"] + "SALT").encode()).hexdigest()
    assert form["sign"] == expected
    data = json.loads(form["json"])
    assert data["from"]["number"] == "79990000001"
    assert data["to_number"] == "79990000002"
    assert data["line_number"] == "79917790890"
    assert data["command_id"] == "cb_42_100"


def test_build_payload_requires_line_number():
    """Без маскирующего номера payload не собирается — иначе клиент увидит мобильный."""
    with pytest.raises(MangoError):
        build_callback_payload(
            api_key="KEY", api_salt="SALT",
            manager_phone="79990000001", client_phone="79990000002",
            extension="15", line_number="", order_id=42,
        )


async def test_initiate_ok(mango_settings):
    with aioresponses() as m:
        m.post(CALLBACK_URL, status=200, payload={"command_id": "x", "result": 1000})
        command_id, result = await MangoClient().initiate_callback(
            "79990000001", "79990000002", 42, extension="15"
        )
    assert command_id.startswith("cb_42_")
    assert result == {"command_id": "x", "result": 1000}


async def test_initiate_error(mango_settings):
    with aioresponses() as m:
        m.post(CALLBACK_URL, status=500, body="boom")
        with pytest.raises(MangoError):
            await MangoClient().initiate_callback(
                "79990000001", "79990000002", 42, extension="15"
            )


async def test_initiate_without_line_number_fails(monkeypatch):
    """Пустой MANGO_LINE_NUMBER → MangoError ещё до сетевого запроса."""
    s = get_settings()
    monkeypatch.setattr(s, "mango_api_key", "KEY")
    monkeypatch.setattr(s, "mango_api_salt", "SALT")
    monkeypatch.setattr(s, "mango_line_number", "")
    with pytest.raises(MangoError):
        await MangoClient().initiate_callback(
            "79990000001", "79990000002", 42, extension="15"
        )


async def test_initiate_without_extension_fails(mango_settings):
    """Без персонального extension — явная ошибка, а не звонок на чужой добавочный."""
    with pytest.raises(MangoExtensionMissingError):
        await MangoClient().initiate_callback(
            "79990000001", "79990000002", 42, extension=None
        )


async def test_initiate_without_extension_fails_on_empty_string(mango_settings):
    """Пустая строка (а не None) из БД тоже должна считаться «не задано»."""
    with pytest.raises(MangoExtensionMissingError):
        await MangoClient().initiate_callback(
            "79990000001", "79990000002", 42, extension=""
        )


@pytest.mark.parametrize("bad", ["88005559802", "7999", "89991234567", "7999123456a"])
def test_line_number_validator_rejects_bad_format(monkeypatch, bad):
    """Кривой формат маски (в т.ч. 8-800 как 88005559802) отсекается на уровне конфига."""
    from app import config
    monkeypatch.setenv("BOT_TOKEN", "dummy")
    monkeypatch.setenv("MANGO_LINE_NUMBER", bad)
    config.get_settings.cache_clear()
    with pytest.raises(Exception):
        config.Settings()
    config.get_settings.cache_clear()
