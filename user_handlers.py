# user_handlers.py

import os
import uuid
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler, \
    CommandHandler
from io import BytesIO
from datetime import datetime

# Абсолютные импорты
from db_utils import get_all_products, get_product, find_promo, insert_ticket, activate_ticket
from utils import cancel_global, escape_html

# Определяем состояния для ConversationHandler
SELECTING_PRODUCT, ENTERING_NAME, ENTERING_EMAIL, CONFIRMING_PAYMENT, FINAL_STATE, WAITING_PROMO_OR_SKIP = range(6)

ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None


# --- Хелперы ---


async def send_admin_notification(context: ContextTypes.DEFAULT_TYPE, payment_ref: str, chat_id: int, name: str,
                                  email: str, product: dict, final_price: int) -> None:
    """
    Отправляет уведомление администратору о необходимости подтвердить оплату,
    сохраняя данные транзакции в bot_data.
    """

    # 1. Сохраняем все данные в bot_data для последующего извлечения
    context.application.bot_data[payment_ref] = {
        'chat_id': chat_id,
        'name': name,
        'email': email,
        'product_id': product['id'],
        'final_price': final_price,
        'product_name': product['name']
    }

    text = (
        f"🚨 **ТРЕБУЕТСЯ ПОДТВЕРЖДЕНИЕ ОПЛАТЫ**\n\n"
        f"**Референс:** `{payment_ref}`\n"
        f"**Продукт:** {product['name']} ({final_price} ₽)\n"
        f"**Покупатель:** {escape_html(name)}\n"
        f"**Email:** {email}\n"
        f"**ID чата:** `{chat_id}`"
    )

    keyboard = [
        # Callback data: issue_<payment_ref> - для выдачи
        [InlineKeyboardButton("✅ Выдать билет (Оплачено)", callback_data=f"issue_{payment_ref}")],
        # Callback data: reject_<payment_ref> - для отклонения
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{payment_ref}")]
    ]

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    except Exception as e:
        logging.error(f"Ошибка при отправке уведомления администратору: {e}")


# Предполагается, что у вас есть библиотека qrcode и PIL (Pillow)
def generate_qr_code(ticket_id: str) -> BytesIO:
    """Генерирует QR-код с заданным ID (использует библиотеку qrcode)."""
    try:
        import qrcode
        from PIL import Image
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(ticket_id)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")

        bio = BytesIO()
        bio.name = 'qr_code.png'
        img.save(bio, 'PNG')
        bio.seek(0)
        return bio

    except ImportError:
        logging.warning("Библиотека 'qrcode' не установлена. Используется заглушка.")
        # Если qrcode не установлен, создаем пустую заглушку
        try:
            from PIL import Image
            img = Image.new('RGB', (200, 200), color='white')
            bio = BytesIO()
            bio.name = 'qr_code_placeholder.png'
            img.save(bio, 'PNG')
            bio.seek(0)
            return bio
        except ImportError:
            # Если даже PIL нет, возвращаем пустые байты
            return BytesIO(b"QR_CODE_PLACEHOLDER")


# НОВАЯ ФУНКЦИЯ: Отправка билета покупателю
async def send_ticket_success_message(bot, ticket_data: dict, qr_bytes: BytesIO) -> None:
    """Отправляет покупателю QR-код и информацию о билете."""

    purchase_date_str = ticket_data.get('purchase_date')
    if isinstance(purchase_date_str, datetime):
        purchase_date_str = purchase_date_str.strftime('%d.%m.%Y %H:%M')
    else:
        purchase_date_str = 'Дата неизвестна'  # В случае ручной выдачи

    message_text = (
        f"🥳 **Поздравляем!** Ваш билет на мероприятие активирован!\n\n"
        f"**Тариф:** {ticket_data['product_name']}\n"
        f"**ID Билета:** `{ticket_data['ticket_id']}`\n"
        f"**Дата покупки:** {purchase_date_str}\n\n"
        f"Пожалуйста, сохраните этот QR-код. Он потребуется для входа."
    )

    await bot.send_photo(
        chat_id=ticket_data['buyer_chat_id'],
        photo=InputFile(qr_bytes, filename=f"ticket_{ticket_data['ticket_id']}.png"),
        caption=message_text,
        parse_mode='Markdown'
    )


