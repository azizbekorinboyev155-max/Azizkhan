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

BOT_TOKEN = os.getenv("MOTUS_BOT_TOKEN")
_ADMIN_ID_RAW = os.getenv("MOTUS_ADMIN_CHAT_ID")
DB_PATH = os.getenv("MOTUS_DB_PATH", "motus.db")

if not BOT_TOKEN:
    raise RuntimeError(
        "❌ MOTUS_BOT_TOKEN muhit o'zgaruvchisi topilmadi! "
        "Railway'da Variables bo'limida MOTUS_BOT_TOKEN nomi to'g'ri "
        "yozilganini tekshiring."
    )
if not _ADMIN_ID_RAW:
    raise RuntimeError(
        "❌ MOTUS_ADMIN_CHAT_ID muhit o'zgaruvchisi topilmadi! "
        "Railway'da Variables bo'limida MOTUS_ADMIN_CHAT_ID nomi to'g'ri "
        "yozilganini tekshiring."
    )
try:
    ADMIN_CHAT_ID = int(_ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError(
        f"❌ MOTUS_ADMIN_CHAT_ID qiymati raqam emas: '{_ADMIN_ID_RAW}'. "
        "Faqat Telegram ID raqamini kiriting (masalan: 8304618603)."
    )

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
            tasdiqlangan INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    # ── YANGI: call_center va evakuator xodimlari jadvali ──────────────────
    c.execute("""
        CREATE TABLE IF NOT EXISTS cc_xodimlar (
            tg_id INTEGER PRIMARY KEY,
            ism TEXT,
            tel TEXT,
            username TEXT,
            tajriba TEXT,
            karta TEXT,
            lat REAL,
            lon REAL,
            faol INTEGER DEFAULT 1,
            tasdiqlangan INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS evakuatorlar (
            tg_id INTEGER PRIMARY KEY,
            ism TEXT,
            tel TEXT,
            username TEXT,
            tajriba TEXT,
            karta TEXT,
            lat REAL,
            lon REAL,
            faol INTEGER DEFAULT 1,
            tasdiqlangan INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)
    try:
        c.execute("ALTER TABLE users ADD COLUMN last_active TEXT")
    except Exception:
        pass
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
    for col, col_type in [
        ("assigned_usta_id", "INTEGER"), ("assigned_usta_ism", "TEXT"),
        ("assigned_usta_tel", "TEXT"), ("client_lat", "REAL"), ("client_lon", "REAL"),
        # ── YANGI: qaysi xizmatga yuborilganligi ──
        ("ariza_turi", "TEXT"),
    ]:
        try:
            c.execute(f"ALTER TABLE arizalar ADD COLUMN {col} {col_type}")
        except Exception:
            pass
    # ustalar jadvaliga tasdiqlangan ustuni qo'shamiz (eski bazalar uchun)
    try:
        c.execute("ALTER TABLE ustalar ADD COLUMN tasdiqlangan INTEGER DEFAULT 0")
    except Exception:
        pass
    # ── YANGI: reyting (baholash) tizimi uchun ustunlar ──
    try:
        c.execute("ALTER TABLE arizalar ADD COLUMN reyting INTEGER")
    except Exception:
        pass
    for _table in ("ustalar", "cc_xodimlar", "evakuatorlar"):
        for _col, _col_type in [("reyting_soni", "INTEGER DEFAULT 0"),
                                 ("reyting_yigindi", "INTEGER DEFAULT 0")]:
            try:
                c.execute(f"ALTER TABLE {_table} ADD COLUMN {_col} {_col_type}")
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        UPDATE users SET mijoz_ism=?, mijoz_tel=?, mijoz_username=?, mijoz_mashina=?,
        mijoz_karta=?, mijoz_lat=?, mijoz_lon=?, is_mijoz=1 WHERE tg_id=?
    """, (ism, tel, username, mashina, karta, lat, lon, tg_id))
    conn.commit()
    conn.close()

def get_mijoz_profile(tg_id: int):
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM arizalar WHERE tg_id=? AND role='mijoz_ariza'", (tg_id,))
    n = c.fetchone()[0]
    conn.close()
    return n

def save_usta(tg_id: int, ism: str, tel: str, username: str, mashina_turi: str,
              soha: str, tajriba: str, karta: str, lat: float, lon: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # tasdiqlangan=0 — admin tasdiqlamaguncha arizalar yuborilmaydi
    c.execute("""
        INSERT INTO ustalar (tg_id, ism, tel, username, mashina_turi, soha, tajriba,
        karta, lat, lon, faol, tasdiqlangan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        ON CONFLICT(tg_id) DO UPDATE SET ism=excluded.ism, tel=excluded.tel,
        username=excluded.username, mashina_turi=excluded.mashina_turi,
        soha=excluded.soha, tajriba=excluded.tajriba, karta=excluded.karta,
        lat=excluded.lat, lon=excluded.lon, faol=1
    """, (tg_id, ism, tel, username, mashina_turi, soha, tajriba, karta, lat, lon,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ── YANGI: CC xodimini saqlash ─────────────────────────────────────────────
def save_cc(tg_id: int, ism: str, tel: str, username: str,
            tajriba: str, karta: str, lat: float, lon: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO cc_xodimlar (tg_id, ism, tel, username, tajriba, karta,
        lat, lon, faol, tasdiqlangan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        ON CONFLICT(tg_id) DO UPDATE SET ism=excluded.ism, tel=excluded.tel,
        username=excluded.username, tajriba=excluded.tajriba, karta=excluded.karta,
        lat=excluded.lat, lon=excluded.lon, faol=1
    """, (tg_id, ism, tel, username, tajriba, karta, lat, lon,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()

# ── YANGI: Evakuator haydovchisini saqlash ────────────────────────────────
def save_evakuator(tg_id: int, ism: str, tel: str, username: str,
                   tajriba: str, karta: str, lat: float, lon: float):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT INTO evakuatorlar (tg_id, ism, tel, username, tajriba, karta,
        lat, lon, faol, tasdiqlangan, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
        ON CONFLICT(tg_id) DO UPDATE SET ism=excluded.ism, tel=excluded.tel,
        username=excluded.username, tajriba=excluded.tajriba, karta=excluded.karta,
        lat=excluded.lat, lon=excluded.lon, faol=1
    """, (tg_id, ism, tel, username, tajriba, karta, lat, lon,
          datetime.now().isoformat()))
    conn.commit()
    conn.close()

def _distance_km(lat1, lon1, lat2, lon2):
    R = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(min(1, math.sqrt(a)))

def get_ustalar_for_ariza(soha_guess: str, client_lat: float, client_lon: float, limit: int = 6):
    """Faqat admin tomonidan TASDIQLANGAN faol ustalarni qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # ── O'ZGARISH: tasdiqlangan=1 sharti qo'shildi ──
    if soha_guess:
        c.execute(
            "SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1 AND tasdiqlangan=1 AND soha LIKE ?",
            (f"%{soha_guess}%",)
        )
        rows = c.fetchall()
        if not rows:
            c.execute(
                "SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1 AND tasdiqlangan=1"
            )
            rows = c.fetchall()
    else:
        c.execute(
            "SELECT tg_id, ism, tel, soha, lat, lon FROM ustalar WHERE faol=1 AND tasdiqlangan=1"
        )
        rows = c.fetchall()
    conn.close()
    result = []
    for tg_id, ism, tel, soha, lat, lon in rows:
        dist = _distance_km(client_lat, client_lon, lat, lon) if lat and lon else 999
        result.append({"tg_id": tg_id, "ism": ism, "tel": tel, "soha": soha, "dist": dist})
    result.sort(key=lambda x: x["dist"])
    return result[:limit]

# ── YANGI: CC xodimlarini olish (tasdiqlangan) ───────────────────────────
def get_cc_for_ariza(limit: int = 3):
    """Admin tomonidan tasdiqlangan faol CC xodimlarini qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT tg_id, ism, tel FROM cc_xodimlar WHERE faol=1 AND tasdiqlangan=1 LIMIT ?",
        (limit,)
    )
    rows = c.fetchall()
    conn.close()
    return [{"tg_id": r[0], "ism": r[1], "tel": r[2]} for r in rows]

# ── YANGI: Evakuatorlarni olish (tasdiqlangan, eng yaqin) ────────────────
def get_evakuatorlar_for_ariza(client_lat: float, client_lon: float, limit: int = 4):
    """Admin tomonidan tasdiqlangan faol evakuatorlarni masofa bo'yicha qaytaradi."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT tg_id, ism, tel, lat, lon FROM evakuatorlar WHERE faol=1 AND tasdiqlangan=1"
    )
    rows = c.fetchall()
    conn.close()
    result = []
    for tg_id, ism, tel, lat, lon in rows:
        dist = _distance_km(client_lat, client_lon, lat, lon) if lat and lon else 999
        result.append({"tg_id": tg_id, "ism": ism, "tel": tel, "dist": dist})
    result.sort(key=lambda x: x["dist"])
    return result[:limit]

def assign_ariza(ariza_id: int, usta_tg_id: int, usta_ism: str, usta_tel: str) -> bool:
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

# ── YANGI: ariza_turi ni saqlash ─────────────────────────────────────────
def set_ariza_turi(ariza_id: int, ariza_turi: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE arizalar SET ariza_turi=? WHERE id=?", (ariza_turi, ariza_id))
    conn.commit()
    conn.close()

def get_ariza_client(ariza_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tg_id FROM arizalar WHERE id=?", (ariza_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

# ── YANGI: ariza ma'lumotlarini to'liq olish ─────────────────────────────
def get_ariza_full(ariza_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT tg_id, role, data, status, client_lat, client_lon, "
        "ariza_turi, assigned_usta_id FROM arizalar WHERE id=?",
        (ariza_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "tg_id":           row[0],
        "role":            row[1],
        "data":            row[2],
        "status":          row[3],
        "client_lat":      row[4],
        "client_lon":      row[5],
        "ariza_turi":      row[6],
        "assigned_usta_id": row[7],
    }
def get_ariza_status(ariza_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT status FROM arizalar WHERE id=?", (ariza_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def set_ariza_reyting(ariza_id: int, reyting: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE arizalar SET reyting=? WHERE id=?", (reyting, ariza_id))
    conn.commit()
    conn.close()

def get_ariza_for_rating(ariza_id: int):
    """Baholash uchun kerakli ma'lumotlarni qaytaradi: mijoz va biriktirilgan xodim."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT tg_id, assigned_usta_id, assigned_usta_ism, reyting FROM arizalar WHERE id=?",
        (ariza_id,)
    )
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {
        "client_tg_id": row[0], "usta_tg_id": row[1],
        "usta_ism": row[2], "reyting": row[3]
    }

def add_worker_rating(tg_id: int, yulduz: int):
    """Ustaning/xodimning reytingini yangilaydi — qaysi jadvalda ekanini
    o'zi topib, yig'indi va sonni oshiradi (o'rtacha bahoni hisoblash uchun)."""
    for table in ("ustalar", "cc_xodimlar", "evakuatorlar"):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"SELECT tg_id FROM {table} WHERE tg_id=?", (tg_id,))
        found = c.fetchone()
        if found:
            c.execute(
                f"UPDATE {table} SET reyting_soni = reyting_soni + 1, "
                f"reyting_yigindi = reyting_yigindi + ? WHERE tg_id=?",
                (yulduz, tg_id)
            )
            conn.commit()
            conn.close()
            return
        conn.close()

def get_worker_rating(tg_id: int):
    """Ustaning/xodimning o'rtacha bahosini qaytaradi: (o'rtacha, baholar_soni)."""
    for table in ("ustalar", "cc_xodimlar", "evakuatorlar"):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(f"SELECT reyting_soni, reyting_yigindi FROM {table} WHERE tg_id=?", (tg_id,))
        row = c.fetchone()
        conn.close()
        if row:
            soni, yigindi = row
            if soni:
                return round(yigindi / soni, 1), soni
            return None, 0
    return None, 0

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
    c.execute(
        "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', '-7 days')"
    )
    faol_7kun = c.fetchone()[0]
    c.execute(
        "SELECT COUNT(*) FROM users WHERE last_active >= datetime('now', 'start of day')"
    )
    bugun = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM ustalar WHERE faol=1 AND tasdiqlangan=1")
    faol_ustalar = c.fetchone()[0]
    # ── YANGI: tasdiqlangan CC va evakuator soni ──
    c.execute("SELECT COUNT(*) FROM cc_xodimlar WHERE faol=1 AND tasdiqlangan=1")
    faol_cc = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM evakuatorlar WHERE faol=1 AND tasdiqlangan=1")
    faol_evak = c.fetchone()[0]
    # Tasdiqlanmagan yangi arizalar (admin ko'rib chiqishi kerak)
    c.execute("SELECT COUNT(*) FROM ustalar WHERE tasdiqlangan=0")
    tasdiqlanmagan_usta = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM cc_xodimlar WHERE tasdiqlangan=0")
    tasdiqlanmagan_cc = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM evakuatorlar WHERE tasdiqlangan=0")
    tasdiqlanmagan_evak = c.fetchone()[0]
    conn.close()
    return (total_users, total_arizalar, by_role, yangi, jarayonda, bajarildi,
            faol_7kun, bugun, faol_ustalar, faol_cc, faol_evak,
            tasdiqlanmagan_usta, tasdiqlanmagan_cc, tasdiqlanmagan_evak)

# ── YANGI: usta/cc/evak ma'lumotlarini tg_id orqali olish ────────────────
def get_usta_info(tg_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ism, tel, username, soha, tajriba, mashina_turi FROM ustalar WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"ism": row[0], "tel": row[1], "username": row[2],
            "soha": row[3], "tajriba": row[4], "mashina_turi": row[5]}

def get_cc_info(tg_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ism, tel, username, tajriba FROM cc_xodimlar WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"ism": row[0], "tel": row[1], "username": row[2], "tajriba": row[3]}

def get_evak_info(tg_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT ism, tel, username, tajriba FROM evakuatorlar WHERE tg_id=?", (tg_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    return {"ism": row[0], "tel": row[1], "username": row[2], "tajriba": row[3]}

# ── YANGI: admin tasdiqlanmagan ro'yxat ──────────────────────────────────
def get_pending_approvals():
    """Hali tasdiqlanmagan usta/cc/evakuatorlar ro'yxati."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT tg_id, ism, tel, soha, created_at FROM ustalar WHERE tasdiqlangan=0 ORDER BY created_at DESC")
    ustalar = c.fetchall()
    c.execute("SELECT tg_id, ism, tel, created_at FROM cc_xodimlar WHERE tasdiqlangan=0 ORDER BY created_at DESC")
    cc = c.fetchall()
    c.execute("SELECT tg_id, ism, tel, created_at FROM evakuatorlar WHERE tasdiqlangan=0 ORDER BY created_at DESC")
    evak = c.fetchall()
    conn.close()
    return ustalar, cc, evak

def approve_worker(tg_id: int, table: str) -> bool:
    """Usta/CC/Evakuatorni tasdiqlash."""
    valid_tables = {"ustalar", "cc_xodimlar", "evakuatorlar"}
    if table not in valid_tables:
        return False
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"UPDATE {table} SET tasdiqlangan=1 WHERE tg_id=?", (tg_id,))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def reject_worker(tg_id: int, table: str) -> bool:
    """Usta/CC/Evakuatorni rad etish (o'chirish)."""
    if table == "users":
        # Mijoz alohida jadvalda emas — hisobini o'chirmaymiz, shunchaki True qaytaramiz
        return True
    valid_tables = {"ustalar", "cc_xodimlar", "evakuatorlar"}
    if table not in valid_tables:
        return False
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(f"DELETE FROM {table} WHERE tg_id=?", (tg_id,))
    changed = c.rowcount > 0
    conn.commit()
    conn.close()
    return changed

# ══════════════════════════════════════════════════════════
#  STATES
# ══════════════════════════════════════════════════════════
(
    ROLE_SELECT, ROLE_MENU,
    MIJ_ISM, MIJ_TEL, MIJ_USERNAME, MIJ_MASHINA,
    MIJ_KARTA, MIJ_LOKATSIYA, MIJ_PROFIL_MENU,
    ARIZA_MUAMMO, ARIZA_XIZMAT_TANLASH, ARIZA_SOHA_TANLASH, ARIZA_LOKATSIYA,
    USTA_ISM, USTA_TEL, USTA_USERNAME, USTA_MASHINA_TURI,
    USTA_SOHA, USTA_SOHA_BOSHQA, USTA_TAJRIBA,
    USTA_KARTA, USTA_LOKATSIYA,
    CC_ISM, CC_TEL, CC_USERNAME, CC_TAJRIBA,
    CC_KARTA, CC_LOKATSIYA,
    EV_ISM, EV_TEL, EV_USERNAME, EV_TAJRIBA,
    EV_KARTA, EV_LOKATSIYA,
   BROADCAST_TEXT,
    ADMIN_REJECT_REASON,
    USTA_MUAMMO_IZOH,
    USTA_MASTER_IZOH,
) = range(38)
PENDING_USTA_IZOH: dict[int, dict] = {}

# {ariza_id: [(xodim_tg_id, message_id), ...]}
PENDING_NOTIFICATIONS: dict[int, list[tuple[int, int]]] = {}
# {tg_id: {"partner": partner_tg_id, "role": "usta"/"Mijoz"/...}}
RELAY_CONNECTIONS: dict[int, dict] = {}

# Admin rad etish jarayoni: {admin_tg_id: {"table": ..., "tg_id": ..., "msg_id": ...}}
PENDING_REJECT: dict[int, dict] = {}

# ── YANGI: har bir arizaning admin paneldagi xabar ID'si (sinxronlash uchun) ──
ADMIN_ARIZA_MSG: dict[int, int] = {}

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

def validate_karta(text: str) -> tuple[bool, str]:
    cleaned = re.sub(r"[\s\-]", "", text.strip())
    if cleaned.lower() in ("yoq", "yo'q", "yo`q", "нет", "-"):
        return True, ""
    if not re.match(r"^\d{16}$", cleaned):
        return False, (
            "⚠️ <b>Karta raqami noto'g'ri!</b>\n\n"
            "✅ 16 ta raqamdan iborat bo'lishi kerak: <code>8600123456789012</code>\n"
            "Yoki hozircha o'tkazib yuborish uchun <i>«yo'q»</i> deb yozing.\n\nQaytadan kiriting:"
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
NORMAL_TEXT = filters.TEXT & ~filters.COMMAND & ~CANCEL_FILTER

def text_step_cancel_kb():
    return ReplyKeyboardMarkup(
        [[KeyboardButton(CANCEL_BTN_TEXT)]],
        resize_keyboard=True, one_time_keyboard=False
    )

def mijoz_main_kb():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🆘 Muammo bor (Ariza qoldirish)")],
            [KeyboardButton("📍 Lokatsiyamni yuborish", request_location=True)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )

def status_kb(ariza_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟡 Jarayonda",   callback_data=f"st_{ariza_id}_jarayonda"),
            InlineKeyboardButton("✅ Bajarildi",   callback_data=f"st_{ariza_id}_bajarildi"),
        ],
        [InlineKeyboardButton("❌ Bekor qilindi", callback_data=f"st_{ariza_id}_bekor")],
        [InlineKeyboardButton("🧑‍🔧 Expert usta yuborish", callback_data=f"st_{ariza_id}_expert")],
    ])

# ── YANGI: Xodim (usta/cc/evak) qabul tugmasi ────────────────────────────
def xodim_qabul_kb(ariza_id: int):
    """Barcha xizmat ko'rsatuvchilar (usta, CC, evakuator) uchun bir xil tugma."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Men qabul qilaman", callback_data=f"acc_{ariza_id}")
    ]])

# ── YANGI: Admin tasdiqlash tugmalari ────────────────────────────────────
def admin_approve_kb(tg_id: int, table: str):
    """Admin yangi xodim arizasini ko'rganda ko'rinadigan tasdiqlash tugmalari."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"appr_{table}_{tg_id}"),
            InlineKeyboardButton("❌ Rad etish",  callback_data=f"rejt_{table}_{tg_id}"),
        ]
    ])
