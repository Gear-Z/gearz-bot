import asyncio
import logging
import asyncpg
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, URLInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder

API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- ФУНКЦИЯ ФОРМАТИРОВАНИЯ ЦЕН ---
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
        [InlineKeyboardButton(text="🚘 Мой гараж", callback_data="garage")],
        [InlineKeyboardButton(text="🏪 Автосалон", callback_data="shop_cats")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    # Выдаем игроку стартовые 500тыс для тестов
    query = """
        INSERT INTO users (user_id, username, money) 
        VALUES ($1, $2, 500000) 
        ON CONFLICT (user_id) DO NOTHING;
    """
    async with bot.db_pool.acquire() as connection:
        await connection.execute(query, user_id, username)

    await message.answer(
        f"Салют, {username}.\nТвой профиль в <b>GearZ</b> активен. Выбирай действие:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )

# --- 1. КАТЕГОРИИ АВТОСАЛОНА ---
@dp.callback_query(F.data == "shop_cats")
async def shop_categories(callback: types.CallbackQuery):
    async with bot.db_pool.acquire() as connection:
        # Вытаскиваем уникальные классы машин из БД
        classes = await connection.fetch("SELECT DISTINCT class FROM cars")
        user_money = await connection.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)

    builder = InlineKeyboardBuilder()
    for row in classes:
        car_class = row['class']
        builder.button(text=f"🔹 {car_class}", callback_data=f"shop_list_{car_class}_0")
    
    builder.button(text="🔙 Главное меню", callback_data="back_main")
    builder.adjust(2) # По 2 кнопки в ряд

    text = f"🏪 <b>Автосалон</b>\nТвой баланс: <b>{format_price(user_money)}</b>\n\nВыбери класс автомобилей:"
    # Если это было фото, меняем на текст, если текст - просто редактируем
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- 2. СПИСОК МАШИН В КАТЕГОРИИ (С ПАГИНАЦИЕЙ) ---
@dp.callback_query(F.data.startswith("shop_list_"))
async def shop_list(callback: types.CallbackQuery):
    # Разбираем callback: shop_list_Спорт_0
    _, _, car_class, page_str = callback.data.split("_")
    page = int(page_str)
    limit = 5
    offset = page * limit

    async with bot.db_pool.acquire() as connection:
        cars = await connection.fetch(
            "SELECT car_id, name, base_price FROM cars WHERE class = $1 ORDER BY base_price ASC LIMIT $2 OFFSET $3",
            car_class, limit + 1, offset
        )
    
    has_next = len(cars) > limit
    cars_to_show = cars[:limit]

    builder = InlineKeyboardBuilder()
    for car in cars_to_show:
        btn_text = f"{car['name']} — {format_price(car['base_price'])}"
        builder.button(text=btn_text, callback_data=f"shop_car_{car['car_id']}")

    # Кнопки листания
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"shop_list_{car_class}_{page-1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"shop_list_{car_class}_{page+1}"))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(InlineKeyboardButton(text="🔙 К классам", callback_data="shop_cats"))
    builder.adjust(1)

    text = f"Класс: <b>{car_class}</b>\nВыберите автомобиль для просмотра:"
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=builder.as_markup(), parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

# --- 3. КАРТОЧКА КОНКРЕТНОЙ МАШИНЫ ---
@dp.callback_query(F.data.startswith("shop_car_"))
async def shop_car_detail(callback: types.CallbackQuery):
    car_id = int(callback.data.split("_")[2])
    
    async with bot.db_pool.acquire() as connection:
        car = await connection.fetchrow("SELECT * FROM cars WHERE car_id = $1", car_id)
        user_money = await connection.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)

    text = (
        f"🚘 <b>{car['name']}</b>\n\n"
        f"⚙️ Мощность: <b>{car['hp']} л.с.</b>\n"
        f"💼 Класс: {car['class']}\n\n"
        f"<i>{car['description']}</i>\n\n"
        f"💰 Цена: <b>{format_price(car['base_price'])}</b>\n"
        f"💳 Твой баланс: {format_price(user_money)}"
    )

    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💸 КУПИТЬ", callback_data=f"buy_{car_id}")],
        [InlineKeyboardButton(text="🔙 Назад к списку", callback_data=f"shop_list_{car['class']}_0")]
    ])

    # Отправляем фотку по URL
    photo = URLInputFile(car['photo_url'])
    await callback.message.answer_photo(photo=photo, caption=text, reply_markup=markup, parse_mode="HTML")
    await callback.message.delete()

@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    if callback.message.photo:
        await callback.message.answer("Главное меню:", reply_markup=get_main_menu())
        await callback.message.delete()
    else:
        await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

# --- 4. ПОКУПКА МАШИНЫ ---
@dp.callback_query(F.data.startswith("buy_"))
async def buy_car(callback: types.CallbackQuery):
    car_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    async with bot.db_pool.acquire() as connection:
        # Открываем транзакцию (если будет ошибка, бабки не спишутся в пустоту)
        async with connection.transaction():
            car = await connection.fetchrow("SELECT name, base_price FROM cars WHERE car_id = $1", car_id)
            user = await connection.fetchrow("SELECT money FROM users WHERE user_id = $1", user_id)
            
            if user['money'] < car['base_price']:
                # Выводим всплывающее окно с ошибкой
                await callback.answer("❌ Недостаточно средств на балансе!", show_alert=True)
                return
            
            # Списываем бабки и добавляем тачку в гараж
            await connection.execute("UPDATE users SET money = money - $1 WHERE user_id = $2", car['base_price'], user_id)
            await connection.execute("INSERT INTO garage (user_id, car_id) VALUES ($1, $2)", user_id, car_id)
    
    # Поздравляем юзера (всплывающее окно)
    await callback.answer(f"✅ Успешная покупка: {car['name']}!", show_alert=True)
    
    # Искусственно вызываем обновление карточки авто, чтобы баланс на экране сразу уменьшился
    await shop_car_detail(callback)

# --- 5. ПРОСМОТР ГАРАЖА ---
@dp.callback_query(F.data == "garage")
async def my_garage(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    # Делаем хитрый JOIN, чтобы вытащить названия машин из таблицы cars по их ID из таблицы garage
    query = """
        SELECT c.name, c.class 
        FROM garage g
        JOIN cars c ON g.car_id = c.car_id
        WHERE g.user_id = $1
    """
    
    async with bot.db_pool.acquire() as connection:
        my_cars = await connection.fetch(query, user_id)
        
    if not my_cars:
        text = "🕸 <b>Твой гараж пуст.</b> Заработай денег и загляни в автосалон!"
    else:
        text = "🚘 <b>ТВОЙ АВТОПАРК:</b>\n\n"
        for i, car in enumerate(my_cars, 1):
            text += f"{i}. <b>{car['name']}</b> <i>[{car['class']}]</i>\n"
            
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")]
    ])
    
    if callback.message.photo:
        await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
        await callback.message.delete()
    else:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# Заглушка для Render
async def ping_render(request):
    return web.Response(text="GearZ is running")

async def main():
    bot.db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    
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