# Этот хелпер используется для ручной выдачи билета в админке
async def issue_ticket_to_user(bot, chat_id: int, user_data: dict) -> bool:
    """
    Генерирует ID, сохраняет в БД (активирует) и отправляет билет пользователю,
    а также отправляет админу для контроля.
    """
    ticket_id = str(uuid.uuid4()).upper().replace('-', '')[:12]

    product_name = user_data['product_name']
    buyer_name = user_data['buyer_name']
    buyer_email = user_data['buyer_email']
    final_price = user_data['final_price']
    buyer_chat_id = user_data.get('buyer_chat_id', chat_id)  # Предполагается, что chat_id в ручном режиме - это админ

    # 1. Запись в БД (активным)
    # При ручной выдаче сразу сохраняем с is_active=FALSE, а затем активируем,
    # чтобы дата покупки совпадала с датой активации.
    if not insert_ticket(ticket_id, product_name, buyer_name, buyer_email, buyer_chat_id, final_price):
        logging.error(
            f"КРИТИЧЕСКАЯ ОШИБКА при ручной выдаче билета {ticket_id}: insert_ticket() не удалось сохранить запись.")
        await bot.send_message(chat_id,
                               f"❌ Произошла ошибка при регистрации билета {ticket_id} в БД. Свяжитесь с поддержкой.")
        return False

    if not activate_ticket(ticket_id):
        logging.error(f"КРИТИЧЕСКАЯ ОШИБКА при ручной выдаче билета {ticket_id}: не удалось активировать билет.")
        # Продолжаем отправку, так как вставка прошла

    # Генерация двух отдельных объектов BytesIO для разных целей
    qr_code_file_admin = generate_qr_code(ticket_id)
    qr_code_file_user = generate_qr_code(ticket_id)

    try:
        # 1. Сообщение администратору (с QR-кодом для контроля)
        caption_admin = (
            f"🎉 **Билет Успешно Выдан (ВРУЧНУЮ)!** 🎉\n\n"
            f"🆔 **ID Билета:** `{ticket_id}`\n"
            f"🎫 **Тариф:** {product_name}\n"
            f"👤 **Покупатель:** {buyer_name}\n"
            f"📧 **Email:** {buyer_email}\n"
            f"💰 **Цена:** {final_price} ₽\n\n"
            f"QR-код отправлен покупателю {buyer_chat_id}."
        )

        await bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(qr_code_file_admin),
            caption=caption_admin,
            parse_mode='Markdown',
            reply_markup=ReplyKeyboardRemove()  # Удаляем клавиатуру
        )

        # 2. Сообщение покупателю
        ticket_data = {
            'ticket_id': ticket_id, 'product_name': product_name, 'buyer_chat_id': buyer_chat_id,
            'purchase_date': datetime.now()
        }
        await send_ticket_success_message(bot, ticket_data, qr_code_file_user)

        return True

    except Exception as e:
        logging.error(f"Ошибка при отправке билета: {e}")
        return False


# --- Начало Диалога ---