def reyting_kb(ariza_id: int):
    """Mijoz xizmatni 1-5 yulduz bilan baholashi uchun tugmalar."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⭐", callback_data=f"rate_{ariza_id}_1"),
            InlineKeyboardButton("⭐⭐", callback_data=f"rate_{ariza_id}_2"),
            InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_{ariza_id}_3"),
        ],
        [
            InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_{ariza_id}_4"),
            InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_{ariza_id}_5"),
        ],
    ])
def relay_end_kb():
    
    """Muloqotni tugatish tugmasi."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔚 Muloqotni yakunlash", callback_data="relay_end")
    ]])

def reg_approve_kb(tg_id: int, table: str):
    """Ro'yxatdan o'tish uchun — faqat Qabul / Bekor."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Qabul qilindi",  callback_data=f"appr_{table}_{tg_id}"),
            InlineKeyboardButton("❌ Bekor qilindi", callback_data=f"rejt_{table}_{tg_id}"),
        ]
    ])

def usta_ariza_kb(ariza_id: int):
    """Usta ariza qabul qilgandan keyin ko'radigan holat tugmalari."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🟡 Jarayonda",         callback_data=f"ust_{ariza_id}_jarayonda"),
            InlineKeyboardButton("✅ Bajarildi",         callback_data=f"ust_{ariza_id}_bajarildi"),
        ],
        [
            InlineKeyboardButton("❌ Muammo yuz berdi",  callback_data=f"ust_{ariza_id}_muammo"),
            InlineKeyboardButton("🧑‍🔧 Master usta kerak", callback_data=f"ust_{ariza_id}_master"),
        ],
    ])

