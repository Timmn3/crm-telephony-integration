"""Зонд правил переадресации Mango — разовая проверка маскировки АОН.

Отвечает на вопрос, который не закрывает документация: какой номер видит вызываемая
сторона, когда ВАТС переадресует звонок по правилу «звонит X — веди на Y». У метода
`forwarding/number/add` нет параметра АОН (как и у `commands/route`), поэтому ответ
даёт только живой звонок.

Запуск на боевом сервере (ключи берутся из окружения контейнера, наружу не уходят):

    ssh s6 "docker exec -i call_masking_bot-bot-1 python3 -" < docs/mango/probe_forwarding.py list
    ... add 79001112233 79004445566
    ... remove 10160018

ВАЖНО: правило перехватывает ЛЮБОЙ входящий с номера `откуда` в ВАТС, а не только
на тестовую линию. Снимать сразу после проверки — режимом `remove`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys

import aiohttp

from app.config import get_settings
from app.utils.phone import mask_phone, normalize_phone

TIMEOUT = aiohttp.ClientTimeout(total=25)


def _form(settings, body: str) -> dict[str, str]:
    """Form-data с подписью. Та же json-строка идёт и в подпись, и в поле `json`."""
    sign = hashlib.sha256(
        (settings.mango_api_key + body + settings.mango_api_salt).encode()
    ).hexdigest()
    return {"vpbx_api_key": settings.mango_api_key, "sign": sign, "json": body}


async def _post(path: str, payload: dict) -> dict:
    settings = get_settings()
    if not (settings.mango_api_key and settings.mango_api_salt):
        raise SystemExit("Не настроены ключи Mango в окружении.")

    body = json.dumps(payload)
    url = f"{settings.mango_api_url.rstrip('/')}/{path}"
    async with aiohttp.ClientSession(timeout=TIMEOUT) as http:
        async with http.post(url, data=_form(settings, body)) as resp:
            status, text = resp.status, await resp.text()

    print(f"    HTTP {status}")
    try:
        return json.loads(text) if text else {}
    except json.JSONDecodeError:
        print("    ответ не JSON:", text[:300])
        return {}


async def show_rules() -> list[dict]:
    """Печатает текущие правила. Пустой json — единственный вариант, который метод ест:
    с limit/offset он отвечает result=3100 (неверные параметры)."""
    data = await _post("forwarding/numbers", {})
    rules = data.get("numbers")
    if rules is None:
        print("    ответ:", json.dumps(data, ensure_ascii=False)[:300])
        return []
    print(f"    result={data.get('result')} правил={data.get('total')}")
    for rule in rules:
        target = rule.get("forward_to_ext") or {}
        print(
            "      forward_id={fid} откуда={src} куда={dst} тип={t} активно={st} коммент={c!r}".format(
                fid=rule.get("forward_id"),
                src=mask_phone(str(rule.get("client_phone_number") or "")),
                dst=mask_phone(str(target.get("forward_number") or "")),
                t=rule.get("forward_type"), st=rule.get("status"),
                c=rule.get("comment"),
            )
        )
    return rules


async def add_rule(src_raw: str, dst_raw: str) -> None:
    src, dst = normalize_phone(src_raw), normalize_phone(dst_raw)
    if not src or not dst:
        raise SystemExit(f"Неверный формат номера: {src_raw!r} / {dst_raw!r}")

    print(f"[1/3] Ставлю правило: {mask_phone(src)} -> {mask_phone(dst)}")
    # Значения строками — как в примере офиц. доки (раздел 3.3.2, стр. 63).
    data = await _post("forwarding/number/add", {
        "client_phone_number": src,
        "client_phone_type": "0",
        "status": "1",
        "forward_type": "ext_forward",
        "forward_to_ext": {
            "forward_number_type": "0",
            "forward_number": dst,
            "forward_wait_sec": "30",
        },
        "comment": "temp test call-masking",
    })
    result = data.get("result")
    if str(result) != "1000":
        print(f"    ОШИБКА: result={result}, ответ={json.dumps(data, ensure_ascii=False)[:300]}")
        print("    Повторять сразу НЕЛЬЗЯ: лимит Mango — 1 неверный запрос в 2 минуты.")
        return
    print("    OK, result=1000")

    # Метод add не возвращает forward_id — забираем его списком, иначе удалять нечем.
    print("[2/3] Забираю forward_id из списка:")
    rules = await show_rules()
    mine = [r for r in rules if str(r.get("client_phone_number") or "").endswith(src[-10:])]
    print("[3/3] Готово. Теперь звони с этого номера на тестовую линию 79016285197.")
    if mine:
        print(f"    После проверки снять: remove {mine[0].get('forward_id')}")
    else:
        print("    ВНИМАНИЕ: своё правило в списке не нашлось — сними вручную по списку выше.")


async def remove_rule(forward_id: str) -> None:
    print(f"Снимаю правило forward_id={forward_id}")
    data = await _post("forwarding/number/remove", {"forward_id": int(forward_id)})
    print(f"    result={data.get('result')}")
    print("Проверяю, что список чист:")
    await show_rules()


def main() -> None:
    args = sys.argv[1:]
    mode = args[0] if args else "list"

    if mode == "list":
        print("Текущие правила переадресации:")
        asyncio.run(show_rules())
    elif mode == "add" and len(args) == 3:
        asyncio.run(add_rule(args[1], args[2]))
    elif mode == "remove" and len(args) == 2:
        asyncio.run(remove_rule(args[1]))
    else:
        print(__doc__)
        raise SystemExit("Режимы: list | add <откуда> <куда> | remove <forward_id>")


if __name__ == "__main__":
    main()
