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

# --- КОНФИГ ЭКОНОМИКИ (СБАЛАНСИРОВАННЫЙ) ---
BIZ_INFO = {
    1: {"name": "Ржавый шиномонтаж", "cost": 30000, "income_ph": 10800},      # 3 руб/сек (Окупаемость ~3 часа)
    2: {"name": "Автомойка 'Под мостом'", "cost": 150000, "income_ph": 43200},# 12 руб/сек 
    3: {"name": "Тюнинг-ателье", "cost": 800000, "income_ph": 180000},        # 50 руб/сек
    4: {"name": "Элитный автосалон", "cost": 5000000, "income_ph": 900000},   # 250 руб/сек
    5: {"name": "Теневой синдикат", "cost": 30000000, "income_ph": 4320000}   # 1200 руб/сек
}

async def init_db():
    async with bot.db_pool.acquire() as conn:
        await conn.execute("""
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_contract TIMESTAMP;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS biz_lvl INTEGER DEFAULT 0;
            ALTER TABLE users ADD COLUMN IF NOT EXISTS last_profit TIMESTAMP;
        """)

def format_price(price: int) -> str:
    if price >= 1_000_000_000:
        return f"{price / 1_000_000_000:g}млрд ₽"
    elif price >= 1_000_000:
        return f"{price / 1_000_000:g}млн ₽"
    elif price >= 1_000:
        return f"{price / 1_000:g}тыс ₽"
    return f"{price} ₽"

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚘 Мой гараж", callback_data="garage"),
         InlineKeyboardButton(text="🏪 Автосалон", callback_data="shop_cats")],
        [InlineKeyboardButton(text="💼 Контракты", callback_data="contracts")],
        [InlineKeyboardButton(text="🏢 Мой бизнес", callback_data="biz_main")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    query = """
        INSERT INTO users (user_id, username, money) 
        VALUES ($1, $2, 1000) 
        ON CONFLICT (user_id) DO NOTHING;
    """
    async with bot.db_pool.acquire() as conn:
        await conn.execute(query, user_id, username)
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", user_id)

    text = (
        f"🏴‍☠️ <b>Добро пожаловать в GearZ, {username}.</b>\n\n"
        f"Улицы не прощают слабости. У тебя в кармане <b>{format_price(user_money)}</b>. "
        f"Делай грязные дела, покупай первые тачки и строй свою империю.\n\n"
        f"Твой ход:"
    )
    await message.answer(text, reply_markup=get_main_menu(), parse_mode="HTML")

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
                await callback.answer(f"⏳ Легавые на хвосте! Заляг на дно еще на {rem//60} мин {rem%60} сек.", show_alert=True)
                return
        
        # Понерфили награду за 5 минут, чтобы бизнес имел больше смысла
        reward = random.randint(800, 2500)
        await conn.execute("UPDATE users SET money = money + $1, last_contract = $2 WHERE user_id = $3", reward, now, user_id)
        new_balance = user['money'] + reward

    phrases = [
        "Вскрыл сейф в ювелирном", 
        "Вынес склад с запчастями", 
        "Выполнил заказ на угон",
        "Провел теневую сделку в порту",
        "Ограбил инкассаторов",
        "Скрутил катализаторы с Майбаха"
    ]
    
    text = (
        f"💼 <b>Дело сделано!</b>\n\n"
        f"💬 <i>{random.choice(phrases)}</i>\n"
        f"💸 Твоя доля: <b>+{format_price(reward)}</b>\n\n"
        f"💳 Баланс: <b>{format_price(new_balance)}</b>"
    )
    
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
                await callback.answer("👑 У тебя уже самый топовый бизнес!", show_alert=True)
                return
                
            cost = BIZ_INFO[next_lvl]['cost']
            if user['money'] < cost:
                await callback.answer(f"❌ Нужно {format_price(cost)} для покупки!", show_alert=True)
                return
                
            await conn.execute("UPDATE users SET money = money - $1, biz_lvl = $2, last_profit = $3 WHERE user_id = $4", 
                               cost, next_lvl, now, user_id)
            await callback.answer(f"✅ Успешно куплено: {BIZ_INFO[next_lvl]['name']}", show_alert=True)
            user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)

        elif action == "collect":
            if user['biz_lvl'] == 0:
                await callback.answer("❌ У тебя еще нет бизнеса!", show_alert=True)
                return
                
            diff = (now - user['last_profit']).total_seconds()
            if diff > 86400: diff = 86400 
            
            earned = int((BIZ_INFO[user['biz_lvl']]['income_ph'] / 3600) * diff)
            if earned < 10:
                await callback.answer("⏳ Касса пуста. Зайди позже!", show_alert=True)
                return
                
            await conn.execute("UPDATE users SET money = money + $1, last_profit = $2 WHERE user_id = $3", earned, now, user_id)
            await callback.answer(f"💸 Собрано: {format_price(earned)}", show_alert=True)
            user = await conn.fetchrow("SELECT money, biz_lvl, last_profit FROM users WHERE user_id = $1", user_id)

        if user['biz_lvl'] == 0:
            text = f"🏢 У тебя еще нет своего дела.\n\nПервый бизнес: <b>{BIZ_INFO[1]['name']}</b>\n💰 Стоимость: {format_price(BIZ_INFO[1]['cost'])}\n📈 Доход: {format_price(BIZ_INFO[1]['income_ph'])} / час"
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💸 Купить бизнес", callback_data="biz_upgrade")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]
            ])
        else:
            lvl = user['biz_lvl']
            info = BIZ_INFO[lvl]
            diff = (now - user['last_profit']).total_seconds()
            if diff > 86400: diff = 86400
            current_profit = int((info['income_ph'] / 3600) * diff)
            
            text = (
                f"🏢 Твой бизнес: <b>{info['name']}</b> (Ур. {lvl})\n"
                f"📈 Доходность: {format_price(info['income_ph'])} / час\n\n"
                f"💵 Накоплено в кассе: <b>{format_price(current_profit)}</b>\n"
                f"<i>(Накопление останавливается через 24 часа AFK)</i>\n\n"
                f"💳 Твой баланс: {format_price(user['money'])}"
            )
            
            keyboard = [[InlineKeyboardButton(text="💰 Собрать прибыль", callback_data="biz_collect")]]
            if lvl + 1 in BIZ_INFO:
                next_info = BIZ_INFO[lvl + 1]
                keyboard.append([InlineKeyboardButton(text=f"⬆️ Улучшить ({format_price(next_info['cost'])})", callback_data="biz_upgrade")])
            keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
            
            markup = InlineKeyboardMarkup(inline_keyboard=keyboard)

    if callback.message.photo:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@dp.callback_query(F.data == "shop_cats")
