# api/webhook.py (ФИНАЛЬНАЯ ВЕРСИЯ)

import os
import json
import asyncio
import logging
from telegram import Update
from bot import setup_application
from db_utils import create_tables

# Настройка логирования для вывода в консоль Vercel
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("TELEGRAM_TOKEN")
APPLICATION = None

def get_application():
    """Инициализирует и возвращает кэшированный экземпляр Application."""
    global APPLICATION
    if APPLICATION is None:
        try:
            # Создание таблиц при холодном старте Vercel.
            create_tables()
            APPLICATION = setup_application(TOKEN)
            logging.info("Telegram Application инициализирован.")
        except Exception as e:
            # Если ошибка здесь (например, DATABASE_URL неверный), логируем и возвращаем None
            logging.error(f"Ошибка инициализации Application (проверьте переменные окружения): {e}")
            APPLICATION = None
    return APPLICATION

# Асинхронная функция для обработки запроса Telegram
async def process_telegram_update(event):
    """Парсит запрос от Vercel и передает его боту."""
    app = get_application()

    if app is None:
        # Если Application не инициализировано, возвращаем 500
        return {'statusCode': 500, 'body': 'Application error'}

    if event.get('httpMethod') != 'POST':
        return {'statusCode': 405, 'body': 'Method Not Allowed'}

    try:
        # Стандартный способ получения JSON в Vercel
        body = event.get('body')
        update_json = json.loads(body)
        update = Update.de_json(data=update_json, bot=app.bot)
        
        # Асинхронная обработка обновления
        await app.process_update(update)

        # Важно: всегда возвращаем 200 OK быстро.
        return {'statusCode': 200, 'body': 'OK'}
    
    except Exception as e:
        # Логируем ошибку в логи Vercel, но возвращаем 200 OK в Telegram
        logging.error(f"Error processing update (логика бота): {e}")
        return {'statusCode': 200, 'body': 'Update processed with error'}

# Синхронная точка входа Vercel
def handler(event, context):
    """Основная точка входа Vercel Serverless Function."""
    # Используем asyncio.run() для запуска асинхронной логики
    return asyncio.run(process_telegram_update(event))
        logging.error(f"Ошибка обработки обновления: {e}")

        return {'statusCode': 200, 'body': 'OK (Error logged)'}