def master_tasdiq_kb(ariza_id: int, usta_tg_id: int):
    """Admin master usta yuborishni tasdiqlash tugmasi."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "🧑‍🔧 Master usta yuborishni tasdiqlash",
            callback_data=f"mconf_{ariza_id}_{usta_tg_id}"
        )
    ]])

class _RelayActiveFilter(filters.MessageFilter):
    def filter(self, message):
        return message.from_user and message.from_user.id in RELAY_CONNECTIONS

relay_active_filter = _RelayActiveFilter()

class _UstaIzohActiveFilter(filters.MessageFilter):
    def filter(self, message):
        return message.from_user and message.from_user.id in PENDING_USTA_IZOH

usta_izoh_active_filter = _UstaIzohActiveFilter()

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
        lm = await context.bot.send_message(chat_id=chat_id, text=frames[0])
        context.user_data["loading_msg_id"] = lm.message_id
        for frame in frames[1:]:
            await asyncio.sleep(1.2)
            try:
                await context.bot.edit_message_text(
                    chat_id=chat_id, message_id=lm.message_id,
                    text=frame
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

async def send_to_admin(context, text: str, ariza_id: int = None,
                        reply_markup=None):
    if not ADMIN_CHAT_ID:
        return None
    try:
        kb = reply_markup if reply_markup else (status_kb(ariza_id) if ariza_id else None)
        msg = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID, text=text,
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=kb
        )
        return msg
    except Exception as e:
        logger.error(f"Admin xabar xatosi: {e}")
        return None

async def sync_admin_ariza(context: ContextTypes.DEFAULT_TYPE, ariza_id: int, status_label: str):
    """Usta/CC/Evakuator tugma bosganda — admin paneldagi ORIGINAL ariza xabarini yangilaydi."""
    msg_id = ADMIN_ARIZA_MSG.get(ariza_id)
    if not msg_id or not ADMIN_CHAT_ID:
        return
    try:
        msg = await context.bot.forward_message(
            chat_id=ADMIN_CHAT_ID, from_chat_id=ADMIN_CHAT_ID, message_id=msg_id
        )
        await context.bot.delete_message(chat_id=ADMIN_CHAT_ID, message_id=msg.message_id)
    except Exception:
        pass
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=ADMIN_CHAT_ID, message_id=msg_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📌 Holat: {status_label}", callback_data="noop")],
                [InlineKeyboardButton("🧑‍🔧 Expert usta yuborish", callback_data=f"st_{ariza_id}_expert")],
            ])
        )
    except Exception as e:
        logger.error(f"Admin panel sync xato ({ariza_id}): {e}")

async def sync_admin_ariza(context: ContextTypes.DEFAULT_TYPE, ariza_id: int, status_label: str):
    """Usta/CC/Evakuator tugma bosganda — admin paneldagi ORIGINAL ariza xabari tugmasini yangilaydi."""
    msg_id = ADMIN_ARIZA_MSG.get(ariza_id)
    if not msg_id or not ADMIN_CHAT_ID:
        return
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=ADMIN_CHAT_ID, message_id=msg_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📌 Holat: {status_label}", callback_data="noop")],
                [InlineKeyboardButton("🧑‍🔧 Expert usta yuborish", callback_data=f"st_{ariza_id}_expert")],
            ])
        )
    except Exception as e:
        logger.error(f"Admin panel sync xato ({ariza_id}): {e}")
        pass
    try:
        await context.bot.edit_message_reply_markup(
            chat_id=ADMIN_CHAT_ID, message_id=msg_id,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"📌 Holat: {status_label}", callback_data="noop")],
                [InlineKeyboardButton("🧑‍🔧 Expert usta yuborish", callback_data=f"st_{ariza_id}_expert")],
            ])
        )
    except Exception as e:
        logger.error(f"Admin panel sync xato ({ariza_id}): {e}")

def progress_bar(current: int, total: int) -> str:
    return f"{'🟩'*current}{'⬜'*(total-current)} {current}/{total}"

# ── YANGI: muammo turini aniqlash (kengaytirilgan) ───────────────────────
def guess_ariza_turi(matn: str) -> str:
    """Mijoz yozgan matndan qaysi xizmat kerakligini taxmin qiladi.
    Qaytariladigan qiymatlar: 'evakuator', 'usta', 'cc', 'taklif', '' (aniqlanmadi)."""
    m = matn.lower()
    # Evakuator belgilari — eng aniq belgilar birinchi
    if any(k in m for k in [
        "evakuat", "yura olmayapti", "yurmayapti", "harakatlanmayapti",
        "qolib ket", "qoldi", "yo'lda to'xtab", "tortib ket", "haydab ket",
        "olib ket", "sudrab ket"
    ]):
        return "evakuator"
    # Usta turlari
    if any(k in m for k in [
        "motor", "dvigatel", "porshen", "gaz", "benzin yeyapti", "moy",
        "svet", "elektr", "akkumulyator", "indikator", "chiroq", "fara",
        "podveska", "xodovoy", "amortizator", "rulda", "tebranayapti",
        "shina", "g'ildirak", "gildirak", "balansirov", "shinomontaj",
        "tormoz", "tutmayapti", "isib ket", "qizib ket", "o'chib qol",
        "start olmayapti", "yoqilmayapti", "kuzov", "bo'yoq", "siqib"
    ]):
        return "usta"
    # Call center / umumiy savol-muammo
    if any(k in m for k in [
        "savol", "so'rov", "narx", "qancha", "necha", "xizmat", "ma'lumot",
        "yordam", "qo'llab", "operator", "bog'lan", "murojaat", "shikoyat"
    ]):
        return "cc"
    # Kompaniyaga taklif / fikr
    if any(k in m for k in [
        "taklif", "fikr", "mulohaza", "yaxshilash", "qo'shish", "o'zgartirish",
        "maqtov", "rahmat", "sifat", "reyting", "baho"
    ]):
        return "taklif"
    return ""

def guess_usta_soha(matn: str) -> str:
    """Usta arizasida qaysi soha kerakligini aniqlaydi."""
    m = matn.lower()
    if any(k in m for k in ["motor", "dvigatel", "porshen", "moy", "isib"]):
        return "Motorist"
    if any(k in m for k in ["svet", "elektr", "akkumulyator", "chiroq", "fara", "indikator"]):
        return "Elektrik"
    if any(k in m for k in ["shina", "gildirak", "balansirov", "shinomontaj"]):
        return "Balansirovka"
    if any(k in m for k in ["podveska", "xodovoy", "amortizator", "rulda", "tebran"]):
        return "Xodovoy"
    return ""

async def check_username(bot, username: str) -> tuple[bool, str]:
    """@username formatini tekshiradi (Telegram API orqali mavjudligini
    tekshirib bo'lmaydi, chunki foydalanuvchi botga yozmagan bo'lsa
    get_chat har doim xato qaytaradi — shuning uchun faqat format tekshiriladi).
    Returns: (found: bool, clean_username: str)"""
    raw = username.strip()
    if raw.lower() in ("yo'q", "yoq", "нет", "-", "none", "no", ""):
        return True, raw
    clean = raw.lstrip("@")
    if not re.match(r"^[A-Za-z0-9_]{5,32}$", clean):
        return False, f"@{clean}"
    return True, f"@{clean}"

# ══════════════════════════════════════════════════════════
#  HAQORAT FILTRI
# ══════════════════════════════════════════════════════════
HAQORAT_SOZLAR = [
    # O'zbek
    "ahmoq", "tentak", "eshak", "hayvon", "nokas", "beshavqa",
    "murdor", "past", "kaltak", "jinni", "devona", "qoqilgan",
    # Rus (transliterasiya)
    "blyad", "suka", "pizda", "huy", "hui", "pidor",
    "blyat", "nahuy", "mudak", "durak", "urod", "tvar",
    # Ingliz
    "fuck", "shit", "bitch", "bastard", "idiot", "moron",
]

def haqorat_bormi(matn: str) -> bool:
    """Matnda haqorat yoki qo'pol so'zlar borligini tekshiradi."""
    m = matn.lower()
    return any(soz in m for soz in HAQORAT_SOZLAR)

async def haqorat_ogohlantir(update) -> bool:
    """Haqorat bo'lsa ogohlantiradi va True qaytaradi."""
    matn = ""
    if update.message.text:
        matn = update.message.text
    elif update.message.caption:
        matn = update.message.caption
    if haqorat_bormi(matn):
        await update.message.reply_text(
            "⛔ <b>Xabaringiz yuborilmadi!</b>\n\n"
            "Xabaringizda qo'pol yoki haqoratli so'zlar aniqlandi.\n"
            "Iltimos, muloqotda hurmatli bo'ling.\n\n"
            "⚠️ Qayta xato takrorlansa hisob bloklanishi mumkin.",
            parse_mode="HTML"
        )
        return True
    return False

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
        "📲 <b>3-qadam:</b> Telegram username'ingizni kiriting\n\n"
        "👤 <b>Masalan:</b> <code>@jasur99</code>\n\n"
        "⚠️ <b>Diqqat:</b> Ma'lumotlaringiz aniqligi — xavfsizligingiz garovi. "
        "Xatolik yuz bersa, <b>«❌ Bekor qilish»</b> tugmasini bosib qaytadan boshlang.\n\n"
        "💡 Sizning ma'lumotlaringiz MOTUS tizimida xizmat sifatini oshirish va "
        "hamkorligimizni uzluksiz davom ettirish uchun kalit vazifasini bajaradi.\n\n"
        "<i>Telegram username'ingiz yo'q bo'lsa «yo'q» deb yozing</i>",
        parse_mode="HTML"
    )
    return MIJ_USERNAME

async def mij_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    found, clean = await check_username(context.bot, raw)
    if not found:
        await update.message.reply_text(
            f"⚠️ <b>@{clean.lstrip('@')} topilmadi.</b>\n\n"
            "Telegram username'ingiz ommaviy bo'lishi kerak yoki to'g'ri yozilganligini tekshiring.\n"
            "Yo'q bo'lsa <i>«yo'q»</i> deb yozing:",
            parse_mode="HTML"
        )
        return MIJ_USERNAME
    context.user_data["username"] = clean
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
        "💳 <b>5-qadam:</b> Karta raqamingizni kiriting\n\n"
        "🔐 <b>Nega karta raqami kerak?</b>\n"
        "Siz va usta (yoki evakuator haydovchisi) o'rtasidagi pul o'tkazmalari "
        "MOTUS nazorati ostida — <b>shaffof, haqqoniy va xavfsiz</b> amalga oshiriladi. "
        "Hech qanday yashirin to'lov yo'q: usta belgilangan narxdan ortiq ham, "
        "kam ham talab qila olmaydi.\n\n"
        "🔒 Karta raqamingiz shifrlangan holda saqlanadi va faqat to'lov "
        "tasdiqlanganda ishlatiladi. Hech kim ko'ra olmaydi.\n\n"
        "📌 <b>Namuna:</b> <code>8600 1234 5678 9012</code>\n"
        "<i>Hozircha qo'shmaslik uchun «yo'q» deb yozing — keyinroq qo'sha olasiz</i>",
        parse_mode="HTML"
    )
    return MIJ_KARTA

