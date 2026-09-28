"""
🎰 LUCKY CASINO BOT
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timedelta
from contextlib import suppress

import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import CommandStart, Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, LabeledPrice, PreCheckoutQuery,
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand,
)

# ==================== ВСТАВЬ СВОИ ЗНАЧЕНИЯ ====================
BOT_TOKEN = "8518609573:AAFCf7TQNoof4oP9iJHt0tQ2LixJj8TiL0E"
ADMIN_IDS = [5572404492]
# =============================================================

DB_PATH = "casino.db"

MIN_BET = 10
MAX_BET = 10000
COMMISSION = 0.05
REF_BONUS_PCT = 0.10
MIN_WITHDRAW = 100
DAILY_BONUS = 10

CASES = {
    "bronze": {"name": "🥉 Бронза", "price": 50, "prizes": [
        ("🧸 Мишка", 40, 20), ("❤️ Сердце", 30, 40), ("🌹 Роза", 15, 70),
        ("💎 Алмаз", 10, 130), ("🏆 Кубок", 4, 260), ("🖼 NFT", 1, 600),
    ]},
    "silver": {"name": "🥈 Серебро", "price": 150, "prizes": [
        ("🧸 Мишка", 35, 60), ("❤️ Сердце", 28, 120), ("🌹 Роза", 20, 220),
        ("💎 Алмаз", 12, 380), ("🏆 Кубок", 4, 800), ("👑 Корона", 0.9, 2500),
        ("🖼 NFT", 0.1, 6000),
    ]},
    "gold":   {"name": "🥇 Золото", "price": 500, "prizes": [
        ("❤️ Сердце", 35, 250), ("🌹 Роза", 28, 500), ("💎 Алмаз", 22, 900),
        ("🏆 Кубок", 10, 2000), ("👑 Корона", 4, 6000), ("🚀 Ракета", 0.9, 15000),
        ("🖼 NFT", 0.1, 40000),
    ]},
    "legend": {"name": "💠 Легенда", "price": 1500, "prizes": [
        ("💎 Алмаз", 40, 1500), ("🏆 Кубок", 30, 3500), ("👑 Корона", 18, 8000),
        ("🚀 Ракета", 9, 25000), ("🌟 Звезда", 2.4, 70000), ("🖼 NFT", 0.6, 200000),
    ]},
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("casino")

# ==================== БД ====================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                balance INTEGER DEFAULT 0,
                total_topup INTEGER DEFAULT 0,
                total_won INTEGER DEFAULT 0,
                referrer_id INTEGER,
                ref_earned INTEGER DEFAULT 0,
                last_bonus TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, amount INTEGER, type TEXT,
                description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rounds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game TEXT, pool INTEGER, winner_id INTEGER, winner_name TEXT,
                payout INTEGER, commission INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER, amount INTEGER, requisites TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()

async def get_user(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)) as c:
            return await c.fetchone()

async def upsert_user(user_id, username, first_name, ref=None):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (user_id,))
        ex = await cur.fetchone()
        if ex:
            await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?",
                             (username or "", first_name or "", user_id))
        else:
            await db.execute(
                "INSERT INTO users (user_id, username, first_name, referrer_id) VALUES (?,?,?,?)",
                (user_id, username or "", first_name or "", ref),
            )
        await db.commit()

async def add_balance(user_id, amount, ttype, desc=""):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (?,?,?,?)",
            (user_id, amount, ttype, desc),
        )
        await db.commit()

async def atomic_bet(user_id, amount):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE users SET balance = balance - ? WHERE user_id = ? AND balance >= ?",
            (amount, user_id, amount),
        )
        await db.commit()
        return cur.rowcount > 0

async def save_round(game, pool, winner_id, winner_name, payout, commission):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO rounds (game, pool, winner_id, winner_name, payout, commission) VALUES (?,?,?,?,?,?)",
            (game, pool, winner_id, winner_name, payout, commission),
        )
        await db.commit()

async def get_top(limit=10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT user_id, username, first_name, total_won, balance FROM users ORDER BY total_won DESC LIMIT ?",
            (limit,),
        ) as c:
            return await c.fetchall()

async def get_referral_stats(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await db.execute("SELECT COUNT(*) FROM users WHERE referrer_id = ?", (user_id,))
        count = (await c1.fetchone())[0]
        c2 = await db.execute("SELECT ref_earned FROM users WHERE user_id = ?", (user_id,))
        r = await c2.fetchone()
        return {"count": count or 0, "earned": (r[0] if r else 0) or 0}

async def try_daily(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,)) as c:
            row = await c.fetchone()
        if not row:
            return False, "Профиль не найден", 0
        last = row["last_bonus"]
        if last:
            last_dt = datetime.fromisoformat(last) if isinstance(last, str) else last
            if datetime.utcnow() - last_dt < timedelta(hours=24):
                left = timedelta(hours=24) - (datetime.utcnow() - last_dt)
                h = left.seconds // 3600
                m = (left.seconds % 3600) // 60
                return False, f"⏳ Бонус через {h}ч {m}м", 0
        await db.execute(
            "UPDATE users SET balance = balance + ?, last_bonus = ? WHERE user_id = ?",
            (DAILY_BONUS, datetime.utcnow().isoformat(), user_id),
        )
        await db.execute(
            "INSERT INTO transactions (user_id, amount, type, description) VALUES (?,?,?,?)",
            (user_id, DAILY_BONUS, "daily", "Ежедневный бонус"),
        )
        await db.commit()
        return True, "Бонус получен!", DAILY_BONUS

async def create_withdrawal(user_id, amount, requisites):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO withdrawals (user_id, amount, requisites) VALUES (?,?,?)",
            (user_id, amount, requisites),
        )
        await db.commit()
        return cur.lastrowid

async def get_pending_wd():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdrawals WHERE status='pending' ORDER BY id") as c:
            return await c.fetchall()

async def set_wd_status(wid, status):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE withdrawals SET status=? WHERE id=?", (status, wid))
        await db.commit()

async def get_wd(wid):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)) as c:
            return await c.fetchone()

def progress_bar(pct, length=10):
    filled = int(pct / 100 * length)
    return "█" * filled + "░" * (length - filled)

# ==================== FSM ====================
class AdminSG(StatesGroup):
    waiting_user = State()
    waiting_amount = State()

class WithdrawSG(StatesGroup):
    waiting_amount = State()
    waiting_req = State()

# ==================== INIT ====================
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()

lobbies = {"wheel": {}, "square": {}}
busy = set()
mines_games = {}
BJ_GAMES = {}

# ==================== START ====================
@router.message(CommandStart())
async def cmd_start(message: Message):
    ref = None
    if message.text and len(message.text.split()) > 1:
        arg = message.text.split()[1]
        if arg.startswith("ref_") and arg[4:].isdigit():
            ref = int(arg[4:])
            if ref == message.from_user.id:
                ref = None
    await upsert_user(message.from_user.id, message.from_user.username,
                     message.from_user.full_name, ref)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="menu_games"),
         InlineKeyboardButton(text="💰 Баланс", callback_data="menu_balance")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="menu_top")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="menu_daily"),
         InlineKeyboardButton(text="👥 Рефералка", callback_data="menu_ref")],
        [InlineKeyboardButton(text="📜 Все команды", callback_data="menu_help")],
    ])
    await message.answer(
        f"🎰 <b>LUCKY CASINO</b>\n\n"
        f"✨ Привет, <b>{message.from_user.first_name}</b>!\n"
        f"🎮 12 игр · 💰 PvP-режимы · 🎁 Ежедневный бонус\n\n"
        f"🎯 Играть можно <b>русскими словами без слэша</b>:\n"
        f"<code>слоты 10</code> · <code>кубик 50</code> · <code>футбол 100</code> · <code>мины 25</code>\n\n"
        f"Или смотри /help",
        reply_markup=kb,
    )

@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 <b>СПИСОК КОМАНД</b>\n\n"
        "<b>🎮 Игры (пиши без слэша):</b>\n"
        "<code>слоты 10</code> · <code>кубик 50</code> · <code>дротики 30</code>\n"
        "<code>баскет 50</code> · <code>футбол 50</code> · <code>боулинг 50</code>\n"
        "<code>монетка 10</code> · <code>мины 100</code> · <code>блэкджек 50</code>\n"
        "<code>колесо 50</code> · <code>квадрат 50</code> (PvP, только в группе)\n"
        "<code>кейсы</code>\n\n"
        "<b>💵 Слэш-команды тоже работают:</b>\n"
        "/slots /dice /darts /basket /football /bowl\n"
        "/coin /mines /bj /wheel /square /cases\n\n"
        "<b>💰 Экономика:</b>\n"
        "/balance — Баланс\n"
        "/topup — Пополнить ⭐\n"
        "/withdraw — Вывод\n"
        "/daily — Ежедневный бонус\n"
        "/top — Топ игроков\n"
        "/profile — Профиль\n"
        "/ref — Реф-ссылка\n\n"
        f"⚡ Мин. ставка: <b>{MIN_BET}⭐</b> · Макс: <b>{MAX_BET:,}⭐</b>"
    )

# ==================== МЕНЮ ====================
@router.callback_query(F.data == "menu_games")
async def cb_menu_games(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Колесо", callback_data="info_wheel"),
         InlineKeyboardButton(text="🟦 Квадрат", callback_data="info_square")],
        [InlineKeyboardButton(text="💣 Мины", callback_data="info_mines"),
         InlineKeyboardButton(text="🎲 Кубик", callback_data="info_dice")],
        [InlineKeyboardButton(text="🎯 Дротики", callback_data="info_darts"),
         InlineKeyboardButton(text="🎰 Слоты", callback_data="info_slots")],
        [InlineKeyboardButton(text="🪙 Монетка", callback_data="info_coin"),
         InlineKeyboardButton(text="🃏 Блэкджек", callback_data="info_bj")],
        [InlineKeyboardButton(text="🏀 Баскетбол", callback_data="info_basket"),
         InlineKeyboardButton(text="⚽ Футбол", callback_data="info_football")],
        [InlineKeyboardButton(text="🎳 Боулинг", callback_data="info_bowl"),
         InlineKeyboardButton(text="🎁 Кейсы", callback_data="menu_cases")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
    ])
    await cb.message.edit_text("🎮 <b>ИГРЫ</b>\n\nВыбери игру:", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("info_"))
async def cb_info(cb: CallbackQuery):
    game = cb.data.split("_", 1)[1]
    infos = {
        "wheel":    ("🎡 Колесо", "PvP. Все ставки в банк. Победитель забирает 95%.\n\n<b>В чате напиши:</b> <code>колесо 50</code>"),
        "square":   ("🟦 Квадрат", "PvP. Та же логика.\n\n<b>В чате напиши:</b> <code>квадрат 50</code>"),
        "mines":    ("💣 Мины", "Открывай клетки 5×5.\n\n<b>В чате напиши:</b> <code>мины 50</code>"),
        "dice":     ("🎲 Кубик", "Больше 3 — победа x2.\n\n<b>В чате напиши:</b> <code>кубик 50</code>"),
        "darts":    ("🎯 Дротики", "x3 при удаче.\n\n<b>В чате напиши:</b> <code>дротики 50</code>"),
        "slots":    ("🎰 Слоты", "🍋/🍇 x1.5, 🎰 x2, 7️⃣ x5.\n\n<b>В чате напиши:</b> <code>слоты 50</code>"),
        "coin":     ("🪙 Монетка", "50/50, победа x2.\n\n<b>В чате напиши:</b> <code>монетка 50</code>"),
        "bj":       ("🃏 Блэкджек", "Набери 21.\n\n<b>В чате напиши:</b> <code>блэкджек 50</code>"),
        "basket":   ("🏀 Баскетбол", "x2 при попадании.\n\n<b>В чате напиши:</b> <code>баскет 50</code>"),
        "football": ("⚽ Футбол", "x2 при голе.\n\n<b>В чате напиши:</b> <code>футбол 50</code>"),
        "bowl":     ("🎳 Боулинг", "x3 при страйке.\n\n<b>В чате напиши:</b> <code>боулинг 50</code>"),
    }
    title, desc = infos.get(game, ("?", "?"))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_games")],
    ])
    await cb.message.edit_text(f"<b>{title}</b>\n\n{desc}", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "menu_back")
async def cb_menu_back(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎮 Игры", callback_data="menu_games"),
         InlineKeyboardButton(text="💰 Баланс", callback_data="menu_balance")],
        [InlineKeyboardButton(text="👤 Профиль", callback_data="menu_profile"),
         InlineKeyboardButton(text="🏆 Топ", callback_data="menu_top")],
        [InlineKeyboardButton(text="🎁 Бонус", callback_data="menu_daily"),
         InlineKeyboardButton(text="👥 Рефералка", callback_data="menu_ref")],
        [InlineKeyboardButton(text="📜 Все команды", callback_data="menu_help")],
    ])
    await cb.message.edit_text("🎰 <b>LUCKY CASINO</b>", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "menu_help")
async def cb_menu_help(cb: CallbackQuery):
    await cb.message.edit_text(
        "📖 <b>Все команды в /help</b>\n\n"
        "🎮 <code>слоты 10</code> · <code>кубик 50</code> · <code>дротики 50</code>\n"
        "🏀 <code>баскет 50</code> · <code>футбол 50</code> · <code>боулинг 50</code>\n"
        "🪙 <code>монетка 10</code> · <code>мины 100</code> · <code>блэкджек 50</code>\n"
        "🎡 <code>колесо 50</code> · <code>квадрат 50</code>\n"
        "💰 /balance /topup /withdraw /daily\n"
        "🏆 /top /profile /ref",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
        ]),
    )
    await cb.answer()

@router.callback_query(F.data == "menu_balance")
async def cb_menu_balance(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    bal = user["balance"] if user else 0
    pct = min(100, int(bal / 1000 * 100))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Пополнить", callback_data="topup_open")],
        [InlineKeyboardButton(text="💸 Вывести", callback_data="withdraw_open")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
    ])
    await cb.message.edit_text(
        f"💰 <b>БАЛАНС</b>\n\n"
        f"💎 Сумма: <b>{bal:,}⭐</b>\n"
        f"📊 [{progress_bar(pct)}] {pct}%\n\n"
        f"⚡ Мин. вывод: <b>{MIN_WITHDRAW}⭐</b>",
        reply_markup=kb,
    )
    await cb.answer()

@router.callback_query(F.data == "menu_profile")
async def cb_menu_profile(cb: CallbackQuery):
    user = await get_user(cb.from_user.id)
    ref = await get_referral_stats(cb.from_user.id)
    if not user:
        await upsert_user(cb.from_user.id, cb.from_user.username, cb.from_user.full_name)
        user = await get_user(cb.from_user.id)
    name = f"@{user['username']}" if user["username"] else user["first_name"]
    await cb.message.edit_text(
        f"👤 <b>ПРОФИЛЬ</b>\n\n"
        f"🆔 <code>{user['user_id']}</code>\n"
        f"📛 {name}\n"
        f"💰 Баланс: <b>{user['balance']:,}⭐</b>\n"
        f"📈 Пополнено: {user['total_topup']:,}⭐\n"
        f"🏆 Выиграно: {user['total_won']:,}⭐\n"
        f"👥 Рефералов: {ref['count']}\n"
        f"💵 С реф: {ref['earned']:,}⭐",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
        ]),
    )
    await cb.answer()

@router.callback_query(F.data == "menu_top")
async def cb_menu_top(cb: CallbackQuery):
    top = await get_top(10)
    medals = ["🥇", "🥈", "🥉"] + ["🎖"] * 7
    lines = []
    for i, u in enumerate(top):
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["user_id"]))
        lines.append(f"{medals[i]} <b>{i+1}.</b> {name} — <b>{u['total_won']:,}⭐</b>")
    text = "🏆 <b>ТОП-10</b>\n\n" + ("\n".join(lines) if lines else "Пусто")
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
    ]))
    await cb.answer()

@router.callback_query(F.data == "menu_daily")
async def cb_menu_daily(cb: CallbackQuery):
    ok, msg, amt = await try_daily(cb.from_user.id)
    if ok:
        await cb.answer(f"🎁 +{amt}⭐", show_alert=True)
    else:
        await cb.answer(msg, show_alert=True)

@router.callback_query(F.data == "menu_ref")
async def cb_menu_ref(cb: CallbackQuery):
    ref = await get_referral_stats(cb.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{cb.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Поделиться",
                              url=f"https://t.me/share/url?url={link}&text=Заходи в казино!")],
        [InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")],
    ])
    await cb.message.edit_text(
        f"👥 <b>РЕФЕРАЛКА</b>\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"👤 Приглашено: <b>{ref['count']}</b>\n"
        f"💵 Заработано: <b>{ref['earned']:,}⭐</b>",
        reply_markup=kb,
    )
    await cb.answer()

# ==================== БАЗОВЫЕ КОМАНДЫ ====================
@router.message(Command("balance"))
async def cmd_balance(message: Message):
    user = await get_user(message.from_user.id)
    bal = user["balance"] if user else 0
    pct = min(100, int(bal / 1000 * 100))
    await message.answer(f"💰 <b>Баланс:</b> <b>{bal:,}⭐</b>\n[{progress_bar(pct)}] {pct}%")

@router.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await get_user(message.from_user.id)
    ref = await get_referral_stats(message.from_user.id)
    if not user:
        await upsert_user(message.from_user.id, message.from_user.username, message.from_user.full_name)
        user = await get_user(message.from_user.id)
    name = f"@{user['username']}" if user["username"] else user["first_name"]
    await message.answer(
        f"👤 <b>{name}</b>\n"
        f"🆔 <code>{user['user_id']}</code>\n\n"
        f"💰 {user['balance']:,}⭐ · 🏆 {user['total_won']:,}⭐\n"
        f"👥 Рефералов: {ref['count']} · 💵 {ref['earned']:,}⭐"
    )

@router.message(Command("top"))
async def cmd_top(message: Message):
    top = await get_top(10)
    medals = ["🥇", "🥈", "🥉"] + ["🎖"] * 7
    lines = []
    for i, u in enumerate(top):
        name = f"@{u['username']}" if u["username"] else (u["first_name"] or str(u["user_id"]))
        lines.append(f"{medals[i]} <b>{i+1}.</b> {name} — {u['total_won']:,}⭐")
    await message.answer("🏆 <b>ТОП-10</b>\n\n" + ("\n".join(lines) or "Пусто"))

@router.message(Command("daily"))
async def cmd_daily(message: Message):
    ok, msg, amt = await try_daily(message.from_user.id)
    if ok:
        await message.answer(f"🎁 <b>+{amt}⭐</b> — ежедневный бонус!")
    else:
        await message.answer(msg)

@router.message(Command("ref"))
async def cmd_ref(message: Message):
    ref = await get_referral_stats(message.from_user.id)
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{message.from_user.id}"
    await message.answer(
        f"👥 <b>Рефералка</b>\n\n"
        f"🔗 <code>{link}</code>\n\n"
        f"Приглашено: {ref['count']} · Заработано: {ref['earned']:,}⭐"
    )

# ==================== ПОПОЛНЕНИЕ ====================
@router.message(Command("topup"))
async def cmd_topup(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50⭐", callback_data="tp_50"),
         InlineKeyboardButton(text="100⭐", callback_data="tp_100"),
         InlineKeyboardButton(text="250⭐", callback_data="tp_250")],
        [InlineKeyboardButton(text="500⭐", callback_data="tp_500"),
         InlineKeyboardButton(text="1000⭐", callback_data="tp_1000"),
         InlineKeyboardButton(text="5000⭐", callback_data="tp_5000")],
    ])
    await message.answer("⭐ <b>Пополнение</b>\n\nВыбери сумму:", reply_markup=kb)

@router.callback_query(F.data == "topup_open")
async def cb_topup_open(cb: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="50⭐", callback_data="tp_50"),
         InlineKeyboardButton(text="100⭐", callback_data="tp_100"),
         InlineKeyboardButton(text="250⭐", callback_data="tp_250")],
        [InlineKeyboardButton(text="500⭐", callback_data="tp_500"),
         InlineKeyboardButton(text="1000⭐", callback_data="tp_1000"),
         InlineKeyboardButton(text="5000⭐", callback_data="tp_5000")],
    ])
    await cb.message.edit_text("⭐ <b>Пополнение</b>", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data.startswith("tp_"))
async def cb_topup(cb: CallbackQuery):
    amount = int(cb.data.split("_")[1])
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=f"Пополнение {amount}⭐",
        description=f"Зачислит {amount} звёзд",
        payload=f"topup:{amount}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=f"{amount}⭐", amount=amount)],
    )
    await cb.answer("Счёт отправлен в личку!", show_alert=True)

@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)

@router.message(F.successful_payment)
async def on_payment(message: Message):
    amount = message.successful_payment.total_amount
    uid = message.from_user.id
    await add_balance(uid, amount, "topup", f"Пополнение {amount}⭐")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET total_topup = total_topup + ? WHERE user_id = ?", (amount, uid))
        await db.commit()
        async with db.execute("SELECT referrer_id FROM users WHERE user_id = ?", (uid,)) as c:
            row = await c.fetchone()
        if row and row[0]:
            bonus = int(amount * REF_BONUS_PCT)
            if bonus > 0:
                await db.execute(
                    "UPDATE users SET balance = balance + ?, ref_earned = ref_earned + ? WHERE user_id = ?",
                    (bonus, bonus, row[0]),
                )
                await db.execute(
                    "INSERT INTO transactions (user_id, amount, type, description) VALUES (?,?,?,?)",
                    (row[0], bonus, "ref_bonus", f"Бонус за друга {uid}"),
                )
                await db.commit()
                with suppress(Exception):
                    await bot.send_message(row[0], f"🎁 <b>+{bonus}⭐</b> — бонус за друга!")
    await message.answer(f"✅ Пополнено <b>+{amount}⭐</b>")

# ==================== ВЫВОД ====================
@router.message(Command("withdraw"))
async def cmd_withdraw(message: Message, state: FSMContext):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await state.set_state(WithdrawSG.waiting_amount)
        await message.answer(f"💸 <b>Вывод</b>\n\nМинимум {MIN_WITHDRAW}⭐\n\nВведи сумму:")
        return
    await _start_withdraw(message, state, int(parts[1]))

@router.callback_query(F.data == "withdraw_open")
async def cb_withdraw_open(cb: CallbackQuery, state: FSMContext):
    await state.set_state(WithdrawSG.waiting_amount)
    await cb.message.edit_text(f"💸 <b>Вывод</b>\n\nМинимум {MIN_WITHDRAW}⭐\n\nВведи сумму:")
    await cb.answer()

@router.message(WithdrawSG.waiting_amount)
async def wd_amount(message: Message, state: FSMContext):
    if not message.text or not message.text.isdigit():
        return await message.answer("Введи число")
    await _start_withdraw(message, state, int(message.text))

async def _start_withdraw(message: Message, state: FSMContext, amount: int):
    if amount < MIN_WITHDRAW:
        return await message.answer(f"❌ Минимум {MIN_WITHDRAW}⭐")
    user = await get_user(message.from_user.id)
    if not user or user["balance"] < amount:
        return await message.answer("❌ Недостаточно средств")
    await state.update_data(wd_amount=amount)
    await state.set_state(WithdrawSG.waiting_req)
    await message.answer("📩 Отправь <b>реквизиты</b>:")

@router.message(WithdrawSG.waiting_req)
async def wd_req(message: Message, state: FSMContext):
    data = await state.get_data()
    amount = data["wd_amount"]
    req = message.text.strip()
    if not await atomic_bet(message.from_user.id, amount):
        await state.clear()
        return await message.answer("❌ Недостаточно средств")
    wid = await create_withdrawal(message.from_user.id, amount, req)
    await state.clear()
    await message.answer(f"✅ <b>Заявка №{wid}</b>\n💰 {amount:,}⭐\n📩 {req}")
    for admin in ADMIN_IDS:
        with suppress(Exception):
            await bot.send_message(
                admin,
                f"💸 <b>Заявка №{wid}</b>\n👤 <code>{message.from_user.id}</code>\n"
                f"💰 {amount:,}⭐\n📩 {req}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"✅ #{wid}", callback_data=f"wd_done_{wid}")]
                ]),
            )

@router.callback_query(F.data.startswith("wd_done_"))
async def cb_wd_done(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    wid = int(cb.data.split("_")[-1])
    w = await get_wd(wid)
    if not w or w["status"] != "pending":
        return await cb.answer("Уже обработана", show_alert=True)
    await set_wd_status(wid, "done")
    await cb.message.edit_text(cb.message.text + "\n\n✅ <b>Выполнено</b>")
    with suppress(Exception):
        await bot.send_message(w["user_id"], f"✅ Заявка №{wid} на {w['amount']:,}⭐ выполнена!")
    await cb.answer("Готово!")

# ==================== КЕЙСЫ ====================
@router.message(Command("cases"))
async def cmd_cases(message: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{c['name']} — {c['price']}⭐", callback_data=f"case_{k}")]
        for k, c in CASES.items()
    ])
    await message.answer("🎁 <b>КЕЙСЫ</b>", reply_markup=kb)

@router.message(F.text.lower() == "кейсы")
async def cmd_cases_ru(message: Message):
    await cmd_cases(message)

@router.callback_query(F.data.startswith("case_"))
async def cb_case(cb: CallbackQuery):
    key = cb.data.split("_", 1)[1]
    if key not in CASES:
        return await cb.answer("Неизвестный кейс", show_alert=True)
    case = CASES[key]
    uid = cb.from_user.id
    if uid in busy:
        return await cb.answer("⏳ Подожди...")
    user = await get_user(uid)
    if not user or user["balance"] < case["price"]:
        return await cb.answer("❌ Недостаточно средств", show_alert=True)
    await cb.answer()
    busy.add(uid)
    try:
        await add_balance(uid, -case["price"], "case_buy", case["name"])
        msg = await cb.message.answer(f"{case['name']} <b>открывается...</b>")
        frames = ["🎁", "🎉", "✨", "💫", "🎊", "⭐", "🌟"]
        for i in range(8):
            await asyncio.sleep(0.3)
            with suppress(Exception):
                await msg.edit_text(f"{frames[i % len(frames)]} <b>Крутим...</b>")
        roll = random.uniform(0, 100)
        cum = 0
        prize, reward = case["prizes"][0][0], case["prizes"][0][2]
        for name, chance, rew in case["prizes"]:
            cum += chance
            if roll <= cum:
                prize, reward = name, rew
                break
        await add_balance(uid, reward, "case_win", f"{case['name']}: {prize}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (reward, uid))
            await db.commit()
        u = await get_user(uid)
        await msg.edit_text(
            f"🎉 <b>{case['name']} открыт!</b>\n\n"
            f"💎 Выпало: <b>{prize}</b>\n"
            f"💰 Награда: <b>+{reward:,}⭐</b>\n"
            f"💼 Баланс: <b>{u['balance']:,}⭐</b>"
        )
    finally:
        busy.discard(uid)

# ==================== PvP ====================
@router.message(Command("wheel"))
async def cmd_wheel(message: Message):
    await _pvp_join(message, "wheel")

@router.message(Command("square"))
async def cmd_square(message: Message):
    await _pvp_join(message, "square")

async def _pvp_join(message: Message, game: str):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer(f"❌ Напиши: <code>{'колесо' if game == 'wheel' else 'квадрат'} СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    if amount > MAX_BET:
        return await message.answer(f"❌ Максимум {MAX_BET:,}⭐")
    chat_id = message.chat.id
    uid = message.from_user.id
    lob = lobbies[game].get(chat_id)
    if lob and lob.get("running"):
        return await message.answer("⏳ Раунд уже идёт")
    if not lob:
        lob = {"players": [], "task": None, "running": False}
        lobbies[game][chat_id] = lob
    if any(p["user_id"] == uid for p in lob["players"]):
        return await message.answer("❌ Ты уже участвуешь")
    if len(lob["players"]) >= 10:
        return await message.answer("❌ Лобби заполнено")
    if not await atomic_bet(uid, amount):
        return await message.answer("❌ Недостаточно средств")

    colors = ["🔴", "🟠", "🟡", "🟢", "🔵", "🟣", "🟤", "⚫", "⚪", "🟩"]
    lob["players"].append({
        "user_id": uid,
        "username": message.from_user.username or message.from_user.first_name,
        "stake": amount,
        "color": colors[len(lob["players"]) % 10],
    })
    emoji = "🎡" if game == "wheel" else "🟦"
    total = sum(p["stake"] for p in lob["players"])
    lines = [f"{p['color']} @{p['username']} — <b>{p['stake']:,}⭐</b> ({p['stake']*100//total}%)"
             for p in lob["players"]]
    text = (
        f"{emoji} <b>{game.upper()}</b> — набор!\n\n"
        f"👥 {len(lob['players'])}/10:\n" + "\n".join(lines) +
        f"\n\n💰 Банк: <b>{total:,}⭐</b>\n⏱️ Старт через 30 сек"
    )
    if lob.get("msg_id"):
        with suppress(Exception):
            await bot.edit_message_text(text, chat_id=chat_id, message_id=lob["msg_id"])
    else:
        msg = await message.answer(text)
        lob["msg_id"] = msg.message_id
    if not lob["task"] or lob["task"].done():
        lob["task"] = asyncio.create_task(_pvp_round(chat_id, game))

async def _pvp_round(chat_id: int, game: str):
    lob = lobbies[game][chat_id]
    await asyncio.sleep(30)
    players = lob["players"]
    if len(players) < 2:
        for p in players:
            await add_balance(p["user_id"], p["stake"], "refund", "Возврат")
        lob["players"] = []
        lob["msg_id"] = None
        with suppress(Exception):
            await bot.send_message(chat_id, "❌ Никто не зашёл, ставки возвращены")
        return
    lob["running"] = True
    total = sum(p["stake"] for p in players)
    roll = random.random() * total
    cum = 0
    winner = players[-1]
    for p in players:
        cum += p["stake"]
        if roll <= cum:
            winner = p
            break
    payout = int(total * (1 - COMMISSION))
    emoji = "🎡" if game == "wheel" else "🟦"
    for i in range(6):
        bar = "🟡" * (i + 1) + "⚫" * (5 - i)
        with suppress(Exception):
            await bot.edit_message_text(
                f"{emoji} <b>{game.upper()}</b>\n\n{bar}\n\n💰 Банк: <b>{total:,}⭐</b>\n🎲 Крутится...",
                chat_id=chat_id, message_id=lob["msg_id"]
            )
        await asyncio.sleep(1)
    await add_balance(winner["user_id"], payout, "win", f"{game}: победа")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?",
                         (payout, winner["user_id"]))
        await db.commit()
    await save_round(game, total, winner["user_id"], winner["username"], payout, total - payout)
    lines = [f"{'🏆' if p['user_id']==winner['user_id'] else '❌'} @{p['username']} — {p['stake']:,}⭐"
             for p in players]
    with suppress(Exception):
        await bot.edit_message_text(
            f"{emoji} <b>{game.upper()} — РЕЗУЛЬТАТ</b>\n\n"
            f"🏆 Победитель: <b>@{winner['username']}</b>\n"
            f"💰 +{payout:,}⭐\n\n"
            f"{chr(10).join(lines)}",
            chat_id=chat_id, message_id=lob["msg_id"]
        )
    lob["players"] = []
    lob["running"] = False
    lob["task"] = None
    lob["msg_id"] = None

# ==================== СОЛО-ИГРЫ ====================
async def _solo_dice(message: Message, emoji: str, win_if, mult: int, name: str):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer(f"❌ Напиши: <code>{name.lower()} СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    if amount > MAX_BET:
        return await message.answer(f"❌ Максимум {MAX_BET:,}⭐")
    uid = message.from_user.id
    if uid in busy:
        return await message.answer("⏳ Подожди...")
    busy.add(uid)
    try:
        if not await atomic_bet(uid, amount):
            return await message.answer("❌ Недостаточно средств")
        await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
        await message.answer(f"{emoji} <b>{name}</b> — бросаю...")
        dice = await bot.send_dice(chat_id=message.chat.id, emoji=emoji)
        value = dice.dice.value
        await asyncio.sleep(3)
        won = win_if(value)
        if won:
            payout = amount * mult
            await add_balance(uid, payout, "win", f"{name} x{mult}")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (payout, uid))
                await db.commit()
            u = await get_user(uid)
            await message.answer(
                f"🎉 <b>ПОБЕДА!</b>\n🎯 {value}\n💰 +{payout:,}⭐\n💼 {u['balance']:,}⭐"
            )
        else:
            u = await get_user(uid)
            await message.answer(f"😢 Проигрыш\n🎯 {value}\n💼 {u['balance']:,}⭐")
    finally:
        busy.discard(uid)

@router.message(Command("dice"))
async def cmd_dice(message: Message):
    await _solo_dice(message, "🎲", lambda v: v > 3, 2, "Кубик")

@router.message(Command("darts"))
async def cmd_darts(message: Message):
    await _solo_dice(message, "🎯", lambda v: v >= 4, 3, "Дротики")

@router.message(Command("basket"))
async def cmd_basket(message: Message):
    await _solo_dice(message, "🏀", lambda v: v >= 4, 2, "Баскетбол")

@router.message(Command("football"))
async def cmd_football(message: Message):
    await _solo_dice(message, "⚽", lambda v: v >= 4, 2, "Футбол")

@router.message(Command("bowl"))
async def cmd_bowl(message: Message):
    await _solo_dice(message, "🎳", lambda v: v == 6, 3, "Боулинг")

SLOT_MULT = {1: 2.0, 22: 1.5, 43: 1.5, 64: 5.0}

@router.message(Command("slots"))
async def cmd_slots(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("❌ Напиши: <code>слоты СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    uid = message.from_user.id
    if uid in busy:
        return await message.answer("⏳ Подожди...")
    busy.add(uid)
    try:
        if not await atomic_bet(uid, amount):
            return await message.answer("❌ Недостаточно средств")
        dice = await bot.send_dice(chat_id=message.chat.id, emoji="🎰")
        value = dice.dice.value
        await asyncio.sleep(3)
        mult = SLOT_MULT.get(value, 0)
        u = await get_user(uid)
        if mult > 0:
            payout = int(amount * mult)
            await add_balance(uid, payout, "win", f"Слот x{mult}")
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (payout, uid))
                await db.commit()
            u = await get_user(uid)
            await message.answer(f"🎰 <b>ПОБЕДА x{mult}!</b>\n💰 +{payout:,}⭐\n💼 {u['balance']:,}⭐")
        else:
            await message.answer(f"😢 Мимо. Баланс: <b>{u['balance']:,}⭐</b>")
    finally:
        busy.discard(uid)

@router.message(Command("coin"))
async def cmd_coin(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("❌ Напиши: <code>монетка СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    uid = message.from_user.id
    if not await atomic_bet(uid, amount):
        return await message.answer("❌ Недостаточно средств")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🦅 Орёл", callback_data=f"coin_h_{amount}"),
         InlineKeyboardButton(text="🪙 Решка", callback_data=f"coin_t_{amount}")],
    ])
    await message.answer(f"🪙 <b>Монетка</b>\nСтавка: {amount:,}⭐\nУгадай:", reply_markup=kb)

@router.callback_query(F.data.startswith("coin_"))
async def cb_coin(cb: CallbackQuery):
    parts = cb.data.split("_")
    choice, amount = parts[1], int(parts[2])
    uid = cb.from_user.id
    result = random.choice(["h", "t"])
    won = result == choice
    payout = amount * 2 if won else 0
    if won:
        await add_balance(uid, payout, "win", "Монетка x2")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (payout, uid))
            await db.commit()
    u = await get_user(uid)
    emoji = "🦅" if result == "h" else "🪙"
    side = "Орёл" if result == "h" else "Решка"
    await cb.message.edit_text(
        f"🪙 Выпало: <b>{emoji} {side}</b>\n\n"
        f"{'🎉 <b>+' + f'{payout:,}' + '⭐</b>' if won else '😢 Проигрыш'}\n"
        f"💼 {u['balance']:,}⭐"
    )
    await cb.answer()

# ==================== МИНЫ ====================
GRID = 5
MINES = 5

@router.message(Command("mines"))
async def cmd_mines(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("❌ Напиши: <code>мины СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    uid = message.from_user.id
    if uid in mines_games:
        return await message.answer("❌ Уже есть игра")
    if not await atomic_bet(uid, amount):
        return await message.answer("❌ Недостаточно средств")
    cells = list(range(GRID * GRID))
    mines_games[uid] = {
        "amount": amount, "mines": set(random.sample(cells, MINES)),
        "opened": set(), "finished": False, "mult": 1.0,
    }
    await bot.send_message(message.chat.id, "💣 <b>МИНЫ</b>", reply_markup=_mines_kb(uid))

def _mines_kb(uid):
    g = mines_games.get(uid)
    if not g:
        return None
    rows = []
    for r in range(GRID):
        row = []
        for c in range(GRID):
            idx = r * GRID + c
            if g["finished"] and idx in g["mines"]:
                txt, cbd = "💣", "mines_noop"
            elif idx in g["opened"]:
                txt, cbd = "💎", "mines_noop"
            else:
                txt, cbd = "⬜", f"mines_o_{idx}"
            row.append(InlineKeyboardButton(text=txt, callback_data=cbd))
        rows.append(row)
    if not g["finished"] and g["opened"]:
        cash = int(g["amount"] * g["mult"])
        rows.append([InlineKeyboardButton(text=f"💰 Забрать {cash:,}⭐", callback_data="mines_cash")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@router.callback_query(F.data.startswith("mines_o_"))
async def cb_mines_open(cb: CallbackQuery):
    uid = cb.from_user.id
    g = mines_games.get(uid)
    if not g or g["finished"]:
        return await cb.answer("Игра не активна")
    idx = int(cb.data.split("_")[-1])
    if idx in g["opened"]:
        return await cb.answer("Уже открыто")
    if idx in g["mines"]:
        g["finished"] = True
        mines_games.pop(uid, None)
        await cb.message.edit_text(f"💥 <b>БУМ! Мина.</b>\nПотеряно: <b>-{g['amount']:,}⭐</b>")
        return await cb.answer("💥")
    g["opened"].add(idx)
    total_safe = GRID * GRID - MINES
    if len(g["opened"]) >= total_safe:
        win = int(g["amount"] * g["mult"])
        mines_games.pop(uid, None)
        await add_balance(uid, win, "win", "Мины: всё поле")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (win, uid))
            await db.commit()
        await cb.message.edit_text(f"🏆 <b>ПОБЕДА!</b>\n+{win:,}⭐")
        return await cb.answer("🏆")
    g["mult"] = round(1 + len(g["opened"]) * (0.35 + len(g["opened"]) * 0.05), 2)
    await cb.message.edit_text(
        f"💣 <b>МИНЫ</b>\n\n"
        f"💰 Ставка: {g['amount']:,}⭐\n"
        f"💎 Открыто: {len(g['opened'])}/{total_safe}\n"
        f"✖️ x{g['mult']:.2f}\n"
        f"💵 {int(g['amount'] * g['mult']):,}⭐",
        reply_markup=_mines_kb(uid),
    )
    await cb.answer("💎")

@router.callback_query(F.data == "mines_cash")
async def cb_mines_cash(cb: CallbackQuery):
    uid = cb.from_user.id
    g = mines_games.get(uid)
    if not g or not g["opened"]:
        return await cb.answer("Нечего забирать")
    win = int(g["amount"] * g["mult"])
    mines_games.pop(uid, None)
    await add_balance(uid, win, "win", "Мины: вывод")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (win, uid))
        await db.commit()
    await cb.message.edit_text(f"💰 <b>Забрано {win:,}⭐</b>")
    await cb.answer("Забрано!")

@router.callback_query(F.data == "mines_noop")
async def cb_mines_noop(cb: CallbackQuery):
    await cb.answer()

# ==================== БЛЭКДЖЕК ====================
def _card_str(card):
    return f"{card[0]}{card[1]}"

def _hand_value(hand):
    val, aces = 0, 0
    for rank, _ in hand:
        if rank in ("J", "Q", "K"):
            val += 10
        elif rank == "A":
            val += 11
            aces += 1
        else:
            val += int(rank)
    while val > 21 and aces:
        val -= 10
        aces -= 1
    return val

def _new_deck():
    deck = [(r, s) for r in ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
                     for s in ["♠","♥","♦","♣"]]
    random.shuffle(deck)
    return deck

@router.message(Command("bj"))
async def cmd_bj(message: Message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        return await message.answer("❌ Напиши: <code>блэкджек СТАВКА</code>")
    amount = int(parts[1])
    if amount < MIN_BET:
        return await message.answer(f"❌ Минимум {MIN_BET}⭐")
    uid = message.from_user.id
    if uid in BJ_GAMES:
        return await message.answer("❌ Уже есть игра")
    if not await atomic_bet(uid, amount):
        return await message.answer("❌ Недостаточно средств")
    deck = _new_deck()
    BJ_GAMES[uid] = {
        "deck": deck,
        "player": [deck.pop(), deck.pop()],
        "dealer": [deck.pop(), deck.pop()],
        "amount": amount,
    }
    await _render_bj(message.chat.id, uid)

async def _render_bj(chat_id, uid, edit_msg=None):
    g = BJ_GAMES.get(uid)
    if not g:
        return
    pv = _hand_value(g["player"])
    txt = (
        f"🃏 <b>БЛЭКДЖЕК</b>\n\n"
        f"👤 Ты: {' '.join(_card_str(c) for c in g['player'])} — <b>{pv}</b>\n"
        f"🤖 Дилер: {_card_str(g['dealer'][0])} 🂠\n\n"
        f"💰 Ставка: {g['amount']:,}⭐"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Взять", callback_data="bj_hit"),
         InlineKeyboardButton(text="🛑 Хватит", callback_data="bj_stand")],
    ])
    if edit_msg:
        with suppress(Exception):
            await edit_msg.edit_text(txt, reply_markup=kb)
    else:
        await bot.send_message(chat_id, txt, reply_markup=kb)

@router.callback_query(F.data == "bj_hit")
async def cb_bj_hit(cb: CallbackQuery):
    uid = cb.from_user.id
    g = BJ_GAMES.get(uid)
    if not g:
        return await cb.answer("Игра не активна")
    g["player"].append(g["deck"].pop())
    pv = _hand_value(g["player"])
    if pv > 21:
        BJ_GAMES.pop(uid, None)
        await cb.message.edit_text(f"💥 <b>Перебор ({pv}). Проигрыш -{g['amount']:,}⭐</b>")
        return await cb.answer("💥")
    await _render_bj(cb.message.chat.id, uid, cb.message)
    await cb.answer()

@router.callback_query(F.data == "bj_stand")
async def cb_bj_stand(cb: CallbackQuery):
    uid = cb.from_user.id
    g = BJ_GAMES.get(uid)
    if not g:
        return await cb.answer("Игра не активна")
    while _hand_value(g["dealer"]) < 17:
        g["dealer"].append(g["deck"].pop())
    pv = _hand_value(g["player"])
    dv = _hand_value(g["dealer"])
    amount = g["amount"]
    BJ_GAMES.pop(uid, None)
    if dv > 21 or pv > dv:
        mult = 2.5 if pv == 21 and len(g["player"]) == 2 else 2
        payout = int(amount * mult)
        await add_balance(uid, payout, "win", f"Блэкджек x{mult}")
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE users SET total_won = total_won + ? WHERE user_id = ?", (payout, uid))
            await db.commit()
        result = f"🎉 <b>ПОБЕДА!</b> +{payout:,}⭐"
    elif pv == dv:
        await add_balance(uid, amount, "refund", "Блэкджек: ничья")
        result = "🤝 <b>Ничья</b>"
    else:
        result = f"😢 <b>Проигрыш</b> -{amount:,}⭐"
    await cb.message.edit_text(
        f"🃏 <b>БЛЭКДЖЕК</b>\n\n"
        f"👤 Ты: {' '.join(_card_str(c) for c in g['player'])} — <b>{pv}</b>\n"
        f"🤖 Дилер: {' '.join(_card_str(c) for c in g['dealer'])} — <b>{dv}</b>\n\n"
        f"{result}"
    )
    await cb.answer()

# ==================== ОТМЕНА ====================
@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Отменено")

# ==================== АДМИНКА ====================
@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💸 Заявки", callback_data="adm_wd")],
        [InlineKeyboardButton(text="✏️ Баланс", callback_data="adm_bal")],
    ])
    await message.answer("⚙️ <b>АДМИНКА</b>", reply_markup=kb)

@router.message(Command("setbal"))
async def cmd_setbal(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) < 3 or not parts[1].lstrip("-").isdigit():
        return await message.answer("Использование: /setbal <id> <сумма>")
    tid, amount = int(parts[1]), int(parts[2])
    await add_balance(tid, amount, "admin_edit", f"Админ: {amount:+}")
    u = await get_user(tid)
    await message.answer(f"✅ <code>{tid}</code> → <b>{u['balance'] if u else 0:,}⭐</b>")

@router.message(Command("withdrawals"))
async def cmd_withdrawals(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    items = await get_pending_wd()
    if not items:
        return await message.answer("💸 Нет заявок")
    for w in items:
        await message.answer(
            f"💸 <b>№{w['id']}</b>\n👤 <code>{w['user_id']}</code>\n"
            f"💰 {w['amount']:,}⭐\n📩 {w['requisites']}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Выполнено", callback_data=f"wd_done_{w['id']}")]
            ]),
        )

@router.callback_query(F.data == "adm_stats")
async def cb_adm_stats(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    async with aiosqlite.connect(DB_PATH) as db:
        c1 = await db.execute("SELECT COUNT(*) FROM users")
        users = (await c1.fetchone())[0]
        c2 = await db.execute("SELECT COALESCE(SUM(balance),0) FROM users")
        bal = (await c2.fetchone())[0]
        c3 = await db.execute("SELECT COALESCE(SUM(total_topup),0) FROM users")
        tp = (await c3.fetchone())[0]
        c4 = await db.execute("SELECT COUNT(*) FROM rounds")
        rd = (await c4.fetchone())[0]
    await cb.message.edit_text(
        f"📊 <b>СТАТИСТИКА</b>\n\n"
        f"👥 {users}\n💰 {bal:,}⭐\n📈 {tp:,}⭐\n🎮 {rd}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="adm_back")]
        ]),
    )
    await cb.answer()

@router.callback_query(F.data == "adm_back")
async def cb_adm_back(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="adm_stats")],
        [InlineKeyboardButton(text="💸 Заявки", callback_data="adm_wd")],
        [InlineKeyboardButton(text="✏️ Баланс", callback_data="adm_bal")],
    ])
    await cb.message.edit_text("⚙️ <b>АДМИНКА</b>", reply_markup=kb)
    await cb.answer()

@router.callback_query(F.data == "adm_wd")
async def cb_adm_wd(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    items = await get_pending_wd()
    if not items:
        await cb.answer("Нет заявок", show_alert=True)
        return
    text = "💸 <b>ЗАЯВКИ</b>\n\n"
    for w in items:
        text += f"№{w['id']} | <code>{w['user_id']}</code> | {w['amount']:,}⭐\n📩 {w['requisites']}\n\n"
    kb_rows = [[InlineKeyboardButton(text=f"✅ #{w['id']}", callback_data=f"wd_done_{w['id']}")] for w in items]
    kb_rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm_back")])
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows))
    await cb.answer()

@router.callback_query(F.data == "adm_bal")
async def cb_adm_bal(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in ADMIN_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    await state.set_state(AdminSG.waiting_user)
    await cb.message.edit_text("✏️ Отправь <b>ID пользователя</b>:")
    await cb.answer()

@router.message(AdminSG.waiting_user)
async def adm_get_user(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("ID должен быть числом")
    await state.update_data(target=int(message.text))
    await state.set_state(AdminSG.waiting_amount)
    await message.answer("Отправь <b>сумму</b> (+/-):")

@router.message(AdminSG.waiting_amount)
async def adm_get_amount(message: Message, state: FSMContext):
    try:
        amount = int(message.text)
    except ValueError:
        return await message.answer("Целое число")
    data = await state.get_data()
    tid = data["target"]
    await add_balance(tid, amount, "admin_edit", f"Админ: {amount:+}")
    u = await get_user(tid)
    await state.clear()
    await message.answer(f"✅ <code>{tid}</code>: <b>{u['balance'] if u else 0:,}⭐</b>")

# ==================== РУССКИЕ КОМАНДЫ ====================
RU_TO_EN = {
    "слоты": "slots", "слот": "slots", "слотик": "slots",
    "кубик": "dice", "кубики": "dice", "кости": "dice", "дайс": "dice",
    "дротик": "darts", "дротики": "darts", "дартс": "darts",
    "баскет": "basket", "баскетбол": "basket", "баскетболик": "basket",
    "футбол": "football", "футбик": "football", "фут": "football",
    "боулинг": "bowl", "боул": "bowl",
    "монетка": "coin", "монета": "coin", "монетку": "coin",
    "мины": "mines", "мина": "mines", "мин": "mines",
    "блэкджек": "bj", "бж": "bj", "очко": "bj",
    "колесо": "wheel", "рулетка": "wheel", "рулетку": "wheel",
    "квадрат": "square", "квадрик": "square",
}

@router.message(StateFilter(None), F.text)
async def ru_game_dispatch(message: Message):
    """Ловит русские команды 'слоты 50' и роутит их в англ. команды."""
    if not message.text:
        return
    text = message.text.strip()
    # Обычные ру-команды без ставки
    if text.lower() in ("кейсы", "кейс", "case", "cases"):
        return await cmd_cases(message)
    if text.lower() in ("баланс", "бал", "balance"):
        return await cmd_balance(message)
    if text.lower() in ("профиль", "проф", "profile"):
        return await cmd_profile(message)
    if text.lower() in ("топ", "top", "лидеры"):
        return await cmd_top(message)
    if text.lower() in ("бонус", "daily", "ежедневка"):
        return await cmd_daily(message)
    if text.lower() in ("реф", "рефералка", "ref", "приглашения"):
        return await cmd_ref(message)
    if text.lower() in ("пополнить", "депозит", "topup", "деп"):
        return await cmd_topup(message)
    if text.lower() in ("вывод", "вывести", "withdraw"):
        return await cmd_withdraw(message, FSMContext)

    # Игровые с ставкой
    m = re.match(r"^([а-яё]+)\s+(\d+)$", text, re.IGNORECASE)
    if not m:
        return
    word = m.group(1).lower()
    amount = m.group(2)
    game = RU_TO_EN.get(word)
    if not game:
        return
    handlers = {
        "slots": cmd_slots, "dice": cmd_dice, "darts": cmd_darts,
        "basket": cmd_basket, "football": cmd_football, "bowl": cmd_bowl,
        "coin": cmd_coin, "mines": cmd_mines, "bj": cmd_bj,
        "wheel": cmd_wheel, "square": cmd_square,
    }
    handler = handlers.get(game)
    if not handler:
        return
    fake = message.model_copy(update={"text": f"/{game} {amount}"})
    await handler(fake)

# ==================== ЗАПУСК ====================
async def set_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="🎰 Запуск"),
        BotCommand(command="help", description="📖 Команды"),
        BotCommand(command="balance", description="💰 Баланс"),
        BotCommand(command="profile", description="👤 Профиль"),
        BotCommand(command="topup", description="⭐ Пополнить"),
        BotCommand(command="withdraw", description="💸 Вывод"),
        BotCommand(command="daily", description="🎁 Бонус"),
        BotCommand(command="top", description="🏆 Топ"),
        BotCommand(command="ref", description="👥 Рефералка"),
        BotCommand(command="cases", description="🎁 Кейсы"),
        BotCommand(command="slots", description="🎰 Слоты"),
        BotCommand(command="dice", description="🎲 Кубик"),
        BotCommand(command="darts", description="🎯 Дротики"),
        BotCommand(command="basket", description="🏀 Баскетбол"),
        BotCommand(command="football", description="⚽ Футбол"),
        BotCommand(command="bowl", description="🎳 Боулинг"),
        BotCommand(command="coin", description="🪙 Монетка"),
        BotCommand(command="mines", description="💣 Мины"),
        BotCommand(command="bj", description="🃏 Блэкджек"),
        BotCommand(command="wheel", description="🎡 Колесо PvP"),
        BotCommand(command="square", description="🟦 Квадрат PvP"),
    ])

async def main():
    await init_db()
    dp.include_router(router)
    await set_commands()
    log.info("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Остановлено")