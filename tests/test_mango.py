"""Тесты Mango-клиента: подпись и инициация звонка."""
import hashlib
import json

import pytest
from aioresponses import aioresponses

from app.services.mango import MangoClient, MangoError, build_callback_payload

CALLBACK_URL = "https://app.mango-office.ru/vpbx/commands/callback"


def test_signature_matches_payload():
    command_id, form = build_callback_payload(
        api_key="KEY", api_salt="SALT",
        manager_phone="79990000001", client_phone="79990000002",
        line_number="78001234567", order_id=42, command_id="cb_42_100",
    )
    expected = hashlib.sha256(("KEY" + form["json"] + "SALT").encode()).hexdigest()
    assert form["sign"] == expected
    data = json.loads(form["json"])
    assert data["from"]["number"] == "79990000001"
    assert data["to_number"] == "79990000002"
    assert data["line_number"] == "78001234567"
    assert data["command_id"] == "cb_42_100"


async def test_initiate_ok():
    with aioresponses() as m:
        m.post(CALLBACK_URL, status=200, payload={"command_id": "x"})
        command_id, result = await MangoClient().initiate_callback(
            "79990000001", "79990000002", 42
        )
    assert command_id.startswith("cb_42_")
    assert result == {"command_id": "x"}


async def test_initiate_error():
    with aioresponses() as m:
        m.post(CALLBACK_URL, status=500, body="boom")
        with pytest.raises(MangoError):
            await MangoClient().initiate_callback("79990000001", "79990000002", 42)