async def mij_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_karta(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML")
        return MIJ_KARTA
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
    save_mijoz_profile(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("mashina"), d.get("karta"), lat, lon
    )
    await send_to_admin(
        context,
        f"🚗 <b>YANGI MIJOZ RO'YXATDAN O'TDI</b>\n\n{summary}",
        reply_markup=reg_approve_kb(update.effective_user.id, "users")
    )
    await update.message.reply_text(
        summary,
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=mijoz_main_kb()
    )
    return MIJ_PROFIL_MENU

# ══════════════════════════════════════════════════════════
#  🆘 ARIZA QOLDIRISH
# ══════════════════════════════════════════════════════════
ARIZA_TOTAL = 2

async def _mijoz_profil_bazadan_yukla(tg_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
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
    ok = await _mijoz_profil_bazadan_yukla(update.effective_user.id, context)
    if not ok:
        await update.message.reply_text(
            "⚠️ Sizni tizimda topa olmadim. Avval bir marta ro'yxatdan o'ting.\n"
            "/start buyrug'ini bosing — bu faqat bir marta kerak bo'ladi, "
            "keyingi safar to'g'ridan-to'g'ri ariza qoldira olasiz.",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # ── O'ZGARISH: Kengaytirilgan misol matnlari ──────────────────────────
    await update.message.reply_text(
        "🆘 <b>Ariza qoldirish</b>\n\n"
        "Xavotir olmang, biz yordam beramiz! 🚗\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 {progress_bar(1, ARIZA_TOTAL)}\n"
        "💬 <b>Muammoingizni qisqacha yozing:</b>\n\n"
        "⚙️ <b>Mashina texnik muammosi (Usta kerak):</b>\n"
        "<i>«Dvigatel isib ketmoqda, qora tutun chiqyapti»</i>\n"
        "<i>«Motor o't olmayapti, starter aylanmayapti»</i>\n"
        "<i>«Tormoz tutmayapti, g'alati tovush bor»</i>\n"
        "<i>«Akkumulyator o'chdi, elektr yo'q»</i>\n"
        "<i>«Podveska urib ketmoqda, silkinyapti»</i>\n\n"
        "🚛 <b>Yo'lda qolib ketsangiz (Evakuator kerak):</b>\n"
        "<i>«Mashina o'chib qoldi, harakatlanmayapti»</i>\n"
        "<i>«Avariya bo'ldi, mashinani tortib ketish kerak»</i>\n"
        "<i>«Shina portladi, ehtiyot g'ildirak yo'q»</i>\n"
        "<i>«Mashinani boshqa manzilga ko'chirish kerak»</i>\n\n"
        "📞 <b>Savol yoki shikoyat (Call Center):</b>\n"
        "<i>«Xizmat narxlari haqida ma'lumot olmoqchiman»</i>\n"
        "<i>«Oldingi buyurtmam haqida savol bor»</i>\n"
        "<i>«Xizmat sifati bo'yicha shikoyatim bor»</i>\n"
        "<i>«MOTUS xizmatlari haqida batafsil bilmoqchiman»</i>\n\n"
        "✏️ <i>Erkin yozavering — keyin qaysi xizmat kerakligini tanlaymiz</i>\n\n"
        "✍️ Yozing 👇",
        parse_mode="HTML",
        reply_markup=text_step_cancel_kb()
    )
    return ARIZA_MUAMMO

async def ariza_muammo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Ovozli xabar
    if update.message.voice:
        context.user_data["ariza_muammo"]  = "[Ovozli xabar — operator eshitadi]"
        context.user_data["ariza_voice_id"] = update.message.voice.file_id
        context.user_data["ariza_turi"]     = ""
    else:
        if await haqorat_ogohlantir(update):
            return ARIZA_MUAMMO
        context.user_data["ariza_muammo"] = update.message.text.strip()

    # Xizmat turini tanlash uchun inline keyboard
    await update.message.reply_text(
        "✅ Muammoingiz qabul qilindi!\n\n"
        "🎯 <b>Qaysi xizmat kerak?</b>\n"
        "Quyidagidan birini tanlang 👇",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔧 Usta xizmati",       callback_data="xizmat_usta")],
            [InlineKeyboardButton("📞 Call Center xizmati", callback_data="xizmat_cc")],
            [InlineKeyboardButton("🚛 Evakuator xizmati",  callback_data="xizmat_evak")],
        ])
    )
    return ARIZA_XIZMAT_TANLASH


async def ariza_xizmat_tanlash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    xizmat = query.data  # xizmat_usta / xizmat_cc / xizmat_evak

    if xizmat == "xizmat_usta":
        context.user_data["ariza_turi"] = "usta"
        await query.edit_message_text(
            "🔧 <b>Usta xizmati tanlandi!</b>\n\n"
            "Qaysi soha ustasi kerak? 👇",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Motorist",                callback_data="soha_motorist")],
                [InlineKeyboardButton("🔌 Elektrik",                 callback_data="soha_elektrik")],
                [InlineKeyboardButton("🛞 Xodovoy",                  callback_data="soha_xodovoy")],
                [InlineKeyboardButton("⚖️ Balansirovka",             callback_data="soha_balansirovka")],
                [InlineKeyboardButton("🔩 Boshqa",                   callback_data="soha_boshqa")],
            ])
        )
        return ARIZA_SOHA_TANLASH

    elif xizmat == "xizmat_cc":
        context.user_data["ariza_turi"]      = "cc"
        context.user_data["ariza_usta_turi"] = "📞 Call Center"
        await query.edit_message_text(
            "📞 <b>Call Center xizmati tanlandi!</b>\n\n"
            "✅ Operatorimiz murojaatingizni ko'rib chiqadi.\n\n"
            "📍 <b>Endi lokatsiyangizni yuboring</b> — "
            "shu bo'yicha sizga yordam beramiz 🎯",
            parse_mode="HTML"
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⬇️ Lokatsiyangizni yuboring:",
            reply_markup=location_kb()
        )
        asyncio.create_task(
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text="⏳ Lokatsiya kutilmoqda...",
                parse_mode="HTML"
            )
        )
        return ARIZA_LOKATSIYA

    elif xizmat == "xizmat_evak":
        context.user_data["ariza_turi"]      = "evakuator"
        context.user_data["ariza_usta_turi"] = "🚛 Evakuator"
        await query.edit_message_text(
            "🚛 <b>Evakuator xizmati tanlandi!</b>\n\n"
            "✅ Eng yaqin evakuator haydovchisi yo'naltiriladi.\n\n"
            "📍 <b>Endi aniq joylashuvingizni yuboring</b> — "
            "shu bo'yicha sizga eng yaqin haydovchi topiladi 🎯",
            parse_mode="HTML"
        )
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text="⬇️ Lokatsiyangizni yuboring:",
            reply_markup=location_kb()
        )
        return ARIZA_LOKATSIYA

    return ARIZA_XIZMAT_TANLASH


async def ariza_soha_tanlash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    soha_map = {
        "soha_motorist":     "⚙️ Motorist",
        "soha_elektrik":     "🔌 Elektrik",
        "soha_xodovoy":      "🛞 Xodovoy",
        "soha_balansirovka": "⚖️ Balansirovka",
        "soha_boshqa":       "🔩 Boshqa",
    }
    soha = soha_map.get(query.data, "🔧 Usta")
    context.user_data["ariza_usta_turi"] = soha

    await query.edit_message_text(
        f"✅ <b>{soha}</b> tanlandi!\n\n"
        "📍 <b>Endi joylashuvingizni yuboring</b> — "
        "shu bo'yicha sizga eng yaqin ustani topamiz 🎯",
        parse_mode="HTML"
    )
    await context.bot.send_message(
        chat_id=query.message.chat_id,
        text="⬇️ Lokatsiyangizni yuboring:",
        reply_markup=location_kb()
    )
    return ARIZA_LOKATSIYA

    # Haqorat tekshiruvi
    if await haqorat_ogohlantir(update):
        return ARIZA_MUAMMO

    context.user_data["ariza_muammo"] = update.message.text.strip()
    ariza_turi = guess_ariza_turi(context.user_data["ariza_muammo"])
    context.user_data["ariza_turi"] = ariza_turi
    if ariza_turi == "usta":
        soha = guess_usta_soha(context.user_data["ariza_muammo"])
        context.user_data["ariza_usta_turi"] = soha or "Aniqlanmagan — operator tayinlaydi"
    elif ariza_turi == "evakuator":
        context.user_data["ariza_usta_turi"] = "🚛 Evakuator"
    elif ariza_turi == "cc":
        context.user_data["ariza_usta_turi"] = "📞 Call Center"
    elif ariza_turi == "taklif":
        context.user_data["ariza_usta_turi"] = "💡 Taklif / Fikr"
    else:
        context.user_data["ariza_usta_turi"] = "Aniqlanmagan — operator tayinlaydi"

    await update.message.reply_text(
        f"✅ Qabul qilindi!\n\n📊 {progress_bar(2, ARIZA_TOTAL)}\n"
        "📍 <b>Endi lokatsiyangizni yuboring</b> — shu bo'yicha sizga eng yaqin "
        "mutaxassisni topamiz 🎯",
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
    ariza_turi = d.get("ariza_turi", "")

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
        f"👨‍🔧 <b>Yo'nalish:</b> {d.get('ariza_usta_turi')}\n"
        f"📍 <b>Lokatsiya:</b> 📌 {lat:.5f}, {lon:.5f}\n"
        f"🗺 <b>Xarita:</b> <a href=\"{maps_link}\">Google Maps</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⏱ <b>20 daqiqa ichida</b> mutaxassis bog'lanadi!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=mijoz_main_kb()
    )
    ariza_id = save_ariza(update.effective_user.id, "mijoz_ariza", summary)
    set_ariza_location(ariza_id, lat, lon)
    set_ariza_turi(ariza_id, ariza_turi)

    last = get_last_contact(update.effective_user.id, "mijoz_ariza", ariza_id)
    jami_ariza = get_client_ariza_count(update.effective_user.id)
    admin_text = (
        f"🆘 <b>YANGI ARIZA</b> #{ariza_id}\n"
        f"🕐 <b>Vaqt:</b> {now_str}\n"
        f"📊 <b>Bu mijozning jami arizalari:</b> {jami_ariza} ta\n"
    )
    if last:
        admin_text += f"🕓 <b>Oxirgi murojaat:</b> {last}\n"
    admin_text += f"\n{summary}"
    admin_msg = await send_to_admin(context, admin_text, ariza_id=ariza_id)
    if admin_msg:
        ADMIN_ARIZA_MSG[ariza_id] = admin_msg.message_id
    # Ovozli xabar bo'lsa adminga alohida yuborish
    voice_id = d.get("ariza_voice_id")
    if voice_id:
        try:
            await context.bot.send_voice(
                chat_id=ADMIN_CHAT_ID,
                voice=voice_id,
                caption=f"🎙 #{ariza_id} — Mijoz ovozli muammo tavsifi"
            )
        except Exception:
            pass

    # ── O'ZGARISH: Ariza turiga qarab tegishli xodimlarga yuborish ──
    asyncio.create_task(_dispatch_ariza(
        context, ariza_id, ariza_turi,
        d.get("ariza_muammo", ""),
        d.get("ariza_usta_turi", ""),
        lat, lon,
        d.get("ism", ""), d.get("tel", ""),
        d.get("username", "—"), d.get("mashina", "—")
    ))
    return MIJ_PROFIL_MENU

# ── YANGI: Markaziy dispatch funksiyasi ──────────────────────────────────
async def _dispatch_ariza(context: ContextTypes.DEFAULT_TYPE, ariza_id: int,
                           ariza_turi: str, muammo: str, usta_turi: str,
                           lat: float, lon: float,
                           mijoz_ism: str, mijoz_tel: str,
                           mijoz_username: str, mashina: str):
    """Ariza turiga qarab tegishli xodimlarga yuboradi:
    - 'usta'      → tasdiqlangan ustalar
    - 'evakuator' → tasdiqlangan evakuator haydovchilari
    - 'cc'        → tasdiqlangan call center xodimlari
    - 'taklif'    → faqat adminga
    - ''          → hamma xizmatlarga (operator aniqlaydi)
    """
    maps_link = f"https://maps.google.com/?q={lat},{lon}"

    # Mijoz ma'lumotlari bloki — barcha xodimlarga bir xil ko'rinadi
    mijoz_blok = (
        f"┌─────────────────────────────\n"
        f"│ 👤 <b>Ism:</b> {mijoz_ism}\n"
        f"│ 📞 <b>Telefon:</b> <code>{mijoz_tel}</code>\n"
        f"│ 📲 <b>Telegram:</b> {mijoz_username}\n"
        f"│ 🚙 <b>Mashina:</b> {mashina}\n"
        f"│ 🔍 <b>Muammo:</b> {muammo}\n"
        f"│ 📍 <b>Manzil:</b> <a href=\"{maps_link}\">Xaritada ko'rish</a>\n"
        f"└─────────────────────────────"
    )

    if ariza_turi == "taklif":
        # Taklif faqat adminга — notify_ustalar chaqirilmaydi
        return

    if ariza_turi == "usta":
        soha_guess = guess_usta_soha(muammo)
        nomzodlar  = get_ustalar_for_ariza(soha_guess, lat, lon)
        if nomzodlar:
            await _notify_workers(
                context, ariza_id, nomzodlar,
                header="🔧 YANGI BUYURTMA",
                emoji="⚙️",
                xizmat_nomi=f"Kerakli usta: <b>{usta_turi}</b>",
                muammo=muammo, maps_link=maps_link, mijoz_blok=mijoz_blok,
            )
        else:
            await send_to_admin(context,
                f"⚠️ Ariza #{ariza_id}: tasdiqlangan faol usta topilmadi. Qo'lda biriktiring.")

    elif ariza_turi == "evakuator":
        evaklar = get_evakuatorlar_for_ariza(lat, lon)
        if evaklar:
            await _notify_workers(
                context, ariza_id, evaklar,
                header="🚛 EVAKUATOR CHAQIRUVI",
                emoji="🆘",
                xizmat_nomi="Xizmat: <b>Evakuatsiya</b>",
                muammo=muammo, maps_link=maps_link, mijoz_blok=mijoz_blok,
            )
        else:
            await send_to_admin(context,
                f"⚠️ Ariza #{ariza_id}: tasdiqlangan faol evakuator topilmadi. Qo'lda biriktiring.")
            

    else:
        cc_lar = get_cc_for_ariza()
        if cc_lar:
            await _notify_workers(
                context, ariza_id, cc_lar,
                header="📞 YANGI MUROJAAT",
                emoji="💬",
                xizmat_nomi="Xizmat: <b>Operator yo'naltiradi</b>",
                muammo=muammo, maps_link=maps_link, mijoz_blok=mijoz_blok,
            )

