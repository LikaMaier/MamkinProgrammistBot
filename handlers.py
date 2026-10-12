from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

import config
import logic
import notify
from db import DB
from utils import STATUS_TITLES, fmt_dt, rub

router = Router()


@router.message(CommandStart())
async def start(m: Message, db: DB):
    await db.upsert_user(m.from_user.model_dump())
    text = (f"👋 Здравствуйте, {escape(m.from_user.first_name or '')}!\n\n"
            f"<b>{escape(config.SHOP_NAME)}</b>\n"
            "Установим и настроим программы на ваш компьютер.\n\n"
            "Выберите услуги, удобную дату и время — всё прямо в Telegram. "
            "Время указано по Москве (МСК).")
    kb = notify.app_button("🛒 Выбрать услуги")
    if not kb:
        text += "\n\n⚠️ Магазин ещё не настроен: не задан WEBAPP_URL."
    await m.answer(text, reply_markup=kb)


@router.message(Command("my"))
async def my(m: Message, db: DB):
    orders = [o for o in await db.user_orders(m.from_user.id) if o["status"] in ("new", "in_work")]
    if not orders:
        return await m.answer("У вас нет предстоящих записей.", reply_markup=notify.app_button("🛒 Записаться"))
    lines = ["<b>Ваши записи:</b>"]
    for o in sorted(orders, key=lambda x: x["start_ts"]):
        lines.append(f"\n№{o['id']} · {fmt_dt(o['start_ts'])}\n{rub(o['total'])} · {STATUS_TITLES[o['status']]}")
    await m.answer("\n".join(lines), reply_markup=notify.app_button("📅 Управлять записями", "my"))


@router.message(Command("admin"))
async def admin(m: Message, db: DB):
    if m.from_user.id not in config.ADMIN_IDS:
        return await m.answer(f"Нет доступа. Ваш ID: <code>{m.from_user.id}</code> — добавьте его в ADMIN_IDS.")
    c = await db.order_counts()
    text = ("<b>Админ-панель</b>\n\n"
            + "\n".join(f"{t}: {c.get(s, 0)}" for s, t in STATUS_TITLES.items()))
    await m.answer(text, reply_markup=notify.app_button("⚙️ Открыть админку", "admin"))


@router.message(Command("id"))
async def my_id(m: Message):
    await m.answer(f"Ваш Telegram ID: <code>{m.from_user.id}</code>")


@router.callback_query(F.data.startswith("st:"))
async def change_status(c: CallbackQuery, db: DB, bot: Bot):
    if c.from_user.id not in config.ADMIN_IDS:
        return await c.answer("Нет доступа", show_alert=True)
    _, oid, status = c.data.split(":")
    try:
        o = await logic.set_status(db, bot, int(oid), status)
    except logic.ApiError as e:
        return await c.answer(str(e), show_alert=True)
    try:
        await c.message.edit_text(notify.order_text(o, admin=True), reply_markup=notify.admin_kb(o),
                                  disable_web_page_preview=True)
    except Exception:
        pass
    await c.answer(f"Статус: {STATUS_TITLES[status]}")


# ---------- оплата ----------
@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery, db: DB):
    try:
        o = await db.get_order(int(q.invoice_payload.split(":")[1]))
    except Exception:
        o = None
    if o and not o["paid"] and o["status"] != "canceled":
        await q.answer(ok=True)
    else:
        await q.answer(ok=False, error_message="Этот заказ уже оплачен или отменён")


@router.message(F.successful_payment)
async def paid(m: Message, db: DB, bot: Bot):
    sp = m.successful_payment
    oid = int(sp.invoice_payload.split(":")[1])
    o = await db.update_order(oid, paid=1, payment_id=sp.telegram_payment_charge_id)
    await notify.client(bot, o, "💳 Оплата получена")
    await notify.admins(bot, o, "💳 Заказ оплачен")
