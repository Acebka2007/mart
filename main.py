import logging
import asyncio
import aiosqlite
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import LabeledPrice, PreCheckoutQuery
from dotenv import load_dotenv
import os
import openai
from PIL import Image
import pytesseract

# ✅ Загрузка переменных окружения
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROVIDER_TOKEN = os.getenv("PROVIDER_TOKEN", "")  # Токен платежного провайдера

if not TELEGRAM_TOKEN or not OPENAI_API_KEY:
    raise ValueError("❌ Отсутствуют TELEGRAM_TOKEN или OPENAI_API_KEY в .env.")

# ✅ Настройка логирования
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ✅ Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN, session=AiohttpSession())
dp = Dispatcher()

# ✅ Настройка OpenAI
openai.api_key = OPENAI_API_KEY

# ✅ Функции для работы с базой данных
async def init_db():
    async with aiosqlite.connect("subscriptions.db") as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            start_date TEXT,
            end_date TEXT
        )
        ''')
        await db.commit()

async def has_active_subscription(user_id: int) -> bool:
    async with aiosqlite.connect("subscriptions.db") as db:
        async with db.execute("SELECT end_date FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                end_date = datetime.strptime(row[0], "%Y-%m-%d")
                return end_date >= datetime.now()
    return False

async def activate_trial(user_id: int) -> None:
    start_date = datetime.now()
    end_date = start_date + timedelta(days=3)
    async with aiosqlite.connect("subscriptions.db") as db:
        await db.execute(
            "REPLACE INTO users (user_id, start_date, end_date) VALUES (?, ?, ?)",
            (user_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        )
        await db.commit()

# ✅ Функция для взаимодействия с OpenAI
async def get_ai_response(prompt: str) -> str:
    try:
        response = await openai.ChatCompletion.acreate(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content
    except Exception as e:
        logging.error(f"Ошибка при запросе к OpenAI: {e}")
        return "Извините, произошла ошибка при обработке вашего запроса."

# ✅ Функция для распознавания текста с изображения
async def extract_text_from_image(image_path: str) -> str:
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='rus+eng')
        return text
    except Exception as e:
        logging.error(f"Ошибка при распознавании текста: {e}")
        return "Не удалось распознать текст на изображении."

# ✅ Обработчики команд
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    if not await has_active_subscription(user_id):
        await activate_trial(user_id)
        await message.reply("Добро пожаловать! Ваш 3-дневный пробный период активирован.")
    else:
        await message.reply("С возвращением! Ваша подписка активна.")

@dp.message(Command("help"))
async def help_command(message: types.Message):
    help_text = """
    🤖 Я цифровой учитель! Вот что я умею:
    
    📚 /solve [задача] - решить задачу
    🧠 /explain [тема] - объяснить тему
    📸 Отправьте мне фото с текстом, и я его распознаю
    
    💳 /subscribe - оформить подписку
    """
    await message.reply(help_text)

@dp.message(Command("solve"))
async def solve_problem(message: types.Message):
    if not await has_active_subscription(message.from_user.id):
        await message.reply("Для использования этой функции необходима активная подписка.")
        return

    problem = message.text.replace("/solve", "").strip()
    if not problem:
        await message.reply("Пожалуйста, укажите задачу после команды /solve.")
        return

    solution = await get_ai_response(f"Реши следующую задачу: {problem}")
    await message.reply(solution)

@dp.message(Command("explain"))
async def explain_topic(message: types.Message):
    if not await has_active_subscription(message.from_user.id):
        await message.reply("Для использования этой функции необходима активная подписка.")
        return

    topic = message.text.replace("/explain", "").strip()
    if not topic:
        await message.reply("Пожалуйста, укажите тему после команды /explain.")
        return

    explanation = await get_ai_response(f"Объясни простыми словами тему: {topic}")
    await message.reply(explanation)

@dp.message(content_types=['photo'])
async def handle_photo(message: types.Message):
    if not await has_active_subscription(message.from_user.id):
        await message.reply("Для использования этой функции необходима активная подписка.")
        return

    file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    file_path = file.file_path
    await bot.download_file(file_path, "temp_image.jpg")
    
    text = await extract_text_from_image("temp_image.jpg")
    os.remove("temp_image.jpg")
    
    if text:
        await message.reply(f"Распознанный текст:\n\n{text}")
    else:
        await message.reply("Не удалось распознать текст на изображении.")

# ✅ Обработка платежей (пример)
@dp.message(Command("subscribe"))
async def subscribe(message: types.Message):
    if await has_active_subscription(message.from_user.id):
        await message.reply("У вас уже есть активная подписка.")
        return

    await bot.send_invoice(
        message.chat.id,
        title="Подписка на цифрового учителя",
        description="Месячная подписка на все функции бота",
        provider_token=PROVIDER_TOKEN,
        currency="RUB",
        prices=[LabeledPrice(label="Подписка", amount=50000)],  # 500 рублей
        start_parameter="subscribe",
        payload="monthly_subscription"
    )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(content_types=['successful_payment'])
async def process_successful_payment(message: types.Message):
    user_id = message.from_user.id
    start_date = datetime.now()
    end_date = start_date + timedelta(days=30)
    async with aiosqlite.connect("subscriptions.db") as db:
        await db.execute(
            "REPLACE INTO users (user_id, start_date, end_date) VALUES (?, ?, ?)",
            (user_id, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
        )
        await db.commit()
    await message.reply("Спасибо за подписку! Ваша подписка активирована на 30 дней.")

# ✅ Запуск бота
async def main():
    await init_db()
    logging.info("🚀 Бот запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