async def _notify_workers(context: ContextTypes.DEFAULT_TYPE, ariza_id: int,
                           workers: list, header: str, emoji: str,
                           xizmat_nomi: str, muammo: str,
                           maps_link: str, mijoz_blok: str):
    """Barcha xodim turlari (usta/cc/evak) uchun umumiy bildirishnoma yuboruvchi."""
    sent = PENDING_NOTIFICATIONS.get(ariza_id, [])
    for w in workers:
        masofa_matn = f"~{w['dist']:.1f} km" if w.get("dist", 999) < 900 else ""
        text = (
            f"🔔 <b>{header}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{emoji} <b>Muammo:</b>\n"
            f"<i>«{muammo}»</i>\n\n"
            f"🎯 {xizmat_nomi}\n"
        )
        if masofa_matn:
            text += f"📡 <b>Sizdan masofa:</b> {masofa_matn}\n"
        text += (
            f"\n👤 <b>Mijoz ma'lumotlari:</b>\n"
            f"{mijoz_blok}\n\n"
            "⚡ <b>Diqqat!</b> Birinchi qabul qilgan — buyurtmani oladi!\n"
            "👇 Quyidagi tugmani bosing:"
        )
        try:
            m = await context.bot.send_message(
                chat_id=w["tg_id"], text=text, parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=xodim_qabul_kb(ariza_id)
            )
            sent.append((w["tg_id"], m.message_id))
        except Exception as e:
            logger.error(f"Xodimga yuborishda xato ({w['tg_id']}): {e}")
    PENDING_NOTIFICATIONS[ariza_id] = sent

# ══════════════════════════════════════════════════════════
#  RELAY — BOT ORQALI XAVFSIZ MULOQOT
# ══════════════════════════════════════════════════════════
async def relay_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usta↔Mijoz, Evak↔Mijoz, CC↔Mijoz bot orqali xabar almashish."""
    user_id = update.effective_user.id
    if user_id not in RELAY_CONNECTIONS:
        return
    conn    = RELAY_CONNECTIONS[user_id]
    partner = conn["partner"]
    role    = conn["role"]
    msg     = update.message

    # Haqorat tekshiruvi
    if await haqorat_ogohlantir(update):
        return

    try:
        if msg.text:
            await context.bot.send_message(
                chat_id=partner,
                text=f"💬 <b>{role}:</b>\n{msg.text}",
                parse_mode="HTML",
                reply_markup=relay_end_kb()
            )
        elif msg.voice:
            await context.bot.send_voice(
                chat_id=partner,
                voice=msg.voice.file_id,
                caption=f"🎙 <b>{role}</b> ovozli xabari",
                parse_mode="HTML",
                reply_markup=relay_end_kb()
            )
        elif msg.photo:
            await context.bot.send_photo(
                chat_id=partner,
                photo=msg.photo[-1].file_id,
                caption=f"📸 <b>{role}</b>" + (f"\n{msg.caption}" if msg.caption else ""),
                parse_mode="HTML",
                reply_markup=relay_end_kb()
            )
        elif msg.sticker:
            await context.bot.send_sticker(chat_id=partner, sticker=msg.sticker.file_id)
        await msg.reply_text("✅ Yetkazildi")
    except Exception as e:
        logger.error(f"Relay xato ({user_id}→{partner}): {e}")
        await msg.reply_text("⚠️ Xabar yetkazishda xato. Partner ulanmagan bo'lishi mumkin.")

async def relay_end_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muloqotni yakunlash tugmasi."""
    query   = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if user_id not in RELAY_CONNECTIONS:
        await query.edit_message_reply_markup(reply_markup=None)
        return

    conn    = RELAY_CONNECTIONS.pop(user_id, {})
    partner = conn.get("partner")

    # Partnerni ham uzamiz
    if partner and partner in RELAY_CONNECTIONS:
        RELAY_CONNECTIONS.pop(partner, None)
        try:
            await context.bot.send_message(
                chat_id=partner,
                text=(
                    "🔚 <b>Muloqot yakunlandi.</b>\n\n"
                    "Boshqa savollar uchun /start bosing.\n"
                    "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        await query.edit_message_text(
            "🔚 <b>Muloqot yakunlandi.</b>\n\n"
            "Boshqa savollar uchun /start bosing.\n"
            "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

# ══════════════════════════════════════════════════════════
#  USTA ARIZA HOLAT TUGMALARI
# ══════════════════════════════════════════════════════════
async def usta_ariza_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await  query.answer()
    parts  = query.data.split("_", 2)   # ust_{ariza_id}_{action}
    ariza_id = int(parts[1])
    action   = parts[2]
    xodim    = update.effective_user
    info     = get_usta_info(xodim.id) or get_evak_info(xodim.id) or get_cc_info(xodim.id)
    xodim_ism = info["ism"] if info else (xodim.first_name or "Xodim")

    if action == "jarayonda":
        update_status(ariza_id, "jarayonda")
        new_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Bajarildi",         callback_data=f"ust_{ariza_id}_bajarildi")],
            [InlineKeyboardButton("❌ Muammo yuz berdi",  callback_data=f"ust_{ariza_id}_muammo")],
            [InlineKeyboardButton("🧑‍🔧 Master usta kerak", callback_data=f"ust_{ariza_id}_master")],
        ])
        try:
            await query.edit_message_reply_markup(reply_markup=new_kb)
        except Exception:
            pass
        await send_to_admin(
            context,
            f"🟡 <b>Ariza #{ariza_id} jarayonda</b>\n\n"
            f"👤 <b>Usta:</b> {xodim_ism}\n"
            f"🕐 {datetime.now().strftime('%d.%m.%Y, soat %H:%M')}"
        )
        await sync_admin_ariza(context, ariza_id, "🟡 Jarayonda")

    elif action == "bajarildi":
        update_status(ariza_id, "bajarildi")
        now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")

        # Usta xabaridan BARCHA tugmalarni o'chirish
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass

        # Ustaga tasdiqlash xabari
        await context.bot.send_message(
            chat_id=xodim.id,
            text=(
                f"✅ <b>Ish yakunlandi!</b>\n\n"
                f"Ariza #{ariza_id} bo'yicha xizmat bajarildi deb belgilandi.\n"
                f"🕐 {now_str}\n\n"
                "Mijoz tez orada xizmatni baholaydi.\n"
                "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
            ),
            parse_mode="HTML"
        )

        # Adminga to'liq xabar
        await send_to_admin(
            context,
            f"✅ <b>ISH YAKUNLANDI — Ariza #{ariza_id}</b>\n\n"
            f"👤 <b>Usta:</b> {xodim_ism}\n"
            f"🕐 {now_str}\n\n"
            "Barcha tugmalar avtomatik o'chirildi."
        )
        await sync_admin_ariza(context, ariza_id, "✅ Bajarildi")

        # Mijozga yakunlanish xabari + baholash
        ariza = get_ariza_full(ariza_id)
        if ariza and ariza.get("tg_id"):
            try:
                await context.bot.send_message(
                    chat_id=ariza["tg_id"],
                    text=(
                        "🎉 <b>Xizmat muvaffaqiyatli yakunlandi!</b>\n\n"
                        f"👨‍🔧 <b>{xodim_ism}</b> mashinangizni tuzatib berdi.\n\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "⭐ <b>Usta xizmatini baholang:</b>\n"
                        "Sizning bahoyingiz boshqa mijozlarga yordam beradi "
                        "va ustani rag'batlantiradi!\n\n"
                        "👇 Yulduzcha tanlang:"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("⭐",       callback_data=f"rate_{ariza_id}_1"),
                            InlineKeyboardButton("⭐⭐",     callback_data=f"rate_{ariza_id}_2"),
                            InlineKeyboardButton("⭐⭐⭐",   callback_data=f"rate_{ariza_id}_3"),
                        ],
                        [
                            InlineKeyboardButton("⭐⭐⭐⭐",   callback_data=f"rate_{ariza_id}_4"),
                            InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_{ariza_id}_5"),
                        ],
                    ])
                )
            except Exception as e:
                logger.error(f"Mijozga yakunlanish xabari: {e}")
    elif action == "muammo":
        PENDING_USTA_IZOH[xodim.id] = {
            "ariza_id": ariza_id,
            "type":     "muammo",
            "ism":      xodim_ism,
        }
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=xodim.id,
            text=(
                "❌ <b>Muammo haqida ma'lumot bering</b>\n\n"
                "Nima yuz berganini qisqacha tushuntiring — "
                "matn yoki ovozli xabar yuborishingiz mumkin 🎙\n\n"
                "<i>Bu ma'lumot call center va adminga yuboriladi.</i>"
            ),
            parse_mode="HTML"
        )

    elif action == "master":
        PENDING_USTA_IZOH[xodim.id] = {
            "ariza_id": ariza_id,
            "type":     "master",
            "ism":      xodim_ism,
        }
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=xodim.id,
            text=(
                "🧑‍🔧 <b>Master usta kerak — tushuntiring</b>\n\n"
                "Nima uchun master usta kerakligi haqida ma'lumot bering — "
                "matn yoki ovozli xabar yuborishingiz mumkin 🎙\n\n"
                "<i>Admin tasdiqlashidan so'ng master usta yo'naltiriladi.</i>"
            ),
            parse_mode="HTML"
        )