async def start_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает список доступных тарифов."""
    products_list = get_all_products()

    if not products_list:
        await update.message.reply_text("К сожалению, на данный момент нет доступных тарифов.")
        return ConversationHandler.END

    keyboard = []
    text = "Выберите желаемый тариф:\n\n"

    for p in products_list:
        text += f"<b>{escape_html(p['name'])}</b> - {p['price']} ₽\n"
        text += f"<i>{escape_html(p['description'])}</i>\n\n"
        keyboard.append([InlineKeyboardButton(f"🎫 {p['name']} ({p['price']} ₽)", callback_data=p['name'])])

    keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data='pay_cancel')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.message:
        await update.message.reply_html(text, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_html(text, reply_markup=reply_markup)

    return SELECTING_PRODUCT


async def product_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет выбранный тариф и предлагает ввести промокод."""
    query = update.callback_query
    await query.answer()

    product_name = query.data
    product = get_product(product_name)

    if not product:
        await query.edit_message_text("❌ Извините, выбранный тариф недоступен. Начните заново с /buy.")
        return ConversationHandler.END

    context.user_data['product_name'] = product_name
    context.user_data['initial_price'] = product['price']
    context.user_data['final_price'] = product['price']
    context.user_data['promo_code'] = None

    text = (
        f"Вы выбрали: **{product_name}** ({product['price']} ₽).\n\n"
        "Введите промокод (если есть) или нажмите 'Пропустить', чтобы перейти к оплате."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➡️ Пропустить", callback_data='skip_promo')],
        [InlineKeyboardButton("⬅️ Назад к выбору", callback_data='back_to_select')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return WAITING_PROMO_OR_SKIP


async def process_promo_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Проверяет введенный промокод."""
    promo_code = update.message.text.strip().upper()
    promo_data = find_promo(promo_code)
    product_name = context.user_data['product_name']
    initial_price = context.user_data['initial_price']

    # В этой версии проверка на привязку промокода к продукту опущена для упрощения.
    # Реализация: if promo_data and product_name in promo_data['affected_products']: ...

    if promo_data and promo_data['is_active']:
        discount = promo_data['discount_percent']
        final_price = int(initial_price * (100 - discount) / 100)

        context.user_data['final_price'] = final_price
        context.user_data['promo_code'] = promo_code

        text = (
            f"✅ Промокод **{promo_code}** применен!\n"
            f"Скидка: {discount}%\n"
            f"Итоговая цена: ~~{initial_price} ₽~~ **{final_price} ₽**\n\n"
            "Введите ваше **ИМЯ и ФАМИЛИЮ** (как в паспорте):"
        )
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=ReplyKeyboardRemove())
        return ENTERING_NAME
    else:
        text = (
            "❌ Промокод недействителен или не найден.\n"
            "Введите другой промокод или нажмите 'Пропустить'."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➡️ Пропустить", callback_data='skip_promo')],
            [InlineKeyboardButton("⬅️ Назад к выбору", callback_data='back_to_select')]
        ])
        await update.message.reply_text(text, reply_markup=keyboard)
        return WAITING_PROMO_OR_SKIP


async def skip_promo_or_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает пропуск промокода или возврат к выбору тарифа."""
    query = update.callback_query
    await query.answer()

    if query.data == 'back_to_select':
        return await start_buy(update, context)

    # Пропуск промокода
    context.user_data['promo_code'] = None
    context.user_data['final_price'] = context.user_data['initial_price']

    final_price = context.user_data['final_price']

    text = (
        f"Промокод пропущен.\n"
        f"Итоговая цена: **{final_price} ₽**\n\n"
        "Введите ваше **ИМЯ и ФАМИЛИЮ** (как в паспорте):"
    )

    # ИСПРАВЛЕНИЕ 3: При редактировании сообщения с Inline-клавиатурой
    # для ее удаления нужно передать reply_markup=None.
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=None)
    return ENTERING_NAME


async def entering_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет имя и запрашивает email."""
    buyer_name = update.message.text.strip()

    if len(buyer_name) < 3:
        await update.message.reply_text("Пожалуйста, введите полное имя и фамилию.")
        return ENTERING_NAME

    context.user_data['buyer_name'] = buyer_name

    await update.message.reply_text("Введите ваш **EMAIL** для получения билета:")
    return ENTERING_EMAIL


async def entering_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет email и показывает окно оплаты."""
    buyer_email = update.message.text.strip()

    # Простейшая валидация email
    if not re.match(r"[^@]+@[^@]+\.[^@]+", buyer_email):
        await update.message.reply_text("❌ Пожалуйста, введите корректный адрес электронной почты.")
        return ENTERING_EMAIL

    context.user_data['buyer_email'] = buyer_email

    product_name = context.user_data['product_name']
    final_price = context.user_data['final_price']

    text = (
        "**ПОДТВЕРЖДЕНИЕ ЗАКАЗА**\n\n"
        f"Тариф: **{product_name}**\n"
        f"Имя: {context.user_data['buyer_name']}\n"
        f"Email: `{buyer_email}`\n"
        f"Промокод: {context.user_data.get('promo_code', 'Нет')}\n"
        f"Итого: **{final_price} ₽**\n\n"
        "Нажмите 'Оплатить', чтобы получить ссылку."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💳 Оплатить {final_price} ₽", callback_data='pay_start')],
        [InlineKeyboardButton("❌ Отмена", callback_data='pay_cancel')]
    ])

    await update.message.reply_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return CONFIRMING_PAYMENT


