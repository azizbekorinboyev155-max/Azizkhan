#!/usr/bin/env python3
"""
🚗 MOTUS Assistant Bot — v5
"""

import logging
import os
import re
import asyncio
import sqlite3
import math
from datetime import datetime
from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters,
    ContextTypes
)
from telegram.error import BadRequest

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN     = os.getenv("MOTUS_BOT_TOKEN", "8875758009:AAHbQynJYLIJRA6pDY3Esa6FRZdlON5zkBo")
ADMIN_CHAT_ID = int(os.getenv("MOTUS_ADMIN_CHAT_ID", "8304618603"))
# DB_PATH: agar serverda doimiy disk bo'lsa (masalan Render/VPS'da /data kabi
# persistent volume), MOTUS_DB_PATH environment variable orqali shu joyga
# ko'rsating — aks holda konteyner qayta ishga tushganda motus.db o'chib
# ketishi mumkin. Lokal ishga tushirishda hech narsa qilmasangiz ham bo'ladi.
DB_PATH       = os.getenv("MOTUS_DB_PATH", "motus.db")

# ══════════════════════════════════════════════════════════
#  SQLITE
# ══════════════════════════════════════════════════════════
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            first_seen TEXT,
            last_active TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS arizalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            role TEXT,
            data TEXT,
            status TEXT DEFAULT 'yangi',
            created_at TEXT
        )
    """)
    # Ustalar — moslashtirish (matching) uchun alohida, strukturaviy jadval
    c.execute("""
        CREATE TABLE IF NOT EXISTS ustalar (
            tg_id INTEGER PRIMARY KEY,
            ism TEXT,
            tel TEXT,
            username TEXT,
            mashina_turi TEXT,
            soha TEXT,
            tajriba TEXT,
            karta TEXT,
            lat REAL,
            lon REAL,
            faol INTEGER DEFAULT 1,
            created_at TEXT
        )
    """)
    # last_active ustuni eski bazada bo'lmasligi mumkin — xato bo'lmasi uchun
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
    except Exception:
        pass
    # Mijoz profilini DOIMIY saqlash uchun ustunlar — shu bo'lmasa mijoz
    # har safar /start bilan to'liq ro'yxatdan o'tishga majbur bo'ladi
    for col, col_type in [
        ("mijoz_ism", "TEXT"), ("mijoz_tel", "TEXT"), ("mijoz_username", "TEXT"),
        ("mijoz_mashina", "TEXT"), ("mijoz_karta", "TEXT"),
        ("mijoz_lat", "REAL"), ("mijoz_lon", "REAL"),
        ("is_mijoz", "INTEGER DEFAULT 0"),
    ]:
        try:
            c.execute(f"ALTER TABLE users ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    # Arizani aynan bir ustaga biriktirish uchun ustunlar
    for col, col_type in [
        ("assigned_usta_id", "INTEGER"), ("assigned_usta_ism", "TEXT"),
        ("assigned_usta_tel", "TEXT"), ("client_lat", "REAL"), ("client_lon", "REAL"),
    ]:
        try:
            c.execute(f"ALTER TABLE arizalar ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    conn.commit()
    conn.close()

def log_user(tg_id: int, first_name: str, username: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute(
        "INSERT INTO users (tg_id, first_name, username, first_seen, last_active) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(tg_id) DO UPDATE SET last_active=excluded.last_active, first_name=excluded.first_name",
        (tg_id, first_name, username or "", now, now)
    )
    conn.commit()
    conn.close()

def save_ariza(tg_id: int, role: str, data_text: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO arizalar (tg_id, role, data, status, created_at) VALUES (?, ?, ?, 'yangi', ?)",
        (tg_id, role, data_text, datetime.now().isoformat())
    )
    ariza_id = c.lastrowid
    conn.commit()
    conn.close()
    return ariza_id

def update_status(ariza_id: int, status: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE arizalar SET status=? WHERE id=?", (status, ariza_id))
    conn.commit()
    conn.close()

def get_last_contact(tg_id: int, role: str, before_id: int):
    """Oldingi murojaat sanasi"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT created_at FROM arizalar WHERE tg_id=? AND role=? AND id<? ORDER BY id DESC LIMIT 1",
        (tg_id, role, before_id)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    try:
        dt = datetime.fromisoformat(row[0])
        return dt.strftime("%d.%m.%Y, soat %H:%M")
    except Exception:
        return row[0]

def save_mijoz_profile(tg_id: int, ism: str, tel: str, username: str,
                        mashina: str, karta: str, lat: float, lon: float):
    """Mijoz to'liq ro'yxatdan o'tgach profilini bazaga DOIMIY yozib qo'yadi.
    Shu tufayli keyingi safar u qayta ro'yxatdan o'tmasdan to'g'ridan-to'g'ri
    ariza qoldira oladi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE users SET mijoz_ism=?, mijoz_tel=?, mijoz_username=?, mijoz_mashina=?,
        mijoz_karta=?, mijoz_lat=?, mijoz_lon=?, is_mijoz=1 WHERE tg_id=?
    """, (ism, tel, username, mashina, karta, lat, lon, tg_id))
    conn.commit()
    conn.close()

def get_mijoz_profile(tg_id: int):
    """Bazadan mijoz profilini qaytaradi (agar avval ro'yxatdan o'tgan bo'lsa)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT mijoz_ism, mijoz_tel, mijoz_username, mijoz_mashina, mijoz_karta,
               mijoz_lat, mijoz_lon
        FROM users WHERE tg_id=? AND is_mijoz=1
    """, (tg_id,))
    row = c.fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    keys = ["ism", "tel", "username", "mashina", "karta", "lat", "lon"]
    return dict(zip(keys, row))

