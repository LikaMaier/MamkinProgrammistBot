import logging
from html import escape

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

import config
from utils import STATUS_TITLES, dur, fmt_dt, rub

log = logging.getLogger(__name__)


def app_button(text="📱 Открыть приложение", view=""):
    if not config.WEBAPP_URL:
        return None
    url = config.WEBAPP_URL + (f"/?v={view}" if view else "/")
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))]])


def order_text(o: dict, admin=False, title=None) -> str:
    head = title or "Заказ №{}".format(o["id"])
    lines = [f"<b>{head}</b>"]
    if title:
        lines.append(f"Заказ №{o['id']}")
    lines.append(f"🗓 {fmt_dt(o['start_ts'])}")
    lines.append(f"⏱ ориентировочно {dur(o['duration_min'])}")
    lines.append("")
    for it in o["items"]:
        mark = "➕" if it.get("is_addon") else "•"
        lines.append(f"{mark} {escape(it['title'])} — {rub(it['price'])}")
    lines.append(f"\n<b>Итого: {rub(o['total'])}</b>" + (" · оплачено ✅" if o.get("paid") else ""))
    if o.get("comment"):
        lines.append(f"💬 {escape(o['comment'])}")
    lines.append(f"Статус: {STATUS_TITLES.get(o['status'], o['status'])}")
    if admin:
        who = escape(o.get("full_name") or "Клиент")
        uname = f" (@{escape(o['username'])})" if o.get("username") else ""
        lines.append(f"\n👤 <a href=\"tg://user?id={o['user_id']}\">{who}</a>{uname}")
        if o.get("phone"):
            lines.append(f"📞 {escape(o['phone'])}")
    return "\n".join(lines)


def admin_kb(o: dict):
    btns = [InlineKeyboardButton(text=STATUS_TITLES[s], callback_data=f"st:{o['id']}:{s}")
            for s in ("in_work", "done", "canceled") if s != o["status"]]
    rows = [btns[:2], btns[2:]] if len(btns) > 2 else [btns]
    if config.WEBAPP_URL:
        rows.append([InlineKeyboardButton(text="📋 Все заказы",
                                          web_app=WebAppInfo(url=config.WEBAPP_URL + "/?v=admin"))])
    return InlineKeyboardMarkup(inline_keyboard=[r for r in rows if r])


async def send(bot: Bot, chat_id: int, text: str, kb=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:  # пользователь мог заблокировать бота
        log.warning("send to %s failed: %s", chat_id, e)


async def admins(bot: Bot, o: dict, title: str):
    for aid in config.ADMIN_IDS:
        await send(bot, aid, order_text(o, admin=True, title=title), admin_kb(o))


async def client(bot: Bot, o: dict, title: str):
    await send(bot, o["user_id"], order_text(o, title=title), app_button("📅 Мои записи", "my"))
