import asyncio
import logging
import asyncpg
import os
import random
from datetime import datetime
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
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
        # Обновляем юзеров
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
        """)
        # Обновляем гараж
        await conn.execute("""
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS tuning_lvl INTEGER DEFAULT 0;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS fuel INTEGER DEFAULT 100;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS condition INTEGER DEFAULT 100;
            ALTER TABLE garage ADD COLUMN IF NOT EXISTS driver_id INTEGER;
        """)
        # Создаем таблицу водителей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS drivers (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                name TEXT,
                skill INTEGER,
                salary_ph INTEGER,
                is_working BOOLEAN DEFAULT FALSE
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
         InlineKeyboardButton(text="🏢 Мой бизнес", callback_data="biz_main")],
        [InlineKeyboardButton(text="🏁 Гонки", callback_data="racing"),
         InlineKeyboardButton(text="🚕 Авто-Империя", callback_data="empire_main")],
        [InlineKeyboardButton(text="📦 Кейсы", callback_data="cases_main"),
         InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

# --- БАЗОВЫЕ ФУНКЦИИ (СТАРТ, ПРОФИЛЬ, АДМИНКА) ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    async with bot.db_pool.acquire() as conn:
        await conn.execute("INSERT INTO users (user_id, username, money) VALUES ($1, $2, 1000) ON CONFLICT (user_id) DO NOTHING;", user_id, username)
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)
    text = f"🏴‍☠️ <b>Добро пожаловать в GearZ, {username}.</b>\n\nБаланс: <b>{format_price(user_money)} ₽</b>.\nТвой ход:"
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
    
    biz = BIZ_INFO[user['biz_lvl']]['name'] if user['biz_lvl'] > 0 else "Нет"
    text = (
        f"👤 <b>ПРОФИЛЬ</b>\n\n💰 Наличные: <b>{format_price(user['money'])} ₽</b>\n⚙️ Детали тюнинга: <b>{user['tuning_parts']} шт.</b>\n"
        f"📦 Кейсы: <b>{user['cases']} шт.</b>\n\n🏢 Бизнес: <b>{biz}</b>\n🚕 Водителей в штате: <b>{drivers_count}</b>\n\n"
        f"🚘 Машин в гараже: <b>{garage_stats['count']}</b>\n💎 Капитал автопарка: <b>{format_price(garage_stats['value'])} ₽</b>\n"
        f"🏁 Гонки (W/L): <b>{user['races_won']} / {user['races_lost']}</b>"
    )
    await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]]), parse_mode="HTML")

# --- КЕЙСЫ ---
@dp.callback_query(F.data == "cases_main")
async def cases_main(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money, cases, tuning_parts FROM users WHERE user_id = $1", callback.from_user.id)
    
    text = f"📦 <b>КЕЙСЫ С КОМПЛЕКТУЮЩИМИ</b>\n\nЗдесь падают детали для тюнинга машин и кэш.\n\nУ тебя кейсов: <b>{user['cases']} шт.</b>\nДеталей на складе: <b>{user['tuning_parts']} шт.</b>\n\n💰 Стоимость 1 кейса: <b>25тыс ₽</b>\n💳 Баланс: {format_price(user['money'])} ₽"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 Купить кейс (25к)", callback_data="case_buy"), InlineKeyboardButton(text="🔓 Открыть кейс", callback_data="case_open")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "case_buy")
async def case_buy(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)
        if money < 25000:
            await callback.answer("❌ Не хватает денег!", show_alert=True)
            return
        await conn.execute("UPDATE users SET money = money - 25000, cases = cases + 1 WHERE user_id = $1", callback.from_user.id)
    await callback.answer("✅ Кейс куплен!", show_alert=True)
    await cases_main(callback)

@dp.callback_query(F.data == "case_open")
async def case_open(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        cases = await conn.fetchval("SELECT cases FROM users WHERE user_id = $1", callback.from_user.id)
        if cases < 1:
            await callback.answer("❌ У тебя нет кейсов!", show_alert=True)
            return
        
        chance = random.randint(1, 100)
        if chance <= 70: # 70% шанс на детали
            parts = random.randint(5, 25)
            await conn.execute("UPDATE users SET cases = cases - 1, tuning_parts = tuning_parts + $1 WHERE user_id = $2", parts, callback.from_user.id)
            msg = f"🔧 Выпало: {parts} деталей для тюнинга!"
        else: # 30% шанс на кэш
            cash = random.randint(10000, 50000)
            await conn.execute("UPDATE users SET cases = cases - 1, money = money + $1 WHERE user_id = $2", cash, callback.from_user.id)
            msg = f"💸 Выпал кэш: {format_price(cash)} ₽!"
            
    await callback.answer(msg, show_alert=True)
    await cases_main(callback)

# --- АВТО-ИМПЕРИЯ (ФАРМ МАШИНАМИ) ---
@dp.callback_query(F.data == "empire_main")
async def empire_main(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money FROM users WHERE user_id = $1", user_id)
        dr_total = await conn.fetchval("SELECT COUNT(*) FROM drivers WHERE user_id = $1", user_id)
        dr_free = await conn.fetchval("SELECT COUNT(*) FROM drivers WHERE user_id = $1 AND is_working = FALSE", user_id)
        cars_total = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1", user_id)
        cars_working = await conn.fetchval("SELECT COUNT(*) FROM garage WHERE user_id = $1 AND driver_id IS NOT NULL", user_id)
        
    text = (f"🚕 <b>АВТО-ИМПЕРИЯ (ЛОГИСТИКА)</b>\n\nПускай свои тачки в ход, нанимай водил и руби пассивный кэш.\n\n"
            f"👤 Водителей в штате: <b>{dr_total}</b> (Свободно: {dr_free})\n"
            f"🚘 Автопарк: <b>{cars_total}</b> (В рейсах: {cars_working})\n\n"
            f"💳 Баланс компании: <b>{format_price(user['money'])} ₽</b>")
            
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👨‍✈️ Нанять водилу (50к)", callback_data="empire_hire")],
        [InlineKeyboardButton(text="🔗 Авто-Назначение", callback_data="empire_assign"), InlineKeyboardButton(text="💰 Собрать выручку", callback_data="empire_collect")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "empire_hire")
async def empire_hire(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)
        if money < 50000:
            await callback.answer("❌ Найм стоит 50тыс ₽!", show_alert=True)
            return
        
        name = random.choice(NAMES)
        skill = random.choices([1, 2, 3, 4, 5], weights=[40, 30, 15, 10, 5])[0] # Редкость
        salary = skill * 8000 # Зарплата в час
        
        await conn.execute("UPDATE users SET money = money - 50000 WHERE user_id = $1", user_id)
        await conn.execute("INSERT INTO drivers (user_id, name, skill, salary_ph) VALUES ($1, $2, $3, $4)", user_id, name, skill, salary)
        
    classes = {1: "Эконом", 2: "Комфорт", 3: "Спорт", 4: "Люкс", 5: "Гиперкар"}
    await callback.answer(f"✅ Нанят {name}!\nДопуск: {classes[skill]}\nЗП: {format_price(salary)}/ч", show_alert=True)
    await empire_main(callback)

@dp.callback_query(F.data == "empire_assign")
async def empire_assign(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    assigned = 0
    async with bot.db_pool.acquire() as conn:
        # Ищем свободных водителей
        free_drivers = await conn.fetch("SELECT id, skill FROM drivers WHERE user_id = $1 AND is_working = FALSE ORDER BY skill DESC", user_id)
        # Ищем свободные машины
        free_cars = await conn.fetch("""
            SELECT g.id as gid, c.class FROM garage g JOIN cars c ON g.car_id = c.car_id 
            WHERE g.user_id = $1 AND g.driver_id IS NULL AND g.fuel > 0 AND g.condition > 0
        """, user_id)
        
        for driver in free_drivers:
            for car in free_cars:
                req_skill = CLASS_REQ.get(car['class'], 1)
                if driver['skill'] >= req_skill:
                    # Назначаем
                    await conn.execute("UPDATE garage SET driver_id = $1 WHERE id = $2", driver['id'], car['gid'])
                    await conn.execute("UPDATE drivers SET is_working = TRUE WHERE id = $1", driver['id'])
                    # Убираем машину из пула
                    free_cars.remove(car)
                    assigned += 1
                    break # Переходим к следующему водителю
                    
        # Задаем точку старта для заработка, если это первый рейс
        await conn.execute("UPDATE users SET last_fleet = $1 WHERE user_id = $2 AND last_fleet IS NULL", datetime.utcnow(), user_id)
        
    if assigned > 0: await callback.answer(f"✅ Успешно отправлено в рейс машин: {assigned}!", show_alert=True)
    else: await callback.answer("❌ Нет подходящих свободных машин, бензина или водителей.", show_alert=True)
    await empire_main(callback)

@dp.callback_query(F.data == "empire_collect")
async def empire_collect(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.utcnow()
    total_profit = 0
    total_fuel_loss = 0
    
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT last_fleet FROM users WHERE user_id = $1", user_id)
        if not user['last_fleet']:
            await callback.answer("❌ Никто не в рейсе!", show_alert=True)
            return
            
        hours_passed = (now - user['last_fleet']).total_seconds() / 3600.0
        if hours_passed < 0.1: # Минимум 6 минут
            await callback.answer("⏳ Рано! Пусть поработают.", show_alert=True)
            return
            
        # Забираем все работающие тачки
        working = await conn.fetch("""
            SELECT g.id as gid, g.fuel, g.condition, g.driver_id, c.base_price, c.name, d.salary_ph 
            FROM garage g JOIN cars c ON g.car_id = c.car_id JOIN drivers d ON g.driver_id = d.id 
            WHERE g.user_id = $1 AND g.driver_id IS NOT NULL
        """, user_id)
        
        if not working:
            await callback.answer("❌ Машины простаивают!", show_alert=True)
            return

        for w in working:
            # Машина фармит 1% от своей цены в час, минус зарплата водилы
            income = int((w['base_price'] * 0.01) * hours_passed)
            expense = int(w['salary_ph'] * hours_passed)
            profit = income - expense
            if profit < 0: profit = 0
            
            # Тратим бенз (10 в час) и состояние (5 в час)
            new_fuel = max(0, int(w['fuel'] - (10 * hours_passed)))
            new_cond = max(0, int(w['condition'] - (5 * hours_passed)))
            
            total_profit += profit
            
            # Если сломалась или обсохла - снимаем водилу
            if new_fuel == 0 or new_cond == 0:
                await conn.execute("UPDATE garage SET driver_id = NULL, fuel = $1, condition = $2 WHERE id = $3", new_fuel, new_cond, w['gid'])
                await conn.execute("UPDATE drivers SET is_working = FALSE WHERE id = $1", w['driver_id'])
            else:
                await conn.execute("UPDATE garage SET fuel = $1, condition = $2 WHERE id = $3", new_fuel, new_cond, w['gid'])
        
        # Обновляем бабки и время
        await conn.execute("UPDATE users SET money = money + $1, last_fleet = $2 WHERE user_id = $3", total_profit, now, user_id)
        
    await callback.answer(f"💸 Таксопарк принес {format_price(total_profit)} ₽!\n🔧 Проверь СТО, некоторые могли обсохнуть.", show_alert=True)
    await empire_main(callback)

# --- ГАРАЖ, ТЮНИНГ И СТО ---
@dp.callback_query(F.data.startswith("garage_"))
async def my_garage(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    limit = 5
    offset = page * limit
    async with bot.db_pool.acquire() as conn:
        cars = await conn.fetch("SELECT g.id, c.name FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1 ORDER BY c.base_price DESC LIMIT $2 OFFSET $3", callback.from_user.id, limit + 1, offset)
        
    if not cars and page == 0:
        await callback.message.edit_text("🕸 <b>Твой гараж пуст.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]]), parse_mode="HTML")
        return

    builder = InlineKeyboardBuilder()
    for car in cars[:limit]: builder.button(text=f"{car['name']}", callback_data=f"mycar_{car['id']}")
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"garage_{page-1}"))
    if len(cars) > limit: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"garage_{page+1}"))
    if nav: builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main"))
    builder.adjust(1)
    await callback.message.edit_text(f"🚘 <b>ТВОЙ АВТОПАРК (Стр. {page+1})</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("mycar_"))
async def my_car_menu(callback: types.CallbackQuery):
    gid = int(callback.data.split("_")[1])
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("""
            SELECT g.id, g.tuning_lvl, g.fuel, g.condition, g.driver_id, c.name, c.base_price, c.hp, c.class 
            FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.id = $1 AND g.user_id = $2
        """, gid, callback.from_user.id)
    if not car: return

    # Пересчет характеристик от тюнинга
    actual_hp = int(car['hp'] * (1 + 0.15 * car['tuning_lvl']))
    sell_price = int(car['base_price'] * 0.75)
    drv_status = "👨‍✈️ В рейсе" if car['driver_id'] else "💤 В гараже"

    text = (f"🚘 <b>{car['name']}</b> {drv_status}\n\n"
            f"⚙️ Мощность: <b>{actual_hp} л.с.</b> <i>(Тюнинг: {car['tuning_lvl']}/5)</i>\n"
            f"⛽ Бак: <b>{car['fuel']}%</b> | 🔧 Состояние: <b>{car['condition']}%</b>\n\n"
            f"💵 Скупщик дает: {format_price(sell_price)} ₽")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Тюнинг", callback_data=f"tune_{gid}"), InlineKeyboardButton(text="🔧 СТО (Ремонт)", callback_data=f"service_{gid}")],
        [InlineKeyboardButton(text="🤝 Продать", callback_data=f"sellcar_{gid}_{sell_price}")],
        [InlineKeyboardButton(text="🔙 В гараж", callback_data="garage_0")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("tune_"))
async def tune_car(callback: types.CallbackQuery):
    gid = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT tuning_lvl, driver_id FROM garage WHERE id = $1", gid)
        if car['driver_id']:
            await callback.answer("❌ Отзови машину из рейса сначала!", show_alert=True)
            return
        if car['tuning_lvl'] >= 5:
            await callback.answer("👑 Тюнинг уже на максимуме!", show_alert=True)
            return
            
        cost_parts = (car['tuning_lvl'] + 1) * 15
        cost_money = (car['tuning_lvl'] + 1) * 150000
        
        user = await conn.fetchrow("SELECT money, tuning_parts FROM users WHERE user_id = $1", user_id)
        if user['money'] < cost_money or user['tuning_parts'] < cost_parts:
            await callback.answer(f"❌ Нужно: {cost_parts} деталей и {format_price(cost_money)} ₽", show_alert=True)
            return
            
        await conn.execute("UPDATE users SET money = money - $1, tuning_parts = tuning_parts - $2 WHERE user_id = $3", cost_money, cost_parts, user_id)
        await conn.execute("UPDATE garage SET tuning_lvl = tuning_lvl + 1 WHERE id = $1", gid)
        
    await callback.answer("✅ Тачка успешно прокачана!", show_alert=True)
    # Имитируем нажатие на машину для обновления UI
    callback.data = f"mycar_{gid}"
    await my_car_menu(callback)

@dp.callback_query(F.data.startswith("service_"))
async def service_car(callback: types.CallbackQuery):
    gid = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT g.fuel, g.condition, c.base_price FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.id = $1", gid)
        if car['fuel'] == 100 and car['condition'] == 100:
            await callback.answer("✅ Машина и так в идеале!", show_alert=True)
            return
            
        # Ремонт и заправка стоят 1% от цены машины
        cost = int(car['base_price'] * 0.01)
        money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)
        
        if money < cost:
            await callback.answer(f"❌ Нужно {format_price(cost)} ₽ для ТО!", show_alert=True)
            return
            
        await conn.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", cost, user_id)
        await conn.execute("UPDATE garage SET fuel = 100, condition = 100 WHERE id = $1", gid)
        
    await callback.answer(f"✅ Полное ТО пройдено за {format_price(cost)} ₽!", show_alert=True)
    callback.data = f"mycar_{gid}"
    await my_car_menu(callback)

# Остальные обработчики (sellcar, shop, contracts, biz, races, back_main, main...) остаются как в прошлом коде, но для краткости и интеграции:
@dp.callback_query(F.data.startswith("sellcar_"))
async def sell_my_car(callback: types.CallbackQuery):
    _, gid, price = callback.data.split("_")
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT driver_id FROM garage WHERE id = $1 AND user_id = $2", int(gid), callback.from_user.id)
        if not car: return
        if car['driver_id']:
            await callback.answer("❌ Нельзя продать машину в рейсе!", show_alert=True)
            return
        await conn.execute("DELETE FROM garage WHERE id = $1", int(gid))
        await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", int(price), callback.from_user.id)
    await callback.answer(f"✅ Продано за {format_price(int(price))} ₽!", show_alert=True)
    callback.data = "garage_0"
    await my_garage(callback)

# --- УРЕЗАННАЯ ВСТАВКА ДЛЯ РАБОТЫ (чтобы код был цельным) ---
@dp.callback_query(F.data == "back_main")
async def back_to_main(c: types.CallbackQuery):
    await c.message.edit_text("Главное меню:", reply_markup=get_main_menu())

async def ping_render(request): return web.Response(text="GearZ is running")

async def main():
    bot.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await init_db()
    app = web.Application()
    app.router.add_get('/', ping_render)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()
    print("GearZ Loaded. Empires await.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
