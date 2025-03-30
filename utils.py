import sqlite3
import json
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

def init_db():
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        # Создаём таблицу, если она ещё не существует
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                active_order TEXT,
                request_count INTEGER DEFAULT 0,
                last_request_date TEXT,
                referrer_id INTEGER,
                in_admin_mode INTEGER DEFAULT 0
            )
        ''')
        # Проверяем, есть ли колонка in_admin_mode
        c.execute("PRAGMA table_info(users)")
        columns = [col[1] for col in c.fetchall()]
        if 'in_admin_mode' not in columns:
            c.execute('ALTER TABLE users ADD COLUMN in_admin_mode INTEGER DEFAULT 0')
            logger.info("Добавлена колонка in_admin_mode в таблицу users")
        # Создаём остальные таблицы
        c.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                admin_id INTEGER PRIMARY KEY,
                rates TEXT,
                locations TEXT,
                active_locations TEXT,
                pairs TEXT,
                active_pairs TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS otps (
                otp TEXT PRIMARY KEY,
                user_id INTEGER,
                expiry TEXT,
                duration INTEGER
            )
        ''')
        conn.commit()
        logger.info("База данных успешно инициализирована")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при инициализации базы данных: {e}")
        raise
    finally:
        conn.close()

def get_user_data(user_id):
    conn = None
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('SELECT active_order, request_count, referrer_id, in_admin_mode FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if result is not None:
            return result[0], result[1], result[2], result[3]
        return None, 0, None, 0
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении данных пользователя {user_id}: {e}")
        return None, 0, None, 0
    finally:
        if conn:
            conn.close()

def save_user_data(user_id, active_order, referrer_id=None, in_admin_mode=None):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users WHERE user_id = ?', (user_id,))
        exists = c.fetchone()[0] > 0

        active_order_json = None
        if active_order is not None:
            try:
                active_order_json = json.dumps(active_order)
            except TypeError as e:
                logger.error(f"Ошибка сериализации active_order для user_id={user_id}: {str(e)}, active_order={active_order}")
                raise

        if not exists:
            c.execute('INSERT INTO users (user_id, active_order, request_count, referrer_id, in_admin_mode) VALUES (?, ?, ?, ?, ?)',
                      (user_id, active_order_json, 0, referrer_id if referrer_id is not None else None, in_admin_mode if in_admin_mode is not None else 0))
            logger.debug(f"Новая запись: user_id={user_id}, active_order={active_order}, referrer_id={referrer_id}, in_admin_mode={in_admin_mode}")
        elif referrer_id is not None and in_admin_mode is not None:
            c.execute('INSERT OR REPLACE INTO users (user_id, active_order, request_count, referrer_id, in_admin_mode) VALUES (?, ?, ?, ?, ?)',
                      (user_id, active_order_json, 0, referrer_id, in_admin_mode))
            logger.debug(f"Полное обновление: user_id={user_id}, active_order={active_order}, referrer_id={referrer_id}, in_admin_mode={in_admin_mode}")
        elif referrer_id is not None:
            c.execute('UPDATE users SET active_order = ?, referrer_id = ? WHERE user_id = ?',
                      (active_order_json, referrer_id, user_id))
            logger.debug(f"Обновление с referrer_id: user_id={user_id}, active_order={active_order}, referrer_id={referrer_id}")
        elif in_admin_mode is not None:
            c.execute('UPDATE users SET active_order = ?, in_admin_mode = ? WHERE user_id = ?',
                      (active_order_json, in_admin_mode, user_id))
            logger.debug(f"Обновление in_admin_mode: user_id={user_id}, active_order={active_order}, in_admin_mode={in_admin_mode}")
        else:
            c.execute('UPDATE users SET active_order = ? WHERE user_id = ?',
                      (active_order_json, user_id))
            logger.debug(f"Обновление active_order: user_id={user_id}, active_order={active_order}")
        conn.commit()
        c.execute('SELECT in_admin_mode FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        logger.debug(f"Проверка после сохранения: user_id={user_id}, in_admin_mode в БД={result[0] if result else None}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении данных пользователя {user_id}: {e}")
        raise
    except TypeError as e:
        logger.error(f"Ошибка сериализации в save_user_data для user_id={user_id}: {str(e)}")
        raise
    finally:
        conn.close()

def check_request_limit(user_id):
    from exbot import bot_config  # Локальный импорт bot_config
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        
        # Проверяем, является ли пользователь владельцем
        if str(user_id) == bot_config["owner_id"]:
            return True  # Владелец не имеет лимита
        
        # Проверяем, является ли пользователь админом
        c.execute('SELECT active_order FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if result and result[0]:  # Если есть active_order
            active_order = json.loads(result[0])
            if 'admin_expiry' in active_order:  # Если есть подписка админа
                expiry = datetime.strptime(active_order['admin_expiry'], '%Y-%m-%d %H:%M:%S')
                if datetime.now() <= expiry:  # Подписка активна
                    return True  # Админ с активной подпиской не имеет лимита
        
        # Логика для обычных пользователей
        c.execute('SELECT request_count, last_request_date FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if not result:
            return True
        request_count, last_request_date = result
        if not last_request_date:
            return request_count < 5
        last_date = datetime.strptime(last_request_date, '%Y-%m-%d')
        current_date = datetime.now()
        if last_date.date() != current_date.date():
            c.execute('UPDATE users SET request_count = 0, last_request_date = ? WHERE user_id = ?',
                      (current_date.strftime('%Y-%m-%d'), user_id))
            conn.commit()
            return True
        return request_count < 5
    except sqlite3.Error as e:
        logger.error(f"Ошибка при проверке лимита запросов для {user_id}: {e}")
        return False
    finally:
        conn.close()

def log_request(user_id):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        current_date = datetime.now().strftime('%Y-%m-%d')
        c.execute('UPDATE users SET request_count = request_count + 1, last_request_date = ? WHERE user_id = ?',
                  (current_date, user_id))
        conn.commit()
        logger.debug(f"Запрос для пользователя {user_id} зарегистрирован")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при логировании запроса для {user_id}: {e}")
        raise
    finally:
        conn.close()

def get_admin_data(admin_id):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('SELECT rates, locations, active_locations, pairs, active_pairs FROM admins WHERE admin_id = ?', (admin_id,))
        result = c.fetchone()
        if result:
            return {
                'rates': json.loads(result[0]) if result[0] else {},
                'locations': json.loads(result[1]) if result[1] else [],
                'active_locations': json.loads(result[2]) if result[2] else [],
                'pairs': json.loads(result[3]) if result[3] else [],
                'active_pairs': json.loads(result[4]) if result[4] else []
            }
        from exbot import bot_config
        default_data = {
            'rates': bot_config["default_rates"],
            'locations': bot_config["default_locations"],
            'active_locations': bot_config["default_active_locations"],
            'pairs': bot_config["default_pairs"],
            'active_pairs': bot_config["default_active_pairs"]
        }
        save_admin_data(admin_id, default_data)
        return default_data
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении данных админа {admin_id}: {e}")
        raise
    finally:
        conn.close()

def save_admin_data(admin_id, admin_data):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO admins (admin_id, rates, locations, active_locations, pairs, active_pairs)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            admin_id,
            json.dumps(admin_data['rates']),
            json.dumps(admin_data['locations']),
            json.dumps(admin_data['active_locations']),
            json.dumps(admin_data['pairs']),
            json.dumps(admin_data['active_pairs'])
        ))
        conn.commit()
        logger.debug(f"Данные админа {admin_id} сохранены: {admin_data}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении данных админа {admin_id}: {e}")
        raise
    finally:
        conn.close()

def save_otp_data(otp, user_id, expiry, duration):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS otps (
                otp TEXT PRIMARY KEY,
                user_id INTEGER,
                expiry TEXT,
                duration INTEGER
            )
        ''')
        c.execute('INSERT OR REPLACE INTO otps (otp, user_id, expiry, duration) VALUES (?, ?, ?, ?)',
                  (otp, user_id, expiry, duration))
        conn.commit()
        logger.debug(f"OTP {otp} сохранён для user_id={user_id}")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при сохранении OTP {otp}: {e}")
        raise
    finally:
        conn.close()

def get_otp_data(otp):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('SELECT user_id, expiry, duration FROM otps WHERE otp = ?', (otp,))
        result = c.fetchone()
        return result
    except sqlite3.Error as e:
        logger.error(f"Ошибка при получении данных OTP {otp}: {e}")
        return None
    finally:
        conn.close()

def delete_otp(otp):
    try:
        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute('DELETE FROM otps WHERE otp = ?', (otp,))
        conn.commit()
        logger.debug(f"OTP {otp} удалён")
    except sqlite3.Error as e:
        logger.error(f"Ошибка при удалении OTP {otp}: {e}")
        raise
    finally:
        conn.close()
