# api/webhook.py

import os
import logging
import json
from http.server import BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application

# Импорт логики инициализации из bot.py
from bot import setup_application
# Импорт логики создания таблиц (для инициализации)
from db_utils import create_tables

# --- Настройка Telegram Application (ГЛОБАЛЬНО) ---

# Загружаем токен из переменных окружения Vercel
TOKEN = os.getenv("TELEGRAM_TOKEN")
application: Application = None


def get_application():
    """Инициализирует и возвращает кэшированный экземпляр Application."""
    global application
    if application is None:
        try:
            # ВАЖНО: Вызываем создание таблиц. В бессерверной среде это запустится
            # при первом холодном старте.
            create_tables()

            application = setup_application(TOKEN)
            logging.info("Telegram Application инициализирован.")
        except Exception as e:
            logging.error(f"Ошибка инициализации Application: {e}")
            application = None
    return application


# --- Vercel Serverless Handler (Точка входа) ---

# Используем асинхронный Vercel/Python хендлер
async def handler(request):
    """Vercel Serverless Handler для обработки Telegram Webhook."""

    app = get_application()

    if app is None:
        return {'statusCode': 500, 'body': 'Application error'}

    if request.method != 'POST':
        return {'statusCode': 405, 'body': 'Method Not Allowed'}

    # Получаем тело запроса
    try:
        # Vercel передает тело запроса через request.json() или request.get_data()
        update_dict = await request.json()
    except Exception:
        logging.error("Не удалось декодировать JSON.")
        return {'statusCode': 400, 'body': 'Invalid JSON'}

    # Создаем и обрабатываем Update асинхронно
    try:
        update = Update.de_json(data=update_dict, bot=app.bot)

        # Обрабатываем обновление. Поскольку мы внутри асинхронной функции Vercel,
        # мы можем просто вызвать process_update().
        await app.process_update(update)

        # Важно: Webhook должен вернуть ответ 200 OK быстро.
        return {'statusCode': 200, 'body': 'OK'}

    except Exception as e:
        # ЛОГИРУЕМ ОШИБКУ, но возвращаем 200 OK, чтобы Telegram не переотправлял запрос
        logging.error(f"Ошибка обработки обновления: {e}")
        return {'statusCode': 200, 'body': 'OK (Error logged)'}