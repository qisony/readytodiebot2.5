# admin_handlers.py

import os
import logging
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CommandHandler, \
    CallbackQueryHandler

# Абсолютные импорты
from db_utils import (
    find_ticket, activate_ticket, get_all_products,
    get_product, update_product_price, get_all_promos,
    add_promocode, toggle_promo_status, get_promo_products,
    add_promo_product, remove_promo_product, find_promocode
)
# Импорт необходимых хелперов из user_handlers
from user_handlers import generate_qr_code, send_ticket_success_message, issue_ticket_to_user
# Импорт из utils.py
from utils import cancel_global, read_qr_code_from_image, escape_html

# Загрузка переменных окружения
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None

# --- ОПРЕДЕЛЕНИЕ СОСТОЯНИЙ ---
ASK_PASSWORD, CHECK_TICKET = range(2)
ADMIN_MENU, SELECT_PRODUCT_TO_EDIT, ENTER_NEW_PRICE, PROMO_MENU, ENTER_PROMO_DATA, SELECT_PROMO_PRODUCTS = range(2, 8)
ADMIN_ISSUE_TICKET_START, ADMIN_ISSUE_TICKET_PRODUCT, ADMIN_ISSUE_TICKET_NAME, ADMIN_ISSUE_TICKET_EMAIL, ADMIN_ISSUE_TICKET_CONFIRM = range(
    8, 13)


# --- ХЕЛПЕРЫ ДЛЯ МЕНЮ ---

