"""Клиент Mango Office API (инициация callback-звонка).

Подпись запроса: sha256(api_key + json_string + api_salt). Та же строка json
используется и в подписи, и в поле form-data `json` — иначе подпись не сойдётся.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=20)

# Mango трактует start_date/end_date как МОСКОВСКОЕ время, а прод-сервер живёт в UTC.
# Без этого сдвига верхняя граница окна срезает последние 3 часа, и выглядит это как
# «статистика отстаёт» (наши грабли, 30.07.2026).
_MSK_OFFSET = timedelta(hours=3)

# Допустимые значения limit для stats/calls/request — список закрытый (офиц. дока,
# раздел 3.4.2.2). Произвольное число = неверный запрос, а за них Mango блокирует
# доступ к API на 2 минуты.
_STATS_LIMITS = (1, 5, 10, 20, 50, 100, 500, 1000, 2000, 5000)


class MangoError(Exception):
    """Ошибка при инициации звонка через Mango."""


class MangoRateLimited(MangoError):
    """Mango ответил 429. Штатная ситуация, а не поломка.

    Лимит на статистику — 1 запрос в 2 секунды на ВЕСЬ продукт, и мы делим его с
    интеграцией amoCRM. Ловили 2 раза из 206 запросов: подождать и повторить.
    """


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


def build_stats_request_payload(
    api_key: str,
    api_salt: str,
    *,
    window_minutes: int,
    now: datetime | None = None,
    limit: int = 100,
) -> dict[str, str]:
    """Формирует form-data для запроса статистики ВХОДЯЩИХ звонков за последние N минут.

    `now` — текущее время сервера (UTC); параметром вынесено ради тестов.
    Границы окна переводятся в московское время: именно так Mango их понимает.
    """
    if limit not in _STATS_LIMITS:
        raise MangoError(
            f"stats: limit={limit} недопустим, разрешены только {_STATS_LIMITS}."
        )
    now_msk = (now or datetime.now()) + _MSK_OFFSET
    data: dict = {
        "start_date": (now_msk - timedelta(minutes=window_minutes)).strftime(
            "%d.%m.%Y %H:%M:%S"
        ),
        "end_date": now_msk.strftime("%d.%m.%Y %H:%M:%S"),
        "limit": limit,
        "offset": 0,
        "context_type": 1,          # только входящие
    }
    json_str = json.dumps(data)
    sign = hashlib.sha256((api_key + json_str + api_salt).encode()).hexdigest()
    return {"vpbx_api_key": api_key, "sign": sign, "json": json_str}


def build_stats_result_payload(api_key: str, api_salt: str, key: str) -> dict[str, str]:
    """Формирует form-data для получения готового отчёта по ключу."""
    json_str = json.dumps({"key": key})
    sign = hashlib.sha256((api_key + json_str + api_salt).encode()).hexdigest()
    return {"vpbx_api_key": api_key, "sign": sign, "json": json_str}


def parse_stats_calls(payload: dict) -> list[dict]:
    """Достаёт список звонков из ответа stats/calls/result.

    Структура ответа: {"result":..., "data":[{"list":[...], "period":...}]}.
    Полезные поля записи: entry_id, caller_number, called_number,
    context_start_time (unix), duration, talk_duration, context_status.
    """
    data = payload.get("data") or []
    if not data or not isinstance(data[0], dict):
        return []
    calls = data[0].get("list") or []
    return [c for c in calls if isinstance(c, dict)]


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

    async def _post_raw(self, path: str, form: dict[str, str]) -> dict:
        """POST без проверки поля `result`.

        Нужен для статистики: `stats/calls/request/` отвечает `{"key": ...}` вообще
        без `result`, и общий `_post_command` счёл бы это ошибкой.
        """
        url = f"{self.settings.mango_api_url.rstrip('/')}/{path.lstrip('/')}"
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as http:
                async with http.post(url, data=form) as resp:
                    status = resp.status
                    text = await resp.text()
        except aiohttp.ClientError as exc:
            logger.error("Сетевая ошибка Mango (%s): %s", path, exc)
            raise MangoError("Сервис Mango недоступен.") from exc

        if status == 429:
            raise MangoRateLimited(f"Mango: превышен лимит запросов ({path}).")
        if status != 200:
            raise MangoError(f"Mango вернул ошибку (HTTP {status}, {path}).")

        try:
            return json.loads(text) if text else {}
        except json.JSONDecodeError:
            raise MangoError(f"Mango вернул не-JSON ответ ({path}).") from None

    async def fetch_recent_incoming(
        self, *, window_minutes: int = 15, limit: int = 100, attempts: int = 5,
    ) -> list[dict]:
        """Возвращает входящие звонки ВАТС за последние `window_minutes` минут.

        Двухшаговый запрос: `stats/calls/request/` отдаёт ключ, по нему
        `stats/calls/result/` возвращает готовый отчёт (формируется ~2 секунды).
        Номера в ответе приходят как 7XXXXXXXXXX — тот же формат, что в `User.phone`.

        ВАЖНО: данные отстают от реальности на 1-2 минуты (замер: 52 и 108 секунд
        после отбоя). Это ограничение Mango, а не нашего кода.
        """
        if not (self.settings.mango_api_key and self.settings.mango_api_salt):
            raise MangoConfigError("Не настроены параметры Mango (key/salt).")

        request_form = build_stats_request_payload(
            api_key=self.settings.mango_api_key,
            api_salt=self.settings.mango_api_salt,
            window_minutes=window_minutes,
            limit=limit,
        )
        answer = await self._post_raw("stats/calls/request/", request_form)
        key = answer.get("key")
        if not key:
            raise MangoError(f"Mango не выдал ключ статистики: {answer}")

        result_form = build_stats_result_payload(
            self.settings.mango_api_key, self.settings.mango_api_salt, str(key)
        )
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(1)
            payload = await self._post_raw("stats/calls/result/", result_form)
            if payload:
                return parse_stats_calls(payload)
        logger.warning("Mango: отчёт статистики не сформировался за %s попыток", attempts)
        return []

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
