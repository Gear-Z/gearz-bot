import asyncio
import logging
import asyncpg
import os
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command

# Токены будем прятать в самом Render, чтобы не светить в коде
API_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher()

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

    await message.answer(f"Салют, {username}.\nТвой профиль в GearZ создан. Гараж пуст, бабки на базе.")

# Эта функция обманывает Render, притворяясь сайтом, чтобы он не вырубал бота
async def ping_render(request):
    return web.Response(text="GearZ is running")

async def main():
    bot.db_pool = await asyncpg.create_pool(DATABASE_URL)
    
    # Запускаем обманку для Render
    app = web.Application()
    app.router.add_get('/', ping_render)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.environ.get("PORT", 8080)))
    await site.start()

    # Запускаем самого бота
    print("Бот в сети, погнали.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
