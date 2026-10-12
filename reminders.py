"""Фоновые напоминания клиентам: за 24 часа и за 1 час до записи."""
import asyncio
import logging
import time

from aiogram import Bot

import notify
from db import DB

log = logging.getLogger(__name__)


async def tick(bot: Bot, db: DB):
    now = int(time.time())
    # за сутки — только если запись делали заранее (иначе напоминание пришло бы сразу после брони)
    for o in await db.q("SELECT id FROM orders WHERE status IN ('new','in_work') AND reminded_24=0 "
                        "AND start_ts BETWEEN ? AND ? AND start_ts - created_at > 93600",
                        (now + 3 * 3600, now + 86400)):
        order = await db.update_order(o["id"], reminded_24=1)
        await notify.client(bot, order, "⏰ Напоминание: запись завтра")
    for o in await db.q("SELECT id FROM orders WHERE status IN ('new','in_work') AND reminded_1=0 "
                        "AND start_ts BETWEEN ? AND ? AND start_ts - created_at > 7200",
                        (now, now + 3600)):
        order = await db.update_order(o["id"], reminded_1=1)
        await notify.client(bot, order, "⏰ Через час начинаем!")


async def reminder_loop(bot: Bot, db: DB):
    while True:
        try:
            await tick(bot, db)
        except Exception:
            log.exception("reminder tick failed")
        await asyncio.sleep(60)