def get_client_ariza_count(tg_id: int) -> int:
    """Mijozning jami nechta ariza qoldirganini qaytaradi (admin uchun oqim)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM arizalar WHERE tg_id=? AND role='mijoz_ariza'", (tg_id,))
    n = c.fetchone()[0]
    conn.close()
    return n

def save_usta(tg_id: int, ism: str, tel: str, username: str, mashina_turi: str,
              soha: str, tajriba: str, karta: str, lat: float, lon: float):
    """Usta ro'yxatdan o'tgach uni alohida `ustalar` jadvaliga yozadi —
    shu jadval orqali mos ustalarga arizalar avtomatik yuboriladi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO ustalar (tg_id, ism, tel, username, mashina_turi, soha, tajriba,
        karta, lat, lon, faol, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(tg_id) DO UPDATE SET ism=excluded.ism, tel=excluded.tel,
        username=excluded.username, mashina_turi=excluded.mashina_turi,
        soha=excluded.soha, tajriba=excluded.tajriba, karta=excluded.karta,
        lat=excluded.lat, lon=excluded.lon, faol=1
    """, (tg_id, ism, tel, username, mashina_turi, soha, tajriba, karta, lat, lon,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()

def _distance_km(lat1, lon1, lat2, lon2):
    """Ikki nuqta orasidagi masofa (km) — eng yaqin ustani topish uchun."""
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(min(1, math.sqrt(a)))

def get_ustalar_for_ariza(soha_guess: str, client_lat: float, client_lon: float, limit: int = 6):
    """Muammo matnidan taxmin qilingan sohaga mos, eng yaqin faol ustalarni topadi.
    Agar soha aniqlanmagan bo'lsa yoki mos usta topilmasa — barcha faol ustalar
    orasidan eng yaqinlarini qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if soha_guess:
        c.execute("SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1 AND soha LIKE ?",
                   (f"%{soha_guess}%",))
        rows = c.fetchall()
        if not rows:
            c.execute("SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1")
            rows = c.fetchall()
    else:
        c.execute("SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1")
        rows = c.fetchall()
    conn.close()
    result = []
    for tg_id, ism, tel, soha, lat, lon in rows:
        dist = _distance_km(client_lat, client_lon, lat, lon) if lat and lon else 999
        result.append({"tg_id": tg_id, "ism": ism, "tel": tel, "soha": soha, "dist": dist})
    result.sort(key=lambda x: x["dist"])
    return result[:limit]

def assign_ariza(ariza_id: int, usta_tg_id: int, usta_ism: str, usta_tel: str) -> bool:
    """Arizani birinchi bo'lib 'qabul qilaman' degan ustaga biriktiradi.
    Agar ariza allaqachon boshqasiga biriktirilgan bo'lsa False qaytaradi
    (poyga holatini — race condition — oldini olish uchun)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE arizalar SET assigned_usta_id=?, assigned_usta_ism=?, assigned_usta_tel=?,
        status='jarayonda' WHERE id=? AND assigned_usta_id IS NULL
    """, (usta_tg_id, usta_ism, usta_tel, ariza_id))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def set_ariza_location(ariza_id: int, lat: float, lon: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE arizalar SET client_lat=?, client_lon=? WHERE id=?", (lat, lon, ariza_id))
    conn.commit()
    conn.close()

def get_ariza_client(ariza_id: int):
    """Ariza egasi (mijoz) tg_id sini qaytaradi — unga xabar yuborish uchun."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tg_id FROM arizalar WHERE id=?", (ariza_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def guess_usta_turi(matn: str) -> str:
    """Mijoz yozgan muammo matnidan qaysi soha kerakligini taxmin qiladi.
    Poll/tugma o'rniga — foydalanuvchi shunchaki yozadi, bot o'zi aniqlaydi."""
    m = matn.lower()
    if any(k in m for k in ["evakuat", "tort", "yura olmayapti", "yurmayapti", "harakatlanmayapti", "qolib ket"]):
        return "Evakuator"
    if any(k in m for k in ["motor", "dvigatel", "dvigatel", "porshen", "gaz", "benzin yeyapti"]):
        return "Motorist"
    if any(k in m for k in ["svet", "elektr", "akkumulyator", "aккум", "indikator", "farа", "chiroq"]):
        return "Elektrik"
    if any(k in m for k in ["shina", "g'ildirak", "gildirak", "balансиров", "balansirov"]):
        return "Balansirovka"
    if any(k in m for k in ["podveska", "xodovoy", "amortizator", "rulda", "tebranayapti"]):
        return "Xodovoy"
    return ""

def get_all_user_ids() -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tg_id FROM users")
    rows = c.fetchall()
    conn.close()
    return [r[0] for r in rows]

def get_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM arizalar")
    total_arizalar = c.fetchone()[0]
    c.execute("SELECT role, COUNT(*) FROM arizalar GROUP BY role")
    by_role = dict(c.fetchall())
    c.execute("SELECT COUNT(*) FROM arizalar WHERE status='yangi'")
    yangi = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM arizalar WHERE status='jarayonda'")
    jarayonda = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM arizalar WHERE status='bajarildi'")
    bajarildi = c.fetchone()[0]
    # Oxirgi 7 kunda faol bo'lganlar
    c.execute(
        "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', '-7 days')"
    )
    faol_7kun = c.fetchone()[0]
    # Bugun kirganlar
    c.execute(
        "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', 'start of day')"
    )
    bugun = c.fetchone()[0]
    # Faol (moslashtirish uchun tayyor) ustalar soni
    c.execute("SELECT COUNT(*) FROM ustalar WHERE faol=1")
    faol_ustalar = c.fetchone()[0]
    conn.close()
    return total_users, total_arizalar, by_role, yangi, jarayonda, bajarildi, faol_7kun, bugun, faol_ustalar

# ══════════════════════════════════════════════════════════
#  STATES
# ══════════════════════════════════════════════════════════
(
    ROLE_SELECT, ROLE_MENU,
    MIJ_ISM, MIJ_TEL, MIJ_USERNAME, MIJ_MASHINA,
    MIJ_KARTA, MIJ_LOKATSIYA, MIJ_PROFIL_MENU,
    ARIZA_MUAMMO, ARIZA_USTA_TURI, ARIZA_USTA_BOSHQA, ARIZA_LOKATSIYA,
    USTA_ISM, USTA_TEL, USTA_USERNAME, USTA_MASHINA_TURI,
    USTA_SOHA, USTA_SOHA_BOSHQA, USTA_TAJRIBA,
    USTA_KARTA, USTA_LOKATSIYA,
    CC_ISM, CC_TEL, CC_USERNAME, CC_TAJRIBA,
    CC_KARTA, CC_LOKATSIYA,
    EV_ISM, EV_TEL, EV_USERNAME, EV_TAJRIBA,
    EV_KARTA, EV_LOKATSIYA,
    BROADCAST_TEXT,
) = range(35)

# Bitta arizani bir nechta ustaga yuborganimizda, kimdir "qabul qilaman"
# desa qolganlarga yuborilgan xabarlarni "band qilindi"ga o'zgartirish uchun
# xotirada saqlab turamiz: {ariza_id: [(usta_tg_id, message_id), ...]}
PENDING_NOTIFICATIONS: dict[int, list[tuple[int, int]]] = {}

STICKER_WELCOME  = "CAACAgIAAxkBAAIBqmVx9VsQpL3HjXRzQnbLU8XJi3lRAAIFAANWnb0KjTVDjVfSe-AeBA"
STICKER_MIJOZ    = "CAACAgIAAxkBAAIBrGVx9VwTqMoxoYRQDGvLf0IqkwOVAAIGAANWnb0KbC5_fAJ7HMYeBA"
STICKER_USTA     = "CAACAgIAAxkBAAIBrmVx9V1aQ3sPYVQ_a3dLRdVQhx8TAAIHAANW"
STICKER_CC       = "CAACAgIAAxkBAAIBsGVx9V6JQ3sPYVQ_a3dLRdVQhx8TAAIHB"
STICKER_EVAK     = "CAACAgIAAxkBAAIBsmVx9V8aQ3sPYVQ_a3dLRdVQhx8TAAIHI"
STICKER_LOADING  = "CAACAgIAAxkBAAIBqmVx9VsQpL3HjXRzQnbLU8XJi3lRAAIFAANWnb0KjTVDjVfSe-AeBA"

# ══════════════════════════════════════════════════════════
#  VALIDATSIYA
# ══════════════════════════════════════════════════════════
def validate_ism(text: str) -> tuple[bool, str]:
    text = text.strip()
    parts = text.split()
    if len(parts) < 2:
        return False, (
            "⚠️ <b>Iltimos, ism VA familiyangizni to'liq yozing!</b>\n\n"
            "📝 Lotin: <code>Abdullayev Jasur</code>\n"
            "📝 Kiril: <code>Абдуллаев Жасур</code>\n\nQaytadan kiriting:"
        )
    uz_pattern = r"^[A-Za-zА-Яа-яЁёʼ''`\-]+$"
    if not all(re.match(uz_pattern, p) for p in parts):
        return False, (
            "⚠️ <b>Faqat harflar bo'lishi kerak!</b>\n\n"
            "📝 Lotin: <code>Toshmatov Sardor</code>\n"
            "📝 Kiril: <code>Тошматов Сардор</code>\n\nQaytadan kiriting:"
        )
    return True, ""

def validate_tel(text: str) -> tuple[bool, str]:
    cleaned = re.sub(r"[\s\-\(\)]", "", text.strip())
    if not re.match(r"^(\+998|998|0)\d{9}$", cleaned):
        return False, (
            "⚠️ <b>Telefon noto'g'ri!</b>\n\n"
            "✅ <code>+998901234567</code>\n"
            "✅ <code>0901234567</code>\n\nQaytadan kiriting:"
        )
    return True, ""

# ══════════════════════════════════════════════════════════
#  KLAVIATURALAR
# ══════════════════════════════════════════════════════════
def role_inline_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚗  Mijoz",                  callback_data="role_mijoz")],
        [InlineKeyboardButton("🔧  Usta",                   callback_data="role_usta")],
        [InlineKeyboardButton("📞  Call Center xodimi",     callback_data="role_cc")],
        [InlineKeyboardButton("🚛  Evakuator haydovchisi",  callback_data="role_evak")],
    ])

def role_menu_kb(role: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("ℹ️  Ma'lumot",           callback_data=f"info_{role}")],
        [InlineKeyboardButton("📝  Ma'lumot qoldirish",  callback_data=f"register_{role}")],
    ])

def usta_turi_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Motorist",               callback_data="usta_motorist")],
        [InlineKeyboardButton("🔌 Elektrik",                callback_data="usta_elektrik")],
        [InlineKeyboardButton("🛞 Xodovoy",                 callback_data="usta_xodovoy")],
        [InlineKeyboardButton("⚖️ Balansirovka",            callback_data="usta_balansirovka")],
        [InlineKeyboardButton("✏️ Boshqa (o'zim yozaman)",  callback_data="usta_boshqa")],
    ])

def tajriba_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Ha, tajribam bor",         callback_data="tajriba_ha")],
        [InlineKeyboardButton("🌱 Yo'q, yangi boshlovchi",  callback_data="tajriba_yoq")],
    ])

def location_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📍 Hozirgi lokatsiyamni yuborish", request_location=True)],
            [KeyboardButton("❌ Bekor qilish")],
        ],
        resize_keyboard=True, one_time_keyboard=True
    )

CANCEL_BTN_TEXT = "❌ Bekor qilish"
CANCEL_FILTER = filters.Regex(r"^❌ Bekor qilish$")
# Oddiy matn kiritish qadamlari uchun filter — "❌ Bekor qilish" tugmasi
# bosilsa bu matn ismi/telefoni deb qabul qilinmasin, balki fallbacks orqali
# cancel() ga tushsin
NORMAL_TEXT = filters.TEXT & ~filters.COMMAND & ~CANCEL_FILTER

def text_step_cancel_kb():
    """Oddiy matn kiritish bosqichlari (ism, tel, karta va h.k.) uchun —
    pastda doim '⬅️ Orqaga / ❌ Bekor qilish' tugmasi ko'rinib tursin."""
    return ReplyKeyboardMarkup(
        [[KeyboardButton(CANCEL_BTN_TEXT)]],
        resize_keyboard=True, one_time_keyboard=False
    )

# ══ ASOSIY TUGMA — MIJOZ PROFIL MENYUSI ═════════════════
# Doimiy pastda ko'rinadigan ReplyKeyboard
def mijoz_main_kb():
    """Mijoz ro'yxatdan o'tgandan keyin DOIM pastda ko'rinadigan tugmalar"""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🆘 Muammo bor (Ariza qoldirish)")],
            [KeyboardButton("📍 Lokatsiyamni yuborish", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False  # O'chib ketmaydi!
    )

def status_kb(ariza_id: int):
    """Admin uchun status tugmalari"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟡 Jarayonda",   callback_data=f"st_{ariza_id}_jarayonda"),
            InlineKeyboardButton("✅ Bajarildi",   callback_data=f"st_{ariza_id}_bajarildi"),
        ],
        [InlineKeyboardButton("❌ Bekor qilindi", callback_data=f"st_{ariza_id}_bekor")],
        [InlineKeyboardButton("🧑‍🔧 Expert usta yuborish", callback_data=f"st_{ariza_id}_expert")],
    ])

# ══════════════════════════════════════════════════════════
#  QUMSOAT ANIMATSIYASI
# ══════════════════════════════════════════════════════════
async def send_loading_and_wait(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    try:
        sm = await context.bot.send_sticker(chat_id=chat_id, sticker=STICKER_LOADING)
        context.user_data["loading_sticker_id"] = sm.message_id
    except Exception:
        context.user_data["loading_sticker_id"] = None
    frames = [
        "⏳ <b>Lokatsiya aniqlanmoqda...</b>\n\nBiroz kuting 🙏",
        "⌛ <b>Lokatsiya aniqlanmoqda...</b>\n\nBiroz kuting 🙏",
        "⏳ <b>Lokatsiya aniqlanmoqda...</b>\n\nBiroz kuting 🙏",
    ]
    try:
        lm = await context.bot.send_message(chat_id=chat_id, text=frames[0], parse_mode="HTML")
        context.user_data["loading_msg_id"] = lm.message_id
        for frame in frames[1:]:
            await asyncio.sleep(1.2)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=lm.message_id,
                    text=frame, parse_mode="HTML"
                )
            except BadRequest:
                pass
    except Exception:
        context.user_data["loading_msg_id"] = None

async def delete_loading(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    for key in ["loading_sticker_id", "loading_msg_id"]:
        mid = context.user_data.pop(key, None)
        if mid:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=mid)
            except Exception:
                pass

GEO_OFF_TEXT = (
    "⚠️ <b>Geolokatsiya o'chiq!</b>\n\n"
    "📱 <b>Telefonda:</b>\n"
    "1️⃣ Sozlamalar → Joylashuv → Yoqing\n"
    "2️⃣ Telegram ilovasiga ruxsat bering\n"
    "3️⃣ Quyidagi tugmani bosing 👇\n\n"
    "💻 <b>Kompyuterda:</b> Brauzerda joylashuv ruxsatini bering"
)

# ══════════════════════════════════════════════════════════
#  YORDAMCHILAR
# ══════════════════════════════════════════════════════════
async def send_sticker_safe(update, sticker_id):
    try:
        await update.effective_chat.send_sticker(sticker_id)
    except Exception:
        pass

async def send_to_admin(context, text: str, ariza_id: int = None):
    if not ADMIN_CHAT_ID:
        return
    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=status_kb(ariza_id) if ariza_id else None
        )
    except Exception as e:
        logger.error(f"Admin xabar xatosi: {e}")

def progress_bar(current: int, total: int) -> str:
    return f"{'🟩'*current}{'⬜'*(total-current)} {current}/{total}"

# ══════════════════════════════════════════════════════════
#  /start
# ══════════════════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    user = update.effective_user
    log_user(user.id, user.first_name or "", user.username or "")
    await send_sticker_safe(update, STICKER_WELCOME)
    await update.message.reply_text(
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "Davom etishdan oldin, <b>kim ekanligingizni</b> tanlang 👇",
        parse_mode="HTML",
        reply_markup=role_inline_kb()
    )
    return ROLE_SELECT

# ══════════════════════════════════════════════════════════
#  ROL TANLASH
# ══════════════════════════════════════════════════════════
ROLE_LABELS = {
    "mijoz": "🚗 Mijoz",
    "usta":  "🔧 Usta",
    "cc":    "📞 Call Center xodimi",
    "evak":  "🚛 Evakuator haydovchisi",
}

async def role_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = query.data.replace("role_", "")
    context.user_data["role"] = role
    name = update.effective_user.first_name or "Do'stim"
    await query.edit_message_text(
        f"🌟 <b>Assalomu alaykum, {name}!</b>\n\n"
        "╔════════════════════════════╗\n"
        "║   🚗  <b>MOTUS Assistant</b>      ║\n"
        "║  <i>Harakatni davom ettiramiz! 🚀</i> ║\n"
        "╚════════════════════════════╝\n\n"
        f"Siz <b>{ROLE_LABELS[role]}</b> sifatida tanlandingiz.\n"
        "👇 Quyidagilardan birini tanlang:",
        parse_mode="HTML",
        reply_markup=role_menu_kb(role)
    )
    return ROLE_MENU

# ══════════════════════════════════════════════════════════
#  INFO MATNLAR
# ══════════════════════════════════════════════════════════
INFO_TEXTS = {
"mijoz": (
    "📖 <b>MOTUS haqida — Mijozlar uchun</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🚗 <b>MOTUS nima?</b>\n"
    "Mashina egalariga yaqin atrofdagi <b>ishonchli va sertifikatlangan</b> "
    "ustaxona xizmatlarini <b>o'rtacha 20 daqiqada</b> topib beruvchi raqamli platforma.\n\n"
    "🎯 <b>Xizmat yo'nalishlari:</b>\n"
    "⚙️ <b>Motorist</b> — dvigatel va motor ta'miri\n"
    "🔌 <b>Elektrik</b> — elektr tizimlar va diagnostika\n"
    "🛞 <b>Xodovoy</b> — to'xtatish tizimi va podvozka\n"
    "⚖️ <b>Balansirovka</b> — shinomontaj va muvozanat\n"
    "🚛 <b>Evakuator</b> — buzilib qolgan mashinani olib ketish\n\n"
    "🔐 <b>Nega ma'lumotlar muhim?</b>\n"
    "• <b>Xavfsizlik</b> — ma'lumotlar faqat xizmat uchun\n"
    "• <b>Tezkorlik</b> — to'liq ma'lumot = tez yordam\n"
    "• <b>Ishonch</b> — har bir usta 3 bosqichli testdan o'tgan\n"
    "• <b>Kafolat</b> — to'lov platforma orqali xavfsiz\n\n"
    "✅ <b>Bizning afzalligimiz:</b>\n"
    "• O'rtacha <b>20 daqiqada</b> usta topiladi\n"
    "• Narxlar <b>belgilangan</b> — ortiqcha pul yo'q\n"
    "• Xizmat <b>kafolatli</b>\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📝 Tayyor bo'lsangiz, <b>Ma'lumot qoldirish</b>ni bosing!"
),
"usta": (
    "🔧 <b>MOTUS haqida — Ustalar uchun</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "💰 <b>Nega aynan MOTUS?</b>\n"
    "Toshkentda <b>600 000+ avtomobil</b> bor — MOTUS siz bilan "
    "mijozni <b>to'g'ridan-to'g'ri</b> bog'laydi!\n\n"
    "📈 <b>Sizga nima beradi?</b>\n"
    "• <b>Doimiy buyurtmalar</b> — mijozlar oqimi kafolatlanadi\n"
    "• <b>To'lov xavfsizligi</b> — pul to'g'ridan-to'g'ri kartangizga\n"
    "• <b>Reklama kerak emas</b> — platforma o'zi topib beradi\n"
    "• <b>Reyting tizimi</b> — yaxshi ishlasangiz ko'proq buyurtma\n\n"
    "📋 <b>Qanday ishlaydi?</b>\n"
    "1️⃣ Ma'lumotlaringizni qoldirasiz\n"
    "2️⃣ 3 bosqichli sinovdan o'tasiz\n"
    "3️⃣ Platformaga rasman qo'shilasiz\n"
    "4️⃣ Buyurtmalar kela boshlaydi!\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🚀 Mahoratingizni daromadga aylantiring!"
),
"cc": (
    "📞 <b>MOTUS haqida — Call Center xodimlari uchun</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🎧 <b>Siz nima qilasiz?</b>\n"
    "Mijoz va usta o'rtasidagi <b>ko'prik</b> sifatida ishlaysiz: "
    "arizalarni qabul qilasiz va tezkor yechim topasiz.\n\n"
    "💼 <b>Nega bu ish qiziqarli?</b>\n"
    "• <b>Moslashuvchan jadval</b> — smenalar bo'yicha\n"
    "• <b>Doimiy daromad</b> — muvaffaqiyatli yo'naltirishlar uchun bonus\n"
    "• <b>Real ta'sir</b> — har bir qo'ng'irog'ingiz kimgadir yordam\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🎯 Jamoamizga qo'shiling!"
),
"evak": (
    "🚛 <b>MOTUS haqida — Evakuator haydovchilari uchun</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    "🆘 <b>Siz nima qilasiz?</b>\n"
    "Yo'lda qolib ketgan mashinalarni <b>xavfsiz va tezkor</b> tarzda "
    "yetkazib berasiz — siz <b>eng katta najotkorsiz</b>!\n\n"
    "💪 <b>Nega bu ish foydali?</b>\n"
    "• <b>Yuqori talab</b> — 600 000+ avtomobil, har kuni o'nlab chaqiruv\n"
    "• <b>Adolatli narx</b> — har bir chaqiruv uchun belgilangan to'lov\n"
    "• <b>Tezkor to'lov</b> — xizmatdan so'ng kartangizga\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "🚀 Yo'lda qolganlarga najot bo'ling!"
),
}

async def show_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = query.data.replace("info_", "")
    await query.edit_message_text(
        INFO_TEXTS.get(role, "Ma'lumot topilmadi."),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📝 Ma'lumot qoldirish", callback_data=f"register_{role}")],
            [InlineKeyboardButton("🏠 Bosh sahifa",        callback_data="home")],
        ])
    )
    return ROLE_MENU

async def show_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    role = query.data.replace("register_", "")
    context.user_data["role"] = role
    if role == "mijoz":   return await start_mijoz_flow(update, context)
    elif role == "usta":  return await start_usta_flow(update, context)
    elif role == "cc":    return await start_cc_flow(update, context)
    elif role == "evak":  return await start_evak_flow(update, context)

async def home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "👋 <b>Kim ekanligingizni qaytadan tanlang:</b>",
        parse_mode="HTML", reply_markup=role_inline_kb()
    )
    return ROLE_SELECT

# ══════════════════════════════════════════════════════════
#  🚗 MIJOZ RO'YXAT (6 bosqich)
# ══════════════════════════════════════════════════════════
MIJOZ_TOTAL = 6

async def start_mijoz_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["role"] = "mijoz"
    await send_sticker_safe(update, STICKER_MIJOZ)
    await query.edit_message_text(
        "🚗 <b>Siz MIJOZ sifatida davom etyapsiz!</b>\n\n"
        "Minglab mijozlar biz orqali ishonchli usta topmoqda. Siz ham qo'shiling! 💪\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {progress_bar(1, MIJOZ_TOTAL)}\n"
        "📝 <b>1-qadam:</b> Ism va familiyangizni to'liq yozing\n\n"
        "📌 Lotin: <code>Abdullayev Jasur</code>\n"
        "📌 Kiril: <code>Абдуллаев Жасур</code>",
        parse_mode="HTML"
    )
    await update.effective_chat.send_message(
        "↩️ Istalgan vaqtda bekor qilish uchun pastdagi tugmadan foydalaning.",
        reply_markup=text_step_cancel_kb()
    )
    return MIJ_ISM

async def mij_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_ism(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return MIJ_ISM
    context.user_data["ism"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(2, MIJOZ_TOTAL)}\n"
        "📞 <b>2-qadam:</b> Telefon raqam:\n📌 <code>+998901234567</code>",
        parse_mode="HTML"
    )
    return MIJ_TEL

async def mij_tel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_tel(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return MIJ_TEL
    context.user_data["tel"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(3, MIJOZ_TOTAL)}\n"
        "📲 <b>3-qadam:</b> Telegram username:\n"
        "📌 <code>@jasur99</code> — yo'q bo'lsa <i>\"yo'q\"</i>",
        parse_mode="HTML"
    )
    return MIJ_USERNAME

async def mij_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(4, MIJOZ_TOTAL)}\n"
        "🚙 <b>4-qadam:</b> Mashina turingiz:\n"
        "📌 <code>Cobalt 2020</code>, <code>Nexia 3</code>, <code>Malibu 2019</code>",
        parse_mode="HTML"
    )
    return MIJ_MASHINA

async def mij_mashina(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mashina"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(5, MIJOZ_TOTAL)}\n"
        "💳 <b>5-qadam:</b> Karta raqam:\n"
        "📌 <code>8600 1234 5678 9012</code> 🔒",
        parse_mode="HTML"
    )
    return MIJ_KARTA

async def mij_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["karta"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(6, MIJOZ_TOTAL)}\n"
        "📍 <b>6-qadam (oxirgi!):</b> Yashash joyi lokatsiyangiz\n"
        "⬇️ Quyidagi tugmani bosing:",
        parse_mode="HTML",
        reply_markup=location_kb()
    )
    asyncio.create_task(send_loading_and_wait(update, context))
    return MIJ_LOKATSIYA

async def mij_lokatsiya_xato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    await update.message.reply_text(GEO_OFF_TEXT, parse_mode="HTML", reply_markup=location_kb())
    asyncio.create_task(send_loading_and_wait(update, context))
    return MIJ_LOKATSIYA

async def mij_lokatsiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    d = context.user_data
    summary = (
        "🎉 <b>Tabriklaymiz! Ro'yxatdan muvaffaqiyatli o'tdingiz!</b>\n\n"
        "╔═══════════════════════════╗\n"
        "║   🚗  MIJOZ MA'LUMOTLARI  ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👤 <b>Ism Familiya:</b> {d.get('ism')}\n"
        f"📞 <b>Telefon:</b> {d.get('tel')}\n"
        f"📲 <b>Telegram:</b> {d.get('username')}\n"
        f"🚙 <b>Mashina:</b> {d.get('mashina')}\n"
        f"💳 <b>Karta:</b> {d.get('karta')}\n"
        f"🏠 <b>Yashash joyi:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✅ Profilingiz tayyor!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    save_ariza(update.effective_user.id, "mijoz_royxat", summary)
    # Profilni bazaga DOIMIY yozamiz — endi mijoz qayta /start bosib
    # ro'yxatdan o'tmasdan ham "Muammo bor" tugmasidan foydalana oladi
    save_mijoz_profile(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("mashina"), d.get("karta"), lat, lon
    )
    await send_to_admin(context, f"🚗 <b>YANGI MIJOZ RO'YXATDAN O'TDI</b>\n\n{summary}")

    # ══ ASOSIY O'ZGARISH: Mijozga doimiy tugmalar beriladi ══
    await update.message.reply_text(
        summary,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=mijoz_main_kb()   # ← DOIMIY pastda qoladi
    )
    return MIJ_PROFIL_MENU

# ══════════════════════════════════════════════════════════
#  🆘 ARIZA QOLDIRISH — muammo tugmasi bosilganda
#  Mijoz profil menyusida "🆘 Muammo bor" matn xabari yuborilganda ham ishlanadi
# ══════════════════════════════════════════════════════════
ARIZA_TOTAL = 2  # endi poll yo'q — faqat: 1) matn  2) lokatsiya

async def _mijoz_profil_bazadan_yukla(tg_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Agar context.user_data'da profil bo'lmasa (masalan conversation
    timeout bo'lgani uchun), bazadan qayta tiklaydi. Profil topilsa True."""
    if context.user_data.get("ism"):
        return True
    profil = get_mijoz_profile(tg_id)
    if not profil:
        return False
    context.user_data.update({
        "ism": profil["ism"], "tel": profil["tel"], "username": profil["username"],
        "mashina": profil["mashina"], "karta": profil["karta"],
    })
    return True

async def muammo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz '🆘 Muammo bor (Ariza qoldirish)' tugmasini bosganida ishlaydi.
    Bu handler ham asosiy /start suhbati ICHIDA (MIJ_PROFIL_MENU holatida),
    ham asosiy suhbat tugab qolgan holatda (pastdagi ariza_direct_conv orqali)
    chaqirilishi mumkin — shuning uchun profilni avval bazadan tekshiradi va
    hech qachon mijozni qayta ro'yxatdan o'tishga majburlamaydi."""
    ok = await _mijoz_profil_bazadan_yukla(update.effective_user.id, context)
    if not ok:
        # Bu odam ilgari hech qachon mijoz sifatida ro'yxatdan o'tmagan
        await update.message.reply_text(
            "⚠️ Sizni tizimda topa olmadim. Avval bir marta ro'yxatdan o'ting.\n"
            "/start buyrug'ini bosing — bu faqat bir marta kerak bo'ladi, "
            "keyingi safar to'g'ridan-to'g'ri ariza qoldira olasiz.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    await update.message.reply_text(
        "🆘 <b>Ariza qoldirish</b>\n\n"
        "Xavotir olmang, biz yordam beramiz! 🚗\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {progress_bar(1, ARIZA_TOTAL)}\n"
        "🔍 <b>Nima muammo kuzatilyapti?</b>\n"
        "<b>Batafsil</b> yozing — to'g'ri ustani topishga yordam beradi.\n"
        "Yozib bo'lgach, keyingi xabarda lokatsiyangizni so'raymiz — boshqa "
        "hech qanday tanlov qilishingiz shart emas.\n\n"
        "📌 <i>Masalan:</i> <code>Dvigatel isib ketmoqda va g'alati tovush chiqaryapti, "
        "indikator yonmoqda</code>\n"
        "📌 <i>Yoki:</i> <code>Mashina yo'lda o'chib qoldi, harakatlanmayapti, "
        "evakuator kerak</code>",
        parse_mode="HTML",
        reply_markup=text_step_cancel_kb()
    )
    return ARIZA_MUAMMO

async def ariza_muammo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["ariza_muammo"] = update.message.text.strip()
    # Poll/tugma o'rniga — matndan o'zimiz qanday usta kerakligini taxmin qilamiz
    guess = guess_usta_turi(context.user_data["ariza_muammo"])
    context.user_data["ariza_usta_turi"] = guess or "Aniqlanmagan — operator tayinlaydi"
    await update.message.reply_text(
        f"✅ Qabul qilindi!\n\n📊 {progress_bar(2, ARIZA_TOTAL)}\n"
        "📍 <b>Endi lokatsiyangizni yuboring</b> — shu bo'yicha sizga eng yaqin "
        "ustani/evakuatorni topamiz 🎯",
        parse_mode="HTML",
        reply_markup=location_kb()
    )
    asyncio.create_task(send_loading_and_wait(update, context))
    return ARIZA_LOKATSIYA

async def ariza_lokatsiya_xato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    await update.message.reply_text(GEO_OFF_TEXT, parse_mode="HTML", reply_markup=location_kb())
    asyncio.create_task(send_loading_and_wait(update, context))
    return ARIZA_LOKATSIYA

async def ariza_lokatsiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    d = context.user_data
    now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")
    summary = (
        "✅ <b>Arizangiz qabul qilindi!</b>\n\n"
        "╔═══════════════════════════╗\n"
        "║   🆘  YANGI ARIZA         ║\n"
        "╚═══════════════════════════╝\n\n"
        f"👤 <b>Ism:</b> {d.get('ism')}\n"
        f"📞 <b>Telefon:</b> {d.get('tel')}\n"
        f"📲 <b>Telegram:</b> {d.get('username')}\n"
        f"🚙 <b>Mashina:</b> {d.get('mashina', '—')}\n"
        f"🔍 <b>Muammo:</b> {d.get('ariza_muammo')}\n"
        f"👨‍🔧 <b>Kerakli usta:</b> {d.get('ariza_usta_turi')}\n"
        f"📍 <b>Lokatsiya:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱ <b>20 daqiqa ichida</b> operator bog'lanadi!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    # Mijozga: summary + doimiy tugmalar qaytariladi
    await update.message.reply_text(
        summary, parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=mijoz_main_kb()   # ← Tugmalar yana paydo bo'ladi
    )
    ariza_id = save_ariza(update.effective_user.id, "mijoz_ariza", summary)
    set_ariza_location(ariza_id, lat, lon)

    # Admin xabariga: sana, oxirgi murojaat, shu mijozning jami arizalar soni
    last = get_last_contact(update.effective_user.id, "mijoz_ariza", ariza_id)
    jami_ariza = get_client_ariza_count(update.effective_user.id)
    admin_text = (
        f"🆘 <b>YANGI ARIZA</b> #{ariza_id}\n"
        f"🕐 <b>Yuborilgan vaqt:</b> {now_str}\n"
        f"📊 <b>Bu mijozning jami arizalari:</b> {jami_ariza} ta\n"
    )
    if last:
        admin_text += f"🕓 <b>Oxirgi murojaat:</b> {last}\n"
    admin_text += f"\n{summary}"
    await send_to_admin(context, admin_text, ariza_id=ariza_id)

    # Mos keladigan eng yaqin faol ustalarga avtomatik yuboramiz —
    # birinchi bo'lib "Qabul qilaman" bosgan usta arizani oladi
    guess = d.get("ariza_usta_turi", "")
    if guess.startswith("Aniqlanmagan"):
        guess = ""
    asyncio.create_task(notify_ustalar(
        context, ariza_id, d.get("ariza_muammo", ""), guess, lat, lon,
        d.get("ism", ""), d.get("tel", "")
    ))
    return MIJ_PROFIL_MENU   # ← Ariza tugagach profil menyusiga qaytadi

# ══════════════════════════════════════════════════════════
#  Admin lokatsiya handler (mijoz profil menyusida lokatsiya yuborsa)
# ══════════════════════════════════════════════════════════
async def mijoz_lokatsiya_yangilash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz '📍 Lokatsiyamni yuborish' tugmasini bosganida lokatsiyasini yangilaydi"""
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    # Profil bazasidagi lokatsiyani ham yangilaymiz (keyingi arizalarda foydali)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET mijoz_lat=?, mijoz_lon=? WHERE tg_id=?",
              (lat, lon, update.effective_user.id))
    conn.commit()
    conn.close()
    await update.message.reply_text(
        f"📍 <b>Lokatsiyangiz qabul qilindi!</b>\n"
        f"📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <a href=\"{maps_link}\">Google Maps da ko'rish</a>",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=mijoz_main_kb()
    )
    await send_to_admin(
        context,
        f"📍 <b>MIJOZ LOKATSIYANI YANGILADI</b>\n"
        f"👤 {context.user_data.get('ism', 'Noma`lum')}\n"
        f"📞 {context.user_data.get('tel', '—')}\n"
        f"🗺 <a href=\"{maps_link}\">Google Maps</a>",
    )
    return MIJ_PROFIL_MENU

# ══════════════════════════════════════════════════════════
#  🔧 USTA (8 bosqich)
# ══════════════════════════════════════════════════════════
USTA_TOTAL = 8

async def start_usta_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["role"] = "usta"
    await send_sticker_safe(update, STICKER_USTA)
    await query.edit_message_text(
        "🔧 <b>Siz USTA sifatida davom etyapsiz!</b>\n\n"
        "Minglab mijozlar sizning xizmatlaringizni kutmoqda! 💪\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 {progress_bar(1, USTA_TOTAL)}\n"
        "📝 <b>1-qadam:</b> Ism va familiyangizni to'liq yozing\n\n"
        "📌 Lotin: <code>Toshmatov Sardor</code>\n"
        "📌 Kiril: <code>Тошматов Сардор</code>",
        parse_mode="HTML"
    )
    await update.effective_chat.send_message(
        "↩️ Istalgan vaqtda bekor qilish uchun pastdagi tugmadan foydalaning.",
        reply_markup=text_step_cancel_kb()
    )
    return USTA_ISM

async def usta_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_ism(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return USTA_ISM
    context.user_data["ism"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(2, USTA_TOTAL)}\n"
        "📞 <b>2-qadam:</b> Telefon:\n📌 <code>+998901234567</code>",
        parse_mode="HTML"
    )
    return USTA_TEL

async def usta_tel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_tel(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return USTA_TEL
    context.user_data["tel"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(3, USTA_TOTAL)}\n"
        "📲 <b>3-qadam:</b> Telegram username:\n"
        "📌 <code>@sardor_usta</code> — yo'q bo'lsa <i>\"yo'q\"</i>",
        parse_mode="HTML"
    )
    return USTA_USERNAME

async def usta_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(4, USTA_TOTAL)}\n"
        "🚗 <b>4-qadam:</b> Qaysi mashinalarga xizmat ko'rsatasiz?\n"
        "📌 <code>Chevrolet, Toyota</code> yoki <code>Barcha markalar</code>",
        parse_mode="HTML"
    )
    return USTA_MASHINA_TURI

