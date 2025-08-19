# XBalanseBot/app/database.py
# v1.8.0 - 2025-08-20 (Render.com deployment ready)
import logging
import os
from datetime import datetime, date, time
from typing import Any, Dict, List, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from config import (
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD,
    DEFAULT_WELCOME_MESSAGE_BOT, DEFAULT_WELCOME_MESSAGE_GROUP, DEFAULT_REMINDER_TEXT
)

logger = logging.getLogger(__name__)

# --- НОВЫЙ УНИВЕРСАЛЬНЫЙ БЛОК ДЛЯ ПОДКЛЮЧЕНИЯ К БД ---
# Проверяем, есть ли переменная DATABASE_URL (стандарт для Render.com)
if database_url := os.environ.get("DATABASE_URL"):
    CONNINFO = database_url
    logger.info("Using DATABASE_URL for database connection.")
else:
    # Если нет, собираем строку подключения из отдельных переменных (для локальной разработки)
    logger.info("Using individual POSTGRES variables for database connection.")
    CONNINFO = (
        f"host={POSTGRES_HOST} port={POSTGRES_PORT} dbname={POSTGRES_DB} "
        f"user={POSTGRES_USER} password={POSTGRES_PASSWORD}"
    )


class Database:
    """Класс для асинхронного управления базой данных PostgreSQL."""

    def __init__(self, conninfo: str):
        self.conninfo = conninfo
        self.pool: Optional[AsyncConnectionPool] = None

    async def initialize(self):
        """Инициализирует пул соединений и структуру базы данных."""
        logger.info("Initializing database connection pool...")
        self.pool = AsyncConnectionPool(self.conninfo, open=False, max_size=10)
        await self.pool.open()
        logger.info("Connection pool opened successfully.")
        await self.init_db()

    async def close(self):
        """Закрывает пул соединений."""
        if self.pool:
            await self.pool.close()
            logger.info("Database connection pool closed.")

    async def init_db(self):
        """Инициализирует структуру базы данных (таблицы) и начальные записи."""
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        id SERIAL PRIMARY KEY,
                        telegram_id BIGINT UNIQUE NOT NULL,
                        username TEXT,
                        balance NUMERIC(18, 4) DEFAULT 0,
                        is_admin BOOLEAN DEFAULT FALSE,
                        transaction_count INTEGER DEFAULT 0,
                        grace_credit_used BOOLEAN DEFAULT FALSE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS activities (
                        id SERIAL PRIMARY KEY,
                        name TEXT UNIQUE NOT NULL,
                        description TEXT,
                        end_date DATE,
                        is_active BOOLEAN DEFAULT TRUE,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS events (
                        id SERIAL PRIMARY KEY,
                        activity_id INTEGER NOT NULL,
                        name TEXT,
                        description TEXT,
                        event_type TEXT NOT NULL CHECK(event_type IN ('recurring', 'single')),
                        event_date TIMESTAMP WITH TIME ZONE,
                        weekday INTEGER,
                        event_time TIME,
                        cost NUMERIC(18, 4) NOT NULL,
                        link TEXT,
                        reminder_time INTEGER,
                        reminder_text TEXT,
                        created_by BIGINT,
                        is_active BOOLEAN DEFAULT TRUE,
                        last_run TIMESTAMP WITH TIME ZONE,
                        FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS user_subscriptions (
                        user_id INTEGER NOT NULL,
                        activity_id INTEGER NOT NULL,
                        subscribed_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY (user_id, activity_id),
                        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY (activity_id) REFERENCES activities(id) ON DELETE CASCADE
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS transactions (
                        id SERIAL PRIMARY KEY,
                        from_user_id INTEGER,
                        to_user_id INTEGER,
                        amount NUMERIC(18, 4) NOT NULL,
                        type TEXT NOT NULL,
                        comment TEXT,
                        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (from_user_id) REFERENCES users(id),
                        FOREIGN KEY (to_user_id) REFERENCES users(id)
                    )
                """)
                await cur.execute("""
                    CREATE TABLE IF NOT EXISTS settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    )
                """)

                # Системные записи
                await cur.execute("""
                    INSERT INTO users (id, telegram_id, username)
                    VALUES (0, 0, 'fund')
                    ON CONFLICT (id) DO NOTHING
                """)
                await cur.execute("""
                    INSERT INTO activities (id, name, description, is_active)
                    VALUES (1, 'Общие события', '', TRUE)
                    ON CONFLICT (id) DO NOTHING
                """)

                # Синхронизация sequences с учётом системных записей
                await cur.execute("""
                    DO $$
                    DECLARE m integer;
                    BEGIN
                        SELECT MAX(id) INTO m FROM users;
                        IF m IS NULL OR m = 0 THEN
                            PERFORM setval('users_id_seq', 1, false); -- следующий nextval() = 1
                        ELSE
                            PERFORM setval('users_id_seq', m, true);  -- следующий nextval() = m+1
                        END IF;
                    END$$;
                """)

                await cur.execute("""
                    DO $$
                    DECLARE m integer;
                    BEGIN
                        SELECT MAX(id) INTO m FROM activities;
                        IF m IS NULL THEN
                            PERFORM setval('activities_id_seq', 1, false);
                        ELSE
                            PERFORM setval('activities_id_seq', m, true);  -- обычно m=1 -> следующий 2
                        END IF;
                    END$$;
                """)

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
                    await cur.execute(
                        "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                        (key, value)
                    )

    async def get_user(self, telegram_id: int = None, username: str = None) -> Optional[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                if telegram_id is not None:
                    await cur.execute("SELECT * FROM users WHERE telegram_id = %s", (telegram_id,))
                elif username:
                    await cur.execute("SELECT * FROM users WHERE username = %s", (username,))
                else:
                    return None
                return await cur.fetchone()

    async def create_user(self, telegram_id: int, username: Optional[str], is_admin: bool = False):
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO users (telegram_id, username, is_admin) VALUES (%s, %s, %s) RETURNING id",
                    (telegram_id, username.lower() if username else None, is_admin)
                )
                new_user_id_row = await cur.fetchone()
                if new_user_id_row:
                    await cur.execute(
                        "INSERT INTO user_subscriptions (user_id, activity_id) VALUES (%s, 1) ON CONFLICT DO NOTHING",
                        (new_user_id_row[0],)
                    )

    async def update_user_username(self, telegram_id: int, username: str):
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE users SET username = %s WHERE telegram_id = %s", (username.lower(), telegram_id))

    async def set_admin_status(self, telegram_id: int, is_admin: bool):
        async with self.pool.connection() as conn:
            await conn.execute("UPDATE users SET is_admin = %s WHERE telegram_id = %s", (is_admin, telegram_id))

    async def get_all_admins(self) -> List[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM users WHERE is_admin = TRUE")
                return await cur.fetchall()

    async def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
                result = await cur.fetchone()
                return result[0] if result else default

    async def set_setting(self, key: str, value: str):
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value)
            )

    async def handle_debt_repayment(self, user_id: int):
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT balance, grace_credit_used FROM users WHERE id = %s", (user_id,))
                user = await cur.fetchone()
                if user and user['grace_credit_used'] and user['balance'] >= 0:
                    await conn.execute("UPDATE users SET grace_credit_used = FALSE WHERE id = %s", (user_id,))
                    logger.info(f"Grace credit flag reset for user_id {user_id} due to positive balance.")

    async def get_all_activities(self) -> List[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM activities WHERE is_active = TRUE ORDER BY name")
                return await cur.fetchall()

    async def get_activity(self, activity_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("SELECT * FROM activities WHERE id = %s", (activity_id,))
                return await cur.fetchone()

    async def get_user_subscriptions(self, telegram_id: int) -> List[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                user = await self.get_user(telegram_id=telegram_id)
                if not user: return []
                await cur.execute("SELECT us.activity_id FROM user_subscriptions us WHERE us.user_id = %s", (user['id'],))
                return await cur.fetchall()

    async def is_user_subscribed(self, telegram_id: int, activity_id: int) -> bool:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                user = await self.get_user(telegram_id=telegram_id)
                if not user: return False
                await cur.execute("SELECT 1 FROM user_subscriptions WHERE user_id = %s AND activity_id = %s", (user['id'], activity_id))
                return await cur.fetchone() is not None

    async def add_subscription(self, telegram_id: int, activity_id: int):
        async with self.pool.connection() as conn:
            user = await self.get_user(telegram_id=telegram_id)
            if user:
                await conn.execute("INSERT INTO user_subscriptions (user_id, activity_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (user['id'], activity_id))

    async def remove_subscription(self, telegram_id: int, activity_id: int):
        async with self.pool.connection() as conn:
            user = await self.get_user(telegram_id=telegram_id)
            if user:
                if activity_id == 1:
                    logger.warning(f"User {telegram_id} tried to unsubscribe from system activity 1.")
                    return
                await conn.execute("DELETE FROM user_subscriptions WHERE user_id = %s AND activity_id = %s", (user['id'], activity_id))

    async def create_activity(self, name: str, description: str, end_date: Optional[date]) -> int:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO activities (name, description, end_date) VALUES (%s, %s, %s) RETURNING id",
                    (name, description, end_date)
                )
                result = await cur.fetchone()
                return result[0] if result else 0

    async def update_activity(self, activity_id: int, name: str = None, description: str = None, end_date: date = None):
        async with self.pool.connection() as conn:
            if name is not None:
                await conn.execute("UPDATE activities SET name = %s WHERE id = %s", (name, activity_id))
            if description is not None:
                await conn.execute("UPDATE activities SET description = %s WHERE id = %s", (description, activity_id))
            if end_date is not None or (isinstance(end_date, type(None))):
                await conn.execute("UPDATE activities SET end_date = %s WHERE id = %s", (end_date, activity_id))

    async def delete_activity(self, activity_id: int):
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM activities WHERE id = %s", (activity_id,))

    async def get_all_events(self) -> List[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT e.*, a.name as activity_name, a.description as activity_description 
                    FROM events e JOIN activities a ON e.activity_id = a.id 
                    WHERE e.is_active = TRUE
                """)
                return await cur.fetchall()

    async def get_event(self, event_id: int) -> Optional[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute("""
                    SELECT e.*, a.name as activity_name, a.description as activity_description 
                    FROM events e JOIN activities a ON e.activity_id = a.id 
                    WHERE e.id = %s
                """, (event_id,))
                return await cur.fetchone()

    async def get_events_for_activity(self, activity_id: int) -> List[Dict[str, Any]]:
        async with self.pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(
                    "SELECT * FROM events WHERE activity_id = %s AND is_active = TRUE ORDER BY event_type, event_date, weekday, event_time",
                    (activity_id,)
                )
                return await cur.fetchall()

    async def create_event(self, **kwargs) -> int:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                columns = ', '.join(kwargs.keys())
                placeholders = ', '.join(['%s'] * len(kwargs))
                query = f"INSERT INTO events ({columns}) VALUES ({placeholders}) RETURNING id"
                await cur.execute(query, tuple(kwargs.values()))
                result = await cur.fetchone()
                return result[0] if result else 0

    async def update_event(self, event_id: int, **kwargs):
        async with self.pool.connection() as conn:
            fields = []
            params = []
            for key, value in kwargs.items():
                fields.append(f"{key} = %s")
                params.append(value)
            if not fields: return
            params.append(event_id)
            query = f"UPDATE events SET {', '.join(fields)} WHERE id = %s"
            await conn.execute(query, tuple(params))

    async def delete_event(self, event_id: int):
        async with self.pool.connection() as conn:
            await conn.execute("DELETE FROM events WHERE id = %s", (event_id,))

db = Database(CONNINFO)