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

# --- КОНФИГ ЭКОНОМИКИ ---
BIZ_INFO = {
    1: {"name": "Ржавый шиномонтаж", "cost": 30000, "income_ph": 10800},
    2: {"name": "Автомойка 'Под мостом'", "cost": 150000, "income_ph": 43200},
    3: {"name": "Тюнинг-ателье", "cost": 800000, "income_ph": 180000},
    4: {"name": "Элитный автосалон", "cost": 5000000, "income_ph": 900000},
    5: {"name": "Теневой синдикат", "cost": 30000000, "income_ph": 4320000}
}

async def init_db():
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_contract TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS biz_lvl INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_profit TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_race TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS races_won INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS races_lost INTEGER DEFAULT 0;
        """)

def format_price(price: int) -> str:
    if price >= 1_000_000_000: return f"{price / 1_000_000_000:g}млрд ₽"
    elif price >= 1_000_000: return f"{price / 1_000_000:g}млн ₽"
    elif price >= 1_000: return f"{price / 1_000:g}тыс ₽"
    return f"{price} ₽"

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚘 Мой гараж", callback_data="garage_0"),
         InlineKeyboardButton(text="🏪 Автосалон", callback_data="shop_cats")],
        [InlineKeyboardButton(text="💼 Контракты", callback_data="contracts"),
         InlineKeyboardButton(text="🏢 Мой бизнес", callback_data="biz_main")],
        [InlineKeyboardButton(text="🏁 Уличные гонки", callback_data="racing"),
         InlineKeyboardButton(text="👤 Профиль", callback_data="profile")]
    ])

# --- СТАРТ И АДМИНКА ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    query = "INSERT INTO users (user_id, username, money) VALUES ($1, $2, 1000) ON CONFLICT (user_id) DO NOTHING;"
    async with bot.db_pool.acquire() as conn:
        await conn.execute(query, user_id, username)
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)

    text = (f"🏴‍☠️ <b>Добро пожаловать в GearZ, {username}.</b>\n\n"
            f"Улицы не прощают слабости. У тебя в кармане <b>{format_price(user_money)}</b>.\n"
            f"Строй свою империю. Твой ход:")
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.message(Command("givemoney"))
async def admin_give(message: types.Message):
    # Защита: Команда работает только для твоего юзернейма
    if message.from_user.username and message.from_user.username.lower() != 'whunx':
        return
    
    args = message.text.split()
    if len(args) == 2 and args[1].isdigit():
        amount = int(args[1])
        async with bot.db_pool.acquire() as conn:
            await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", amount, message.from_user.id)
        await message.answer(f"👑 <b>Админ-панель:</b> Баланс пополнен на {format_price(amount)}!", parse_mode="HTML")

# --- ПРОФИЛЬ ---
@dp.callback_query(F.data == "profile")
async def user_profile(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        garage_stats = await conn.fetchrow("""
            SELECT COUNT(g.id) as car_count, COALESCE(SUM(c.base_price), 0) as total_value
            FROM garage g JOIN cars c ON g.car_id = c.car_id WHERE g.user_id = $1
        """, user_id)

    biz_name = BIZ_INFO[user['biz_lvl']]['name'] if user['biz_lvl'] > 0 else "Нет бизнеса"
    
    text = (
        f"👤 <b>ПРОФИЛЬ ИГРОКА</b>\n\n"
        f"💰 Наличные: <b>{format_price(user['money'])}</b>\n"
        f"🏢 Бизнес: <b>{biz_name}</b>\n\n"
        f"🚘 Машин в гараже: <b>{garage_stats['car_count']} шт.</b>\n"
        f"💎 Капитал автопарка: <b>{format_price(garage_stats['total_value'])}</b>\n\n"
        f"🏁 Гонки (Побед/Поражений): <b>{user['races_won']} / {user['races_lost']}</b>"
    )
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# --- УЛИЧНЫЕ ГОНКИ ---
@dp.callback_query(F.data == "racing")
async def street_racing(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.utcnow()
    
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money, last_race FROM users WHERE user_id = $1", user_id)
        
        if user['last_race']:
            diff = (now - user['last_race']).total_seconds()
            if diff < 180: # Кулдаун 3 минуты
                await callback.answer(f"⏳ Мотор еще перегрет! Остынь {(180 - int(diff))//60} мин {(180 - int(diff))%60} сек.", show_alert=True)
                return

        # Ищем самую мощную тачку юзера
        my_best_car = await conn.fetchrow("""
            SELECT c.name, c.hp FROM garage g 
            JOIN cars c ON g.car_id = c.car_id 
            WHERE g.user_id = $1 ORDER BY c.hp DESC LIMIT 1
        """, user_id)

        if not my_best_car:
            await callback.answer("❌ У тебя нет ни одной тачки для гонок!", show_alert=True)
            return

        # Ищем случайного противника из базы
        enemy = await conn.fetchrow("SELECT name, hp FROM cars ORDER BY RANDOM() LIMIT 1")
        
        my_hp = my_best_car['hp']
        en_hp = enemy['hp']
        
        # Формула шанса победы (с защитой от 100% и 0%)
        win_chance = int((my_hp / (my_hp + en_hp)) * 100)
        win_chance = max(10, min(90, win_chance)) 
        
        is_win = random.randint(1, 100) <= win_chance
        reward = int(en_hp * 20) # Награда зависит от крутости врага (Победил Бугатти - сорвал куш)

        if is_win:
            await conn.execute("UPDATE users SET money = money + $1, last_race = $2, races_won = races_won + 1 WHERE user_id = $3", reward, now, user_id)
            result_text = f"🏆 <b>ТЫ ПОБЕДИЛ!</b>\n\nТвой {my_best_car['name']} ({my_hp} л.с.) обошел {enemy['name']} ({en_hp} л.с.).\n💸 Выигрыш: <b>+{format_price(reward)}</b>"
        else:
            loss = int(reward * 0.2) # Теряем 20% от потенциального выигрыша
            # Запрещаем уходить в минус
            actual_loss = min(loss, user['money'])
            await conn.execute("UPDATE users SET money = money - $1, last_race = $2, races_lost = races_lost + 1 WHERE user_id = $3", actual_loss, now, user_id)
            result_text = f"💥 <b>ПОРАЖЕНИЕ...</b>\n\nПротивник на {enemy['name']} ({en_hp} л.с.) ушел в отрыв.\n📉 Потери: <b>-{format_price(actual_loss)}</b>"

    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    await callback.message.edit_text(result_text, reply_markup=markup, parse_mode="HTML")

# --- ГАРАЖ (ТРЕЙД-ИН И ПАГИНАЦИЯ) ---
@dp.callback_query(F.data.startswith("garage_"))
async def my_garage(callback: types.CallbackQuery):
    page = int(callback.data.split("_")[1])
    limit = 5
    offset = page * limit
    user_id = callback.from_user.id

    async with bot.db_pool.acquire() as conn:
        cars = await conn.fetch("""
            SELECT g.id as garage_id, c.name, c.class, c.base_price 
            FROM garage g JOIN cars c ON g.car_id = c.car_id 
            WHERE g.user_id = $1 ORDER BY c.base_price DESC LIMIT $2 OFFSET $3
        """, user_id, limit + 1, offset)
        
    if not cars and page == 0:
        await callback.message.edit_text("🕸 <b>Твой гараж пуст.</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]]), parse_mode="HTML")
        return

    builder = InlineKeyboardBuilder()
    for car in cars[:limit]:
        # Клик по машине открывает её меню (для продажи)
        builder.button(text=f"{car['name']} [{car['class']}]", callback_data=f"mycar_{car['garage_id']}")

    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"garage_{page-1}"))
    if len(cars) > limit: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"garage_{page+1}"))
    if nav: builder.row(*nav)
    
    builder.row(InlineKeyboardButton(text="🔙 Главное меню", callback_data="back_main"))
    builder.adjust(1)

    await callback.message.edit_text(f"🚘 <b>ТВОЙ АВТОПАРК (Стр. {page+1}):</b>\n<i>Нажми на авто, чтобы продать его</i>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("mycar_"))
async def my_car_menu(callback: types.CallbackQuery):
    garage_id = int(callback.data.split("_")[1])
    
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("""
            SELECT g.id, c.name, c.base_price, c.hp, c.class 
            FROM garage g JOIN cars c ON g.car_id = c.car_id 
            WHERE g.id = $1 AND g.user_id = $2
        """, garage_id, callback.from_user.id)

    if not car:
        await callback.answer("Ошибка: Автомобиль не найден!", show_alert=True)
        return

    sell_price = int(car['base_price'] * 0.75) # Продажа за 75% стоимости
    
    text = (f"🚘 <b>{car['name']}</b>\n"
            f"⚙️ Мощность: {car['hp']} л.с.\n"
            f"💼 Класс: {car['class']}\n\n"
            f"Рыночная цена в салоне: {format_price(car['base_price'])}\n"
            f"💵 <b>Скупщик предлагает: {format_price(sell_price)}</b>")

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤝 Продать перекупу", callback_data=f"sellcar_{car['id']}_{sell_price}")],
        [InlineKeyboardButton(text="🔙 В гараж", callback_data="garage_0")]
    ])
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data.startswith("sellcar_"))
async def sell_my_car(callback: types.CallbackQuery):
    _, garage_id, price = callback.data.split("_")
    garage_id, price = int(garage_id), int(price)
    user_id = callback.from_user.id

    async with bot.db_pool.acquire() as conn:
        # Проверяем, существует ли еще машина (защита от двойного клика)
        car_exists = await conn.fetchval("SELECT id FROM garage WHERE id = $1 AND user_id = $2", garage_id, user_id)
        if not car_exists:
            await callback.answer("Машина уже продана!", show_alert=True)
            return
            
        async with conn.transaction():
            await conn.execute("DELETE FROM garage WHERE id = $1", garage_id)
            await conn.execute("UPDATE users SET money = money + $1 WHERE user_id = $2", price, user_id)
            
    await callback.answer(f"✅ Успешно продано за {format_price(price)}!", show_alert=True)
    await my_garage(callback) # Искусственно вызываем garage_0 (нужно подменить callback.data, но вызовем через костыль ниже)
    callback.data = "garage_0"
    await my_garage(callback)

# --- КОНТРАКТЫ И БИЗНЕС (Остаются без изменений, логика та же) ---
@dp.callback_query(F.data == "contracts")
async def do_contract(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    now = datetime.utcnow()
    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money, last_contract FROM users WHERE user_id = $1", user_id)
        if user['last_contract']:
            diff = (now - user['last_contract']).total_seconds()
            if diff < 300:
                rem = int(300 - diff)
                await callback.answer(f"⏳ Легавые на хвосте! Жди {rem//60} мин {rem%60} сек.", show_alert=True)
                return
        reward = random.randint(800, 2500)
        await conn.execute("UPDATE users SET money = money + $1, last_contract = $2 WHERE user_id = $3", reward, now, user_id)
        new_balance = user['money'] + reward

    phrases = ["Вскрыл сейф", "Скрутил колеса", "Выполнил угон", "Ограбил инкассаторов"]
    text = f"💼 <b>Дело сделано!</b>\n\n💬 <i>{random.choice(phrases)}</i>\n💸 Доля: <b>+{format_price(reward)}</b>\n💳 Баланс: <b>{format_price(new_balance)}</b>"
    
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=get_main_menu(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("biz_"))
async def handle_biz(callback: types.CallbackQuery):
    action = callback.data.split("_")[1]
    user_id = callback.from_user.id
    now = datetime.utcnow()

    async with bot.db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)
        
        if action == "upgrade":
            next_lvl = user['biz_lvl'] + 1
            if next_lvl not in BIZ_INFO:
                await callback.answer("👑 У тебя топовый бизнес!", show_alert=True)
                return
            cost = BIZ_INFO[next_lvl]['cost']
            if user['money'] < cost:
                await callback.answer(f"❌ Нужно {format_price(cost)}!", show_alert=True)
                return
            await conn.execute("UPDATE users SET money = money - $1, biz_lvl = $2, last_profit = $3 WHERE user_id = $4", cost, next_lvl, now, user_id)
            await callback.answer(f"✅ Куплено: {BIZ_INFO[next_lvl]['name']}", show_alert=True)
            user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)

        elif action == "collect":
            if user['biz_lvl'] == 0:
                await callback.answer("❌ Нет бизнеса!", show_alert=True)
                return
            diff = min((now - user['last_profit']).total_seconds(), 86400)
            earned = int((BIZ_INFO[user['biz_lvl']]['income_ph'] / 3600) * diff)
            if earned < 10:
                await callback.answer("⏳ Касса пуста!", show_alert=True)
                return
            await conn.execute("UPDATE users SET money = money + $1, last_profit = $2 WHERE user_id = $3", earned, now, user_id)
            await callback.answer(f"💸 Собрано: {format_price(earned)}", show_alert=True)
            user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)

        if user['biz_lvl'] == 0:
            text = f"🏢 Первый бизнес: <b>{BIZ_INFO[1]['name']}</b>\n💰 Стоимость: {format_price(BIZ_INFO[1]['cost'])}\n📈 Доход: {format_price(BIZ_INFO[1]['income_ph'])} / час"
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Купить бизнес", callback_data="biz_upgrade")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]
            ])
        else:
            lvl = user['biz_lvl']
            info = BIZ_INFO[lvl]
            diff = min((now - user['last_profit']).total_seconds(), 86400)
            current_profit = int((info['income_ph'] / 3600) * diff)
            text = (f"🏢 Бизнес: <b>{info['name']}</b> (Ур. {lvl})\n📈 Доход: {format_price(info['income_ph'])}/час\n\n"
                    f"💵 В кассе: <b>{format_price(current_profit)}</b>\n💳 Баланс: {format_price(user['money'])}")
            keyboard = [[InlineKeyboardButton(text="💰 Собрать", callback_data="biz_collect")]]
            if lvl + 1 in BIZ_INFO:
                keyboard.append([InlineKeyboardButton(text=f"⬆️ Улучшить ({format_price(BIZ_INFO[lvl+1]['cost'])})", callback_data="biz_upgrade")])
            keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    if callback.message.photo:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# --- МАГАЗИН И ПРОЧЕЕ ---
@dp.callback_query(F.data == "shop_cats")
async def shop_categories(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        classes = await conn.fetch("SELECT DISTINCT class FROM cars")
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)
    builder = InlineKeyboardBuilder()
    for row in classes: builder.button(text=f"🔹 {row['class']}", callback_data=f"shop_list_{row['class']}_0")
    builder.button(text="🔙 Главное меню", callback_data="back_main")
    builder.adjust(2)
    text = f"🏪 <b>Автосалон</b>\n💳 Баланс: <b>{format_price(user_money)}</b>\n\nВыбери класс:"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("shop_list_"))
async def shop_list(callback: types.CallbackQuery):
    _, _, car_class, page_str = callback.data.split("_")
    page, limit = int(page_str), 5
    offset = page * limit
    async with bot.db_pool.acquire() as conn:
        cars = await conn.fetch("SELECT car_id, name, base_price FROM cars WHERE class = $1 ORDER BY base_price ASC LIMIT $2 OFFSET $3", car_class, limit + 1, offset)
    builder = InlineKeyboardBuilder()
    for car in cars[:limit]: builder.button(text=f"{car['name']} — {format_price(car['base_price'])}", callback_data=f"shop_car_{car['car_id']}")
    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"shop_list_{car_class}_{page-1}"))
    if len(cars) > limit: nav.append(InlineKeyboardButton(text="➡️", callback_data=f"shop_list_{car_class}_{page+1}"))
    if nav: builder.row(*nav)
    builder.row(InlineKeyboardButton(text="🔙 К классам", callback_data="shop_cats"))
    builder.adjust(1)
    text = f"Класс: <b>{car_class}</b>\nВыберите авто:"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("shop_car_"))
async def shop_car_detail(callback: types.CallbackQuery):
    await callback.answer()
    car_id = int(callback.data.split("_")[2])
    async with bot.db_pool.acquire() as conn:
        car = await conn.fetchrow("SELECT * FROM cars WHERE car_id = $1", car_id)
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)
    text = (f"🚘 <b>{car['name']}</b>\n\n⚙️ Мощность: <b>{car['hp']} л.с.</b>\n💼 Класс: {car['class']}\n\n"
            f"<i>{car['description']}</i>\n\n💰 Цена: <b>{format_price(car['base_price'])}</b>\n💳 Баланс: {format_price(user_money)}")
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 КУПИТЬ", callback_data=f"buy_{car_id}")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"shop_list_{car['class']}_0")]
    ])
    try:
        await callback.message.answer_photo(photo=car['photo_url'], caption=text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    except:
        await callback.message.answer(f"<i>[Фото недоступно]</i>\n\n{text}", reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()

@dp.callback_query(F.data.startswith("buy_"))
async def buy_car(callback: types.CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    async with bot.db_pool.acquire() as conn:
        async with conn.transaction():
            car = await conn.fetchrow("SELECT name, base_price FROM cars WHERE car_id = $1", car_id)
            user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)
            if user_money < car['base_price']:
                await callback.answer("❌ Недостаточно средств!", show_alert=True)
                return
            await conn.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", car['base_price'], user_id)
            await conn.execute("INSERT INTO garage (user_id, car_id) VALUES ($1, $2)", user_id, car_id)
    await callback.answer(f"✅ Куплено: {car['name']}!", show_alert=True)
    await shop_car_detail(callback)

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    if callback.message.photo:
        await callback.message.answer("Главное меню:", reply_markup=get_main_menu())
        await callback.message.delete()
    else:
        await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

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
    print("Бот в сети, погнали.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