async def usta_mashina_turi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["mashina_turi"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(5, USTA_TOTAL)}\n"
        "🛠 <b>5-qadam:</b> Qaysi soha ustasisiz?\nTanlang 👇",
        parse_mode="HTML", reply_markup=usta_turi_kb()
    )
    return USTA_SOHA

async def usta_soha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    soha_map = {
        "usta_motorist": "⚙️ Motorist", "usta_elektrik": "🔌 Elektrik",
        "usta_xodovoy": "🛞 Xodovoy", "usta_balansirovka": "⚖️ Balansirovka",
    }
    cb = query.data
    if cb == "usta_boshqa":
        await query.edit_message_text(
            f"📊 {progress_bar(5, USTA_TOTAL)}\n✏️ <b>Soha nomini yozing:</b>",
            parse_mode="HTML"
        )
        return USTA_SOHA_BOSHQA
    context.user_data["soha"] = soha_map[cb]
    await query.edit_message_text(
        f"✅ Soha: <b>{soha_map[cb]}</b>\n\n📊 {progress_bar(6, USTA_TOTAL)}\n"
        "📅 <b>6-qadam:</b> Tajribangiz necha yil?\n📌 <code>5 yil</code>",
        parse_mode="HTML"
    )
    return USTA_TAJRIBA

