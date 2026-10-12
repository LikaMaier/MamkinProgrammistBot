"""HTTP-сервер: раздаёт Mini App и JSON API."""
import hashlib
import hmac
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl

from aiogram import Bot
from aiohttp import web

import config
import logic
from db import DB
from logic import ApiError
from utils import STATUS_TITLES, fmt_dt

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"


# ---------- авторизация через Telegram initData ----------
def check_init_data(init_data: str, token: str):
    try:
        data = dict(parse_qsl(init_data, keep_blank_values=True))
        h = data.pop("hash", None)
        if not h or not token:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calc, h):
            return None
        if time.time() - int(data.get("auth_date", 0)) > 7 * 86400:
            return None
        return json.loads(data["user"])
    except Exception:
        return None


@web.middleware
async def mw(request: web.Request, handler):
    if request.path.startswith("/api/"):
        user = check_init_data(request.headers.get("X-Init-Data", ""), config.BOT_TOKEN)
        if not user and config.DEV_MODE:
            user = {"id": config.DEV_USER_ID, "first_name": "Dev", "username": "dev"}
        if not user:
            return web.json_response({"error": "Откройте приложение из Telegram"}, status=401)
        request["user"] = user
        request["admin"] = user["id"] in config.ADMIN_IDS
        if request.path.startswith("/api/admin/") and not request["admin"]:
            return web.json_response({"error": "Нет доступа"}, status=403)
    try:
        return await handler(request)
    except ApiError as e:
        return web.json_response({"error": str(e)}, status=400)
    except (ValueError, KeyError, TypeError) as e:
        log.info("bad request: %r", e)
        return web.json_response({"error": "Некорректные данные"}, status=400)


def J(data, status=200):
    return web.json_response(data, status=status, dumps=lambda x: json.dumps(x, ensure_ascii=False))


def ctx(r: web.Request) -> tuple[DB, Bot]:
    return r.app["db"], r.app["bot"]


def pub(o: dict) -> dict:
    o = dict(o)
    o["when"] = fmt_dt(o["start_ts"])
    o["status_title"] = STATUS_TITLES.get(o["status"], o["status"])
    o["can_change"] = logic.can_change(o)
    return o


def _date(s: str) -> date:
    return date.fromisoformat(s)


# ---------- клиент ----------
async def me(r):
    db, _ = ctx(r)
    await db.upsert_user(r["user"])
    u = await db.get_user(r["user"]["id"])
    return J({"user": u, "is_admin": r["admin"], "shop_name": config.SHOP_NAME,
              "payment_mode": config.PAYMENT_MODE, "cancel_hours": config.CANCEL_DEADLINE_HOURS})


async def services(r):
    db, _ = ctx(r)
    show_all = r.query.get("all") == "1" and r["admin"]
    return J(await db.list_services(include_inactive=show_all))


async def days(r):
    db, _ = ctx(r)
    exclude = int(r.query["exclude"]) if r.query.get("exclude") else None
    return J(await logic.days_overview(db, int(r.query.get("duration", 60)), exclude))


async def slots(r):
    db, _ = ctx(r)
    exclude = int(r.query["exclude"]) if r.query.get("exclude") else None
    res = await logic.day_slots(db, _date(r.query["date"]), int(r.query.get("duration", 60)), exclude)
    for s in res:  # клиенту не раскрываем, чьи это заказы
        s.pop("order_id", None)
    return J(res)


async def create_order(r):
    db, bot = ctx(r)
    o = await logic.create_order(db, bot, r["user"], await r.json())
    out = {"order": pub(o)}
    if config.PAYMENT_MODE in ("stars", "provider"):
        try:
            out["invoice_url"] = await logic.invoice_link(db, bot, o)
        except Exception as e:
            log.warning("invoice failed: %s", e)
    return J(out)


async def my_orders(r):
    db, _ = ctx(r)
    return J([pub(o) for o in await db.user_orders(r["user"]["id"])])


async def cancel_order(r):
    db, bot = ctx(r)
    return J(pub(await logic.cancel_by_client(db, bot, int(r.match_info["id"]), r["user"]["id"])))


async def reschedule_order(r):
    db, bot = ctx(r)
    body = await r.json()
    return J(pub(await logic.reschedule(db, bot, int(r.match_info["id"]), r["user"]["id"], int(body["ts"]))))


async def pay_order(r):
    db, bot = ctx(r)
    o = await logic._own(db, int(r.match_info["id"]), r["user"]["id"])
    return J({"invoice_url": await logic.invoice_link(db, bot, o)})


# ---------- администратор ----------
async def a_orders(r):
    db, _ = ctx(r)
    status = r.query.get("status", "")
    return J({"orders": [pub(o) for o in await db.all_orders(status)], "counts": await db.order_counts()})


async def a_status(r):
    db, bot = ctx(r)
    body = await r.json()
    return J(pub(await logic.set_status(db, bot, int(r.match_info["id"]), body["status"])))


