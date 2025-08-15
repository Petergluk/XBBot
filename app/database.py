# 2025-07-24 14:27:01
import sqlite3
import logging
from datetime import datetime, date, time
from config import DB_PATH, DEFAULT_WELCOME_MESSAGE_BOT, DEFAULT_WELCOME_MESSAGE_GROUP, DEFAULT_REMINDER_TEXT

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
sqlite3.register_adapter(time, adapt_time_iso)
sqlite3.register_converter("time", convert_time_iso)


class Database:
    """Класс для управления базой данных SQLite."""

    def __init__(self, db_path: str):
        """
        Инициализирует объект базы данных.

        Args:
            db_path (str): Путь к файлу базы данных.
        """
        self.db_path = db_path
        self.init_db()

    def get_connection(self):
        """
        Возвращает соединение с базой данных.

        Returns:
            sqlite3.Connection: Объект соединения с БД.
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def init_db(self):
        """Инициализирует структуру базы данных и создает начальные записи."""
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
            # ИЗМЕНЕНО: Имя системного пользователя унифицировано на 'fund' согласно вашему требованию.
            conn.execute("INSERT OR IGNORE INTO users (id, telegram_id, username) VALUES (0, 0, 'fund')")
            conn.execute("INSERT OR IGNORE INTO activities (id, name, description, is_active) VALUES (1, 'Общие события', '', 1)")
            
            default_settings = {
                'demurrage_rate': '0.01', 'demurrage_enabled': '0', 'exchange_rate': '1.0',
                'welcome_message_bot': DEFAULT_WELCOME_MESSAGE_BOT,
                'welcome_message_group': DEFAULT_WELCOME_MESSAGE_GROUP,
                'welcome_bonus_amount': '1000',
                'default_reminder_text': DEFAULT_REMINDER_TEXT,
                'demurrage_interval_days': '1',
                'demurrage_last_run': '1970-01-01'
            }
            for key, value in default_settings.items():
                conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()

    def get_user(self, telegram_id: int = None, username: str = None):
        """
        Получает пользователя из БД по telegram_id или username.

        Args:
            telegram_id (int, optional): Telegram ID пользователя.
            username (str, optional): Имя пользователя.

        Returns:
            sqlite3.Row | None: Данные пользователя или None, если не найден.
        """
        with self.get_connection() as conn:
            if telegram_id is not None:
                return conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if username:
                return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return None

    def create_user(self, telegram_id: int, username: str | None, is_admin: bool = False):
        """
        Создает нового пользователя в БД.

        Args:
            telegram_id (int): Telegram ID.
            username (str | None): Имя пользователя.
            is_admin (bool): Является ли пользователь админом.
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (telegram_id, username, is_admin) VALUES (?, ?, ?)",
                (telegram_id, username.lower() if username else None, is_admin)
            )
            new_user_id = cursor.lastrowid
            cursor.execute("INSERT OR IGNORE INTO user_subscriptions (user_id, activity_id) VALUES (?, 1)", (new_user_id,))
            conn.commit()

    def update_user_username(self, telegram_id: int, username: str):
        """
        Обновляет имя пользователя.

        Args:
            telegram_id (int): Telegram ID.
            username (str): Новое имя пользователя.
        """
        with self.get_connection() as conn:
            conn.execute(
                "UPDATE users SET username = ? WHERE telegram_id = ?",
                (username.lower(), telegram_id)
            )
            conn.commit()

    def set_admin_status(self, telegram_id: int, is_admin: bool):
        """
        Устанавливает или снимает права администратора.

        Args:
            telegram_id (int): Telegram ID.
            is_admin (bool): Статус администратора.
        """
        with self.get_connection() as conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE telegram_id = ?", (is_admin, telegram_id))
            conn.commit()

    def get_all_admins(self):
        """
        Возвращает список всех администраторов.

        Returns:
            list[sqlite3.Row]: Список администраторов.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM users WHERE is_admin = 1").fetchall()

    def get_setting(self, key: str, default: str = None):
        """
        Получает значение настройки из БД.

        Args:
            key (str): Ключ настройки.
            default (str, optional): Значение по умолчанию.

        Returns:
            str | None: Значение настройки.
        """
        with self.get_connection() as conn:
            result = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return result['value'] if result else default

    def set_setting(self, key: str, value: str):
        """
        Устанавливает значение настройки в БД.

        Args:
            key (str): Ключ настройки.
            value (str): Значение настройки.
        """
        with self.get_connection() as conn:
            conn.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
            
    def handle_debt_repayment(self, user_id: int):
        """
        Сбрасывает флаг использованного кредита, если баланс стал положительным.

        Args:
            user_id (int): ID пользователя в БД.
        """
        with self.get_connection() as conn:
            user = conn.execute("SELECT balance, grace_credit_used FROM users WHERE id = ?", (user_id,)).fetchone()
            if user and user['grace_credit_used'] and user['balance'] >= 0:
                conn.execute("UPDATE users SET grace_credit_used = 0 WHERE id = ?", (user_id,))
                conn.commit()
                logger.info(f"Grace credit flag reset for user_id {user_id} due to positive balance.")

    def get_all_activities(self) -> list[sqlite3.Row]:
        """
        Возвращает список всех активных активностей.

        Returns:
            list[sqlite3.Row]: Список активностей.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM activities WHERE is_active = 1 ORDER BY name").fetchall()

    def get_activity(self, activity_id: int) -> sqlite3.Row | None:
        """
        Получает одну активность по ее ID.

        Args:
            activity_id (int): ID активности.

        Returns:
            sqlite3.Row | None: Данные активности.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM activities WHERE id = ?", (activity_id,)).fetchone()

    def get_user_subscriptions(self, telegram_id: int) -> list[sqlite3.Row]:
        """
        Получает список подписок пользователя.

        Args:
            telegram_id (int): Telegram ID пользователя.

        Returns:
            list[sqlite3.Row]: Список ID активностей, на которые подписан пользователь.
        """
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if not user: return []
            return conn.execute("SELECT us.activity_id FROM user_subscriptions us WHERE us.user_id = ?", (user['id'],)).fetchall()

    def is_user_subscribed(self, telegram_id: int, activity_id: int) -> bool:
        """
        Проверяет, подписан ли пользователь на активность.

        Args:
            telegram_id (int): Telegram ID пользователя.
            activity_id (int): ID активности.

        Returns:
            bool: True, если подписан.
        """
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if not user: return False
            result = conn.execute("SELECT 1 FROM user_subscriptions WHERE user_id = ? AND activity_id = ?", (user['id'], activity_id)).fetchone()
            return result is not None

    def add_subscription(self, telegram_id: int, activity_id: int):
        """
        Добавляет подписку пользователя на активность.

        Args:
            telegram_id (int): Telegram ID пользователя.
            activity_id (int): ID активности.
        """
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if user:
                conn.execute("INSERT OR IGNORE INTO user_subscriptions (user_id, activity_id) VALUES (?, ?)", (user['id'], activity_id))
                conn.commit()

    def remove_subscription(self, telegram_id: int, activity_id: int):
        """
        Удаляет подписку пользователя с активности.

        Args:
            telegram_id (int): Telegram ID пользователя.
            activity_id (int): ID активности.
        """
        with self.get_connection() as conn:
            user = self.get_user(telegram_id=telegram_id)
            if user:
                if activity_id == 1:
                    logger.warning(f"User {telegram_id} tried to unsubscribe from system activity 1.")
                    return
                conn.execute("DELETE FROM user_subscriptions WHERE user_id = ? AND activity_id = ?", (user['id'], activity_id))
                conn.commit()

    def create_activity(self, name: str, description: str, end_date: date | None) -> int:
        """
        Создает новую активность.

        Args:
            name (str): Название активности.
            description (str): Описание.
            end_date (date | None): Дата окончания.

        Returns:
            int: ID новой активности.
        """
        with self.get_connection() as conn:
            cursor = conn.execute("INSERT INTO activities (name, description, end_date) VALUES (?, ?, ?)", (name, description, end_date))
            conn.commit()
            return cursor.lastrowid

    def update_activity(self, activity_id: int, name: str = None, description: str = None, end_date: date = None):
        """
        Обновляет данные активности.

        Args:
            activity_id (int): ID активности.
            name (str, optional): Новое название.
            description (str, optional): Новое описание.
            end_date (date, optional): Новая дата окончания.
        """
        with self.get_connection() as conn:
            if name is not None:
                conn.execute("UPDATE activities SET name = ? WHERE id = ?", (name, activity_id))
            if description is not None:
                conn.execute("UPDATE activities SET description = ? WHERE id = ?", (description, activity_id))
            if end_date is not None or (isinstance(end_date, type(None))):
                conn.execute("UPDATE activities SET end_date = ? WHERE id = ?", (end_date, activity_id))
            conn.commit()

    def delete_activity(self, activity_id: int):
        """
        Удаляет активность.

        Args:
            activity_id (int): ID активности.
        """
        with self.get_connection() as conn:
            conn.execute("DELETE FROM activities WHERE id = ?", (activity_id,))
            conn.commit()

    def get_all_events(self) -> list[sqlite3.Row]:
        """
        Возвращает список всех активных событий.

        Returns:
            list[sqlite3.Row]: Список событий.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT e.*, a.name as activity_name, a.description as activity_description FROM events e JOIN activities a ON e.activity_id = a.id WHERE e.is_active = 1").fetchall()

    def get_event(self, event_id: int) -> sqlite3.Row | None:
        """
        Получает одно событие по его ID.

        Args:
            event_id (int): ID события.

        Returns:
            sqlite3.Row | None: Данные события.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT e.*, a.name as activity_name, a.description as activity_description FROM events e JOIN activities a ON e.activity_id = a.id WHERE e.id = ?", (event_id,)).fetchone()

    def get_events_for_activity(self, activity_id: int) -> list[sqlite3.Row]:
        """
        Получает события для конкретной активности.

        Args:
            activity_id (int): ID активности.

        Returns:
            list[sqlite3.Row]: Список событий.
        """
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM events WHERE activity_id = ? AND is_active = 1 ORDER BY event_type, event_date, weekday, event_time", (activity_id,)).fetchall()

    def create_event(self, **kwargs) -> int:
        """
        Создает новое событие.

        Args:
            **kwargs: Поля для вставки в таблицу events.

        Returns:
            int: ID нового события.
        """
        with self.get_connection() as conn:
            columns = ', '.join(kwargs.keys())
            placeholders = ', '.join(['?'] * len(kwargs))
            query = f"INSERT INTO events ({columns}) VALUES ({placeholders})"
            cursor = conn.execute(query, tuple(kwargs.values()))
            conn.commit()
            return cursor.lastrowid

    def update_event(self, event_id: int, **kwargs):
        """
        Обновляет данные события.

        Args:
            event_id (int): ID события.
            **kwargs: Поля для обновления.
        """
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
        """
        Удаляет событие.

        Args:
            event_id (int): ID события.
        """
        with self.get_connection() as conn:
            conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            conn.commit()

db = Database(DB_PATH)
