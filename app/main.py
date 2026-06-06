"""Точка входа: запуск aiogram polling и FastAPI (uvicorn) одновременно."""
from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api.webhooks import app as fastapi_app
from app.bot.bot import create_bot, create_dispatcher
from app.bot.commands import setup_all_commands
from app.config import get_settings
from app.db.database import async_session_factory, dispose_engine, init_db
from app.logging_config import setup_logging
from app.services.bootstrap import ensure_amo_token, seed_groups

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    await init_db()
    await seed_groups()
    await ensure_amo_token()

    bot = create_bot()
    dp = create_dispatcher()

    async with async_session_factory() as session:
        await setup_all_commands(bot, session)

    fastapi_app.state.bot = bot

    uvicorn_config = uvicorn.Config(
        fastapi_app,
        host=settings.server_host,
        port=settings.server_port,
        log_level=settings.log_level.lower(),
        access_log=False,
    )
    server = uvicorn.Server(uvicorn_config)

    logger.info(
        "Старт: polling бота + HTTP-сервер на %s:%s",
        settings.server_host, settings.server_port,
    )
    try:
        await asyncio.gather(
            dp.start_polling(bot),
            server.serve(),
        )
    finally:
        await bot.session.close()
        await dispose_engine()
        logger.info("Приложение остановлено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger(__name__).info("Остановка по сигналу")