def _service_payload(b: dict) -> dict:
    title = str(b.get("title", "")).strip()
    if not title:
        raise ApiError("Укажите название услуги")
    price, duration = int(b.get("price", 0)), int(b.get("duration_min", 0))
    if price < 0 or duration < 0:
        raise ApiError("Цена и длительность не могут быть отрицательными")
    return {"title": title[:120], "description": str(b.get("description", "")).strip()[:600],
            "price": price, "duration_min": duration, "is_addon": 1 if b.get("is_addon") else 0,
            "emoji": (str(b.get("emoji") or "💻").strip() or "💻")[:8], "active": 0 if b.get("active") is False else 1,
            "sort": int(b.get("sort", 0))}


async def a_service_create(r):
    db, _ = ctx(r)
    sid = await db.create_service(_service_payload(await r.json()))
    return J({"id": sid})


async def a_service_update(r):
    db, _ = ctx(r)
    await db.update_service(int(r.match_info["id"]), _service_payload(await r.json()))
    return J({"ok": True})


async def a_service_delete(r):
    db, _ = ctx(r)
    await db.delete_service(int(r.match_info["id"]))
    return J({"ok": True})


async def a_schedule_get(r):
    db, _ = ctx(r)
    return J(await db.get_schedule())


async def a_schedule_put(r):
    db, _ = ctx(r)
    b = await r.json()
    slot = int(b["slot_min"])
    if slot not in (15, 20, 30, 45, 60, 90, 120):
        raise ApiError("Шаг слота: 15, 20, 30, 45, 60, 90 или 120 минут")
    week = {}
    for i in range(7):
        v = b["week"].get(str(i))
        if v:
            datetime.strptime(v[0], "%H:%M"), datetime.strptime(v[1], "%H:%M")
            if v[0] >= v[1]:
                raise ApiError("Время начала дня должно быть раньше окончания")
            week[str(i)] = [v[0], v[1]]
        else:
            week[str(i)] = None
    sch = {"slot_min": slot, "days_ahead": max(1, min(90, int(b["days_ahead"]))),
           "min_lead_hours": max(0, min(72, float(b["min_lead_hours"]))), "week": week}
    await db.set_setting("schedule", sch)
    return J(sch)


async def a_days(r):
    db, _ = ctx(r)
    sch = await db.get_schedule()
    off = await db.days_off()
    res = []
    d0 = logic.today()
    for i in range(int(sch["days_ahead"]) + 1):
        dd = d0 + timedelta(days=i)
        slots_ = await logic.day_slots(db, dd, int(sch["slot_min"]))
        res.append({"date": dd.isoformat(), "off": dd.isoformat() in off,
                    "weekend": not sch["week"].get(str(dd.weekday())),
                    "booked": sum(s["state"] == "booked" for s in slots_),
                    "free": sum(s["state"] == "free" for s in slots_)})
    return J(res)


async def a_day_toggle(r):
    db, _ = ctx(r)
    d = _date((await r.json())["date"]).isoformat()
    return J({"off": await db.toggle_day_off(d)})


async def a_slots(r):
    db, _ = ctx(r)
    sch = await db.get_schedule()
    return J(await logic.day_slots(db, _date(r.query["date"]), int(sch["slot_min"])))


async def a_block(r):
    db, _ = ctx(r)
    return J({"blocked": await db.toggle_block(int((await r.json())["ts"]))})


# ---------- статика ----------
async def index(_):
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def health(_):
    return web.Response(text="ok")


def create_app(bot: Bot, db: DB) -> web.Application:
    app = web.Application(middlewares=[mw])
    app["bot"], app["db"] = bot, db
    R = app.router
    R.add_get("/", index)
    R.add_get("/health", health)
    R.add_get("/api/me", me)
    R.add_get("/api/services", services)
    R.add_get("/api/days", days)
    R.add_get("/api/slots", slots)
    R.add_post("/api/orders", create_order)
    R.add_get("/api/orders/my", my_orders)
    R.add_post("/api/orders/{id}/cancel", cancel_order)
    R.add_post("/api/orders/{id}/reschedule", reschedule_order)
    R.add_post("/api/orders/{id}/pay", pay_order)
    R.add_get("/api/admin/orders", a_orders)
    R.add_post("/api/admin/orders/{id}/status", a_status)
    R.add_post("/api/admin/services", a_service_create)
    R.add_put("/api/admin/services/{id}", a_service_update)
    R.add_delete("/api/admin/services/{id}", a_service_delete)
    R.add_get("/api/admin/schedule", a_schedule_get)
    R.add_put("/api/admin/schedule", a_schedule_put)
    R.add_get("/api/admin/days", a_days)
    R.add_post("/api/admin/days/toggle", a_day_toggle)
    R.add_get("/api/admin/slots", a_slots)
    R.add_post("/api/admin/slots/toggle", a_block)
    return app
