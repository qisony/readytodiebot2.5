# utils.py

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler
import io
import html

# Импорты для чтения QR-кода
try:
    from PIL import Image
    from pyzbar.pyzbar import decode
except ImportError:
    print("WARNING: PIL (Pillow) или pyzbar не установлены. Сканирование QR-кодов работать не будет.")
    Image = None
    decode = None


def escape_html(text: str) -> str:
    """Экранирует символы <, > и & для безопасного использования в HTML-разметке."""
    # Используем встроенную функцию html.escape
    return html.escape(text)


def read_qr_code_from_image(image_bytes: bytes) -> str | None:
    """
    Читает QR-код с изображения, переданного в виде байтов, и возвращает строку ID.
    """
    if Image is None or decode is None:
        return None

    try:
        image = Image.open(io.BytesIO(image_bytes))
        decoded_objects = decode(image)

        if decoded_objects:
            # Возвращаем данные первого найденного QR-кода, декодированные в UTF-8
            return decoded_objects[0].data.decode('utf-8').strip().upper()
        return None
    except Exception as e:
        print(f"Ошибка при чтении QR-кода: {e}")
        return None


async def cancel_global(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Общий откат для всех диалогов."""

    # Удаляем пользовательскую клавиатуру при отмене, если она была активна
    reply_markup = {'remove_keyboard': True}

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Текущая операция отменена.", parse_mode='HTML')
        # В callback не можем удалить ReplyKeyboard, поэтому просто редактируем сообщение
    else:
        await update.message.reply_text("❌ Текущая операция отменена.", reply_markup=reply_markup)

    return ConversationHandler.END