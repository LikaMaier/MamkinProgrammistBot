import os
from zoneinfo import ZoneInfo

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# ID администраторов через запятую: ADMIN_IDS=123456789,987654321
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
# Публичный https-адрес сервиса (Railway → Settings → Networking → Generate Domain)
WEBAPP_URL = os.getenv("WEBAPP_URL", "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))
DB_PATH = os.getenv("DB_PATH", "data/shop.db")
SHOP_NAME = os.getenv("SHOP_NAME", "SoftSetup — установка ПО")

TZ = ZoneInfo("Europe/Moscow")

# Оплата: off — без оплаты, stars — Telegram Stars, provider — ЮKassa и др. через BotFather
PAYMENT_MODE = os.getenv("PAYMENT_MODE", "off").lower()
PAYMENT_PROVIDER_TOKEN = os.getenv("PAYMENT_PROVIDER_TOKEN", "")
STAR_RATE_RUB = float(os.getenv("STAR_RATE_RUB", "1.8"))  # сколько ₽ в одной звезде

# За сколько часов до записи клиент ещё может отменить/перенести её сам
CANCEL_DEADLINE_HOURS = int(os.getenv("CANCEL_DEADLINE_HOURS", "2"))

# Режим разработки: позволяет открыть Mini App в обычном браузере без Telegram
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"
DEV_USER_ID = int(os.getenv("DEV_USER_ID", "1"))
