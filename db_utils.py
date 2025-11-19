# db_utils.py

import os
import logging
import psycopg2
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logging.warning("DATABASE_URL не задан в окружении.")


# --- БАЗОВЫЕ ФУНКЦИИ ---

def connect_db():
    """Устанавливает соединение с PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logging.error(f"Ошибка подключения к БД: {e}")
        return None


def create_tables():
    """Создает необходимые таблицы (tickets, products, promocodes)."""
    conn = connect_db()
    if conn is None:
        return
    cursor = conn.cursor()

    # ИЗМЕНЕНИЕ: Добавлено buyer_chat_id
    create_ticket_table_query = """
            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id VARCHAR(50) PRIMARY KEY,
                product_name VARCHAR(50) NOT NULL, 
                buyer_name VARCHAR(100) NOT NULL,
                buyer_email VARCHAR(100) NOT NULL,
                buyer_chat_id BIGINT NOT NULL,    -- <-- ДОБАВЛЕНО: Идентификатор чата покупателя
                final_price INTEGER NOT NULL,
                is_active BOOLEAN DEFAULT FALSE,
                purchase_date TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
            );
            """
    cursor.execute(create_ticket_table_query)
    # Таблица продуктов
    create_product_table_query = """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name VARCHAR(50) UNIQUE NOT NULL,
            description TEXT,
            price INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE
        );
        """
    # Таблица промокодов
    create_promocode_table_query = """
        CREATE TABLE IF NOT EXISTS promocodes (
            id SERIAL PRIMARY KEY,
            code VARCHAR(50) UNIQUE NOT NULL,
            discount_percent INTEGER NOT NULL,
            is_active BOOLEAN DEFAULT TRUE
        );
        """

    # Таблица связей промокодов и продуктов
    create_promocode_products_query = """
        CREATE TABLE IF NOT EXISTS promocode_products (
            promocode_id INTEGER NOT NULL REFERENCES promocodes(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            PRIMARY KEY (promocode_id, product_id)
        );
        """

    try:
        cursor.execute(create_ticket_table_query)
        cursor.execute(create_product_table_query)
        cursor.execute(create_promocode_table_query)
        cursor.execute(create_promocode_products_query)
        conn.commit()
        logging.info("Все таблицы (tickets, products, promocodes) успешно созданы/обновлены.")

        initialize_products(conn)

    except Exception as e:
        logging.error(f"Ошибка при создании/обновлении таблиц: {e}")
    finally:
        cursor.close()
        conn.close()


# --- ФУНКЦИИ ПРОДУКТОВ И ПРОМОКОДОВ ---

def initialize_products(conn):
    """Инициализирует базовые тарифы (VIP, STANDART, 1+1)."""
    cursor = conn.cursor()
    products_to_add = [
        ('VIP', 'Включает доступ в VIP-зону и Fast-Pass.', 15000),
        ('STANDART', 'Базовый вход, доступ в основную зону.', 5000),
        ('1+1', 'Два билета по цене одного, ограниченное предложение.', 7500),
    ]

    for name, desc, price in products_to_add:
        cursor.execute("SELECT name FROM products WHERE name = %s", (name,))
        if cursor.fetchone() is None:
            insert_query = """
            INSERT INTO products (name, description, price) VALUES (%s, %s, %s)
            """
            cursor.execute(insert_query, (name, desc, price))
            logging.info(f"Инициализирован тариф: {name}")

    conn.commit()


def get_all_products():
    """Получает все доступные тарифы."""
    conn = connect_db()
    if conn is None: return []
    cursor = conn.cursor()
    select_query = "SELECT name, description, price FROM products WHERE is_active = TRUE ORDER BY price DESC;"
    try:
        cursor.execute(select_query)
        results = cursor.fetchall()
        return [{'name': r[0], 'description': r[1], 'price': r[2]} for r in results]
    except Exception as e:
        logging.error(f"Ошибка при получении тарифов: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def get_product(name: str):
    """Получает информацию об одном тарифе."""
    conn = connect_db()
    if conn is None: return None
    cursor = conn.cursor()
    select_query = "SELECT id, name, description, price, is_active FROM products WHERE name = %s;"
    try:
        cursor.execute(select_query, (name,))
        result = cursor.fetchone()
        if result:
            return {'id': result[0], 'name': result[1], 'description': result[2], 'price': result[3],
                    'is_available': result[4]}
        return None
    except Exception as e:
        logging.error(f"Ошибка при получении тарифа {name}: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def update_product_price(product_name: str, new_price: int) -> bool:
    """Обновляет цену продукта."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    update_query = "UPDATE products SET price = %s WHERE name = %s;"
    try:
        cursor.execute(update_query, (new_price, product_name))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при обновлении цены продукта {product_name}: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def find_promo(code: str):
    """Ищет промокод по коду и возвращает данные."""
    conn = connect_db()
    if conn is None: return None
    cursor = conn.cursor()
    select_query = """
    SELECT p.id, p.code, p.discount_percent, p.is_active, 
           json_agg(pr.name) FILTER (WHERE pr.name IS NOT NULL) AS affected_products
    FROM promocodes p
    LEFT JOIN promocode_products pp ON p.id = pp.promocode_id
    LEFT JOIN products pr ON pp.product_id = pr.id
    WHERE p.code = %s
    GROUP BY p.id;
    """
    try:
        cursor.execute(select_query, (code,))
        result = cursor.fetchone()
        if result:
            return {
                'id': result[0], 'code': result[1], 'discount_percent': result[2],
                'is_active': result[3], 'affected_products': result[4] if result[4] else []
            }
        return None
    except Exception as e:
        logging.error(f"Ошибка при поиске промокода {code}: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def get_all_promos():
    """Получает все промокоды."""
    conn = connect_db()
    if conn is None: return []
    cursor = conn.cursor()
    select_query = "SELECT id, code, discount_percent, is_active FROM promocodes ORDER BY id DESC;"
    try:
        cursor.execute(select_query)
        results = cursor.fetchall()
        return [{'id': r[0], 'code': r[1], 'discount_percent': r[2], 'is_active': r[3]} for r in results]
    except Exception as e:
        logging.error(f"Ошибка при получении промокодов: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def add_promocode(code, discount_percent):
    """Добавляет новый промокод."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    insert_query = "INSERT INTO promocodes (code, discount_percent) VALUES (%s, %s);"
    try:
        cursor.execute(insert_query, (code, discount_percent))
        conn.commit()
        return True
    except psycopg2.errors.UniqueViolation:
        logging.warning(f"Промокод {code} уже существует.")
        return False
    except Exception as e:
        logging.error(f"Ошибка при добавлении промокода: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def toggle_promo_status(promo_id, is_active):
    """Включает/выключает промокод."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    update_query = "UPDATE promocodes SET is_active = %s WHERE id = %s;"
    try:
        cursor.execute(update_query, (is_active, promo_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при смене статуса промокода {promo_id}: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def get_promo_products(promo_id):
    """Получает список продуктов, на которые действует промокод."""
    conn = connect_db()
    if conn is None: return []
    cursor = conn.cursor()
    select_query = """
    SELECT p.id, p.name 
    FROM products p
    JOIN promocode_products pp ON p.id = pp.product_id
    WHERE pp.promocode_id = %s;
    """
    try:
        cursor.execute(select_query, (promo_id,))
        results = cursor.fetchall()
        return [{'id': r[0], 'name': r[1]} for r in results]
    except Exception as e:
        logging.error(f"Ошибка при получении продуктов промокода {promo_id}: {e}")
        return []
    finally:
        cursor.close()
        conn.close()


def add_promo_product(promo_id, product_name):
    """Добавляет продукт к промокоду."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()

    # 1. Найти ID продукта
    product_data = get_product(product_name)
    if not product_data:
        logging.error(f"Продукт {product_name} не найден.")
        return False
    product_id = product_data['id']

    # 2. Вставить связь
    insert_query = "INSERT INTO promocode_products (promocode_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING;"
    try:
        cursor.execute(insert_query, (promo_id, product_id))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении продукта к промокоду: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def remove_promo_product(promo_id, product_id):
    """Удаляет связь между промокодом и продуктом."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    delete_query = """
    DELETE FROM promocode_products 
    WHERE promocode_id = %s AND product_id = %s;
    """
    try:
        cursor.execute(delete_query, (promo_id, product_id))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка при удалении продукта из промокода: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def find_promocode(code):
    """
    Ищет промокод по его строковому значению (коду) и возвращает данные.
    """
    conn = connect_db()
    if conn is None: return None
    cursor = conn.cursor()
    select_query = """
    SELECT id, code, discount_percent, is_active
    FROM promocodes
    WHERE code = %s;
    """
    try:
        cursor.execute(select_query, (code,))
        result = cursor.fetchone()

        if result:
            return {
                'id': result[0],
                'code': result[1],
                'discount_percent': result[2],
                'is_active': result[3]
            }
        return None
    except Exception as e:
        logging.error(f"Ошибка при поиске промокода: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# --- ФУНКЦИИ БИЛЕТОВ ---

# ИЗМЕНЕНИЕ: Добавлен buyer_chat_id в параметры и запрос
def insert_ticket(ticket_id, product_name, buyer_name, buyer_email, buyer_chat_id, final_price):
    """Добавляет новый билет в БД."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    insert_query = """
        INSERT INTO tickets (ticket_id, product_name, buyer_name, buyer_email, buyer_chat_id, final_price, is_active)
        VALUES (%s, %s, %s, %s, %s, %s, FALSE)
        """
    try:
        cursor.execute(insert_query, (ticket_id, product_name, buyer_name, buyer_email, buyer_chat_id, final_price))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Ошибка при добавлении билета: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


def find_ticket(ticket_id: str):
    """Ищет билет по ID и возвращает все данные."""
    conn = connect_db()
    if conn is None: return None
    cursor = conn.cursor()
    # ИЗМЕНЕНИЕ: Добавлен buyer_chat_id в выборку
    select_query = """
    SELECT ticket_id, product_name, buyer_name, buyer_email, buyer_chat_id, final_price, is_active, purchase_date
    FROM tickets WHERE ticket_id = %s;
    """
    try:
        cursor.execute(select_query, (ticket_id,))
        result = cursor.fetchone()

        if result:
            return {
                'ticket_id': result[0],
                'product_name': result[1],
                'buyer_name': result[2],
                'buyer_email': result[3],
                'buyer_chat_id': result[4],  # <--- ДОБАВЛЕНО
                'final_price': result[5],
                'is_active': result[6],
                'purchase_date': result[7]
            }
        return None
    except Exception as e:
        logging.error(f"Ошибка при поиске билета: {e}")
        return None
    finally:
        cursor.close()
        conn.close()


def activate_ticket(ticket_id: str) -> bool:
    """Активирует билет (устанавливает is_active = TRUE)."""
    conn = connect_db()
    if conn is None: return False
    cursor = conn.cursor()
    update_query = "UPDATE tickets SET is_active = TRUE WHERE ticket_id = %s AND is_active = FALSE;"
    try:
        cursor.execute(update_query, (ticket_id,))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logging.error(f"Ошибка при активации билета: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()