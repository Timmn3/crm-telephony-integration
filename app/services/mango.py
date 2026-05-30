"""Клиент Mango Office API (инициация callback-звонка).

Подпись запроса: sha256(api_key + json_string + api_salt). Та же строка json
используется и в подписи, и в поле form-data `json` — иначе подпись не сойдётся.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)


class MangoError(Exception):
    """Ошибка при инициации звонка через Mango."""


def build_callback_payload(
    api_key: str,
    api_salt: str,
    manager_phone: str,
    client_phone: str,
    line_number: str,
    order_id: int,
    command_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Формирует (command_id, form-data) для callback-запроса к Mango.

    Телефоны — в формате 7XXXXXXXXXX.
    """
    if command_id is None:
        command_id = f"cb_{order_id}_{int(time.time())}"

    data = {
        "command_id": command_id,
        "from": {"extension": "", "number": manager_phone},
        "to_number": client_phone,
        "line_number": line_number,
    }
    json_str = json.dumps(data)
    sign = hashlib.sha256((api_key + json_str + api_salt).encode()).hexdigest()
    form = {
        "vpbx_api_key": api_key,
        "sign": sign,
        "json": json_str,
    }
    return command_id, form


class MangoClient:
    """Асинхронный клиент Mango Office."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def initiate_callback(
        self, manager_phone: str, client_phone: str, order_id: int
    ) -> tuple[str, dict]:
        """Инициирует звонок callback. Возвращает (command_id, ответ Mango).

        Бросает MangoError при сетевой ошибке или ошибочном ответе API.
        ВАЖНО: номера клиента/менеджера не логируются.
        """
        if not (self.settings.mango_api_key and self.settings.mango_api_salt
                and self.settings.mango_line_number):
            raise MangoError("Не настроены параметры Mango (key/salt/line_number).")

        command_id, form = build_callback_payload(
            api_key=self.settings.mango_api_key,
            api_salt=self.settings.mango_api_salt,
            manager_phone=manager_phone,
            client_phone=client_phone,
            line_number=self.settings.mango_line_number,
            order_id=order_id,
        )
        url = f"{self.settings.mango_api_url.rstrip('/')}/commands/callback"

        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
                async with http.post(url, data=form) as resp:
                    text = await resp.text()
                    status = resp.status
        except aiohttp.ClientError as exc:
            logger.error("Сетевая ошибка Mango (order=%s): %s", order_id, exc)
            raise MangoError("Сервис Mango недоступен.") from exc

        try:
            result = json.loads(text) if text else {}
        except json.JSONDecodeError:
            result = {"raw": text}

        if status != 200:
            logger.error("Mango ответил %s (order=%s): %s", status, order_id, result)
            raise MangoError(f"Mango вернул ошибку (HTTP {status}).")

        logger.info("Звонок инициирован через Mango: order=%s command_id=%s",
                    order_id, command_id)
        return command_id, result
