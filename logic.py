"""Бизнес-логика: расчёт слотов, создание/перенос/отмена заказов, оплата."""
import asyncio
import math
import time
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.types import LabeledPrice

import config
import notify
from db import DB
from utils import STATUS_TITLES

_lock = asyncio.Lock()  # защищает от двойной записи на один слот


class ApiError(Exception):
    pass


def today() -> date:
    return datetime.now(config.TZ).date()


def _at(day: date, hm: str) -> int:
    h, m = map(int, hm.split(":"))
    return int(datetime(day.year, day.month, day.day, h, m, tzinfo=config.TZ).timestamp())


def need_seconds(duration_min: int, slot_min: int) -> int:
    blocks = max(1, math.ceil(duration_min / slot_min))
    return blocks * slot_min * 60


async def day_slots(db: DB, day: date, duration_min: int, exclude_id=None):
    """Слоты дня. state: free | booked | blocked | past | short (заказ не помещается)."""
    sch = await db.get_schedule()
    slot = int(sch["slot_min"]) * 60
    hours = sch["week"].get(str(day.weekday()))
    if not hours or await db.is_day_off(day.isoformat()):
        return []
    a, b = _at(day, hours[0]), _at(day, hours[1])
    need = need_seconds(duration_min, int(sch["slot_min"]))
    orders = await db.busy_orders(a, b, exclude_id)
    blocked = await db.blocked_in(a, b)
    edge = time.time() + float(sch.get("min_lead_hours", 0)) * 3600
    out = []
    t = a
    while t + slot <= b:
        booked = next((o for o in orders if o["start_ts"] < t + slot and o["end_ts"] > t), None)
        if t in blocked:
            state = "blocked"
        elif booked:
            state = "booked"
        elif t < edge:
            state = "past"
        elif (t + need > b
              or any(o["start_ts"] < t + need and o["end_ts"] > t for o in orders)
              or any(t <= x < t + need for x in blocked)):
            state = "short"
        else:
            state = "free"
        out.append({"ts": t, "time": datetime.fromtimestamp(t, config.TZ).strftime("%H:%M"),
                    "state": state, "order_id": booked["id"] if booked else None})
        t += slot
    return out


async def days_overview(db: DB, duration_min: int, exclude_id=None):
    sch = await db.get_schedule()
    res = []
    d0 = today()
    for i in range(int(sch["days_ahead"]) + 1):
        d = d0 + timedelta(days=i)
        slots = await day_slots(db, d, duration_min, exclude_id)
        res.append({"date": d.isoformat(), "free": sum(s["state"] == "free" for s in slots)})
    return res


async def _check_slot(db: DB, ts: int, duration_min: int, exclude_id=None):
    d = datetime.fromtimestamp(ts, config.TZ).date()
    slots = await day_slots(db, d, duration_min, exclude_id)
    if not any(s["ts"] == ts and s["state"] == "free" for s in slots):
        raise ApiError("Это время уже занято — выберите другое")


async def build_cart(db: DB, main_ids, addon_ids):
    main = await db.get_services([int(i) for i in main_ids])
    adds = await db.get_services([int(i) for i in addon_ids])
    main = [s for s in main if not s["is_addon"]]
    adds = [s for s in adds if s["is_addon"]]
    if not main:
        raise ApiError("Выберите хотя бы одну основную услугу")
    items = [{"id": s["id"], "title": s["title"], "price": s["price"], "duration_min": s["duration_min"],
              "is_addon": bool(s["is_addon"])} for s in main + adds]
    total = sum(i["price"] for i in items)
    duration = max(15, sum(i["duration_min"] for i in items))
    return items, total, duration


