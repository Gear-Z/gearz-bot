import asyncio
import logging
import asyncpg
import os
import random
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from aiogram.utils.keyboard import InlineKeyboardBuilder

API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- КОНФИГ ЭКОНОМИКИ И СИСТЕМ ---
BIZ_INFO = {
    1: {"name": "Ржавый шиномонтаж", "cost": 30000, "income_ph": 10800},
    2: {"name": "Автомойка 'Под мостом'", "cost": 150000, "income_ph": 43200},
    3: {"name": "Тюнинг-ателье", "cost": 800000, "income_ph": 180000},
    4: {"name": "Элитный автосалон", "cost": 5000000, "income_ph": 900000},
    5: {"name": "Теневой синдикат", "cost": 30000000, "income_ph": 4320000}
}
CLASS_REQ = {"Эконом": 1, "Комфорт": 2, "Спорт": 3, "Люкс": 4, "Гиперкар": 5}
NAMES = ["Лёха", "Саня", "Мага", "Джон", "Артём", "Михалыч", "Томас", "Серёга"]

async def init_db():
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_contract TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS biz_lvl INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_profit TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_race TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS races_won INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS races_lost INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS cases INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS tuning_parts INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_fleet TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS garage_limit INTEGER DEFAULT 3;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_theft TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS syndicate_id INTEGER;
        """)
        await conn.execute("""
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS tuning_lvl INTEGER DEFAULT 0;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS fuel INTEGER DEFAULT 100;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS condition INTEGER DEFAULT 100;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS driver_id INTEGER;
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                name TEXT,
                skill INTEGER,
                salary_ph INTEGER,
                is_working BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS syndicates (
                id SERIAL PRIMARY KEY,
                name TEXT UNIQUE,
                owner_id BIGINT,
                treasury BIGINT DEFAULT 0
            );
        """)

def format_price(price: int) -> str:
    if price >= 1_000_000_000: return f"{price / 1_000_000_000:g}млрд"
    elif price >= 1_000_000: return f"{price / 1_000_000:g}млн"
    elif price >= 1_000: return f"{price / 1_000:g}тыс"
    return f"{price}"

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚘 Гараж", callback_data="garage_0"),
         InlineKeyboardButton(text="🏪 Автосалон", callback_data="shop_cats")],
        [InlineKeyboardButton(text="💼 Контракты", callback_data="contracts"),
         InlineKeyboardButton(text="🏢 Бизнес", callback_data="biz_main")],
        [InlineKeyboardButton(text="🏁 Гонки", callback_data="racing"),
         InlineKeyboardButton(text="🚕 Империя", callback_data="empire_main")],
        [InlineKeyboardButton(text="🥷 Теневой рынок", callback_data="shadow_main"),
         InlineKeyboardButton(text="🏴‍☠️ Синдикаты", callback_data="syndicate_main")],
        [InlineKeyboardButton(text="⭐ Донат-Шоп", callback_data="donate_shop"),
         InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

# --- СТАРТ И АДМИНКА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id, username, money) VALUES ($1, $2, 1000) ON CONFLICT (user_id) DO NOTHING;", user_id, username)
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)
    text = f"🏴‍☠️ <b>Добро пожаловать в GearZ, {username}.</b>\n\nБаланс: <b>{format_price(user_money)} ₽</b>.\nСтрой свою империю. Твой ход:"
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(Command("givemoney"))
async def admin_give(message: types.Message):
    if message.from_user.username and message.from_user.username.lower() != 'whunx': return
    args = message.text.split()
    if len(args) == 2 and args[1].isdigit():
        async with bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", int(args[1]), message.from_user.id)
        await message.answer(f"👑 Баланс пополнен на {format_price(int(args[1]))} ₽!", parse_mode="HTML")

@dp.callback_query(F.data == "profile")
async def user_profile(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", callback.from_user.id)
        garage_stats = await conn.fetchrow("SELECT COUNT(g.id) as count, COALESCE(SUM(c.base_price), 0) as value FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1", callback.from_user.id)
        drivers_count = await conn.fetchval("SELECT COUNT(*) FROM drivers WHERE user_id = $1", callback.from_user.id)
        syn_name = await conn.fetchval("SELECT name FROM syndicates WHERE id = $1", user['syndicate_id']) if user['syndicate_id'] else "Нет"
    biz = BIZ_INFO[user['biz_lvl']]['name'] if user['biz_lvl'] > 0 else "Нет"
    text = (f"👤 <b>ПРОФИЛЬ</b>\n\n💰 Наличные: <b>{format_price(user['money'])} ₽</b>\n⚙️ Детали: <b>{user['tuning_parts']} шт.</b>\n"
            f"📦 Кейсы: <b>{user['cases']} шт.</b>\n\n🏢 Бизнес: <b>{biz}</b>\n🏴‍☠️ Синдикат: <b>{syn_name}</b>\n🚕 Водителей: <b>{drivers_count}</b>\n\n"
            f"🚘 Автопарк: <b>{garage_stats['count']} / {user['garage_limit']} мест</b>\n💎 Капитал: <b>{format_price(garage_stats['value'])} ₽</b>\n"
            f"🏁 Гонки (W/L): <b>{user['races_won']} / {user['races_lost']}</b>")
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]]), parse_mode="HTML")

# --- ДОНАТ ЗА ЗВЕЗДЫ ---
@dp.callback_query(F.data == "donate_shop")
async def donate_shop(callback: types.CallbackQuery):
    text = (f"⭐ <b>МАГАЗИН ЭКСКЛЮЗИВОВ (TELEGRAM STARS)</b>\n\n"
            f"1️⃣ <b>Пакет «Мажор»</b> (10 млн ₽ + 50 кейсов) — <b>100 ⭐</b>\n\n"
            f"2️⃣ <b>Лимитный гиперкар</b> (Koenigsegg Jesko + Макс Тюнинг) — <b>350 ⭐</b>\n\n"
            f"3️⃣ <b>Легендарный водила</b> (Скилл 5 ур, ЗП 0 ₽) — <b>150 ⭐</b>")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Купить «Мажор» (100 ⭐)", callback_data="buy_star_1")],
        [InlineKeyboardButton(text="🏎 Купить Jesko (350 ⭐)", callback_data="buy_star_2")],
        [InlineKeyboardButton(text="👨‍✈️ Легендарный водила (150 ⭐)", callback_data="buy_star_3")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_star_"))
async def process_donate(callback: types.CallbackQuery):
    item_id = callback.data.split("_")[2]
    prices = {"1": (100, "Пакет «Мажор»"), "2": (350, "Koenigsegg Jesko"), "3": (150, "Легендарный водитель")}
    price_stars, title = prices[item_id]
    await bot.send_invoice(
        chat_id=callback.from_user.id, title=title, description="Покупка в GearZ",
        payload=f"donate_{item_id}", currency="XTR", prices=[LabeledPrice(label=title, amount=price_stars)]
    )
    await callback.answer()

@dp.pre_checkout_query(F.true)
async def pre_checkout(query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment(message: types.Message):
    payload, user_id = message.successful_payment.invoice_payload, message.from_user.id
    async with bot.db_pool.acquire() as conn:
        if payload == "donate_1":
            await conn.execute("UPDATE users SET money = money + 10000000, cases = cases + 50 WHERE user_id = $1", user_id)
            await message.answer("⭐ Успешно! Начислено 10 млн ₽ и 50 кейсов.", parse_mode="HTML")
        elif payload == "donate_2":
            jesko_id = await conn.fetchval("SELECT car_id FROM cars WHERE name LIKE '%Jesko%' LIMIT 1")
            if jesko_id: await conn.execute("INSERT INTO garage (user_id, car_id, tuning_lvl) VALUES ($1, $2, 5)", user_id, jesko_id)
            await message.answer("⭐ Успешно! Лимитный Koenigsegg Jesko у тебя в гараже.", parse_mode="HTML")
        elif payload == "donate_3":
            await conn.execute("INSERT INTO drivers (user_id, name, skill, salary_ph) VALUES ($1, 'Эль Капитан', 5, 0)", user_id)
            await message.answer("⭐ Успешно! Легендарный водитель нанят.", parse_mode="HTML")

# --- БИЗНЕС (ПАССИВ) ---
@dp.callback_query(F.data == "biz_main")
async def handle_biz(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)
        if user['biz_lvl'] == 0:
            text = f"🏢 У тебя нет бизнеса.\n\nПервый бизнес: <b>{BIZ_INFO[1]['name']}</b>\n💰 Цена: {format_price(BIZ_INFO[1]['cost'])} ₽\n📈 Доход: {format_price(BIZ_INFO[1]['income_ph'])} / час"
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Купить шиномонтаж", callback_data="biz_upgrade")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]
            ])
        else:
            lvl, info = user['biz_lvl'], BIZ_INFO[user['biz_lvl']]
            diff = min((now - user['last_profit']).total_seconds(), 86400) if user['last_profit'] else 0
            earned = int((info['income_ph'] / 3600) * diff)
            text = (f"🏢 Бизнес: <b>{info['name']}</b> (Ур. {lvl})\n📈 Доход: {format_price(info['income_ph'])}/час\n\n"
                    f"💵 В кассе: <b>{format_price(earned)} ₽</b>\n💳 Баланс: {format_price(user['money'])} ₽")
            keyboard = [[InlineKeyboardButton(text="💰 Собрать прибыль", callback_data="biz_collect")]]
            if lvl + 1 in BIZ_INFO:
                keyboard.append([InlineKeyboardButton(text=f"⬆️ Улучшить до {BIZ_INFO[lvl+1]['name']} ({format_price(BIZ_INFO[lvl+1]['cost'])})", callback_data="biz_upgrade")])
            keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "biz_upgrade")
async def biz_upgrade(callback: types.CallbackQuery):
    uid, now = callback.from_user.id, datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT money, biz_lvl FROM users WHERE user_id = $1", uid)
        next_lvl = u['biz_lvl'] + 1
        if next_lvl not in BIZ_INFO: return await callback.answer("👑 Максимальный уровень бизнеса!", show_alert=True)
        cost = BIZ_INFO[next_lvl]['cost']
        if u['money'] < cost: return await callback.answer(f"❌ Нужно {format_price(cost)} ₽!", show_alert=True)
        await conn.execute("UPDATE users SET money = money - $1, biz_lvl = $2, last_profit = $3 WHERE user_id = $4", cost, next_lvl, now, uid)
    await callback.answer("✅ Успешно куплено/улучшено!", show_alert=True)
    await handle_biz(callback)

@dp.callback_query(F.data == "biz_collect")
async def biz_collect(callback: types.CallbackQuery):
    uid, now = callback.from_user.id, datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT biz_lvl, last_profit FROM users WHERE user_id = $1", uid)
        if u['biz_lvl'] == 0 or not u['last_profit']: return await callback.answer("❌ Нет бизнеса!", show_alert=True)
        diff = min((now - u['last_profit']).total_seconds(), 86400)
        earned = int((BIZ_INFO[u['biz_lvl']]['income_ph'] / 3600) * diff)
        if earned < 10: return await callback.answer("⏳ Касса пуста!", show_alert=True)
        await conn.execute("UPDATE users SET money = money + $1, last_profit = $2 WHERE user_id = $3", earned, now, uid)
    await callback.answer(f"💸 Собрано: {format_price(earned)} ₽!", show_alert=True)
    await handle_biz(callback)

# --- ТЕНЕВОЙ РЫНОК, УГОН, КЕЙСЫ И КАЗИНО ---
@dp.callback_query(F.data == "shadow_main")
async def shadow_main(callback: types.CallbackQuery):
    text = f"🥷 <b>ТЕНЕВОЙ РЫНОК И КАЗИНО</b>\n\nВыбирай тему:"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Кейсы (25к ₽)", callback_data="cases_main"), InlineKeyboardButton(text="🧨 Угон авто (100к ₽)", callback_data="theft_main")],
        [InlineKeyboardButton(text="🎰 Казино (Шанс 10%)", callback_data="casino_main")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "cases_main")
async def cases_main(callback: types.CallbackQuery):
    u = await bot.db_pool.fetchrow("SELECT cases, tuning_parts FROM users WHERE user_id = $1", callback.from_user.id)
    text = f"📦 <b>КЕЙСЫ</b>\nУ тебя кейсов: <b>{u['cases']} шт.</b>\nДеталей на складе: <b>{u['tuning_parts']} шт.</b>\n\n💰 Стоимость 1 кейса: <b>25тыс ₽</b>"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Купить кейс", callback_data="case_buy"), InlineKeyboardButton(text="🔓 Открыть кейс", callback_data="case_open")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="shadow_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "case_buy")
async def case_buy(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        if await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id) < 25000:
            return await callback.answer("❌ Не хватает денег!", show_alert=True)
        await conn.execute("UPDATE users SET money = money - 25000, cases = cases + 1 WHERE user_id = $1", callback.from_user.id)
    await callback.answer("✅ Кейс куплен!", show_alert=True); await cases_main(callback)

@dp.callback_query(F.data == "case_open")
async def case_open(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        if await conn.fetchval("SELECT cases FROM users WHERE user_id = $1", callback.from_user.id) < 1:
            return await callback.answer("❌ У тебя нет кейсов!", show_alert=True)
        if random.randint(1, 100) <= 70:
            p = random.randint(5, 25)
            await conn.execute("UPDATE users SET cases = cases - 1, tuning_parts = tuning_parts + $1 WHERE user_id = $2", p, callback.from_user.id)
            msg = f"🔧 Выпало: {p} деталей тюнинга!"
        else:
            cash = random.randint(10000, 50000)
            await conn.execute("UPDATE users SET cases = cases - 1, money = money + $1 WHERE user_id = $2", cash, callback.from_user.id)
            msg = f"💸 Выпал кэш: {format_price(cash)} ₽!"
    await callback.answer(msg, show_alert=True); await cases_main(callback)

@dp.callback_query(F.data == "theft_main")
async def theft_main(callback: types.CallbackQuery):
    text = f"🧨 <b>УГОН АВТОМОБИЛЯ</b>\n\nНаводка стоит 100 000 ₽.\n🟢 30% шанс угнать тачку.\n🔴 70% шанс облавы и штрафа."
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔥 Рискнуть и угнать (100к)", callback_data="theft_attempt")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="shadow_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "theft_attempt")
async def theft_attempt(callback: types.CallbackQuery):
    uid, now = callback.from_user.id, datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT money, garage_limit, last_theft FROM users WHERE user_id = $1", uid)
        if u['last_theft'] and (now - u['last_theft']).total_seconds() < 1800:
            return await callback.answer("⏳ Копы ищут тебя! Заляг на дно.", show_alert=True)
        if u['money'] < 100000: return await callback.answer("❌ Нет 100к на наводку!", show_alert=True)
        cc = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1", uid)
        if cc >= u['garage_limit']: return await callback.answer("❌ Гараж забит!", show_alert=True)
        
        await conn.execute("UPDATE users SET money = money - 100000, last_theft = $1 WHERE user_id = $2", now, uid)
        if random.randint(1, 100) <= 30:
            car = await conn.fetchrow("SELECT car_id, name, base_price FROM cars ORDER BY RANDOM() LIMIT 1")
            await conn.execute("INSERT INTO garage (user_id, car_id) VALUES ($1, $2)", uid, car['car_id'])
            await callback.answer(f"🎉 Успех! Угнан {car['name']} ({format_price(car['base_price'])} ₽)!", show_alert=True)
        else:
            fine = random.randint(50000, 200000)
            await conn.execute("UPDATE users SET money = GREATEST(0, money - $1) WHERE user_id = $2", fine, uid)
            await callback.answer(f"🚨 Облава! Штраф {format_price(fine)} ₽.", show_alert=True)
    await shadow_main(callback)

@dp.callback_query(F.data == "casino_main")
async def casino_main(callback: types.CallbackQuery):
    text = f"🎰 <b>ПОДПОЛЬНОЕ КАЗИНО</b>\nШанс победы: 10%. Множитель: х5."
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="10 000 ₽", callback_data="bet_10000"), InlineKeyboardButton(text="50 000 ₽", callback_data="bet_50000")],
        [InlineKeyboardButton(text="250 000 ₽", callback_data="bet_250000"), InlineKeyboardButton(text="1 000 000 ₽", callback_data="bet_1000000")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="shadow_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("bet_"))
async def process_bet(callback: types.CallbackQuery):
    amount, uid = int(callback.data.split("_")[1]), callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        m = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", uid)
        if m < amount: return await callback.answer("❌ Не хватает средств!", show_alert=True)
        if random.randint(1, 100) <= 10:
            win = amount * 5
            await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", win - amount, uid)
            await callback.answer(f"🎉 ДЖЕКПОТ! Выигрыш {format_price(win)} ₽!", show_alert=True)
        else:
            await conn.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", amount, uid)
            await callback.answer(f"😢 Мимо! Потеряно {format_price(amount)} ₽.", show_alert=True)
    await casino_main(callback)

# --- СИНДИКАТЫ (КЛАНЫ) ---
@dp.callback_query(F.data == "syndicate_main")
async def syndicate_main(callback: types.CallbackQuery):
    uid = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT syndicate_id, money FROM users WHERE user_id = $1", uid)
        if not u['syndicate_id']:
            text = f"🏴‍☠️ <b>СИНДИКАТЫ</b>\nТы не состоишь в клане.\n\n💰 Создание стоит <b>50 млн ₽</b>"
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛠 Создать синдикат (50 млн)", callback_data="syn_create")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
            ])
        else:
            syn = await conn.fetchrow("SELECT * FROM syndicates WHERE id = $1", u['syndicate_id'])
            cnt = await conn.fetchval("SELECT COUNT(*) FROM users WHERE syndicate_id = $1", u['syndicate_id'])
            text = f"🏴‍☠️ Клан: <b>{syn['name']}</b>\n👥 Участников: {cnt}\n💰 Общак: <b>{format_price(syn['treasury'])} ₽</b>"
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🚪 Покинуть клан", callback_data="syn_leave")],
                [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
            ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "syn_create")
async def syn_create(callback: types.CallbackQuery):
    uid = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        if await conn.fetchval("SELECT money FROM users WHERE user_id = $1", uid) < 50000000:
            return await callback.answer("❌ Нужно 50 млн ₽!", show_alert=True)
        await conn.execute("UPDATE users SET money = money - 50000000 WHERE user_id = $1", uid)
        await conn.execute("INSERT INTO syndicates (name, owner_id) VALUES ($1, $2)", f"Клан #{uid}", uid)
        sid = await conn.fetchval("SELECT id FROM syndicates WHERE owner_id = $1", uid)
        await conn.execute("UPDATE users SET syndicate_id = $1 WHERE user_id = $2", sid, uid)
    await callback.answer("✅ Синдикат создан!", show_alert=True); await syndicate_main(callback)

@dp.callback_query(F.data == "syn_leave")
async def syn_leave(callback: types.CallbackQuery):
    await bot.db_pool.execute("UPDATE users SET syndicate_id = NULL WHERE user_id = $1", callback.from_user.id)
    await callback.answer("✅ Покинул синдикат.", show_alert=True); await syndicate_main(callback)

# --- ГАРАЖ, ТЮНИНГ И СТО ---
@dp.callback_query(F.data.startswith("garage_"))
async def my_garage(callback: types.CallbackQuery):
    uid = callback.from_user.id
    if callback.data == "garage_expand":
        async with bot.db_pool.acquire() as conn:
            lim = await conn.fetchval("SELECT garage_limit FROM users WHERE user_id = $1", uid)
            cost = lim * 200000
            if await conn.fetchval("SELECT money FROM users WHERE user_id = $1", uid) < cost:
                return await callback.answer(f"❌ Нужно {format_price(cost)} ₽!", show_alert=True)
            await conn.execute("UPDATE users SET money = money - $1, garage_limit = garage_limit + 1 WHERE user_id = $2", cost, uid)
            await callback.answer("✅ Гараж расширен!", show_alert=True)
        callback.data = "garage_0"

    page, limit = int(callback.data.split("_")[1]), 5
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT garage_limit FROM users WHERE user_id = $1", uid)
        cc = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1", uid)
        cars = await conn.fetch("SELECT g.id, c.name FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1 ORDER BY c.base_price DESC LIMIT $2 OFFSET $3", uid, limit + 1, page * limit)

    builder = InlineKeyboardBuilder()
    for car in cars[:limit]: builder.button(text=f"{car['name']}", callback_data=f"mycar_{car['id']}")
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"garage_{page-1}"))
    if len(cars) > limit: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"garage_{page+1}"))
    if nav: builder.row(*nav)
    
    expand_cost = u['garage_limit'] * 200000
    builder.row(InlineKeyboardButton(text=f"📈 Купить место ({format_price(expand_cost)})", callback_data="garage_expand"))
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main"))
    builder.adjust(1)
    
    text = f"🚘 <b>ГАРАЖ (Стр. {page+1})</b>\nЗанято: <b>{cc} / {u['garage_limit']} мест</b>"
    if not cars and page == 0: text = f"🕸 <b>Гараж пуст.</b>\nМест: <b>0 / {u['garage_limit']}</b>"
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("mycar_"))
async def my_car_menu(callback: types.CallbackQuery):
    gid = int(callback.data.split("_")[1])
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT g.*, c.name, c.base_price, c.hp, c.class FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.id = $1 AND g.user_id = $2", gid, callback.from_user.id)
    if not car: return
    actual_hp = int(car['hp'] * (1 + 0.15 * car['tuning_lvl']))
    sell_price = int(car['base_price'] * 0.75)
    drv = "👨‍✈️ В рейсе" if car['driver_id'] else "💤 В гараже"
    text = (f"🚘 <b>{car['name']}</b> {drv}\n⚙️ Мощность: <b>{actual_hp} л.с.</b> (Тюнинг: {car['tuning_lvl']}/5)\n"
            f"⛽ Бак: {car['fuel']}% | 🔧 Состояние: {car['condition']}%\n💵 Скупщик дает: {format_price(sell_price)} ₽")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Тюнинг", callback_data=f"tune_{gid}"), InlineKeyboardButton(text="🔧 СТО", callback_data=f"service_{gid}")],
        [InlineKeyboardButton(text="🤝 Продать", callback_data=f"sellcar_{gid}_{sell_price}")],
        [InlineKeyboardButton(text="🔙 В гараж", callback_data="garage_0")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("tune_"))
async def tune_car(callback: types.CallbackQuery):
    gid, uid = int(callback.data.split("_")[1]), callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT tuning_lvl, driver_id FROM garage WHERE id = $1", gid)
        if car['driver_id']: return await callback.answer("❌ Отзови из рейса!", show_alert=True)
        if car['tuning_lvl'] >= 5: return await callback.answer("👑 Макс. тюнинг!", show_alert=True)
        parts, cost = (car['tuning_lvl'] + 1) * 15, (car['tuning_lvl'] + 1) * 150000
        u = await conn.fetchrow("SELECT money, tuning_parts FROM users WHERE user_id = $1", uid)
        if u['money'] < cost or u['tuning_parts'] < parts: return await callback.answer(f"❌ Нужно: {parts} деталей и {format_price(cost)} ₽", show_alert=True)
        await conn.execute("UPDATE users SET money = money - $1, tuning_parts = tuning_parts - $2 WHERE user_id = $3", cost, parts, uid)
        await conn.execute("UPDATE garage SET tuning_lvl = tuning_lvl + 1 WHERE id = $1", gid)
    await callback.answer("✅ Прокачано!", show_alert=True); callback.data = f"mycar_{gid}"; await my_car_menu(callback)

@dp.callback_query(F.data.startswith("service_"))
async def service_car(callback: types.CallbackQuery):
    gid, uid = int(callback.data.split("_")[1]), callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT g.fuel, g.condition, c.base_price FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.id = $1", gid)
        if car['fuel'] == 100 and car['condition'] == 100: return await callback.answer("✅ Авто в идеале!", show_alert=True)
        cost = int(car['base_price'] * 0.01)
        if await conn.fetchval("SELECT money FROM users WHERE user_id = $1", uid) < cost: return await callback.answer("❌ Мало денег!", show_alert=True)
        await conn.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", cost, uid)
        await conn.execute("UPDATE garage SET fuel = 100, condition = 100 WHERE id = $1", gid)
    await callback.answer("✅ ТО пройдено!", show_alert=True); callback.data = f"mycar_{gid}"; await my_car_menu(callback)

@dp.callback_query(F.data.startswith("sellcar_"))
async def sell_my_car(callback: types.CallbackQuery):
    _, gid, price = callback.data.split("_")
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT driver_id FROM garage WHERE id = $1 AND user_id = $2", int(gid), callback.from_user.id)
        if not car: return
        if car['driver_id']: return await callback.answer("❌ Отзови из рейса!", show_alert=True)
        await conn.execute("DELETE FROM garage WHERE id = $1", int(gid))
        await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", int(price), callback.from_user.id)
    await callback.answer(f"✅ Продано за {format_price(int(price))} ₽!", show_alert=True)
    callback.data = "garage_0"; await my_garage(callback)

# --- АВТОСАЛОН ---
@dp.callback_query(F.data == "shop_cats")
async def shop_categories(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        classes = await conn.fetch("SELECT DISTINCT class FROM cars")
        m = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)
    builder = InlineKeyboardBuilder()
    for row in classes: builder.button(text=f"🔹 {row['class']}", callback_data=f"shop_list_{row['class']}_0")
    builder.button(text="🔙 Главное меню", callback_data="back_main"); builder.adjust(2)
    text = f"🏪 <b>АВТОСАЛОН</b>\n💳 Баланс: <b>{format_price(m)} ₽</b>\n\nВыбери класс:"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else: await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("shop_list_"))
async def shop_list(callback: types.CallbackQuery):
    _, _, cls, p = callback.data.split("_"); p, lim = int(p), 5
    async with bot.db_pool.acquire() as conn:
        cars = await conn.fetch("SELECT car_id, name, base_price FROM cars WHERE class = $1 ORDER BY base_price ASC LIMIT $2 OFFSET $3", cls, lim + 1, p * lim)
    b = InlineKeyboardBuilder()
    for car in cars[:lim]: b.button(text=f"{car['name']} — {format_price(car['base_price'])} ₽", callback_data=f"shop_car_{car['car_id']}")
    nav = []
    if p > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"shop_list_{cls}_{p-1}"))
    if len(cars) > lim: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"shop_list_{cls}_{p+1}"))
    if nav: b.row(*nav)
    b.row(InlineKeyboardButton(text="🔙 К классам", callback_data="shop_cats")); b.adjust(1)
    text = f"Класс: <b>{cls}</b>"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=b.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else: await callback.message.edit_text(text, reply_markup=b.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("shop_car_"))
async def shop_car_detail(callback: types.CallbackQuery):
    await callback.answer()
    cid = int(callback.data.split("_")[2])
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT * FROM cars WHERE car_id = $1", cid)
        m = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)
    text = (f"🚘 <b>{car['name']}</b>\n⚙️ {car['hp']} л.с. | 💼 {car['class']}\n\n<i>{car['description']}</i>\n\n"
            f"💰 Цена: <b>{format_price(car['base_price'])} ₽</b>\n💳 Баланс: {format_price(m)} ₽")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 КУПИТЬ", callback_data=f"buy_{cid}")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"shop_list_{car['class']}_0")]
    ])
    try:
        await callback.message.answer_photo(photo=car['photo_url'], caption=text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    except:
        await callback.message.answer(f"<i>[Фото недоступно]</i>\n{text}", reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()

@dp.callback_query(F.data.startswith("buy_"))
async def buy_car(callback: types.CallbackQuery):
    cid, uid = int(callback.data.split("_")[1]), callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        async with conn.transaction():
            u = await conn.fetchrow("SELECT money, garage_limit FROM users WHERE user_id = $1", uid)
            cc = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1", uid)
            if cc >= u['garage_limit']: return await callback.answer("❌ Гараж забит! Купи расширение.", show_alert=True)
            car = await conn.fetchrow("SELECT name, base_price FROM cars WHERE car_id = $1", cid)
            if u['money'] < car['base_price']: return await callback.answer("❌ Недостаточно средств!", show_alert=True)
            await conn.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", car['base_price'], uid)
            await conn.execute("INSERT INTO garage (user_id, car_id) VALUES ($1, $2)", uid, cid)
    await callback.answer(f"✅ Куплено: {car['name']}!", show_alert=True)
    callback.data = "shop_cats"; await shop_categories(callback)

# --- КОНТРАКТЫ, ГОНКИ И ИМПЕРИЯ ---
@dp.callback_query(F.data == "contracts")
async def do_contract(callback: types.CallbackQuery):
    uid, now = callback.from_user.id, datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT money, last_contract FROM users WHERE user_id = $1", uid)
        if u['last_contract'] and (now - u['last_contract']).total_seconds() < 300:
            rem = int(300 - (now - u['last_contract']).total_seconds())
            return await callback.answer(f"⏳ Легавые на хвосте! Жди {rem//60} мин {rem%60} сек.", show_alert=True)
        r = random.randint(800, 2500)
        await conn.execute("UPDATE users SET money = money + $1, last_contract = $2 WHERE user_id = $3", r, now, uid)
    text = f"💼 <b>Дело сделано!</b>\n💸 Твоя доля: <b>+{format_price(r)} ₽</b>"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML"); await callback.message.delete()
    else: await callback.message.edit_text(text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "racing")
async def racing(callback: types.CallbackQuery):
    uid, now = callback.from_user.id, datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT money, last_race FROM users WHERE user_id = $1", uid)
        if u['last_race'] and (now - u['last_race']).total_seconds() < 180:
            return await callback.answer("⏳ Мотор перегрет!", show_alert=True)
        best = await conn.fetchrow("SELECT c.name, c.hp, g.tuning_lvl FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1 ORDER BY (c.hp * (1 + 0.15 * g.tuning_lvl)) DESC LIMIT 1", uid)
        if not best: return await callback.answer("❌ Нет машин в гараже для гонок!", show_alert=True)
        my_hp = int(best['hp'] * (1 + 0.15 * best['tuning_lvl']))
        en = await conn.fetchrow("SELECT name, hp FROM cars ORDER BY RANDOM() LIMIT 1")
        win_chance = max(10, min(90, int((my_hp / (my_hp + en['hp'])) * 100)))
        reward = int(en['hp'] * 20)
        
        if random.randint(1, 100) <= win_chance:
            await conn.execute("UPDATE users SET money=money+$1, last_race=$2, races_won=races_won+1 WHERE user_id=$3", reward, now, uid)
            text = f"🏆 <b>ПОБЕДА!</b>\n{best['name']} обошел {en['name']}.\n💸 Выигрыш: +{format_price(reward)} ₽"
        else:
            loss = min(int(reward * 0.2), u['money'])
            await conn.execute("UPDATE users SET money=money-$1, last_race=$2, races_lost=races_lost+1 WHERE user_id=$3", loss, now, uid)
            text = f"💥 <b>ПОРАЖЕНИЕ</b>\n{en['name']} ушел в отрыв.\n📉 Потери: -{format_price(loss)} ₽"
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]]), parse_mode="HTML")

@dp.callback_query(F.data == "empire_main")
async def empire_main(callback: types.CallbackQuery):
    uid = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        dr_total = await conn.fetchval("SELECT COUNT(*) FROM drivers WHERE user_id = $1", uid)
        cars_working = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1 AND driver_id IS NOT NULL", uid)
    text = f"🚕 <b>АВТО-ИМПЕРИЯ</b>\nВодителей в штате: {dr_total}\nМашин в рейсах: {cars_working}"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍✈️ Нанять водилу (50к)", callback_data="empire_hire")],
        [InlineKeyboardButton(text="🔗 Авто-Назначение", callback_data="empire_assign"), InlineKeyboardButton(text="💰 Собрать выручку", callback_data="empire_collect")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "empire_hire")
async def empire_hire(callback: types.CallbackQuery):
    uid = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        if await conn.fetchval("SELECT money FROM users WHERE user_id = $1", uid) < 50000:
            return await callback.answer("❌ Найм стоит 50тыс ₽!", show_alert=True)
        name, skill = random.choice(NAMES), random.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0]
        await conn.execute("UPDATE users SET money = money - 50000 WHERE user_id = $1", uid)
        await conn.execute("INSERT INTO drivers (user_id, name, skill, salary_ph) VALUES ($1, $2, $3, $4)", uid, name, skill, skill * 8000)
    await callback.answer(f"✅ Нанят {name} (Скилл {skill})!", show_alert=True); await empire_main(callback)

@dp.callback_query(F.data == "empire_assign")
async def empire_assign(callback: types.CallbackQuery):
    uid, assigned = callback.from_user.id, 0
    async with bot.db_pool.acquire() as conn:
        drivers = await conn.fetch("SELECT id, skill FROM drivers WHERE user_id = $1 AND is_working = FALSE ORDER BY skill DESC", uid)
        cars = await conn.fetch("SELECT g.id as gid, c.class FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1 AND g.driver_id IS NULL AND g.fuel > 0 AND g.condition > 0", uid)
        for d in drivers:
            for car in cars:
                if d['skill'] >= CLASS_REQ.get(car['class'], 1):
                    await conn.execute("UPDATE garage SET driver_id = $1 WHERE id = $2", d['id'], car['gid'])
                    await conn.execute("UPDATE drivers SET is_working = TRUE WHERE id = $1", d['id'])
                    cars.remove(car); assigned += 1; break
        await conn.execute("UPDATE users SET last_fleet = $1 WHERE user_id = $2 AND last_fleet IS NULL", datetime.utcnow(), uid)
    await callback.answer(f"✅ Отправлено в рейс машин: {assigned}!", show_alert=True); await empire_main(callback)

@dp.callback_query(F.data == "empire_collect")
async def empire_collect(callback: types.CallbackQuery):
    uid, now, total_profit = callback.from_user.id, datetime.utcnow(), 0
    async with bot.db_pool.acquire() as conn:
        u = await conn.fetchrow("SELECT last_fleet FROM users WHERE user_id = $1", uid)
        if not u['last_fleet']: return await callback.answer("❌ Никто не в рейсе!", show_alert=True)
        hp = (now - u['last_fleet']).total_seconds() / 3600.0
        if hp < 0.1: return await callback.answer("⏳ Рано собирать! Пусть поработают.", show_alert=True)
        
        working = await conn.fetch("SELECT g.id as gid, g.fuel, g.condition, g.driver_id, c.base_price, d.salary_ph FROM garage g JOIN cars c ON g.car_id = c.car_id JOIN drivers d ON g.driver_id = d.id WHERE g.user_id = $1 AND g.driver_id IS NOT NULL", uid)
        for w in working:
            profit = max(0, int((w['base_price'] * 0.01) * hp) - int(w['salary_ph'] * hp))
            nf, nc = max(0, int(w['fuel'] - 10 * hp)), max(0, int(w['condition'] - 5 * hp))
            total_profit += profit
            if nf == 0 or nc == 0:
                await conn.execute("UPDATE garage SET driver_id = NULL, fuel = $1, condition = $2 WHERE id = $3", nf, nc, w['gid'])
                await conn.execute("UPDATE drivers SET is_working = FALSE WHERE id = $1", w['driver_id'])
            else:
                await conn.execute("UPDATE garage SET fuel = $1, condition = $2 WHERE id = $3", nf, nc, w['gid'])
        await conn.execute("UPDATE users SET money = money + $1, last_fleet = $2 WHERE user_id = $3", total_profit, now, uid)
    await callback.answer(f"💸 Таксопарк принес {format_price(total_profit)} ₽!", show_alert=True); await empire_main(callback)

# --- ГЛАВНОЕ МЕНЮ И ЗАПУСК ---
@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    if callback.message.photo:
        await callback.message.answer("Главное меню:", reply_markup=get_main_menu())
        await callback.message.delete()
    else: await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

async def ping_render(request): return web.Response(text="GearZ is running")

async def main():
    bot.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await init_db()
    app = web.Application(); app.router.add_get('/', ping_render)
    runner = web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080))).start()
    print("GearZ Ultimate Loaded.")
    await dp.start_polling(bot)

if __name__ == "__main__": asyncio.run(main())
