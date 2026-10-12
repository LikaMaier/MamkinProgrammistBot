import json
import os
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS services(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price INTEGER NOT NULL DEFAULT 0,
    duration_min INTEGER NOT NULL DEFAULT 60,
    is_addon INTEGER NOT NULL DEFAULT 0,
    emoji TEXT NOT NULL DEFAULT '💻',
    active INTEGER NOT NULL DEFAULT 1,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS days_off(date TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS blocked_slots(ts INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS users(
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    phone TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    items TEXT NOT NULL,
    total INTEGER NOT NULL,
    duration_min INTEGER NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new',
    paid INTEGER NOT NULL DEFAULT 0,
    payment_id TEXT,
    reminded_24 INTEGER NOT NULL DEFAULT 0,
    reminded_1 INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_start ON orders(start_ts);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id);
"""

DEFAULT_SCHEDULE = {
    "slot_min": 60,          # шаг сетки слотов, минут
    "days_ahead": 21,        # на сколько дней вперёд открыта запись
    "min_lead_hours": 2,     # минимум часов от «сейчас» до записи
    "week": {                # 0 = понедельник … 6 = воскресенье; null = выходной
        "0": ["10:00", "19:00"], "1": ["10:00", "19:00"], "2": ["10:00", "19:00"],
        "3": ["10:00", "19:00"], "4": ["10:00", "19:00"], "5": ["11:00", "16:00"],
        "6": None,
    },
}

LIC = " Лицензия — ваша, приобретается отдельно у правообладателя или официального реселлера."

# Версия стартового каталога: при увеличении каталог в пустом магазине (без заказов) пересоздаётся
CATALOG_VERSION = 2

SEED_SERVICES = [
    # title, description, price, duration, is_addon, emoji, sort
    ("Установка AutoCAD 2024 + СПДС (Windows)",
     "Установка и настройка AutoCAD 2024 с модулем СПДС: русский интерфейс, шаблоны, единицы, печать." + LIC, 623, 60, 0, "📐", 1),
    ("Установка SketchUp 2026 + Enscape + V-Ray (Windows)",
     "SketchUp Pro с плагинами визуализации Enscape и V-Ray, проверка рендера и настройка библиотек." + LIC, 1046, 90, 0, "🏠", 2),
    ("Установка Adobe Illustrator 2026 (Windows)",
     "Установка через Creative Cloud, вход в ваш аккаунт Adobe, шрифты и рабочее пространство." + LIC, 623, 45, 0, "🎨", 3),
    ("Установка ArchiCAD 28 (macOS)",
     "Установка ArchiCAD 28 на Mac, русская локализация, библиотеки объектов и шаблон проекта." + LIC, 623, 60, 0, "🏛️", 4),
    ("Установка Microsoft Office 2024 (macOS)",
     "Word, Excel, PowerPoint, Outlook на Mac, вход в учётную запись Microsoft, настройка почты." + LIC, 700, 45, 0, "📄", 5),
    ("Установка CorelDRAW Graphics Suite 2025 (Windows)",
     "CorelDRAW, Photo-Paint и шрифты пакета, настройка цветовых профилей для печати." + LIC, 623, 45, 0, "🖌️", 6),
    ("Установка ZBrush 2026 (Windows)",
     "Установка ZBrush, настройка графического планшета, кистей и горячих клавиш." + LIC, 623, 45, 0, "🗿", 7),
    ("Установка AutoCAD 2026 (Windows)",
     "Установка и настройка AutoCAD 2026: интерфейс, шаблоны, стили печати, перенос пользовательских настроек." + LIC, 623, 60, 0, "📐", 8),
    ("Перенос проектов и настроек", "Перенесём проекты, библиотеки, шаблоны и пользовательские настройки со старого компьютера.", 400, 30, 1, "📦", 10),
    ("Шрифты и шаблоны ГОСТ / СПДС", "Установка шрифтов ГОСТ, рамок, штампов и шаблонов оформления чертежей.", 300, 20, 1, "📏", 11),
    ("Резервная копия данных", "Сохраним файлы, фото и документы перед работами.", 800, 30, 1, "💾", 12),
    ("Удалённый доступ", "Настроим удалённую помощь для быстрой поддержки в будущем.", 500, 15, 1, "🖥️", 13),
    ("Выезд мастера", "Работы у вас на месте вместо удалённого подключения.", 1000, 0, 1, "🚗", 14),
]

ORDER_FIELDS = {"status", "paid", "payment_id", "reminded_24", "reminded_1",
                "start_ts", "end_ts", "comment", "phone"}
SERVICE_FIELDS = ("title", "description", "price", "duration_min", "is_addon", "emoji", "active", "sort")


def _order(row):
    if row is None:
        return None
    o = dict(row)
    o["items"] = json.loads(o["items"])
    return o


class DB:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()
        version = await self.get_setting("catalog_version") or 1
        if version < CATALOG_VERSION and not await self.q1("SELECT id FROM orders LIMIT 1"):
            await self.conn.execute("DELETE FROM services")  # магазин ещё пустой — меняем стартовый каталог
            await self.conn.commit()
        if version < CATALOG_VERSION:
            await self.set_setting("catalog_version", CATALOG_VERSION)
        if not await self.q1("SELECT id FROM services LIMIT 1"):
            await self.conn.executemany(
                "INSERT INTO services(title,description,price,duration_min,is_addon,emoji,sort) VALUES(?,?,?,?,?,?,?)",
                SEED_SERVICES)
            await self.conn.commit()
        if await self.get_setting("schedule") is None:
            await self.set_setting("schedule", DEFAULT_SCHEDULE)

    async def close(self):
        if self.conn:
            await self.conn.close()

    # --- низкоуровневые помощники ---
    async def q(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    async def q1(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        r = await cur.fetchone()
        return dict(r) if r else None

    async def x(self, sql, args=()):
        cur = await self.conn.execute(sql, args)
        await self.conn.commit()
        return cur.lastrowid

    # --- настройки ---
    async def get_setting(self, key):
        r = await self.q1("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(r["value"]) if r else None

    async def set_setting(self, key, value):
        await self.x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    async def get_schedule(self):
        return await self.get_setting("schedule") or DEFAULT_SCHEDULE

    # --- услуги ---
    async def list_services(self, include_inactive=False):
        sql = "SELECT * FROM services" + ("" if include_inactive else " WHERE active=1") + " ORDER BY is_addon, sort, id"
        return await self.q(sql)

    async def get_services(self, ids):
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        return await self.q(f"SELECT * FROM services WHERE id IN ({marks}) AND active=1", tuple(ids))

    async def create_service(self, data):
        cols = ",".join(SERVICE_FIELDS)
        return await self.x(f"INSERT INTO services({cols}) VALUES({','.join('?' * len(SERVICE_FIELDS))})",
                            tuple(data[k] for k in SERVICE_FIELDS))

    async def update_service(self, sid, data):
        sets = ",".join(f"{k}=?" for k in SERVICE_FIELDS)
        await self.x(f"UPDATE services SET {sets} WHERE id=?", tuple(data[k] for k in SERVICE_FIELDS) + (sid,))

    async def delete_service(self, sid):
        await self.x("DELETE FROM services WHERE id=?", (sid,))

    # --- расписание ---
    async def is_day_off(self, d: str) -> bool:
        return bool(await self.q1("SELECT 1 FROM days_off WHERE date=?", (d,)))

    async def days_off(self):
        return {r["date"] for r in await self.q("SELECT date FROM days_off")}

    async def toggle_day_off(self, d: str) -> bool:
        if await self.is_day_off(d):
            await self.x("DELETE FROM days_off WHERE date=?", (d,))
            return False
        await self.x("INSERT INTO days_off(date) VALUES(?)", (d,))
        return True

    async def blocked_in(self, a, b):
        return {r["ts"] for r in await self.q("SELECT ts FROM blocked_slots WHERE ts>=? AND ts<?", (a, b))}

    async def toggle_block(self, ts: int) -> bool:
        if await self.q1("SELECT 1 FROM blocked_slots WHERE ts=?", (ts,)):
            await self.x("DELETE FROM blocked_slots WHERE ts=?", (ts,))
            return False
        await self.x("INSERT INTO blocked_slots(ts) VALUES(?)", (ts,))
        return True

    async def busy_orders(self, a, b, exclude_id=None):
        return await self.q(
            "SELECT id,start_ts,end_ts FROM orders WHERE status!='canceled' AND start_ts<? AND end_ts>? AND id!=?",
            (b, a, exclude_id or 0))

    # --- пользователи ---
    async def upsert_user(self, u: dict, phone: str | None = None):
        full = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "Без имени"
        await self.x(
            "INSERT INTO users(id,username,full_name,phone,created_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name, "
            "phone=COALESCE(excluded.phone, users.phone)",
            (u["id"], u.get("username"), full, phone, int(time.time())))

    async def get_user(self, uid):
        return await self.q1("SELECT * FROM users WHERE id=?", (uid,))

    # --- заказы ---
    async def create_order(self, **o):
        now = int(time.time())
        oid = await self.x(
            "INSERT INTO orders(user_id,items,total,duration_min,start_ts,end_ts,comment,phone,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (o["user_id"], json.dumps(o["items"], ensure_ascii=False), o["total"], o["duration_min"],
             o["start_ts"], o["end_ts"], o["comment"], o["phone"], now, now))
        return await self.get_order(oid)

    async def get_order(self, oid):
        cur = await self.conn.execute(
            "SELECT o.*, u.username, u.full_name FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?", (oid,))
        return _order(await cur.fetchone())

    async def update_order(self, oid, **fields):
        bad = set(fields) - ORDER_FIELDS
        if bad:
            raise ValueError(f"unknown fields {bad}")
        fields["updated_at"] = int(time.time())
        sets = ",".join(f"{k}=?" for k in fields)
        await self.x(f"UPDATE orders SET {sets} WHERE id=?", tuple(fields.values()) + (oid,))
        return await self.get_order(oid)

    async def user_orders(self, uid):
        cur = await self.conn.execute(
            "SELECT o.*, u.username, u.full_name FROM orders o LEFT JOIN users u ON u.id=o.user_id "
            "WHERE o.user_id=? ORDER BY o.start_ts DESC LIMIT 100", (uid,))
        return [_order(r) for r in await cur.fetchall()]

    async def all_orders(self, status: str = ""):
        where = "WHERE o.status=?" if status else ""
        args = (status,) if status else ()
        cur = await self.conn.execute(
            "SELECT o.*, u.username, u.full_name FROM orders o LEFT JOIN users u ON u.id=o.user_id "
            f"{where} ORDER BY CASE WHEN o.status IN ('new','in_work') THEN o.start_ts ELSE -o.start_ts END LIMIT 300",
            args)
        return [_order(r) for r in await cur.fetchall()]

    async def order_counts(self):
        rows = await self.q("SELECT status, COUNT(*) n FROM orders GROUP BY status")
        return {r["status"]: r["n"] for r in rows}
