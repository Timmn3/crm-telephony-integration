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


class MangoConfigError(MangoError):
    """Mango не настроен (нет key/salt/line_number) — нужна правка .env."""


class MangoExtensionMissingError(MangoConfigError):
    """У сотрудника не задан персональный extension — звонить не на что.

    Раньше тут был тихий fallback на общий MANGO_EXTENSION из .env, из-за чего
    звонок улетал на чужой добавочный, а не тому админу, который его запросил.
    """


def build_callback_payload(
    api_key: str,
    api_salt: str,
    manager_phone: str,
    client_phone: str,
    extension: str,
    order_id: int,
    line_number: str,
    command_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Формирует (command_id, form-data) для callback-запроса к Mango.

    Телефоны — в формате 7XXXXXXXXXX.
    extension — внутренний короткий номер сотрудника в ВАТС Mango (обязателен).
    line_number — корпоративный номер-маска (АОН), который видит клиент. Обязателен:
    без него Mango подставляет личный мобильный сотрудника — маскирование ломается.
    """
    if not line_number:
        raise MangoError(
            "MANGO_LINE_NUMBER не задан — звонок без маскирующего номера запрещён."
        )
    if command_id is None:
        command_id = f"cb_{order_id}_{int(time.time())}"

    data: dict = {
        "command_id": command_id,
        "from": {"extension": extension, "number": manager_phone},
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


def build_route_payload(
    api_key: str,
    api_salt: str,
    call_id: str,
    to_number: str,
    command_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Формирует (command_id, form-data) для команды `route` — перевода вызова.

    Применяется к УЖЕ существующему входящему вызову: сотрудник звонит на служебную
    линию, а мы переводим этот вызов на нужный внешний номер (офиц. дока Mango,
    раздел 3.2.7, стр. 46 — перевод на номер формата pstn разрешён безусловно).

    В отличие от callback, у route НЕТ параметра line_number: свой АОН здесь задать
    нельзя, номер вызываемому показывает сама ВАТС. См. docs/mango/README.md.
    """
    if not call_id:
        raise MangoError("route: не передан call_id — переводить нечего.")
    if not to_number:
        raise MangoError("route: не передан to_number — переводить некуда.")
    if command_id is None:
        command_id = f"rt_{int(time.time() * 1000)}"

    data: dict = {
        "command_id": command_id,
        "call_id": call_id,
        "to_number": to_number,
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

    async def _post_command(
        self, path: str, form: dict[str, str], *, context: str
    ) -> dict:
        """Шлёт команду в Mango и разбирает ответ. Общая часть всех команд.

        `context` — что писать в логи для привязки к сущности (напр. "order=42").
        Телефоны в context передавать НЕЛЬЗЯ — он попадает в логи.
        """
        url = f"{self.settings.mango_api_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
                async with http.post(url, data=form) as resp:
                    text = await resp.text()
                    status = resp.status
        except aiohttp.ClientError as exc:
            logger.error("Сетевая ошибка Mango (%s): %s", context, exc)
            raise MangoError("Сервис Mango недоступен.") from exc

        try:
            result = json.loads(text) if text else {}
        except json.JSONDecodeError:
            result = {"raw": text}

        if status != 200:
            logger.error("Mango ответил %s (%s): %s", status, context, result)
            raise MangoError(f"Mango вернул ошибку (HTTP {status}).")

        logger.info("Mango ответ (%s): %s", context, result)

        # 1000 — «успешно принято к обработке», это НЕ ошибка (частые грабли).
        result_code = result.get("result") if isinstance(result, dict) else None
        if result_code not in (None, 0, "0", 1000, "1000"):
            logger.error("Mango вернул код ошибки %s (%s)", result_code, context)
            raise MangoError(f"Mango вернул код ошибки: {result_code}")

        return result

    async def route_call(
        self, call_id: str, to_number: str, command_id: str | None = None,
    ) -> tuple[str, dict]:
        """Переводит существующий входящий вызов на номер `to_number`.

        Возвращает (command_id, ответ Mango). Номер `to_number` НЕ логируется.
        """
        if not (self.settings.mango_api_key and self.settings.mango_api_salt):
            raise MangoConfigError("Не настроены параметры Mango (key/salt).")

        command_id, form = build_route_payload(
            api_key=self.settings.mango_api_key,
            api_salt=self.settings.mango_api_salt,
            call_id=call_id,
            to_number=to_number,
            command_id=command_id,
        )
        result = await self._post_command(
            "commands/route", form, context=f"route call_id={call_id}"
        )
        logger.info("Команда route отправлена: call_id=%s command_id=%s",
                    call_id, command_id)
        return command_id, result

    async def initiate_callback(
        self, manager_phone: str, client_phone: str, order_id: int,
        extension: str | None,
    ) -> tuple[str, dict]:
        """Инициирует звонок callback. Возвращает (command_id, ответ Mango).

        extension — персональный внутренний номер сотрудника в Mango ВАТС.
        Обязателен: без него неясно, кому звонить, а тихий откат на общий номер
        уже приводил к тому, что звонок улетал не тому сотруднику.
        ВАЖНО: номера клиента/менеджера не логируются.
        """
        if not extension:
            raise MangoExtensionMissingError(
                "У сотрудника не задан персональный extension в Mango."
            )
        if not (self.settings.mango_api_key and self.settings.mango_api_salt
                and self.settings.mango_line_number):
            raise MangoConfigError(
                "Не настроены параметры Mango (key/salt/line_number)."
            )

        command_id, form = build_callback_payload(
            api_key=self.settings.mango_api_key,
            api_salt=self.settings.mango_api_salt,
            manager_phone=manager_phone,
            client_phone=client_phone,
            extension=extension,
            line_number=self.settings.mango_line_number,
            order_id=order_id,
        )
        result = await self._post_command(
            "commands/callback", form, context=f"order={order_id}"
        )
        logger.info("Звонок инициирован через Mango: order=%s command_id=%s",
                    order_id, command_id)
        return command_id, result
