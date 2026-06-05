"""Тестовый вызов Mango Callback API. Запускать локально для диагностики.

Использование:
  .venv/Scripts/python.exe scripts/test_mango.py <manager_phone>

  manager_phone — телефон менеджера в формате 79XXXXXXXXX
"""
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path

import aiohttp
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
cfg = dotenv_values(ROOT / ".env")

API_KEY    = cfg["MANGO_API_KEY"]
API_SALT   = cfg["MANGO_API_SALT"]
API_URL    = cfg["MANGO_API_URL"].rstrip("/")
LINE_NUM   = cfg["MANGO_LINE_NUMBER"]
CLIENT_PHONE = cfg.get("AMOCRM_STUB_PHONE", "")


async def test_callback(manager_phone: str) -> None:
    command_id = f"test_{int(time.time())}"
    data = {
        "command_id": command_id,
        "from": {"extension": "", "number": manager_phone},
        "to_number": CLIENT_PHONE,
        "line_number": LINE_NUM,
    }
    json_str = json.dumps(data, ensure_ascii=False)
    sign = hashlib.sha256((API_KEY + json_str + API_SALT).encode()).hexdigest()
    form = {"vpbx_api_key": API_KEY, "sign": sign, "json": json_str}

    url = f"{API_URL}/commands/callback"
    print(f"POST {url}")
    print(f"Payload: {json_str}")
    print()

    async with aiohttp.ClientSession() as session:
        async with session.post(url, data=form) as resp:
            text = await resp.text()
            print(f"HTTP {resp.status}")
            print(f"Response: {text}")
            try:
                parsed = json.loads(text)
                result_code = parsed.get("result")
                print()
                if result_code == 0:
                    print("✅ Успех! Ждите звонка.")
                else:
                    print(f"❌ Ошибка Mango result={result_code}")
                    print(KNOWN_ERRORS.get(result_code, "Неизвестный код ошибки."))
            except Exception:
                pass


KNOWN_ERRORS = {
    3103: "Линия (line_number) не найдена в данном ВАТС.",
    3104: "Ошибка подписи — неверный api_key или api_salt.",
    3105: "Неверный формат номера телефона.",
    3106: "Нет доступа к данной линии.",
    3201: "Абонент не найден (extension не существует).",
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Укажи телефон менеджера: .venv/Scripts/python.exe scripts/test_mango.py 79XXXXXXXXX")
        sys.exit(1)
    asyncio.run(test_callback(sys.argv[1]))