async def usta_izoh_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usta muammo yoki master usta izohini qabul qiladi."""
    user_id = update.effective_user.id
    if user_id not in PENDING_USTA_IZOH:
        return

    info     = PENDING_USTA_IZOH.pop(user_id)
    ariza_id = info["ariza_id"]
    izoh_turi = info["type"]
    usta_ism  = info["ism"]
    msg       = update.message

    if izoh_turi == "muammo":
        header = f"❌ <b>MUAMMO — Ariza #{ariza_id}</b>"
        user_msg = "⚠️ Muammo haqida ma'lumotingiz qabul qilindi. Call center xodimimiz tez orada bog'lanadi."
        admin_note = f"\n\n📋 <b>Usta izohi:</b>"
    else:
        header = f"🧑‍🔧 <b>MASTER USTA KERAK — Ariza #{ariza_id}</b>"
        user_msg = "✅ So'rovingiz qabul qilindi. Admin tasdiqlashidan so'ng master usta yo'naltiriladi."
        admin_note = f"\n\n📋 <b>Usta izohi:</b>"

    base_text = (
        f"{header}\n\n"
        f"👤 <b>Usta:</b> {usta_ism}\n"
        f"🕐 {datetime.now().strftime('%d.%m.%Y, soat %H:%M')}"
        f"{admin_note}"
    )

    cc_lar = get_cc_for_ariza()
    cc_sent = []
    kb_master = master_tasdiq_kb(ariza_id, user_id) if izoh_turi == "master" else None

    for cc in cc_lar:
        try:
            if msg.text:
                m = await context.bot.send_message(
                    chat_id=cc["tg_id"],
                    text=base_text + f"\n<i>{msg.text}</i>",
                    parse_mode="HTML",
                    reply_markup=kb_master
                )
            elif msg.voice:
                await context.bot.send_message(
                    chat_id=cc["tg_id"],
                    text=base_text,
                    parse_mode="HTML",
                    reply_markup=kb_master
                )
                m = await context.bot.send_voice(
                    chat_id=cc["tg_id"],
                    voice=msg.voice.file_id,
                    caption="🎙 Usta ovozli izohi"
                )
            cc_sent.append((cc["tg_id"], m.message_id))
        except Exception as e:
            logger.error(f"CC ga yuborishda xato: {e}")

    try:
        if msg.text:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=base_text + f"\n<i>{msg.text}</i>",
                parse_mode="HTML",
                reply_markup=kb_master
            )
        elif msg.voice:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=base_text,
                parse_mode="HTML",
                reply_markup=kb_master
            )
            await context.bot.send_voice(
                chat_id=ADMIN_CHAT_ID,
                voice=msg.voice.file_id,
                caption="🎙 Usta ovozli izohi"
            )
    except Exception as e:
        logger.error(f"Admin ga yuborishda xato: {e}")

    await msg.reply_text(user_msg, parse_mode="HTML")
    await sync_admin_ariza(
        context, ariza_id,
        "❌ Muammo bildirildi" if izoh_turi == "muammo" else "🧑‍🔧 Master so'raldi"
    )

    if izoh_turi == "master" and cc_sent:
        context.application.bot_data[f"master_cc_{ariza_id}_{user_id}"] = cc_sent


async def admin_master_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin master usta yuborishni tasdiqlaydi."""
    query = update.callback_query
    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Faqat admin!", show_alert=True)
        return
    await query.answer()

    _, rest    = query.data.split("_", 1)
    parts      = rest.split("_")
    ariza_id   = int(parts[0])
    usta_tg_id = int(parts[1])

    now_str = datetime.now().strftime("%d.%m.%Y, soat %H:%M")

    try:
        await query.edit_message_text(
            (query.message.text or "") +
            f"\n\n✅ <b>Tasdiqlandi — {now_str}</b>",
            parse_mode="HTML"
        )
    except Exception:
        pass

    cc_lar = get_cc_for_ariza()
    for cc in cc_lar:
        try:
            await context.bot.send_message(
                chat_id=cc["tg_id"],
                text=(
                    f"✅ <b>MASTER USTA YUBORILDI — Ariza #{ariza_id}</b>\n\n"
                    f"🕐 {now_str}\n\n"
                    "Admin tomonidan tasdiqlandi. Iltimos, usta bilan bog'lanib "
                    "master ustani yo'naltiring."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"CC ga master tasdiqlash: {e}")

    try:
        await context.bot.send_message(
            chat_id=usta_tg_id,
            text=(
                f"✅ <b>Master usta yo'naltirildi — Ariza #{ariza_id}</b>\n\n"
                "Admin tomonidan tasdiqlandi. Call center xodimimiz tez orada "
                "siz bilan bog'lanadi va master ustani yo'naltiradi.\n\n"
                "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Ustaga master tasdiqlash: {e}")

    # ── YANGI: mijozga ham xabar boradi ──
    mijoz_tg_id = get_ariza_client(ariza_id)
    if mijoz_tg_id:
        try:
            await context.bot.send_message(
                chat_id=mijoz_tg_id,
                text=(
                    f"🧑‍🔧 <b>Master usta yo'lga chiqmoqda!</b>\n\n"
                    f"Arizangiz #{ariza_id} bo'yicha qo'shimcha mutaxassis "
                    "(master usta) tez orada sizning manzilingizga yo'naltirildi "
                    "va tez orada yetib boradi.\n\n"
                    f"🕐 {now_str}\n\n"
                    "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Mijozga master tasdiqlash: {e}")

# ══════════════════════════════════════════════════════════
#  Lokatsiya yangilash (profil menyusida)
# ══════════════════════════════════════════════════════════
async def mijoz_lokatsiya_yangilash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    loc = update.message.location
    lat, lon = loc.latitude, loc.longitude
    maps_link = f"https://maps.google.com/?q={lat},{lon}"
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
        "📲 <b>3-qadam:</b> Telegram username'ingizni kiriting\n\n"
        "👤 <b>Masalan:</b> <code>@sardor_usta</code>\n\n"
        "⚠️ <b>Diqqat:</b> Bu ma'lumot mijozlar siz bilan to'g'ridan-to'g'ri "
        "muloqot qilishi uchun ishlatiladi. To'g'ri kiriting — xatolik bo'lsa "
        "<b>«❌ Bekor qilish»</b> tugmasini bosib qaytadan boshlang.\n\n"
        "💡 Username'ingiz platformadagi professional profilingizning bir qismi — "
        "bu mijozlar ishonchini oshiradi va sizga ko'proq buyurtma keltiradi.\n\n"
        "<i>Yo'q bo'lsa «yo'q» deb yozing</i>",
        parse_mode="HTML"
    )
    return USTA_USERNAME

async def usta_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    found, clean = await check_username(context.bot, raw)
    if not found:
        await update.message.reply_text(
            f"⚠️ <b>@{clean.lstrip('@')} topilmadi.</b>\n\n"
            "To'g'ri username kiriting yoki <i>«yo'q»</i> deb yozing:",
            parse_mode="HTML"
        )
        return USTA_USERNAME
    context.user_data["username"] = clean
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
        "💳 <b>7-qadam:</b> To'lov kartangiz raqamini kiriting\n\n"
        "💰 <b>Nega karta so'raymiz?</b>\n"
        "Siz bajargan har bir xizmat uchun haq <b>bevosita shu kartangizga</b> "
        "o'tkaziladi — mijoz to'laganidan so'ng darhol. "
        "Naqd pul kutish, mijoz bilan kelishuv yo'q: tizim avtomatik hal qiladi.\n\n"
        "🤝 <b>Professional hamkorlik:</b> Karta ma'lumotlaringiz xizmat haqingizni "
        "kechikishlarsiz, to'liq va himoyalangan tizim orqali qabul qilishingizni ta'minlaydi.\n\n"
        "📌 <b>Namuna:</b> <code>8600 1234 5678 9012</code>",
        parse_mode="HTML"
    )
    return USTA_KARTA

async def usta_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_karta(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML")
        return USTA_KARTA
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
        "✅ Test o'tgach — platformaga kirgizilasiz va arizalar kela boshlaydi!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "usta", summary)
    save_usta(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("mashina_turi"), d.get("soha", ""), d.get("tajriba"), d.get("karta"),
        lat, lon
    )
    last = get_last_contact(update.effective_user.id, "usta", ariza_id)
    admin_text = (
        f"🔧 <b>YANGI USTA ARIZASI</b> #{ariza_id}\n"
        f"🕐 {now_str}\n"
    )
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    # ── O'ZGARISH: Admin tasdiqlash tugmasi bilan yuboriladi ──
    await send_to_admin(
        context, admin_text,
        reply_markup=reg_approve_kb(update.effective_user.id, "ustalar")
    )
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
        "📲 <b>3-qadam:</b> Telegram username'ingizni kiriting\n\n"
        "👤 <b>Masalan:</b> <code>@nilufar_cc</code>\n\n"
        "⚠️ <b>Diqqat:</b> Bu ma'lumot ish jarayonida mijozlar va ustalar bilan "
        "tezkor muloqot uchun zarur. Aniq kiriting — "
        "xatolik bo'lsa <b>«❌ Bekor qilish»</b> tugmasini bosing.\n\n"
        "💡 Sizning ma'lumotlaringiz MOTUS jamoasidagi ishingizni uzluksiz va "
        "samarali davom ettirishga yordam beradi.\n\n"
        "<i>Yo'q bo'lsa «yo'q» deb yozing</i>",
        parse_mode="HTML"
    )
    return CC_USERNAME

async def cc_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    found, clean = await check_username(context.bot, raw)
    if not found:
        await update.message.reply_text(
            f"⚠️ <b>@{clean.lstrip('@')} topilmadi.</b>\n\n"
            "To'g'ri username kiriting yoki <i>«yo'q»</i> deb yozing:",
            parse_mode="HTML"
        )
        return CC_USERNAME
    context.user_data["username"] = clean
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
        "💳 <b>5-qadam:</b> Ish haqi o'tkaziladigan karta raqamingizni kiriting\n\n"
        "💼 <b>Nega karta so'raymiz?</b>\n"
        "Oylik maoshingiz, bonuslar va mukofotlar <b>bevosita shu kartangizga</b> "
        "o'tkaziladi — hech qanday kechikishsiz. "
        "MOTUS moliya tizimi shaffof ishlaydi: har bir to'lov tarixi sizga ko'rinadi.\n\n"
        "🔒 Ma'lumot faqat HR bo'limi va moliya xizmati uchun — uchinchi tomonga berilmaydi.\n\n"
        "📌 <b>Namuna:</b> <code>8600 1234 5678 9012</code>",
        parse_mode="HTML"
    )
    return CC_KARTA

async def cc_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_karta(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML")
        return CC_KARTA
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
        "📋 HR menejerimiz tez orada bog'lanadi!\n"
        "✅ Suhbatdan o'tgach — platformaga kirgizilasiz!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "call_center", summary)
    # ── O'ZGARISH: cc_xodimlar jadvaliga yoziladi ──
    save_cc(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("tajriba"), d.get("karta"), lat, lon
    )
    last = get_last_contact(update.effective_user.id, "call_center", ariza_id)
    admin_text = (
        f"📞 <b>YANGI CALL CENTER ARIZASI</b> #{ariza_id}\n"
        f"🕐 {now_str}\n"
    )
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    # ── O'ZGARISH: Admin tasdiqlash tugmasi bilan yuboriladi ──
    await send_to_admin(
        context, admin_text,
        reply_markup=reg_approve_kb(update.effective_user.id, "cc_xodimlar")
    )
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
        "📲 <b>3-qadam:</b> Telegram username'ingizni kiriting\n\n"
        "👤 <b>Masalan:</b> <code>@bobur_evak</code>\n\n"
        "⚠️ <b>Diqqat:</b> Yo'lda qolgan mijozlar siz bilan bog'lanishi uchun "
        "to'g'ri username kerak. Xatolik bo'lsa "
        "<b>«❌ Bekor qilish»</b> tugmasini bosing.\n\n"
        "💡 Aniq ma'lumot — tez aloqa — ko'proq buyurtma. "
        "MOTUS orqali har bir chaqiruv sizning daromadingiz!\n\n"
        "<i>Yo'q bo'lsa «yo'q» deb yozing</i>",
        parse_mode="HTML"
    )
    return EV_USERNAME

async def ev_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    found, clean = await check_username(context.bot, raw)
    if not found:
        await update.message.reply_text(
            f"⚠️ <b>@{clean.lstrip('@')} topilmadi.</b>\n\n"
            "To'g'ri username kiriting yoki <i>«yo'q»</i> deb yozing:",
            parse_mode="HTML"
        )
        return EV_USERNAME
    context.user_data["username"] = clean
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
        "💳 <b>5-qadam:</b> To'lovlar qabul qilinadigan karta raqamingizni kiriting\n\n"
        "🚛 <b>Nega karta so'raymiz?</b>\n"
        "Har bir evakuatsiya xizmati uchun haq <b>bevosita shu kartangizga</b> "
        "o'tkaziladi — mijoz xizmatdan so'ng platformaga to'laydi, siz esa "
        "naqd pul bilan shug'ullanmaysiz.\n\n"
        "🔐 Naqd pul muammosi yo'q, xavfsiz to'lov tizimi orqali ishlaysiz. "
        "Har bir chaqiruv — kafolatlangan daromad.\n\n"
        "📌 <b>Namuna:</b> <code>8600 1234 5678 9012</code>",
        parse_mode="HTML"
    )
    return EV_KARTA