async def usta_soha_boshqa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["soha"] = f"✏️ Boshqa: {update.message.text.strip()}"
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(6, USTA_TOTAL)}\n"
        "📅 <b>6-qadam:</b> Tajribangiz necha yil?\n📌 <code>5 yil</code>",
        parse_mode="HTML"
    )
    return USTA_TAJRIBA

async def usta_tajriba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tajriba"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(7, USTA_TOTAL)}\n"
        "💳 <b>7-qadam:</b> Karta raqam:\n📌 <code>8600 1234 5678 9012</code> 💰",
        parse_mode="HTML"
    )
    return USTA_KARTA

async def usta_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["karta"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(8, USTA_TOTAL)}\n"
        "📍 <b>8-qadam (oxirgi!):</b> Ishxona lokatsiyasi\n⬇️ Tugmani bosing:",
        parse_mode="HTML", reply_markup=location_kb()
    )
    asyncio.create_task(send_loading_and_wait(update, context))
    return USTA_LOKATSIYA

async def usta_lokatsiya_xato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    await update.message.reply_text(GEO_OFF_TEXT, parse_mode="HTML", reply_markup=location_kb())
    asyncio.create_task(send_loading_and_wait(update, context))
    return USTA_LOKATSIYA

async def usta_lokatsiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    d = context.user_data
    now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")
    summary = (
        "🎉 <b>Tabriklaymiz! MOTUS ustalar jamoasiga qo'shildingiz!</b>\n\n"
        "╔═══════════════════════════╗\n║   🔧  USTA MA'LUMOTLARI   ║\n╚═══════════════════════════╝\n\n"
        f"👤 <b>Ism:</b> {d.get('ism')}\n📞 <b>Tel:</b> {d.get('tel')}\n"
        f"📲 <b>Telegram:</b> {d.get('username')}\n🚗 <b>Mashina:</b> {d.get('mashina_turi')}\n"
        f"🛠 <b>Soha:</b> {d.get('soha')}\n📅 <b>Tajriba:</b> {d.get('tajriba')}\n"
        f"💳 <b>Karta:</b> {d.get('karta')}\n"
        f"📍 <b>Ishxona:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Operatorimiz bog'lanib, 3 bosqichli testdan o'tishingizni so'raydi.\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "usta", summary)
    # Ustani alohida `ustalar` jadvaliga ham yozamiz — shu orqali unga mos
    # keladigan yangi arizalar avtomatik yuboriladi
    save_usta(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("mashina_turi"), d.get("soha", ""), d.get("tajriba"), d.get("karta"),
        lat, lon
    )
    last = get_last_contact(update.effective_user.id, "usta", ariza_id)
    admin_text = f"🔧 <b>YANGI USTA</b> #{ariza_id}\n🕐 {now_str}\n"
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    await send_to_admin(context, admin_text, ariza_id=ariza_id)
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  📞 CALL CENTER (6 bosqich)
# ══════════════════════════════════════════════════════════
CC_TOTAL = 6

async def start_cc_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["role"] = "call_center"
    await send_sticker_safe(update, STICKER_CC)
    await query.edit_message_text(
        "📞 <b>Siz CALL CENTER xodimi sifatida davom etyapsiz!</b>\n\n"
        "MOTUS 24/7 jamoasiga xush kelibsiz! ☎️\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 {progress_bar(1, CC_TOTAL)}\n"
        "📝 <b>1-qadam:</b> Ism va familiyangizni to'liq yozing\n\n"
        "📌 Lotin: <code>Xasanova Nilufar</code>\n"
        "📌 Kiril: <code>Хасанова Нилуфар</code>",
        parse_mode="HTML"
    )
    await update.effective_chat.send_message(
        "↩️ Istalgan vaqtda bekor qilish uchun pastdagi tugmadan foydalaning.",
        reply_markup=text_step_cancel_kb()
    )
    return CC_ISM

async def cc_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_ism(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return CC_ISM
    context.user_data["ism"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(2, CC_TOTAL)}\n"
        "📞 <b>2-qadam:</b> Telefon:\n📌 <code>+998901234567</code>",
        parse_mode="HTML"
    )
    return CC_TEL

async def cc_tel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_tel(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return CC_TEL
    context.user_data["tel"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(3, CC_TOTAL)}\n"
        "📲 <b>3-qadam:</b> Telegram username:\n"
        "📌 <code>@nilufar_cc</code> — yo'q bo'lsa <i>\"yo'q\"</i>",
        parse_mode="HTML"
    )
    return CC_USERNAME

async def cc_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(4, CC_TOTAL)}\n"
        "💼 <b>4-qadam:</b> Tajribangiz bormi?\nTanlang 👇",
        parse_mode="HTML", reply_markup=tajriba_kb()
    )
    return CC_TAJRIBA

