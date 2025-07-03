# app/database.py

import sqlite3
import logging
from datetime import datetime, date, time
from config import DB_PATH, DEFAULT_WELCOME_MESSAGE_BOT, DEFAULT_WELCOME_MESSAGE_GROUP

logger = logging.getLogger(__name__)

def adapt_datetime_iso(val: datetime) -> str:
    """Адаптер для преобразования объекта datetime в строку ISO 8601."""
    return val.isoformat()

def convert_datetime_iso(val: bytes) -> datetime:
    """Конвертер для преобразования строки ISO 8601 из БД в объект datetime."""
    return datetime.fromisoformat(val.decode())

def adapt_time_iso(val: time) -> str:
    """Адаптер для преобразования объекта time в строку ISO 8601."""
    return val.isoformat()

def convert_time_iso(val: bytes) -> time:
    """Конвертер для преобразования строки ISO 8601 из БД в объект time."""
    return time.fromisoformat(val.decode())

sqlite3.register_adapter(datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_datetime_iso)
# РЕФАКТОРИНГ: Добавлены адаптер и конвертер для типа TIME
sqlite3.register_adapter(time, adapt_time_iso)
sqlite3.register_converter("time", convert_time_iso)


class Database:
    """Класс для управления базой данных SQLite."""

    def __init__(self, db_path: str):
        """
        Инициализирует объект базы данных и создает таблицы, если их нет.

        Args:
            db_path (str): Путь к файлу базы данных.
        """
        self.db_path = db_path
        self.init_db()

    def switch_to_memory(self):
        """
        Переключает путь к базе данных на специальное значение для хранения в RAM.
        Используется для полной изоляции тестов.
        """
        self.db_path = ":memory:"
        self.init_db()

    def get_connection(self):
        """
        Возвращает соединение с базой данных.
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        """
        Инициализирует структуру базы данных, создавая все необходимые таблицы.
        """
        with self.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_id INTEGER UNIQUE NOT NULL,
                    username TEXT,
                    balance NUMERIC(18, 4) DEFAULT 0,
                    is_admin BOOLEAN DEFAULT 0,
                    transaction_count INTEGER DEFAULT 0,
                    grace_credit_used BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    end_date DATE,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # РЕФАКТОРИНГ: Структура таблицы events полностью изменена
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    activity_id INTEGER NOT NULL,
                    name TEXT,
                    description TEXT,
                    event_type TEXT NOT NULL CHECK(event_type IN ('recurring', 'single')),
                    event_date TIMESTAMP,
                    weekday INTEGER,
                    event_time TIME,
                    cost NUMERIC(18, 4) NOT NULL,
                    link TEXT,
                    reminder_time INTEGER,
                    reminder_text TEXT,
                    created_by INTEGER,
                    is_active BOOLEAN DEFAULT 1,
                    last_run TIMESTAMP,
                    FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE,
                    FOREIGN KEY (created_by) REFERENCES users(telegram_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_subscriptions (
                    user_id INTEGER NOT NULL,
                    activity_id INTEGER NOT NULL,
                    subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, activity_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER,
                    to_user_id INTEGER,
                    amount NUMERIC(18, 4) NOT NULL,
                    type TEXT NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (from_user_id) REFERENCES users(id),
                    FOREIGN KEY (to_user_id) REFERENCES users(id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            conn.execute("INSERT OR IGNORE INTO users (id, telegram_id, username) VALUES (0, 0, 'community_fund')")
            # РЕФАКТОРИНГ: Добавлена системная активность "Общие события"
            conn.execute("INSERT OR IGNORE INTO activities (id, name, description, is_active) VALUES (1, 'Общие события', '', 1)")
            
            default_settings = {
                'demurrage_rate': '0.01', 'demurrage_enabled': '0', 'exchange_rate': '1.0',
                'welcome_message_bot': DEFAULT_WELCOME_MESSAGE_BOT,
                'welcome_message_group': DEFAULT_WELCOME_MESSAGE_GROUP,
                'welcome_bonus_amount': '1000'
            }
            for key, value in default_settings.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()

    def get_user(self, telegram_id: int = None, username: str = None):
        """Получает пользователя по telegram_id или username."""
        with self.get_connection() as conn:
            if telegram_id is not None:
                return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if username:
                return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return None

    def create_user(self, telegram_id: int, username: str | None, is_admin: bool = False):
        """
        Создает нового пользователя и автоматически подписывает его на общие события.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)",
                (telegram_id, username.lower() if username else None, is_admin)
            )
            new_user_id = cursor.lastrowid
            # РЕФАКТОРИНГ: Автоматическая подписка на активность "Общие события" (id=1)
            cursor.execute("INSERT OR IGNORE INTO user_subscriptions (user_id, activity_id) VALUES (?, 1)", (new_user_id,))
            conn.commit()

    def update_user_username(self, telegram_id: int, username: str):
        """Обновляет username существующего пользователя."""
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET username = ? WHERE telegram_id = ?",
                (username.lower(), telegram_id)
            )
            conn.commit()

    def set_admin_status(self, telegram_id: int, is_admin: bool):
        """
        Устанавливает или снимает права администратора для пользователя.
        """
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE telegram_id = ?", (is_admin, telegram_id))
            conn.commit()

    def get_all_admins(self):
        """Возвращает список всех администраторов."""
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM users WHERE is_admin = 1").fetchall()

    def get_setting(self, key: str, default: str = None):
        """Получает значение настройки по ключу."""
        with self.get_connection() as conn:
            result = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return result['value'] if result else default

    def set_setting(self, key: str, value: str):
        """Устанавливает значение настройки."""
        with self.get_connection() as conn:
            conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
            
    def handle_debt_repayment(self, user_id: int):
        """Сбрасывает флаг кредита, если баланс стал положительным."""
        with self.get_connection() as conn:
            user = conn.execute("SELECT balance, grace_credit_used FROM users WHERE id = ?", (user_id,)).fetchone()
            if user and user['grace_credit_used'] and user['balance'] >= 0:
                conn.execute("UPDATE users SET grace_credit_used = 0 WHERE id = ?", (user_id,))
                conn.commit()
                logger.info(f"Grace credit flag reset for user_id {user_id} due to positive balance.")

    def get_all_activities(self) -> list[sqlite3.Row]:
        """Возвращает список всех активных активностей."""
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM activities WHERE is_active = 1 ORDER BY name").fetchall()

    def get_activity(self, activity_id: int) -> sqlite3.Row | None:
        """Получает одну активность по ID."""
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()

    def get_user_subscriptions(self, telegram_id: int) -> list[sqlite3.Row]:
        """Получает подписки пользователя по его telegram_id."""
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if not user: return []
            return conn.execute("""
                SELECT us.activity_id FROM user_subscriptions us
                WHERE us.user_id = ?
            """, (user['id'],)).fetchall()

    def is_user_subscribed(self, telegram_id: int, activity_id: int) -> bool:
        """Проверяет, подписан ли пользователь на активность."""
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if not user: return False
            result = conn.execute(
                "SELECT 1 FROM user_subscriptions WHERE user_id = ? AND activity_id = ?",
                (user['id'], activity_id)
            ).fetchone()
            return result is not None

    def add_subscription(self, telegram_id: int, activity_id: int):
        """Добавляет подписку пользователя на активность."""
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if user:
                conn.execute("INSERT OR IGNORE INTO user_subscriptions (user_id, activity_id) VALUES (?, ?)", (user['id'], activity_id))
                conn.commit()

    def remove_subscription(self, telegram_id: int, activity_id: int):
        """Удаляет подписку пользователя."""
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if user:
                # РЕФАКТОРИНГ: Запрет отписки от системной активности "Общие события"
                if activity_id == 1:
                    logger.warning(f"User {telegram_id} tried to unsubscribe from system activity 1.")
                    return
                conn.execute("DELETE FROM user_subscriptions WHERE user_id = ? AND activity_id = ?", (user['id'], activity_id))
                conn.commit()

    def create_activity(self, name: str, description: str, end_date: date | None) -> int:
        """Создает новую активность."""
        with self.get_connection() as conn:
            cursor = conn.execute(
                "INSERT INTO activities (name, description, end_date) VALUES (?, ?, ?)",
                (name, description, end_date)
            )
            conn.commit()
            return cursor.lastrowid

    def update_activity(self, activity_id: int, name: str = None, description: str = None, end_date: date = None):
        """Обновляет данные активности."""
        with self.get_connection() as conn:
            if name is not None:
                conn.execute("UPDATE activities SET name = ? WHERE id = ?", (name, activity_id))
            if description is not None:
                conn.execute("UPDATE activities SET description = ? WHERE id = ?", (description, activity_id))
            if end_date is not None or (isinstance(end_date, type(None))):
                conn.execute("UPDATE activities SET end_date = ? WHERE id = ?", (end_date, activity_id))
            conn.commit()

    def delete_activity(self, activity_id: int):
        """Удаляет активность (каскадно удаляя события и подписки)."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
            conn.commit()

    def get_all_events(self) -> list[sqlite3.Row]:
        """Возвращает список всех активных событий."""
        with self.get_connection() as conn:
            return conn.execute("""
                SELECT e.*, a.name as activity_name, a.description as activity_description
                FROM events e
                JOIN activities a ON e.activity_id = a.id
                WHERE e.is_active = 1
            """).fetchall()

    def get_event(self, event_id: int) -> sqlite3.Row | None:
        """Получает одно событие по ID."""
        with self.get_connection() as conn:
            return conn.execute("""
                SELECT e.*, a.name as activity_name, a.description as activity_description 
                FROM events e 
                JOIN activities a ON e.activity_id = a.id 
                WHERE e.id = ?
            """, (event_id,)).fetchone()

    # ИЗМЕНЕНО: Добавлен новый метод для Задачи 3
    def get_events_for_activity(self, activity_id: int) -> list[sqlite3.Row]:
        """
        Возвращает список всех активных событий для конкретной активности.

        Args:
            activity_id (int): ID активности.

        Returns:
            list[sqlite3.Row]: Список событий.
        """
        with self.get_connection() as conn:
            return conn.execute("""
                SELECT * FROM events 
                WHERE activity_id = ? AND is_active = 1
                ORDER BY event_type, event_date, weekday, event_time
            """, (activity_id,)).fetchall()

    # РЕФАКТОРИНГ: Метод create_event обновлен для работы с новой структурой
    def create_event(self, **kwargs) -> int:
        """Создает новое событие, используя именованные аргументы."""
        with self.get_connection() as conn:
            columns = ', '.join(kwargs.keys())
            placeholders = ', '.join(['?'] * len(kwargs))
            query = f"INSERT INTO events ({columns}) VALUES ({placeholders})"
            cursor = conn.execute(query, tuple(kwargs.values()))
            conn.commit()
            return cursor.lastrowid

    def update_event(self, event_id: int, **kwargs):
        """Обновляет поля события."""
        with self.get_connection() as conn:
            fields = []
            params = []
            for key, value in kwargs.items():
                fields.append(f"{key} = ?")
                params.append(value)
            
            if not fields: return
            
            params.append(event_id)
            query = f"UPDATE events SET {', '.join(fields)} WHERE id = ?"
            conn.execute(query, tuple(params))
            conn.commit()

    def delete_event(self, event_id: int):
        """Удаляет событие."""
        with self.get_connection() as conn:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()

db = Database(DB_PATH)