async def create_order(db: DB, bot: Bot, user: dict, data: dict):
    items, total, duration = await build_cart(db, data.get("services", []), data.get("addons", []))
    ts = int(data.get("ts") or 0)
    phone = str(data.get("phone") or "").strip()[:32]
    comment = str(data.get("comment") or "").strip()[:1000]
    sch = await db.get_schedule()
    async with _lock:
        await _check_slot(db, ts, duration)
        await db.upsert_user(user, phone or None)
        o = await db.create_order(user_id=user["id"], items=items, total=total, duration_min=duration,
                                  start_ts=ts, end_ts=ts + need_seconds(duration, int(sch["slot_min"])),
                                  comment=comment, phone=phone)
    await notify.admins(bot, o, "🔔 Новый заказ")
    await notify.client(bot, o, "✅ Вы записаны!")
    return o


def can_change(o: dict) -> bool:
    return (o["status"] in ("new", "in_work")
            and o["start_ts"] - time.time() > config.CANCEL_DEADLINE_HOURS * 3600)


async def _own(db: DB, oid: int, uid: int):
    o = await db.get_order(oid)
    if not o or o["user_id"] != uid:
        raise ApiError("Заказ не найден")
    return o


async def cancel_by_client(db: DB, bot: Bot, oid: int, uid: int):
    o = await _own(db, oid, uid)
    if not can_change(o):
        raise ApiError(f"Отменить можно не позднее чем за {config.CANCEL_DEADLINE_HOURS} ч до записи")
    o = await db.update_order(oid, status="canceled")
    await notify.admins(bot, o, "⚠️ Клиент отменил запись")
    return o


async def reschedule(db: DB, bot: Bot, oid: int, uid: int, ts: int):
    o = await _own(db, oid, uid)
    if not can_change(o):
        raise ApiError(f"Перенести можно не позднее чем за {config.CANCEL_DEADLINE_HOURS} ч до записи")
    sch = await db.get_schedule()
    async with _lock:
        await _check_slot(db, ts, o["duration_min"], exclude_id=oid)
        o = await db.update_order(oid, start_ts=ts,
                                  end_ts=ts + need_seconds(o["duration_min"], int(sch["slot_min"])),
                                  reminded_24=0, reminded_1=0)
    await notify.admins(bot, o, "🔁 Клиент перенёс запись")
    await notify.client(bot, o, "🔁 Запись перенесена")
    return o


async def set_status(db: DB, bot: Bot, oid: int, status: str):
    if status not in STATUS_TITLES:
        raise ApiError("Неизвестный статус")
    o = await db.get_order(oid)
    if not o:
        raise ApiError("Заказ не найден")
    if o["status"] == status:
        return o
    o = await db.update_order(oid, status=status)
    titles = {"in_work": "🔧 Заказ взят в работу", "done": "✅ Заказ выполнен — спасибо!",
              "canceled": "❌ Заказ отменён", "new": "Статус заказа обновлён"}
    await notify.client(bot, o, titles[status])
    return o


async def invoice_link(db: DB, bot: Bot, o: dict) -> str:
    if config.PAYMENT_MODE not in ("stars", "provider"):
        raise ApiError("Онлайн-оплата не подключена")
    if o["paid"] or o["status"] == "canceled":
        raise ApiError("Заказ не требует оплаты")
    title = f"Заказ №{o['id']}"
    desc = ", ".join(i["title"] for i in o["items"])[:255]
    if config.PAYMENT_MODE == "stars":
        stars = max(1, math.ceil(o["total"] / config.STAR_RATE_RUB))
        return await bot.create_invoice_link(title=title, description=desc, payload=f"order:{o['id']}",
                                             currency="XTR", prices=[LabeledPrice(label=title, amount=stars)])
    if not config.PAYMENT_PROVIDER_TOKEN:
        raise ApiError("Не задан PAYMENT_PROVIDER_TOKEN")
    return await bot.create_invoice_link(title=title, description=desc, payload=f"order:{o['id']}",
                                         provider_token=config.PAYMENT_PROVIDER_TOKEN, currency="RUB",
                                         prices=[LabeledPrice(label=title, amount=o["total"] * 100)])