async def cc_tajriba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    t = "✅ Ha, tajribam bor" if query.data == "tajriba_ha" else "🌱 Yo'q, yangi boshlovchi"
    context.user_data["tajriba"] = t
    await query.edit_message_text(
        f"Tanlandi: <b>{t}</b>\n\n📊 {progress_bar(5, CC_TOTAL)}\n"
        "💳 <b>5-qadam:</b> Karta raqam:\n📌 <code>8600 1234 5678 9012</code>",
        parse_mode="HTML"
    )
    return CC_KARTA

async def cc_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["karta"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(6, CC_TOTAL)}\n"
        "📍 <b>6-qadam (oxirgi!):</b> Yashash joyi lokatsiyasi\n⬇️",
        parse_mode="HTML", reply_markup=location_kb()
    )
    asyncio.create_task(send_loading_and_wait(update, context))
    return CC_LOKATSIYA

async def cc_lokatsiya_xato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    await update.message.reply_text(GEO_OFF_TEXT, parse_mode="HTML", reply_markup=location_kb())
    asyncio.create_task(send_loading_and_wait(update, context))
    return CC_LOKATSIYA

async def cc_lokatsiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    d = context.user_data
    now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")
    summary = (
        "🎉 <b>Ariza qabul qilindi!</b>\n\n"
        "╔══════════════════════════════╗\n║  📞  CALL CENTER XODIMI      ║\n╚══════════════════════════════╝\n\n"
        f"👤 <b>Ism:</b> {d.get('ism')}\n📞 <b>Tel:</b> {d.get('tel')}\n"
        f"📲 <b>Telegram:</b> {d.get('username')}\n💼 <b>Tajriba:</b> {d.get('tajriba')}\n"
        f"💳 <b>Karta:</b> {d.get('karta')}\n"
        f"📍 <b>Lokatsiya:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 HR menejerimiz tez orada bog'lanadi!\n🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "call_center", summary)
    last = get_last_contact(update.effective_user.id, "call_center", ariza_id)
    admin_text = f"📞 <b>YANGI CALL CENTER</b> #{ariza_id}\n🕐 {now_str}\n"
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    await send_to_admin(context, admin_text, ariza_id=ariza_id)
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  🚛 EVAKUATOR (6 bosqich)
# ══════════════════════════════════════════════════════════
EV_TOTAL = 6

async def start_evak_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["role"] = "evakuator"
    await send_sticker_safe(update, STICKER_EVAK)
    await query.edit_message_text(
        "🚛 <b>Siz EVAKUATOR HAYDOVCHISI sifatida davom etyapsiz!</b>\n\n"
        "Yo'lda qolgan odamlarga yordam beruvchi qahramonlar jamoasiga xush kelibsiz! 🦺\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n📊 {progress_bar(1, EV_TOTAL)}\n"
        "📝 <b>1-qadam:</b> Ism va familiyangizni to'liq yozing\n\n"
        "📌 Lotin: <code>Rahimov Bobur</code>\n"
        "📌 Kiril: <code>Раҳимов Бобур</code>",
        parse_mode="HTML"
    )
    await update.effective_chat.send_message(
        "↩️ Istalgan vaqtda bekor qilish uchun pastdagi tugmadan foydalaning.",
        reply_markup=text_step_cancel_kb()
    )
    return EV_ISM

async def ev_ism(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_ism(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return EV_ISM
    context.user_data["ism"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(2, EV_TOTAL)}\n"
        "📞 <b>2-qadam:</b> Telefon:\n📌 <code>+998901234567</code>",
        parse_mode="HTML"
    )
    return EV_TEL

async def ev_tel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_tel(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML"); return EV_TEL
    context.user_data["tel"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(3, EV_TOTAL)}\n"
        "📲 <b>3-qadam:</b> Telegram username:\n"
        "📌 <code>@bobur_evak</code> — yo'q bo'lsa <i>\"yo'q\"</i>",
        parse_mode="HTML"
    )
    return EV_USERNAME

async def ev_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["username"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(4, EV_TOTAL)}\n"
        "🚛 <b>4-qadam:</b> Haydovchilik tajribasi (yillarda):\n"
        "📌 <code>3 yil</code>, <code>7 yil</code>, <code>10 yildan ortiq</code>",
        parse_mode="HTML"
    )
    return EV_TAJRIBA

async def ev_tajriba(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["tajriba"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(5, EV_TOTAL)}\n"
        "💳 <b>5-qadam:</b> Karta raqam:\n📌 <code>8600 1234 5678 9012</code>",
        parse_mode="HTML"
    )
    return EV_KARTA

async def ev_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["karta"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Qabul!\n\n📊 {progress_bar(6, EV_TOTAL)}\n"
        "📍 <b>6-qadam (oxirgi!):</b> Turar joy lokatsiyasi\n⬇️",
        parse_mode="HTML", reply_markup=location_kb()
    )
    asyncio.create_task(send_loading_and_wait(update, context))
    return EV_LOKATSIYA

async def ev_lokatsiya_xato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    await update.message.reply_text(GEO_OFF_TEXT, parse_mode="HTML", reply_markup=location_kb())
    asyncio.create_task(send_loading_and_wait(update, context))
    return EV_LOKATSIYA

async def ev_lokatsiya(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    d = context.user_data
    now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")
    summary = (
        "🎉 <b>Ajoyib! Ariza qabul qilindi!</b>\n\n"
        "╔══════════════════════════════════╗\n║  🚛  EVAKUATOR HAYDOVCHISI       ║\n╚══════════════════════════════════╝\n\n"
        f"👤 <b>Ism:</b> {d.get('ism')}\n📞 <b>Tel:</b> {d.get('tel')}\n"
        f"📲 <b>Telegram:</b> {d.get('username')}\n🚗 <b>Tajriba:</b> {d.get('tajriba')}\n"
        f"💳 <b>Karta:</b> {d.get('karta')}\n"
        f"📍 <b>Lokatsiya:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📋 Dispatcher menejerimiz tez orada bog'lanadi!\n🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "evakuator", summary)
    # Evakuator haydovchisini ham `ustalar` jadvaliga yozamiz (soha=Evakuator) —
    # aks holda "mashina yo'lda qoldi" arizalari ularga avtomatik yetib bormaydi
    save_usta(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        "Evakuator xizmati", "🚛 Evakuator", d.get("tajriba"), d.get("karta"), lat, lon
    )
    last = get_last_contact(update.effective_user.id, "evakuator", ariza_id)
    admin_text = f"🚛 <b>YANGI EVAKUATOR</b> #{ariza_id}\n🕐 {now_str}\n"
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    await send_to_admin(context, admin_text, ariza_id=ariza_id)
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  STATUS TUGMALARI (faqat admin)
# ══════════════════════════════════════════════════════════
def usta_taklif_kb(ariza_id: int):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Men qabul qilaman", callback_data=f"acc_{ariza_id}")
    ]])