async def payment_options(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает заглушку оплаты (в реальном боте здесь будет ссылка на Qiwi/YooMoney/etc)."""
    query = update.callback_query
    await query.answer()

    if query.data == 'pay_cancel':
        return await cancel_global(update, context)

    final_price = context.user_data['final_price']

    # *** ЗАГЛУШКА ОПЛАТЫ ***
    text = (
        f"🔗 **Ссылка на оплату {final_price} ₽**\n\n"
        "В реальном проекте здесь будет интеграция с платежной системой (Qiwi/ЮMoney/etc.).\n"
        "Для демонстрации, после перевода средств, нажмите 'Я оплатил'."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Я оплатил", callback_data='paid_confirmed')],
        [InlineKeyboardButton("❌ Отмена", callback_data='pay_cancel')]
    ])

    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return CONFIRMING_PAYMENT


# ИЗМЕНЕНИЕ: В paid_confirmed добавляем сохранение buyer_chat_id и кнопку для администратора
async def paid_confirmed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатие 'Я оплатил' и отправляет уведомление администратору."""
    query = update.callback_query
    await query.answer()

    # Извлекаем все необходимые данные, сохраненные на предыдущих шагах
    name = context.user_data.get('name')
    email = context.user_data.get('email')
    product = context.user_data.get('product')
    final_price = context.user_data.get('final_price')

    if not all([name, email, product, final_price is not None]):
        await query.edit_message_text("❌ Ошибка: Недостаточно данных для оформления. Попробуйте снова.")
        context.user_data.clear()
        return ConversationHandler.END

    # Генерация уникального референса транзакции
    payment_ref = str(uuid.uuid4()).split('-')[0].upper()

    # Отправка уведомления администратору (и сохранение данных в bot_data)
    await send_admin_notification(
        context,
        payment_ref,
        update.effective_user.id,  # chat_id покупателя
        name,
        email,
        product,
        final_price
    )

    await query.edit_message_text(
        f"✅ Ваш запрос отправлен администратору. Референс: `{payment_ref}`.\n"
        "Мы уведомим вас, как только оплата будет подтверждена и билет выдан."
    )

    # Очистка контекста пользователя для завершения диалога
    context.user_data.clear()
    return ConversationHandler.END


# --- РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ---

buy_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("buy", start_buy)],
    states={
        SELECTING_PRODUCT: [
            CallbackQueryHandler(product_selected, pattern=r'^(?!pay_cancel$).+'),
            CallbackQueryHandler(cancel_global, pattern='^pay_cancel$')
        ],
        WAITING_PROMO_OR_SKIP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, process_promo_input),
            CallbackQueryHandler(skip_promo_or_back, pattern=r'^(skip_promo|back_to_select)$')
        ],
        ENTERING_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_name)],
        ENTERING_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, entering_email)],
        CONFIRMING_PAYMENT: [
            CallbackQueryHandler(payment_options, pattern='^pay_start$'),
            CallbackQueryHandler(paid_confirmed, pattern='^paid_confirmed$'),
            CallbackQueryHandler(cancel_global, pattern='^pay_cancel$')
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel_global), CallbackQueryHandler(cancel_global, pattern='^pay_cancel$')],
    per_message=False,
    name="buy_conv_handler"
)