from datetime import datetime

from config import TZ

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря"]

STATUS_TITLES = {
    "new": "🆕 Новый",
    "in_work": "🔧 В работе",
    "done": "✅ Выполнен",
    "canceled": "❌ Отменён",
}


def fmt_dt(ts: int) -> str:
    d = datetime.fromtimestamp(ts, TZ)
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}, {d:%H:%M} МСК"


def rub(n: int) -> str:
    return f"{int(n):,}".replace(",", " ") + " ₽"


def dur(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"