async def notify_ustalar(context: ContextTypes.DEFAULT_TYPE, ariza_id: int, muammo: str,
                          usta_turi_guess: str, lat: float, lon: float,
                          mijoz_ism: str, mijoz_tel: str):
    """Yangi arizani mos keladigan eng yaqin faol ustalarga yuboradi.
    Ustalardan birinchi bo'lib 'Qabul qilaman' bosgani arizani oladi."""
    nomzodlar = get_ustalar_for_ariza(usta_turi_guess, lat, lon)
    if not nomzodlar:
        await send_to_admin(
            context,
            f"⚠️ Ariza #{ariza_id} uchun tizimda mos faol usta topilmadi — "
            "iltimos, qo'lda biriktiring."
        )
        return
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
    sent = []
    for u in nomzodlar:
        masofa_matn = f"~{u['dist']:.1f} km" if u["dist"] < 900 else "masofa noma'lum"
        text = (
            "🆘 <b>YANGI BUYURTMA!</b>\n\n"
            f"🔍 <b>Muammo:</b> {muammo}\n"
            f"📍 <b>Masofa sizgacha:</b> {masofa_matn}\n"
            f"🗺 <a href=\"{maps_link}\">Xaritada ko'rish</a>\n\n"
            "Birinchi bo'lib qabul qilgan usta bu buyurtmani oladi 👇"
        )
        try:
            m = await context.bot.send_message(
                chat_id=u["tg_id"], text=text, parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=usta_taklif_kb(ariza_id)
            )
            sent.append((u["tg_id"], m.message_id))
        except Exception as e:
            logger.error(f"Ustaga yuborishda xato ({u['tg_id']}): {e}")
    PENDING_NOTIFICATIONS[ariza_id] = sent