async def shop_categories(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as conn:
        classes = await conn.fetch("SELECT DISTINCT class FROM cars")
        user_money = await conn.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)

    builder = InlineKeyboardBuilder()
    for row in classes:
        builder.button(text=f"🔹 {row['class']}", callback_data=f"shop_list_{row['class']}_0")
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
    for car in cars[:limit]:
        builder.button(text=f"{car['name']} — {format_price(car['base_price'])}", callback_data=f"shop_car_{car['car_id']}")

    nav = []
    if page > 0: nav.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"shop_list_{car_class}_{page-1}"))
    if len(cars) > limit: nav.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"shop_list_{car_class}_{page+1}"))
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

    text = (
        f"🚘 <b>{car['name']}</b>\n\n⚙️ Мощность: <b>{car['hp']} л.с.</b>\n💼 Класс: {car['class']}\n\n"
        f"<i>{car['description']}</i>\n\n💰 Цена: <b>{format_price(car['base_price'])}</b>\n💳 Баланс: {format_price(user_money)}"
    )

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

@dp.callback_query(F.data == "garage")
async def my_garage(callback: types.CallbackQuery):
    query = """
        SELECT c.name, c.class 
        FROM garage g
        JOIN cars c ON g.car_id = c.car_id
        WHERE g.user_id = $1
    """
    async with bot.db_pool.acquire() as conn:
        my_cars = await conn.fetch(query, callback.from_user.id)
        
    if not my_cars: text = "🕸 <b>Твой гараж пуст.</b> Заработай денег на контрактах!"
    else:
        text = "🚘 <b>ТВОЙ АВТОПАРК:</b>\n\n"
        for i, car in enumerate(my_cars, 1): text += f"{i}. <b>{car['name']}</b> <i>[{car['class']}]</i>\n"
            
    markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]])
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

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
