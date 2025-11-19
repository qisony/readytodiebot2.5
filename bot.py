# bot.py

import os
import logging
import sys
from dotenv import load_dotenv
from telegram import Update, BotCommand, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from datetime import datetime

# --- Настройка логирования для Serverless-окружения ---
# Удаляем логику FileHandler, т.к. на Vercel нет локальной ФС.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# --- Загрузка переменных окружения ---
# load_dotenv() оставлен для локального тестирования
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
ADMIN_ID = os.getenv("ADMIN_ID")

if not TOKEN:
    logging.critical("TELEGRAM_TOKEN не найден. Проверьте файл .env")
    # В режиме Vercel sys.exit(1) не нужен, но оставлен для ясности.

# --- Абсолютные импорты ---
from db_utils import create_tables
from user_handlers import buy_conv_handler, start_buy
from admin_handlers import admin_conv_handler, issue_ticket_from_admin_notification
from utils import cancel_global


# --- Хелперы ---

async def log_updates_and_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Логирует все обновления и нажатия кнопок (для отладки)."""
    # Этот хендлер будет работать и на Vercel, записывая логи в CloudWatch/Vercel Logs
    log_message = f"[{datetime.now().strftime('%H:%M:%S')}] "

    if update.message:
        user = update.message.from_user
        chat_id = update.message.chat_id
        text = update.message.text

        log_message += f"MSG | Chat:{chat_id} | User:{user.first_name} ({user.id}) | Text: '{text}'"

    elif update.callback_query:
        user = update.callback_query.from_user
        chat_id = update.callback_query.message.chat_id
        data = update.callback_query.data

        log_message += f"CBQ | Chat:{chat_id} | User:{user.first_name} ({user.id}) | Data: '{data}'"

    logging.info(log_message)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обрабатывает команду /start."""
    user = update.message.from_user
    welcome_message = (
        f"Привет, **{user.first_name}**! 👋\n\n"
        "Я бот для продажи билетов. Чтобы начать покупку, используй команду /buy."
    )

    if str(user.id) == ADMIN_ID:
        welcome_message += "\n\n🔑 **Режим Администратора**: используй /admin для доступа к меню управления."

    await update.message.reply_text(
        welcome_message,
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )


async def set_bot_commands(application: Application) -> None:
    """
    Устанавливает меню команд бота.

    ВНИМАНИЕ: На Vercel эта функция должна быть вызвана ОДИН РАЗ вручную
    (например, через скрипт или прямое обращение к API Telegram) после деплоя,
    а не через JobQueue, как было раньше.
    """
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("buy", "🛒 Купить билет"),
        BotCommand("cancel", "❌ Отменить текущее действие"),
        BotCommand("admin", "🔑 Режим администратора"),
    ]

    await application.bot.set_my_commands(commands)
    logging.info("Меню команд успешно установлено.")


# --- ФУНКЦИЯ ИНИЦИАЛИЗАЦИИ ДЛЯ WEBHOOK ---
def setup_application(token: str) -> Application:
    """
    Создает и настраивает экземпляр Application для Webhook.
    Используется в api/webhook.py.
    """
    # 1. Создание Application
    application = Application.builder().token(token).build()

    # 2. Добавление обработчиков

    # ХЕНДЛЕРЫ ЛОГИРОВАНИЯ
    application.add_handler(CallbackQueryHandler(log_updates_and_actions), group=-2)
    application.add_handler(MessageHandler(filters.ALL, log_updates_and_actions), group=-1)

    # Основные команды
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("cancel", cancel_global))

    # Диалоги
    application.add_handler(buy_conv_handler)
    application.add_handler(admin_conv_handler)

    # ГЛОБАЛЬНЫЙ ХЕНДЛЕР для выдачи билета из уведомления
    application.add_handler(CallbackQueryHandler(issue_ticket_from_admin_notification, pattern=r'^issue_ticket_'))

    return application

# ВНИМАНИЕ: Функции main() и if __name__ == "__main__": удалены.
# Запуск теперь происходит через Vercel/api/webhook.py, который импортирует setup_application.