def get_admin_main_menu_keyboard():
    """Возвращает основную клавиатуру меню администратора."""
    keyboard = [
        [InlineKeyboardButton("🔍 Проверить/Активировать билет", callback_data="menu_check_ticket")],
        [InlineKeyboardButton("💲 Управление ценами", callback_data="menu_edit_price")],
        [InlineKeyboardButton("🎁 Управление промокодами", callback_data="menu_promo")],
        [InlineKeyboardButton("🎫 Ручная выдача билета", callback_data="menu_issue_ticket")],
        [InlineKeyboardButton("🚪 Выход", callback_data="menu_exit")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_promo_menu_keyboard():
    """Возвращает клавиатуру меню управления промокодами."""
    keyboard = [
        [InlineKeyboardButton("➕ Добавить промокод", callback_data="promo_add")],
        [InlineKeyboardButton("📋 Список промокодов", callback_data="promo_list")],
        [InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")]
    ]
    return InlineKeyboardMarkup(keyboard)


def get_ticket_check_keyboard(ticket_id: str | None = None, is_active: bool | None = None) -> InlineKeyboardMarkup:
    """Генерирует клавиатуру для меню проверки билета."""
    keyboard = []
    if ticket_id and is_active is not None and not is_active:
        # Билет найден, но не активен -> Предлагаем активацию
        keyboard.append([InlineKeyboardButton("✅ Активировать билет", callback_data=f"activate_{ticket_id}")])

    keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")])
    return InlineKeyboardMarkup(keyboard)


# --- ОСНОВНЫЕ ФУНКЦИИ АДМИНИСТРАТОРА ---

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает команду /admin, запрашивает пароль."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ только для главного администратора.")
        return ConversationHandler.END

    await update.message.reply_text("🔑 Введите пароль администратора:")
    return ASK_PASSWORD


async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Проверяет введенный пароль и отображает меню."""
    if update.message.text == ADMIN_PASSWORD:
        context.user_data['is_admin'] = True
        await update.message.reply_text(
            "✅ Пароль верный. Добро пожаловать в панель администратора!",
            reply_markup=get_admin_main_menu_keyboard()
        )
        return ADMIN_MENU
    else:
        await update.message.reply_text("❌ Пароль неверный. Попробуйте снова или нажмите /cancel.")
        return ASK_PASSWORD


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отображает главное меню администратора."""
    text = "⚙️ **Панель администратора**\nВыберите действие:"

    # Определяем, откуда пришел вызов: от кнопки или от MessageHandler
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text,
            reply_markup=get_admin_main_menu_keyboard(),
            parse_mode='Markdown'
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=get_admin_main_menu_keyboard(),
            parse_mode='Markdown'
        )

    return ADMIN_MENU


async def admin_menu_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает навигацию по меню администратора."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data

    if callback_data == "menu_exit":
        await query.edit_message_text("🚪 Выход из режима администратора.")
        context.user_data.clear()
        return ConversationHandler.END

    elif callback_data == "menu_check_ticket":
        await query.edit_message_text("🔍 **Проверка билета**\nОтправьте QR-код или введите ID билета:",
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")]])
                                      )
        return CHECK_TICKET

    elif callback_data == "menu_edit_price":
        return await start_edit_price(query, context)

    elif callback_data == "menu_promo":
        return await promo_menu_handler(query, context)

    elif callback_data == "menu_issue_ticket":
        return await start_issue_ticket(query, context)

    return ADMIN_MENU


# --- ПРОВЕРКА И АКТИВАЦИЯ БИЛЕТА (НОВЫЕ ФУНКЦИИ) ---

async def process_ticket_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обрабатывает ввод ID билета (из текста или QR) и ищет его.
    """
    ticket_id = None

    if update.message.photo:
        # 1. Обработка фото (QR-код)
        # Получаем объект File (самое большое разрешение)
        photo_file = await update.message.photo[-1].get_file()
        image_bytes = await photo_file.download_as_bytes()

        # Используем функцию из utils
        ticket_id = read_qr_code_from_image(image_bytes)

        if not ticket_id:
            await update.message.reply_text("❌ QR-код не распознан. Попробуйте снова или введите ID вручную.")
            return CHECK_TICKET  # Остаемся в состоянии

    elif update.message.text:
        # 2. Обработка текста (ID билета)
        ticket_id = update.message.text.strip().upper()

    if not ticket_id:
        await update.message.reply_text("❌ Введите ID билета или отправьте QR-код.")
        return CHECK_TICKET

    # 3. Поиск билета в БД
    ticket = find_ticket(ticket_id)

    if not ticket:
        text = f"❌ **Билет ID: `{ticket_id}`** не найден."
        keyboard = get_ticket_check_keyboard()
    else:
        status_text = "🟢 **АКТИВЕН**" if ticket['is_active'] else "🔴 **НЕ АКТИВИРОВАН**"

        text = (
            f"🎫 **Статус билета**\n\n"
            f"**ID:** `{ticket['ticket_id']}`\n"
            f"**Продукт:** {ticket['product_name']}\n"
            f"**Покупатель:** {escape_html(ticket['buyer_name'])} ({ticket['buyer_email']})\n"
            f"**Цена:** {ticket['final_price']} ₽\n"
            f"**Статус:** {status_text}\n"
            f"**Дата покупки:** {ticket['purchase_date'].strftime('%d.%m.%Y %H:%M')}"
        )
        keyboard = get_ticket_check_keyboard(ticket_id, ticket['is_active'])

    await update.message.reply_text(
        text,
        reply_markup=keyboard,
        parse_mode='Markdown'
    )

    # Сохраняем ID для возможной активации на следующем шаге
    context.user_data['temp_ticket_id'] = ticket_id

    return CHECK_TICKET


async def handle_ticket_activation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает нажатие на кнопку активации билета."""
    query = update.callback_query
    await query.answer()

    if query.data == 'menu_main':
        return await admin_menu(update, context)

    # data вида 'activate_TICKETID'
    ticket_id = query.data.split('_')[1]

    if activate_ticket(ticket_id):
        # Отправка уведомления пользователю
        ticket_data = find_ticket(ticket_id)
        if ticket_data and ticket_data.get('buyer_chat_id'):
            # send_ticket_success_message из user_handlers.py
            await send_ticket_success_message(context.bot, ticket_data['buyer_chat_id'], ticket_id)

            # Обновление сообщения для администратора
        await query.edit_message_text(
            f"✅ **Билет ID: `{ticket_id}`** успешно активирован!\n\n"
            "Покупателю отправлено подтверждение (если доступен chat_id).",
            parse_mode='Markdown',
            reply_markup=get_admin_main_menu_keyboard()
        )

    else:
        # Билет уже был активирован или произошла ошибка
        await query.edit_message_text(
            f"❌ Не удалось активировать **Билет ID: `{ticket_id}`**. "
            "Он либо уже активен, либо произошла ошибка БД.",
            parse_mode='Markdown',
            reply_markup=get_admin_main_menu_keyboard()
        )

    context.user_data.pop('temp_ticket_id', None)
    return ADMIN_MENU


# --- УПРАВЛЕНИЕ ЦЕНАМИ ---

async def start_edit_price(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отображает меню выбора продукта для редактирования цены."""
    products = get_all_products()
    if not products:
        await query.edit_message_text("❌ Нет доступных продуктов для редактирования.",
                                      reply_markup=InlineKeyboardMarkup(
                                          [[InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")]])
                                      )
        return ADMIN_MENU

    text = "💲 **Редактирование цен**\nВыберите продукт, цену которого хотите изменить:"
    keyboard = []
    for p in products:
        keyboard.append([InlineKeyboardButton(f"{p['name']} ({p['price']} ₽)", callback_data=f"editprice_{p['id']}")])

    keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SELECT_PRODUCT_TO_EDIT


async def select_product_to_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет ID продукта и запрашивает новую цену."""
    query = update.callback_query
    await query.answer()

    if query.data == "menu_main":
        return await admin_menu(update, context)

    product_id = int(query.data.split('_')[1])
    product = get_product(product_id)

    if not product:
        await query.edit_message_text("❌ Продукт не найден.", reply_markup=get_admin_main_menu_keyboard())
        return ADMIN_MENU

    context.user_data['edit_product_id'] = product_id

    await query.edit_message_text(
        f"✍️ Вы выбрали **{product['name']}** (текущая цена: **{product['price']}** ₽).\n"
        "Введите новую цену (только число):",
        parse_mode='Markdown'
    )
    return ENTER_NEW_PRICE


async def process_new_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает введенную новую цену и сохраняет в БД."""
    try:
        new_price = int(update.message.text.strip())
        if new_price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное положительное число для цены.")
        return ENTER_NEW_PRICE

    product_id = context.user_data.get('edit_product_id')
    if update_product_price(product_id, new_price):
        await update.message.reply_text(
            f"✅ Цена для продукта ID {product_id} успешно обновлена до **{new_price}** ₽.",
            reply_markup=get_admin_main_menu_keyboard(),
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Ошибка при обновлении цены в БД.",
                                        reply_markup=get_admin_main_menu_keyboard())

    context.user_data.pop('edit_product_id', None)
    return ADMIN_MENU


# --- УПРАВЛЕНИЕ ПРОМОКОДАМИ ---

async def promo_menu_handler(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отображает меню управления промокодами."""
    text = "🎁 **Управление промокодами**\nВыберите действие:"

    await query.edit_message_text(
        text,
        reply_markup=get_promo_menu_keyboard(),
        parse_mode='Markdown'
    )
    return PROMO_MENU


async def start_add_promocode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Переходит в состояние ввода данных промокода."""
    query = update.callback_query
    await query.answer()

    text = (
        "✍️ **Введите данные для нового промокода в формате:**\n\n"
        "`КОД ПРОЦЕНТ`\n\n"
        "Например: `SALE15 15` (создаст промокод SALE15 со скидкой 15%)."
    )

    keyboard = [[InlineKeyboardButton("🔙 Назад в меню промокодов", callback_data="menu_promo")]]

    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    return ENTER_PROMO_DATA


async def process_promo_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает введенные данные для создания промокода."""
    text = update.message.text

    match = re.match(r'^\s*([A-Z0-9]+)\s+(\d{1,2})\s*$', text.strip(), re.IGNORECASE)

    if not match:
        await update.message.reply_text(
            "❌ Неверный формат. Пожалуйста, введите в формате `КОД ПРОЦЕНТ` (например, `SALE15 15`).",
            parse_mode='Markdown'
        )
        return ENTER_PROMO_DATA

    code, discount_percent_str = match.groups()
    discount_percent = int(discount_percent_str)
    code_upper = code.upper()

    if discount_percent < 1 or discount_percent > 99:
        await update.message.reply_text("❌ Процент скидки должен быть от 1 до 99.")
        return ENTER_PROMO_DATA

    existing_promo = find_promocode(code_upper)
    if existing_promo:
        await update.message.reply_text(f"❌ Промокод `{code_upper}` уже существует!", parse_mode='Markdown')
        return ENTER_PROMO_DATA

    # Добавление в базу данных
    promo_id = add_promocode(code_upper, discount_percent)

    if promo_id:
        context.user_data['temp_promo_id'] = promo_id
        context.user_data['temp_promo_code'] = code_upper
        context.user_data['temp_promo_products'] = []

        # Переход к выбору продуктов
        # В этом случае вызываем start функцию, передавая update.message
        return await select_promo_products_start(update.message, context)
    else:
        await update.message.reply_text("❌ Произошла ошибка при добавлении промокода в БД.")
        return ENTER_PROMO_DATA


async def select_promo_products_start(update: Update | None, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс выбора продуктов, к которым применяется промокод."""
    products = get_all_products()
    promo_id = context.user_data.get('temp_promo_id')
    promo_code = context.user_data.get('temp_promo_code')

    # Получаем процент скидки для отображения
    promo_data = find_promocode(promo_code)
    discount_percent = promo_data['discount_percent'] if promo_data else '??'

    if not products or not promo_id:
        if update and update.message:
            await update.message.reply_text("❌ Ошибка: Нет продуктов или ID промокода.")
        return await admin_menu(update, context)

    text = (
        f"✅ Промокод `{promo_code}` ({discount_percent}%) успешно создан. Выберите продукты, "
        "к которым он будет применяться (нажмите, чтобы добавить/удалить). "
        "Нажмите **Готово**, чтобы завершить."
    )

    current_products = get_promo_products(promo_id)
    selected_ids = {p['id'] for p in current_products}

    keyboard = []
    for p in products:
        status = "🟢" if p['id'] in selected_ids else "⚪"
        keyboard.append([InlineKeyboardButton(f"{status} {p['name']}", callback_data=f"promoprod_{p['id']}")])

    keyboard.append([InlineKeyboardButton("💾 Готово (Завершить привязку)", callback_data="finish_promo_products")])

    if update and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard),
                                                      parse_mode='Markdown')
    elif update and update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        # Если ни update.message, ни update.callback_query нет
        return PROMO_MENU

    return SELECT_PROMO_PRODUCTS


async def handle_promo_product_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Добавляет или удаляет продукт из списка промокодов."""
    query = update.callback_query
    await query.answer()

    promo_id = context.user_data.get('temp_promo_id')
    callback_data = query.data

    if callback_data == "finish_promo_products":
        promo_code = context.user_data.pop('temp_promo_code', 'промокод')
        context.user_data.pop('temp_promo_id', None)

        await query.edit_message_text(
            f"🎉 Привязка продуктов для промокода `{promo_code}` завершена!",
            reply_markup=get_promo_menu_keyboard(),
            parse_mode='Markdown'
        )
        return PROMO_MENU

    if callback_data.startswith("promoprod_"):
        product_id = int(callback_data.split('_')[1])

        current_products = get_promo_products(promo_id)
        is_attached = any(p['id'] == product_id for p in current_products)

        if is_attached:
            remove_promo_product(promo_id, product_id)
        else:
            add_promo_product(promo_id, product_id)

        # Обновляем меню с новым статусом
        return await select_promo_products_start(update, context)

    return SELECT_PROMO_PRODUCTS


async def manage_promo_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает список промокодов и их активацию/деактивацию."""
    query = update.callback_query
    await query.answer()

    callback_data = query.data

    # 1. Активация/деактивация
    if callback_data.startswith("activate_promo_") or callback_data.startswith("deactivate_promo_"):
        parts = callback_data.split('_')
        action = parts[0]
        promo_id = int(parts[-1])

        is_active = (action == 'activate')

        if toggle_promo_status(promo_id, is_active):
            await query.answer(f"Промокод {'активирован' if is_active else 'деактивирован'}.", show_alert=True)
            # Переходим к отображению обновленного списка
            callback_data = 'promo_list'
        else:
            await query.answer("❌ Ошибка при изменении статуса.", show_alert=True)
            return PROMO_MENU

    # 2. Отображение списка
    if callback_data == 'promo_list':
        promos = get_all_promos()

        if not promos:
            text = "📋 **Список промокодов**\nПромокоды не найдены."
            await query.edit_message_text(text, reply_markup=get_promo_menu_keyboard(), parse_mode='Markdown')
            return PROMO_MENU

        text = "📋 **Список промокодов**\nНажмите, чтобы изменить статус:\n"
        keyboard = []

        for promo in promos:
            status = "🟢 Активен" if promo['is_active'] else "🔴 Неактивен"
            action = "deactivate" if promo['is_active'] else "activate"

            keyboard.append([
                InlineKeyboardButton(
                    f"{promo['code']} ({promo['discount_percent']}%) — {status}",
                    callback_data=f"{action}_promo_{promo['id']}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu_promo")])

        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return PROMO_MENU

    return PROMO_MENU  # По умолчанию


# --- РУЧНАЯ ВЫДАЧА БИЛЕТА ---

async def start_issue_ticket(query: Update.callback_query, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начинает процесс ручной выдачи билета."""
    products = get_all_products()
    if not products:
        await query.edit_message_text("❌ Нет доступных продуктов.",
                                      reply_markup=get_admin_main_menu_keyboard()
                                      )
        return ADMIN_MENU

    text = "🎫 **Ручная выдача**\nВыберите продукт для выдачи:"
    keyboard = []
    for p in products:
        keyboard.append(
            [InlineKeyboardButton(f"{p['name']} ({p['price']} ₽)", callback_data=f"issue_product_{p['id']}")])

    keyboard.append([InlineKeyboardButton("🔙 В главное меню", callback_data="menu_main")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ADMIN_ISSUE_TICKET_PRODUCT


async def admin_issue_ticket_product(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет ID продукта и запрашивает имя покупателя."""
    query = update.callback_query
    await query.answer()

    if query.data == "menu_main":
        return await admin_menu(update, context)

    product_id = int(query.data.split('_')[2])
    context.user_data['issue_product_id'] = product_id

    await query.edit_message_text("✍️ Введите **имя** покупателя (ФИО):", parse_mode='Markdown')
    return ADMIN_ISSUE_TICKET_NAME


async def admin_issue_ticket_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет имя и запрашивает email."""
    context.user_data['issue_name'] = escape_html(update.message.text.strip())

    await update.message.reply_text("📧 Введите **email** покупателя:")
    return ADMIN_ISSUE_TICKET_EMAIL


async def admin_issue_ticket_email(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Сохраняет email и запрашивает подтверждение."""
    email = update.message.text.strip()
    # Простое Regex для проверки формата email (не полная валидация)
    if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        await update.message.reply_text("❌ Введите корректный email адрес:")
        return ADMIN_ISSUE_TICKET_EMAIL

    context.user_data['issue_email'] = email

    # Подтверждение
    product = get_product(context.user_data['issue_product_id'])

    text = (
        "❓ **Подтвердите выдачу билета (БЕСПЛАТНО):**\n\n"
        f"**Продукт:** {product['name']} ({product['price']} ₽)\n"
        f"**Имя:** {context.user_data['issue_name']}\n"
        f"**Email:** {context.user_data['issue_email']}\n"
    )

    keyboard = [
        [InlineKeyboardButton("✅ Подтвердить выдачу", callback_data="issue_confirm")],
        [InlineKeyboardButton("❌ Отменить", callback_data="menu_main")]
    ]

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ADMIN_ISSUE_TICKET_CONFIRM


async def handle_issue_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает подтверждение и выдает билет."""
    query = update.callback_query
    await query.answer()

    if query.data == "issue_confirm":
        # Вызов функции, которая создает билет и отправляет его
        await issue_ticket_to_user(
            chat_id=ADMIN_ID,  # Отправляем себе
            name=context.user_data['issue_name'],
            email=context.user_data['issue_email'],
            product_id=context.user_data['issue_product_id'],
            final_price=0,  # Бесплатно
            context=context
        )

        await query.edit_message_text(f"🎉 **БЕСПЛАТНЫЙ** билет для {context.user_data['issue_name']} выдан!",
                                      parse_mode='Markdown')

        # Очистка контекста
        context.user_data.pop('issue_product_id', None)
        context.user_data.pop('issue_name', None)
        context.user_data.pop('issue_email', None)

    return await admin_menu(update, context)


async def admin_issue_ticket_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Игнорирует текстовый ввод, если ожидается нажатие кнопки."""
    await update.message.reply_text("Пожалуйста, нажмите **'✅ Подтвердить выдачу'** или **'❌ Отменить'**.")
    return ADMIN_ISSUE_TICKET_CONFIRM



# --- ГЛОБАЛЬНЫЙ ХЕНДЛЕР УВЕДОМЛЕНИЙ ОБ ОПЛАТЕ ---
# issue_ticket_to_user и escape_html должны быть импортированы в начале файла.

async def issue_ticket_from_admin_notification(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Обрабатывает CallbackQuery от администратора для подтверждения/отклонения оплаты.
    Callback data pattern: 'issue_<payment_ref>' или 'reject_<payment_ref>'
    """
    query = update.callback_query
    await query.answer()

    # Проверка, что действие совершает именно ADMIN_ID
    # (ADMIN_ID должен быть импортирован из os.getenv("ADMIN_ID") в начале файла)
    if query.from_user.id != ADMIN_ID:
        await query.edit_message_text("⛔ Вы не являетесь администратором.")
        return

    # 1. Парсинг данных: 'issue_REF' или 'reject_REF'
    action, payment_ref = query.data.split('_', 1)

    # 2. Извлечение сохраненных деталей из bot_data и их удаление
    transaction_data = context.application.bot_data.pop(payment_ref, None)

    if not transaction_data:
        await query.edit_message_text(
            f"❌ Ошибка: Детали транзакции `{payment_ref}` не найдены или уже обработаны.",
            parse_mode='Markdown'
        )
        return

    # 3. Обработка действия
    if action == 'issue':
        # Вызов функции для создания билета
        try:
            # issue_ticket_to_user(chat_id, name, email, product_id, final_price, context)
            await issue_ticket_to_user(
                chat_id=transaction_data['chat_id'],
                name=transaction_data['name'],
                email=transaction_data['email'],
                product_id=transaction_data['product_id'],
                final_price=transaction_data['final_price'],
                context=context
            )

            # Уведомление администратора (редактируем исходное сообщение)
            await query.edit_message_text(
                f"✅ Билет для **{escape_html(transaction_data['name'])}** ({transaction_data['final_price']} ₽) успешно выдан!",
                parse_mode='Markdown'
            )

        except Exception as e:
            logging.error(f"Ошибка при выдаче билета после подтверждения оплаты: {e}")
            await query.edit_message_text(
                f"❌ Критическая ошибка при выдаче билета для `{payment_ref}`. Подробности в логах.",
                parse_mode='Markdown'
            )

    elif action == 'reject':
        # Уведомление администратора об отклонении
        await query.edit_message_text(
            f"❌ Транзакция `{payment_ref}` отклонена.",
            parse_mode='Markdown'
        )
        # Уведомление покупателя
        try:
            await context.bot.send_message(
                chat_id=transaction_data['chat_id'],
                text="❌ Администратор отклонил подтверждение вашей оплаты. Пожалуйста, свяжитесь с поддержкой."
            )
        except Exception as e:
            logging.warning(
                f"Не удалось уведомить пользователя {transaction_data['chat_id']} об отклонении оплаты: {e}")



# --- РЕГИСТРАЦИЯ КОНВЕРСЕЙШЕН ХЕНДЛЕРОВ ---

admin_conv_handler = ConversationHandler(
    entry_points=[CommandHandler("admin", admin_start)],
    states={
        ASK_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_password)],

        ADMIN_MENU: [
            CallbackQueryHandler(admin_menu_navigate, pattern=r'^menu_'),
            CallbackQueryHandler(handle_issue_confirm, pattern=r'^issue_confirm$')  # Обработка ручной выдачи
        ],

        CHECK_TICKET: [
            # ИСПРАВЛЕНИЕ: Добавлены обработчики process_ticket_input и handle_ticket_activation
            MessageHandler(filters.TEXT & ~filters.COMMAND | filters.PHOTO, process_ticket_input),
            CallbackQueryHandler(handle_ticket_activation, pattern=r'^(activate_|menu_main)$')
        ],

        SELECT_PRODUCT_TO_EDIT: [CallbackQueryHandler(select_product_to_edit, pattern=r'^editprice_|^menu_main$')],
        ENTER_NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_new_price)],

        # УПРАВЛЕНИЕ ПРОМОКОДАМИ
        PROMO_MENU: [
            CallbackQueryHandler(start_add_promocode, pattern=r'^promo_add$'),
            CallbackQueryHandler(manage_promo_actions, pattern=r'^(promo_list|activate_promo_|deactivate_promo_)'),
            CallbackQueryHandler(admin_menu, pattern=r'^menu_main$'),
        ],
        ENTER_PROMO_DATA: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, process_promo_data),
            CallbackQueryHandler(promo_menu_handler, pattern=r'^menu_promo$')  # Кнопка "назад"
        ],
        SELECT_PROMO_PRODUCTS: [
            CallbackQueryHandler(handle_promo_product_selection, pattern=r'^(promoprod_|finish_promo_products)$'),
        ],

        # РУЧНАЯ ВЫДАЧА БИЛЕТА
        ADMIN_ISSUE_TICKET_PRODUCT: [
            CallbackQueryHandler(admin_issue_ticket_product, pattern=r'^issue_product_|^menu_main$')],
        ADMIN_ISSUE_TICKET_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_issue_ticket_name)],
        ADMIN_ISSUE_TICKET_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_issue_ticket_email)],
        # ИСПРАВЛЕНИЕ: Добавлен обработчик для игнорирования текстового ввода в состоянии подтверждения
        ADMIN_ISSUE_TICKET_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_issue_ticket_confirm)],
        # Обработка подтверждения issue_confirm происходит в ADMIN_MENU.
    },
    fallbacks=[CommandHandler("cancel", cancel_global)],
    map_to_parent=[(ConversationHandler.END, ADMIN_MENU)],
)