async def usta_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usta 'Men qabul qilaman' tugmasini bosganda ishga tushadi — birinchi
    bosgan usta arizani oladi, mijozga esa 'usta yo'lga chiqdi' xabari boradi."""
    query = update.callback_query
    ariza_id = int(query.data.replace("acc_", ""))
    usta = update.effective_user

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ism, tel FROM ustalar WHERE tg_id=?", (usta.id,))
    row = c.fetchone()
    conn.close()
    usta_ism = row[0] if row else (usta.first_name or "Usta")
    usta_tel = row[1] if row else "—"

    muvaffaqiyatli = assign_ariza(ariza_id, usta.id, usta_ism, usta_tel)

    if not muvaffaqiyatli:
        await query.answer("😔 Kechirasiz, bu buyurtmani boshqa usta allaqachon qabul qilgan.", show_alert=True)
        try:
            await query.edit_message_text(
                (query.message.text or "") + "\n\n❌ <b>Band qilindi</b> (boshqa usta qabul qildi)",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    await query.answer("✅ Ariza sizga biriktirildi!")
    client_tg_id = get_ariza_client(ariza_id)
    maps_link = None
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT client_lat, client_lon FROM arizalar WHERE id=?", (ariza_id,))
    r = c.fetchone()
    conn.close()
    if r and r[0]:
        maps_link = f"https://maps.google.com/?q={r[0]},{r[1]}"

    # Ustaga — mijoz ma'lumotlari bilan tasdiqlash
    try:
        client_info = f"\n\n👤 Mijoz bilan bog'laning va yo'lga chiqing."
        if maps_link:
            client_info += f"\n🗺 <a href=\"{maps_link}\">Mijoz manzili</a>"
        await query.edit_message_text(
            (query.message.text or "") + f"\n\n✅ <b>Siz qabul qildingiz!</b>{client_info}",
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception:
        pass

    # Qolgan ustalarga — "band qilindi" deb xabar berish
    for other_tg_id, other_msg_id in PENDING_NOTIFICATIONS.get(ariza_id, []):
        if other_tg_id == usta.id:
            continue
        try:
            await context.bot.edit_message_text(
                chat_id=other_tg_id, message_id=other_msg_id,
                text="❌ <b>Band qilindi</b> — bu buyurtmani boshqa usta oldi. "
                     "Keyingi buyurtmalarda omad! 🍀",
                parse_mode="HTML"
            )
        except Exception:
            pass
    PENDING_NOTIFICATIONS.pop(ariza_id, None)

    # Admin ogohlantirish
    await send_to_admin(
        context,
        f"🧑‍🔧 <b>Ariza #{ariza_id}</b> ustaga biriktirildi:\n"
        f"👤 {usta_ism} | 📞 {usta_tel}"
    )

    # ══ MIJOZGA CREATIV, ISHONCH BERUVCHI XABAR ══
    if client_tg_id:
        try:
            await context.bot.send_message(
                chat_id=client_tg_id,
                text=(
                    "🚗💨 <b>Xushxabar!</b>\n\n"
                    f"<b>{usta_ism}</b> sizning arizangizni qabul qildi va "
                    "hoziroq yo'lga chiqdi!\n\n"
                    f"📞 <b>Bog'lanish uchun:</b> {usta_tel}\n"
                    "📍 Iltimos, joyingizda kuting — tez orada yetib boradi.\n\n"
                    "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Mijozga xabar yuborishda xato: {e}")

STATUS_LABELS = {
    "jarayonda": "🟡 Jarayonda",
    "bajarildi": "✅ Bajarildi",
    "bekor":     "❌ Bekor qilindi",
    "expert":    "🧑‍🔧 Expert usta yuborildi",
}

async def status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if ADMIN_CHAT_ID and query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Faqat admin!", show_alert=True)
        return
    await query.answer()
    parts = query.data.split("_", 2)
    ariza_id, new_status = int(parts[1]), parts[2]

    if new_status == "expert":
        await query.message.reply_text(
            f"🧑‍🔧 Ariza #{ariza_id} — expert usta yuborish belgilandi.\n"
            "Iltimos, mos ustani qo'lda toping va mijoz bilan bog'lang.",
            parse_mode="HTML"
        )
        return

    update_status(ariza_id, new_status)
    label = STATUS_LABELS.get(new_status, new_status)
    old_text = query.message.text or ""
    new_text = f"{old_text}\n\n📌 <b>Holat yangilandi:</b> {label}"
    try:
        await query.edit_message_text(
            new_text, parse_mode="HTML",
            reply_markup=status_kb(ariza_id),
            disable_web_page_preview=True
        )
    except Exception:
        await query.message.reply_text(f"📌 Ariza #{ariza_id}: {label}")

# ══════════════════════════════════════════════════════════
#  /broadcast — Barcha foydalanuvchilarga xabar yuborish
# ══════════════════════════════════════════════════════════
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faqat admin: /broadcast — barcha foydalanuvchilarga xabar yuboring"""
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    await update.message.reply_text(
        "📢 <b>Broadcast xabari</b>\n\n"
        "Quyida yuboriladigan xabarni yozing.\n"
        "Bekor qilish uchun /cancel",
        parse_mode="HTML"
    )
    return BROADCAST_TEXT

async def broadcast_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return ConversationHandler.END
    msg = update.message.text
    user_ids = get_all_user_ids()
    sent = 0
    failed = 0
    status_msg = await update.message.reply_text(
        f"⏳ Yuborilmoqda... 0/{len(user_ids)}", parse_mode="HTML"
    )
    for uid in user_ids:
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📢 <b>MOTUS xabari:</b>\n\n{msg}",
                parse_mode="HTML"
            )
            sent += 1
        except Exception:
            failed += 1
        if (sent + failed) % 20 == 0:
            try:
                await status_msg.edit_text(
                    f"⏳ Yuborilmoqda... {sent+failed}/{len(user_ids)}"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)  # Flood limitdan himoya
    await status_msg.edit_text(
        f"✅ <b>Broadcast tugadi!</b>\n\n"
        f"📨 Yuborildi: <b>{sent}</b>\n"
        f"❌ Yuborilmadi: <b>{failed}</b>\n"
        f"👥 Jami: <b>{len(user_ids)}</b>",
        parse_mode="HTML"
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  /stats
# ══════════════════════════════════════════════════════════
async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    total_users, total_arizalar, by_role, yangi, jarayonda, bajarildi, faol_7kun, bugun, faol_ustalar = get_stats()
    role_lines = "\n".join(f"  • {r}: <b>{c}</b>" for r, c in by_role.items()) or "  • Hozircha yo'q"
    await update.message.reply_text(
        "📊 <b>MOTUS Bot — Statistika</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Jami ro'yxatdan o'tganlar:</b> {total_users}\n"
        f"🟢 <b>Bugun faol:</b> {bugun}\n"
        f"📅 <b>Oxirgi 7 kunda faol:</b> {faol_7kun}\n"
        f"🔧 <b>Tizimda faol ustalar:</b> {faol_ustalar}\n\n"
        f"📝 <b>Jami arizalar:</b> {total_arizalar}\n"
        f"📋 <b>Rol bo'yicha:</b>\n{role_lines}\n\n"
        f"🆕 <b>Yangi (ko'rilmagan):</b> {yangi}\n"
        f"🟡 <b>Jarayonda:</b> {jarayonda}\n"
        f"✅ <b>Bajarilgan:</b> {bajarildi}",
        parse_mode="HTML"
    )

# ══════════════════════════════════════════════════════════
#  BEKOR QILISH
# ══════════════════════════════════════════════════════════
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await delete_loading(context, update.effective_chat.id)
    context.user_data.clear()
    await update.message.reply_text(
        "❌ <b>Bekor qilindi.</b>\n\n/start — qaytadan boshlash",
        parse_mode="HTML", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤔 /start — boshlash | /cancel — bekor qilish"
    )

# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
def main():
    init_db()
    app = (
        Application.builder().token(BOT_TOKEN)
        .read_timeout(30).write_timeout(30)
        .connect_timeout(30).pool_timeout(30)
        .build()
    )

    # Mijoz "profil menyusi"da ishlaydigan handlerlar — bularni asosiy
    # suhbatda ham, quyidagi mustaqil ariza_direct_conv'da ham ishlatamiz,
    # shunda mijoz conversation_timeout tugagandan keyin ham "🆘 Muammo bor"
    # tugmasidan foydalana oladi va ro'yxatdan qayta o'tishi shart emas.
    mij_profil_menu_handlers = [
        MessageHandler(
            filters.TEXT & filters.Regex("^🆘 Muammo bor") & ~filters.COMMAND,
            muammo_handler
        ),
        MessageHandler(filters.LOCATION, mijoz_lokatsiya_yangilash),
        CallbackQueryHandler(home, pattern="^home$"),
    ]

    # Broadcast alohida ConversationHandler (faqat admin uchun)
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_cmd)],
        states={
            BROADCAST_TEXT: [MessageHandler(NORMAL_TEXT, broadcast_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(CANCEL_FILTER, cancel)],
    )

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ROLE_SELECT: [CallbackQueryHandler(role_chosen, pattern="^role_")],
            ROLE_MENU: [
                CallbackQueryHandler(show_info,     pattern="^info_"),
                CallbackQueryHandler(show_register, pattern="^register_"),
                CallbackQueryHandler(home,          pattern="^home$"),
            ],
            # Mijoz ro'yxat
            MIJ_ISM:      [MessageHandler(NORMAL_TEXT, mij_ism)],
            MIJ_TEL:      [MessageHandler(NORMAL_TEXT, mij_tel)],
            MIJ_USERNAME: [MessageHandler(NORMAL_TEXT, mij_username)],
            MIJ_MASHINA:  [MessageHandler(NORMAL_TEXT, mij_mashina)],
            MIJ_KARTA:    [MessageHandler(NORMAL_TEXT, mij_karta)],
            MIJ_LOKATSIYA:[
                MessageHandler(filters.LOCATION, mij_lokatsiya),
                MessageHandler(NORMAL_TEXT, mij_lokatsiya_xato),
            ],
            # Mijoz profil menyusi — DOIMIY tugmalar
            MIJ_PROFIL_MENU: mij_profil_menu_handlers,
            # Ariza oqimi
            ARIZA_MUAMMO:     [MessageHandler(NORMAL_TEXT, ariza_muammo)],
            ARIZA_LOKATSIYA:  [
                MessageHandler(filters.LOCATION, ariza_lokatsiya),
                MessageHandler(NORMAL_TEXT, ariza_lokatsiya_xato),
            ],
            # Usta
            USTA_ISM:         [MessageHandler(NORMAL_TEXT, usta_ism)],
            USTA_TEL:         [MessageHandler(NORMAL_TEXT, usta_tel)],
            USTA_USERNAME:    [MessageHandler(NORMAL_TEXT, usta_username)],
            USTA_MASHINA_TURI:[MessageHandler(NORMAL_TEXT, usta_mashina_turi)],
            USTA_SOHA:        [CallbackQueryHandler(usta_soha, pattern="^usta_")],
            USTA_SOHA_BOSHQA: [MessageHandler(NORMAL_TEXT, usta_soha_boshqa)],
            USTA_TAJRIBA:     [MessageHandler(NORMAL_TEXT, usta_tajriba)],
            USTA_KARTA:       [MessageHandler(NORMAL_TEXT, usta_karta)],
            USTA_LOKATSIYA:   [
                MessageHandler(filters.LOCATION, usta_lokatsiya),
                MessageHandler(NORMAL_TEXT, usta_lokatsiya_xato),
            ],
            # Call Center
            CC_ISM:      [MessageHandler(NORMAL_TEXT, cc_ism)],
            CC_TEL:      [MessageHandler(NORMAL_TEXT, cc_tel)],
            CC_USERNAME: [MessageHandler(NORMAL_TEXT, cc_username)],
            CC_TAJRIBA:  [CallbackQueryHandler(cc_tajriba, pattern="^tajriba_")],
            CC_KARTA:    [MessageHandler(NORMAL_TEXT, cc_karta)],
            CC_LOKATSIYA:[
                MessageHandler(filters.LOCATION, cc_lokatsiya),
                MessageHandler(NORMAL_TEXT, cc_lokatsiya_xato),
            ],
            # Evakuator
            EV_ISM:      [MessageHandler(NORMAL_TEXT, ev_ism)],
            EV_TEL:      [MessageHandler(NORMAL_TEXT, ev_tel)],
            EV_USERNAME: [MessageHandler(NORMAL_TEXT, ev_username)],
            EV_TAJRIBA:  [MessageHandler(NORMAL_TEXT, ev_tajriba)],
            EV_KARTA:    [MessageHandler(NORMAL_TEXT, ev_karta)],
            EV_LOKATSIYA:[
                MessageHandler(filters.LOCATION, ev_lokatsiya),
                MessageHandler(NORMAL_TEXT, ev_lokatsiya_xato),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(CANCEL_FILTER, cancel)],
        allow_reentry=True,
        conversation_timeout=600,
    )

    # ══ MUSTAQIL ARIZA OQIMI ══
    # Agar mijoz avval to'liq ro'yxatdan o'tgan bo'lsa-yu, asosiy suhbat
    # (10 daqiqalik conversation_timeout tufayli) tugab qolgan bo'lsa ham,
    # "🆘 Muammo bor" tugmasi shu handler orqali ishlab, profilni bazadan
    # o'zi tiklaydi — mijoz /start bilan qayta ro'yxatdan o'tishi shart emas.
    ariza_direct_conv = ConversationHandler(
        entry_points=[MessageHandler(
            filters.TEXT & filters.Regex("^🆘 Muammo bor") & ~filters.COMMAND,
            muammo_handler
        )],
        states={
            ARIZA_MUAMMO:    [MessageHandler(NORMAL_TEXT, ariza_muammo)],
            ARIZA_LOKATSIYA: [
                MessageHandler(filters.LOCATION, ariza_lokatsiya),
                MessageHandler(NORMAL_TEXT, ariza_lokatsiya_xato),
            ],
            MIJ_PROFIL_MENU: mij_profil_menu_handlers,
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(CANCEL_FILTER, cancel)],
        allow_reentry=True,
        conversation_timeout=600,
    )

    app.add_handler(broadcast_conv)
    app.add_handler(conv)
    app.add_handler(ariza_direct_conv)
    app.add_handler(CallbackQueryHandler(status_callback, pattern="^st_"))
    app.add_handler(CallbackQueryHandler(usta_accept_callback, pattern="^acc_"))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))

    logger.info("🚗 MOTUS Bot v5 ishga tushdi!")
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=10)

if __name__ == "__main__":
    main()