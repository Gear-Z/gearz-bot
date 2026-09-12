import asyncio
import logging
import asyncpg
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# Главное меню с кнопками
def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚘 Мой гараж", callback_data="garage")],
        [InlineKeyboardButton(text="🏪 Автосалон", callback_data="shop")]
    ])

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    query = """
        INSERT INTO users (user_id, username) 
        VALUES ($1, $2) 
        ON CONFLICT (user_id) DO NOTHING;
    """
    async with bot.db_pool.acquire() as connection:
        await connection.execute(query, user_id, username)

    await message.answer(
        f"Салют, {username}.\nТвой профиль в <b>GearZ</b> активен. Выбирай действие:",
        reply_markup=get_main_menu(),
        parse_mode="HTML"
    )

# Обработчик кнопки "Автосалон"
@dp.callback_query(F.data == "shop")
async def shop_menu(callback: types.CallbackQuery):
    # Экономим трафик: запрашиваем только нужные колонки
    query = "SELECT car_id, name, class, base_price FROM cars ORDER BY base_price ASC LIMIT 5"
    
    async with bot.db_pool.acquire() as connection:
        cars = await connection.fetch(query)
        # Получаем баланс игрока
        user_money = await connection.fetchval("SELECT money FROM users WHERE user_id = $1", callback.from_user.id)

    text = f"<b>Автосалон</b>\nТвой баланс: {user_money}$\n\nДоступные тачки:\n\n"
    keyboard = []
    
    for car in cars:
        text += f"[{car['class']}] <b>{car['name']}</b> — {car['base_price']}$\n"
        keyboard.append([InlineKeyboardButton(text=f"Купить {car['name']}", callback_data=f"buy_{car['car_id']}")])
    
    keyboard.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_main")])
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

# Обработчик кнопки "Назад"
@dp.callback_query(F.data == "back_main")
async def back_to_main(callback: types.CallbackQuery):
    await callback.message.edit_text("Главное меню:", reply_markup=get_main_menu())

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
