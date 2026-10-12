import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
from aiohttp import web

import config
from api import create_app
from db import DB
from handlers import router
from reminders import reminder_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    if not config.BOT_TOKEN:
        raise SystemExit("Задайте переменную окружения BOT_TOKEN")
    bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    db = DB(config.DB_PATH)
    await db.connect()

    dp = Dispatcher()
    dp["db"] = db
    dp.include_router(router)

    runner = web.AppRunner(create_app(bot, db))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", config.PORT).start()
    logging.info("HTTP server on :%s", config.PORT)

    await bot.set_my_commands([
        BotCommand(command="start", description="Записаться на услугу"),
        BotCommand(command="my", description="Мои записи"),
        BotCommand(command="admin", description="Админ-панель"),
    ])
    if config.WEBAPP_URL:
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(
            text="Магазин", web_app=WebAppInfo(url=config.WEBAPP_URL + "/")))

    asyncio.create_task(reminder_loop(bot, db))
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