async def ev_karta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ok, err = validate_karta(update.message.text)
    if not ok:
        await update.message.reply_text(err, parse_mode="HTML")
        return EV_KARTA
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
        "📋 Dispatcher menejerimiz tez orada bog'lanadi!\n"
        "✅ Suhbatdan o'tgach — platformaga kirgizilasiz!\n\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )
    await update.message.reply_text(
        summary, parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(), disable_web_page_preview=True
    )
    ariza_id = save_ariza(update.effective_user.id, "evakuator", summary)
    # ── O'ZGARISH: evakuatorlar jadvaliga alohida yoziladi ──
    save_evakuator(
        update.effective_user.id, d.get("ism"), d.get("tel"), d.get("username"),
        d.get("tajriba"), d.get("karta"), lat, lon
    )
    last = get_last_contact(update.effective_user.id, "evakuator", ariza_id)
    admin_text = (
        f"🚛 <b>YANGI EVAKUATOR ARIZASI</b> #{ariza_id}\n"
        f"🕐 {now_str}\n"
    )
    if last:
        admin_text += f"🕓 Oxirgi murojaat: {last}\n"
    admin_text += f"\n{summary}"
    # ── O'ZGARISH: Admin tasdiqlash tugmasi bilan yuboriladi ──
    await send_to_admin(
        context, admin_text,
        reply_markup=reg_approve_kb(update.effective_user.id, "evakuatorlar")
    )
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  ✅ ADMIN TASDIQLASH / RAD ETISH
# ══════════════════════════════════════════════════════════
async def admin_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_CHAT_ID:
        await query.answer("⛔ Faqat admin!", show_alert=True)
        return
    await query.answer()

    data   = query.data
    action, rest = data.split("_", 1)
    tg_id  = int(rest.rsplit("_", 1)[1])
    table  = rest.rsplit("_", 1)[0]

    TABLE_MAP = {
        "ustalar":      "🔧 Usta",
        "cc_xodimlar":  "📞 Call Center xodimi",
        "evakuatorlar": "🚛 Evakuator haydovchisi",
        "users":        "🚗 Mijoz",
    }
    role_label = TABLE_MAP.get(table, "Foydalanuvchi")

    if action == "appr":
        if table != "users":
            approve_worker(tg_id, table)

        tabriq = (
            f"🎉 <b>Tabriklaymiz!</b>\n\n"
            f"Siz MOTUS platformasiga <b>{role_label}</b> sifatida "
            f"qabul qilindingiz! ✅\n\n"
        )
        if table == "users":
            tabriq += (
                "📞 Tez orada <b>call center xodimimiz</b> siz bilan bog'lanadi "
                "va platformadan foydalanishni boshlashingizga yordam beradi.\n\n"
                "🚗 Endi ilovamiz orqali eng yaqin ustani topa olasiz va "
                "xizmatdan foydalanishingiz mumkin!\n\n"
                "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
            )
        else:
            tabriq += (
                "📞 Tez orada <b>call center xodimimiz</b> siz bilan bog'lanadi "
                "va platformaga to'liq kiritilishingizni rasmiylashtiradi.\n\n"
                "Endi sizga mos arizalar avtomatik yuboriladi.\n"
                "Har bir arizada <b>«✅ Men qabul qilaman»</b> tugmasini "
                "bosib buyurtmani oling!\n\n"
                "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
            )
        try:
            await context.bot.send_message(
                chat_id=tg_id, text=tabriq, parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Tasdiqlash xabari: {e}")

        old = query.message.text or ""
        try:
            await query.edit_message_text(
                old + f"\n\n✅ <b>Qabul qilindi</b> — {role_label} platformaga qo'shildi.",
                parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass
        return ConversationHandler.END

    elif action == "rejt":
        PENDING_REJECT[query.from_user.id] = {
            "table":    table,
            "tg_id":    tg_id,
            "role":     role_label,
            "msg_id":   query.message.message_id,
            "chat_id":  query.message.chat_id,
            "old_text": query.message.text or ""
        }
        try:
            await query.edit_message_text(
                (query.message.text or "") +
                f"\n\n✏️ <b>Bekor qilish sababini yozing</b>:\n"
                f"<i>«/skip» yozsangiz — sababsiz bekor qilinadi</i>",
                parse_mode="HTML", disable_web_page_preview=True
            )
        except Exception:
            pass
        return ADMIN_REJECT_REASON


async def admin_reject_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sabab yozgandan keyin rad etishni yakunlaydi."""
    admin_id = update.effective_user.id
    if admin_id != ADMIN_CHAT_ID:
        return ConversationHandler.END

    info = PENDING_REJECT.pop(admin_id, None)
    if not info:
        return ConversationHandler.END

    sabab = update.message.text.strip()
    if sabab.lower() == "/skip":
        sabab = "Sabab ko'rsatilmadi"

    ok = reject_worker(info["tg_id"], info["table"])

    if ok:
        try:
            await context.bot.send_message(
                chat_id=info["tg_id"],
                text=(
                    "😔 <b>Arizangiz ko'rib chiqildi.</b>\n\n"
                    "Afsuski, hozircha platformamizga qo'shila olmadingiz.\n\n"
                    f"📋 <b>Sabab:</b> {sabab}\n\n"
                    "Savollar bo'lsa qayta /start bosib murojaat qiling."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Rad etish xabari yuborilmadi: {e}")

        try:
            await context.bot.edit_message_text(
                chat_id=info["chat_id"],
                message_id=info["msg_id"],
                text=info["old_text"] + f"\n\n❌ <b>Rad etildi</b> — Sabab: {sabab}",
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception:
            pass

        await update.message.reply_text("✅ Rad etish yakunlandi.")
    else:
        await update.message.reply_text("⚠️ Bazadan o'chirishda xatolik yuz berdi.")

    return ConversationHandler.END

# ══════════════════════════════════════════════════════════
#  ✅ XODIM QABUL TUGMASI (acc_)
# ══════════════════════════════════════════════════════════
async def xodim_accept_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usta/CC/Evakuator 'Men qabul qilaman' bosganida ishlaydi."""
    query = update.callback_query
    ariza_id = int(query.data.replace("acc_", ""))
    xodim = update.effective_user

    # Xodim ma'lumotlarini barcha jadvallardan qidiramiz
    info = get_usta_info(xodim.id) or get_cc_info(xodim.id) or get_evak_info(xodim.id)
    xodim_ism = info["ism"] if info else (xodim.first_name or "Xodim")
    xodim_tel = info["tel"] if info else "—"
    xodim_username = info.get("username", "—") if info else "—"

    muvaffaqiyatli = assign_ariza(ariza_id, xodim.id, xodim_ism, xodim_tel)

    if not muvaffaqiyatli:
        await query.answer(
            "😔 Kechirasiz, bu buyurtmani boshqa mutaxassis allaqachon qabul qilgan.",
            show_alert=True
        )
        try:
            await query.edit_message_text(
                (query.message.text or "") + "\n\n❌ <b>Band qilindi</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        return

    await query.answer("✅ Buyurtma sizga biriktirildi!")

    # Ariza ma'lumotlarini olish
    ariza = get_ariza_full(ariza_id)
    client_tg_id = ariza["tg_id"] if ariza else None
    maps_link = None
    if ariza and ariza.get("client_lat"):
        maps_link = f"https://maps.google.com/?q={ariza['client_lat']},{ariza['client_lon']}"

    # ── YANGI: Xodimga mijoz ma'lumotlari bilan tasdiqlash xabari ──────────
    mijoz_profil = get_mijoz_profile(client_tg_id) if client_tg_id else None
    mijoz_ism  = mijoz_profil["ism"]  if mijoz_profil else "—"
    mijoz_tel  = mijoz_profil["tel"]  if mijoz_profil else "—"
    mijoz_user = mijoz_profil["username"] if mijoz_profil else "—"
    mijoz_mash = mijoz_profil["mashina"]  if mijoz_profil else "—"

    xodim_confirm_text = (
        "✅ <b>Buyurtma sizda!</b>\n\n"
        "╔══════════════════════════════╗\n"
        "║   👤  MIJOZ MA'LUMOTLARI     ║\n"
        "╚══════════════════════════════╝\n\n"
        f"👤 <b>Ism:</b> {mijoz_ism}\n"
        f"📞 <b>Telefon:</b> {mijoz_tel}\n"
        f"📲 <b>Telegram:</b> {mijoz_user}\n"
        f"🚙 <b>Mashina:</b> {mijoz_mash}\n"
    )
    if maps_link:
        xodim_confirm_text += f"🗺 <b>Manzil:</b> <a href=\"{maps_link}\">Xaritada ko'rish</a>\n"
    xodim_confirm_text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📞 Mijoz bilan bog'laning va yo'lga chiqing!\n"
        "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
    )

    try:
        await query.edit_message_text(
            xodim_confirm_text,
            parse_mode="HTML", disable_web_page_preview=True
        )
    except Exception:
        pass

    # Qolgan xodimlarga — "band qilindi" xabari
    for other_tg_id, other_msg_id in PENDING_NOTIFICATIONS.get(ariza_id, []):
        if other_tg_id == xodim.id:
            continue
        try:
            await context.bot.edit_message_text(
                chat_id=other_tg_id, message_id=other_msg_id,
                text=(
                    "❌ <b>Band qilindi</b>\n\n"
                    "Bu buyurtmani boshqa mutaxassis oldi.\n"
                    "Keyingi buyurtmalarda omad! 🍀"
                ),
                parse_mode="HTML"
            )
        except Exception:
            pass
    PENDING_NOTIFICATIONS.pop(ariza_id, None)

    # Admin ogohlantirishga xodim ma'lumotlari ham qo'shiladi
    await send_to_admin(
        context,
        f"🧑‍🔧 <b>Ariza #{ariza_id} qabul qilindi</b>\n\n"
        f"👤 <b>Xodim:</b> {xodim_ism}\n"
        f"📞 <b>Tel:</b> {xodim_tel}\n"
        f"📲 <b>Telegram:</b> {xodim_username}"
    )

    # ── Mijozga xodim ma'lumotlari bilan kreativ xabar ──────────────
    if client_tg_id:
        # Xodim turi aniqlaymiz
        if get_usta_info(xodim.id):
            xodim_turi   = "🔧 Usta"
            xodim_tavsif = "yo'lga chiqdi va tez orada yetib keladi"
        elif get_evak_info(xodim.id):
            xodim_turi   = "🚛 Evakuator"
            xodim_tavsif = "yo'lga chiqdi — mashinangizni xavfsiz olib ketadi"
        else:
            xodim_turi   = "📞 Operator"
            xodim_tavsif = "siz bilan tez orada bog'lanadi"
        try:
            await context.bot.send_message(
                chat_id=client_tg_id,
                text=(
                    "🚀 <b>Xushxabar!</b>\n\n"
                    f"<b>{xodim_ism}</b> ({xodim_turi}) sizning "
                    f"arizangizni qabul qildi!\n\n"
                    "╔══════════════════════════════╗\n"
                    "║  🧑‍🔧  MUTAXASSIS MA'LUMOTI   ║\n"
                    "╚══════════════════════════════╝\n\n"
                    f"👤 <b>Ism:</b> {xodim_ism}\n"
                    f"🎯 <b>Turi:</b> {xodim_turi}\n"
                    f"📞 <b>Telefon:</b> <code>{xodim_tel}</code>\n"
                    f"📲 <b>Telegram:</b> {xodim_username}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🚦 <b>{xodim_ism}</b> {xodim_tavsif}!\n\n"
                    + ("📞 Call center xodimimiz tez orada siz va "
                       f"{xodim_turi.split(' ')[-1].lower()} o'rtasidagi aloqani "
                       "mustahkamlaydi va jarayonni oxirigacha kuzatib boradi.\n\n"
                       if xodim_turi != "📞 Operator" else
                       "📞 U tez orada sizga qo'ng'iroq qiladi yoki shu bot orqali yozadi.\n\n") +
                    "🌟 <b>MOTUS — Harakatni davom ettiramiz!</b>"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Mijozga xabar yuborishda xato: {e}")

        # Relay muloqotni boshlash
        RELAY_CONNECTIONS[xodim.id] = {
            "partner": client_tg_id,
            "role": xodim_turi,
            "ariza_id": ariza_id
        }
        RELAY_CONNECTIONS[client_tg_id] = {
            "partner": xodim.id,
            "role": "🚗 Mijoz",
            "ariza_id": ariza_id
        }
        relay_info = (
            "💬 <b>Bot orqali xavfsiz muloqot boshlandi!</b>\n\n"
            "Endi siz bot orqali bevosita xabar yubora olasiz:\n"
            "✅ Matn xabar\n"
            "✅ Ovozli xabar 🎙\n"
            "✅ Rasm 📸\n\n"
            "⚠️ Shaxsiy ma'lumotlaringiz (telefon, manzil) "
            "faqat zarur bo'lganda ulashing.\n\n"
            "Muloqot tugagach 👇 tugmani bosing:"
        )
        try:
            await context.bot.send_message(
                chat_id=xodim.id,
                text=relay_info,
                parse_mode="HTML",
                reply_markup=relay_end_kb()
            )
            await context.bot.send_message(
                chat_id=client_tg_id,
                text=relay_info,
                parse_mode="HTML",
                reply_markup=relay_end_kb()
            )
        except Exception as relay_err:
            logger.error(f"Relay boshlashda xato: {relay_err}")

        # Usta bo'lsa — ariza holat tugmalarini ham yuboramiz
        if get_usta_info(xodim.id):
            try:
                await context.bot.send_message(
                    chat_id=xodim.id,
                    text=(
                        "📋 <b>Ish holati tugmalari:</b>\n\n"
                        "Ishni boshlaganingizda va yakunlaganingizda "
                        "quyidagi tugmalardan foydalaning 👇"
                    ),
                    parse_mode="HTML",
                    reply_markup=usta_ariza_kb(ariza_id)
                )
            except Exception as e:
                logger.error(f"Usta ariza tugmalari: {e}")

# ══════════════════════════════════════════════════════════
#  STATUS TUGMALARI (faqat admin)
# ══════════════════════════════════════════════════════════
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

    eski_status = get_ariza_status(ariza_id)
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

    # ── YANGI: ariza "bajarildi" deb birinchi marta belgilanganda —
    # mijozdan xizmatni baholashni so'raymiz
    if new_status == "bajarildi" and eski_status != "bajarildi":
        client_tg_id = get_ariza_client(ariza_id)
        if client_tg_id:
            try:
                await context.bot.send_message(
                    chat_id=client_tg_id,
                    text=(
                        "✅ <b>Xizmat bajarildi!</b>\n\n"
                        "Xizmat sifatini baholab bera olasizmi? 🙏\n"
                        "Fikringiz boshqa mijozlarga va ustalarimizga yordam beradi."
                    ),
                    parse_mode="HTML",
                    reply_markup=reyting_kb(ariza_id)
                )
            except Exception as e:
                logger.error(f"Baholash so'rovini yuborishda xato: {e}")

# ══════════════════════════════════════════════════════════
#  ⭐ REYTING (baholash)
# ══════════════════════════════════════════════════════════
async def rate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz yulduzcha tugmasini bosganda ishlaydi."""
    query = update.callback_query
    await query.answer()

    _, ariza_id_str, yulduz_str = query.data.split("_")
    ariza_id = int(ariza_id_str)
    yulduz = int(yulduz_str)

    ariza = get_ariza_for_rating(ariza_id)
    if not ariza:
        await query.edit_message_text("⚠️ Ariza topilmadi.")
        return
    if ariza["reyting"] is not None:
        await query.answer("Siz allaqachon baho bergansiz, rahmat!", show_alert=True)
        return

    set_ariza_reyting(ariza_id, yulduz)
    yulduzcha = "⭐" * yulduz

    try:
        await query.edit_message_text(
            f"✅ <b>Rahmat!</b>\n\nSiz {yulduzcha} ({yulduz}/5) baho berdingiz.\n"
            "Fikringiz biz uchun muhim! 🙏",
            parse_mode="HTML"
        )
    except Exception:
        pass

    usta_tg_id = ariza.get("usta_tg_id")
    if usta_tg_id:
        add_worker_rating(usta_tg_id, yulduz)
        ortacha, soni = get_worker_rating(usta_tg_id)
        await send_to_admin(
            context,
            f"⭐ <b>Yangi baho</b> — Ariza #{ariza_id}\n"
            f"👤 {ariza.get('usta_ism', '—')}: {yulduzcha} ({yulduz}/5)\n"
            f"📊 O'rtacha reyting: {ortacha} ({soni} ta baho)"
        )
        # Past baho bo'lsa, xodimning o'ziga ham bildirish (o'sish uchun)
        if yulduz <= 2:
            try:
                await context.bot.send_message(
                    chat_id=usta_tg_id,
                    text=(
                        f"📊 Ariza #{ariza_id} bo'yicha mijozdan {yulduzcha} ({yulduz}/5) "
                        "baho oldingiz.\nXizmat sifatini oshirishga harakat qiling! 💪"
                    ),
                    parse_mode="HTML"
                )
            except Exception:
                pass

# ══════════════════════════════════════════════════════════
#  /broadcast
# ══════════════════════════════════════════════════════════
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await asyncio.sleep(0.05)
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
    (total_users, total_arizalar, by_role, yangi, jarayonda, bajarildi,
     faol_7kun, bugun, faol_ustalar, faol_cc, faol_evak,
     tasdiqlanmagan_usta, tasdiqlanmagan_cc, tasdiqlanmagan_evak) = get_stats()
    role_lines = "\n".join(f"  • {r}: <b>{c}</b>" for r, c in by_role.items()) or "  • Hozircha yo'q"
    await update.message.reply_text(
        "📊 <b>MOTUS Bot — Statistika</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👥 <b>Jami ro'yxatdan o'tganlar:</b> {total_users}\n"
        f"🟢 <b>Bugun faol:</b> {bugun}\n"
        f"📅 <b>Oxirgi 7 kunda faol:</b> {faol_7kun}\n\n"
        f"🔧 <b>Tasdiqlangan ustalar:</b> {faol_ustalar}\n"
        f"📞 <b>Tasdiqlangan CC xodimlar:</b> {faol_cc}\n"
        f"🚛 <b>Tasdiqlangan evakuatorlar:</b> {faol_evak}\n\n"
        f"⏳ <b>Tasdiqlanmagan:</b>\n"
        f"  • Ustalar: <b>{tasdiqlanmagan_usta}</b>\n"
        f"  • CC: <b>{tasdiqlanmagan_cc}</b>\n"
        f"  • Evakuator: <b>{tasdiqlanmagan_evak}</b>\n\n"
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
async def admin_free_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin botga yozgan ISTALGAN xabar (matn/ovoz/rasm) — hamma foydalanuvchiga."""
    logger.info(f"admin_free_broadcast chaqirildi: yuboruvchi_id={update.effective_user.id}, ADMIN_CHAT_ID={ADMIN_CHAT_ID}")
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    # Relay yoki boshqa jarayonda bo'lsa — o'tkazib yuborish
    if PENDING_REJECT.get(ADMIN_CHAT_ID):
        return

    msg      = update.message
    user_ids = get_all_user_ids()
    sent = failed = 0

    status_msg = await msg.reply_text(f"📡 Yuborilmoqda... 0/{len(user_ids)}")

    for uid in user_ids:
        if uid == ADMIN_CHAT_ID:
            continue
        try:
            if msg.text:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 <b>MOTUS xabari:</b>\n\n{msg.text}",
                    parse_mode="HTML"
                )
            elif msg.voice:
                await context.bot.send_voice(
                    chat_id=uid,
                    voice=msg.voice.file_id,
                    caption="📢 MOTUS ovozli xabari"
                )
            elif msg.photo:
                await context.bot.send_photo(
                    chat_id=uid,
                    photo=msg.photo[-1].file_id,
                    caption="📢 MOTUS" + (f"\n{msg.caption}" if msg.caption else "")
                )
            sent += 1
        except Exception:
            failed += 1
        if (sent + failed) % 30 == 0:
            try:
                await status_msg.edit_text(f"📡 Yuborilmoqda... {sent+failed}/{len(user_ids)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)

    await status_msg.edit_text(
        f"✅ <b>Yuborildi!</b>\n\n"
        f"📨 Muvaffaqiyatli: <b>{sent}</b>\n"
        f"❌ Xato: <b>{failed}</b>",
        parse_mode="HTML"
    )

async def unknown_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤔 /start — boshlash | /cancel — bekor qilish"
    )

    # ══════════════════════════════════════════════════════════
#  UMUMIY XATO USHLAGICH
# ══════════════════════════════════════════════════════════
async def global_error_handler(update, context: ContextTypes.DEFAULT_TYPE):
    """Botning istalgan joyida kutilmagan xato chiqsa shu yerga tushadi.
    Bot yiqilib qolmaydi, xato faqat logga yoziladi va (agar imkoni bo'lsa)
    adminga ham xabar beriladi."""
    logger.error("Kutilmagan xato:", exc_info=context.error)
    try:
        if ADMIN_CHAT_ID:
            xabar = (
                "⚠️ <b>Botda kutilmagan xato yuz berdi</b>\n\n"
                f"<code>{context.error}</code>"
            )
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID, text=xabar, parse_mode="HTML"
            )
    except Exception:
        pass

# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════
def main():
    init_db()
    from telegram.ext import PicklePersistence
    persistence = PicklePersistence(filepath=os.getenv("MOTUS_PERSISTENCE_PATH", "motus_persistence.pkl"))
    app = (
        Application.builder().token(BOT_TOKEN)
        .persistence(persistence)
        .read_timeout(30).write_timeout(30)
        .connect_timeout(30).pool_timeout(30)
        .build()
    )

    mij_profil_menu_handlers = [
        MessageHandler(
            filters.TEXT & filters.Regex("^🆘 Muammo bor") & ~filters.COMMAND,
            muammo_handler
        ),
        MessageHandler(filters.LOCATION, mijoz_lokatsiya_yangilash),
        CallbackQueryHandler(home, pattern="^home$"),
    ]

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
            # Mijoz profil menyusi
            MIJ_PROFIL_MENU: mij_profil_menu_handlers,
            # Ariza oqimi
            ARIZA_MUAMMO: [
                MessageHandler(NORMAL_TEXT, ariza_muammo),
                MessageHandler(filters.VOICE, ariza_muammo),
            ],
            ARIZA_XIZMAT_TANLASH: [
                CallbackQueryHandler(ariza_xizmat_tanlash, pattern="^xizmat_"),
            ],
            ARIZA_SOHA_TANLASH: [
                CallbackQueryHandler(ariza_soha_tanlash, pattern="^soha_"),
            ],
            ARIZA_LOKATSIYA: [
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

    ariza_direct_conv = ConversationHandler(
    entry_points=[MessageHandler(
        filters.TEXT & filters.Regex("^🆘 Muammo bor") & ~filters.COMMAND,
        muammo_handler
    )],
    states={
        ARIZA_MUAMMO: [
            MessageHandler(NORMAL_TEXT, ariza_muammo),
            MessageHandler(filters.VOICE, ariza_muammo),
        ],
        ARIZA_XIZMAT_TANLASH: [
            CallbackQueryHandler(ariza_xizmat_tanlash, pattern="^xizmat_"),
        ],
        ARIZA_SOHA_TANLASH: [
            CallbackQueryHandler(ariza_soha_tanlash, pattern="^soha_"),
        ],
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
    # Admin rad etish + sabab conversation
    admin_reject_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_approve_callback, pattern="^(appr|rejt)_")],
        states={
            ADMIN_REJECT_REASON: [
                MessageHandler(NORMAL_TEXT, admin_reject_finish),
                CommandHandler("skip", admin_reject_finish),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_chat=False,
        per_user=True,
    )
    app.add_handler(admin_reject_conv)
    app.add_handler(ariza_direct_conv)
    app.add_handler(CallbackQueryHandler(lambda u, c: u.callback_query.answer(), pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(status_callback,        pattern="^st_"))
    app.add_handler(CallbackQueryHandler(xodim_accept_callback,  pattern="^acc_"))
    app.add_handler(CallbackQueryHandler(rate_callback,          pattern="^rate_"))
    app.add_handler(CallbackQueryHandler(usta_ariza_callback,           pattern="^ust_"))
    app.add_handler(CallbackQueryHandler(admin_master_confirm_callback, pattern="^mconf_"))
    app.add_handler(MessageHandler(
        usta_izoh_active_filter & ~filters.COMMAND,
        usta_izoh_handler
    ))
    # ── YANGI: Admin tasdiqlash/rad etish callback handleri ──
    
    # Admin istalgan xabar → hamma foydalanuvchiga
    app.add_handler(MessageHandler(
        filters.Chat(ADMIN_CHAT_ID) & ~filters.COMMAND,
        admin_free_broadcast
    ))
    # Relay muloqot handlerlari
    app.add_handler(CallbackQueryHandler(relay_end_callback, pattern="^relay_end$"))
    app.add_handler(MessageHandler(
        relay_active_filter & ~filters.COMMAND,
        relay_handler
    ))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_error_handler(global_error_handler)

    logger.info("🚗 MOTUS Bot v5 ishga tushdi!")
    app.run_polling(drop_pending_updates=True, poll_interval=0.5, timeout=10)

if __name__ == "__main__":
    main()
