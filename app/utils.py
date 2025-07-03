# app/utils.py

import logging
from datetime import datetime, timedelta, time
from decimal import Decimal
from aiogram import Bot
from config import MAIN_GROUP_ID
from app.database import db

logger = logging.getLogger(__name__)

def format_amount(amount: Decimal) -> str:
    """
    Форматирует сумму для вывода, убирая лишние нули и избегая научной нотации.

    Args:
        amount (Decimal): Сумма для форматирования.

    Returns:
        str: Отформатированная строка.
    """
    if amount is None:
        return "0"
    
    s = f'{amount:f}'
    
    if '.' in s:
        s = s.rstrip('0').rstrip('.')
        
    return s

async def get_user_balance(telegram_id: int) -> Decimal:
    """Получает баланс пользователя."""
    user = db.get_user(telegram_id=telegram_id)
    return Decimal(str(user['balance'])) if user else Decimal('0')

async def get_transaction_count(telegram_id: int) -> int:
    """Получает количество транзакций пользователя."""
    user = db.get_user(telegram_id=telegram_id)
    return user['transaction_count'] if user else 0

async def is_admin(telegram_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    user = db.get_user(telegram_id=telegram_id)
    return bool(user['is_admin']) if user else False

async def is_user_in_group(bot: Bot, telegram_id: int) -> bool:
    """Проверяет, состоит ли пользователь в основной группе."""
    try:
        member = await bot.get_chat_member(MAIN_GROUP_ID, telegram_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Could not check user {telegram_id} in group {MAIN_GROUP_ID}: {e}")
        return False

async def ensure_user_exists(telegram_id: int, username: str | None) -> bool:
    """
    Проверяет существование пользователя и создает его, если он отсутствует.
    Также обновляет username, если он появился или изменился.
    """
    user = db.get_user(telegram_id=telegram_id)
    
    if not user:
        db.create_user(telegram_id, username)
        logger.info(f"New user created: {username or telegram_id}")
        return True
    
    if username and (not user['username'] or user['username'] != username.lower()):
        db.update_user_username(telegram_id, username)
        logger.info(f"Username for user {telegram_id} updated to {username.lower()}")

    return False

# РЕФАКТОРИНГ: Старая функция get_next_occurrence удалена.
# Новая функция для вычисления следующего запуска на основе структурированных данных.
def get_next_run_time(
    event_type: str, 
    event_date: datetime | None, 
    weekday: int | None, 
    event_time: time | None,
    last_run: datetime | None = None
) -> datetime | None:
    """
    Вычисляет следующую дату и время для события на основе его типа и расписания.

    Args:
        event_type (str): 'single' или 'recurring'.
        event_date (datetime | None): Дата и время для разового события.
        weekday (int | None): День недели (0=Пн) для регулярного события.
        event_time (time | None): Время для регулярного события.
        last_run (datetime, optional): Время последнего запуска. 
                                       Используется для вычисления следующего запуска.

    Returns:
        datetime | None: Объект datetime следующего события или None, если запуск невозможен.
    """
    now = datetime.now()
    base_time = last_run or now

    if event_type == 'single':
        # Разовое событие, которое еще не прошло
        if event_date and event_date > now:
            return event_date
        return None

    if event_type == 'recurring' and weekday is not None and event_time is not None:
        # Начинаем поиск со следующего дня после последнего запуска (или с сегодня)
        next_run_candidate = (base_time + timedelta(days=1)).date()
        
        # Ищем следующий подходящий день недели
        days_ahead = weekday - next_run_candidate.weekday()
        if days_ahead < 0:
            days_ahead += 7
        
        next_date = next_run_candidate + timedelta(days=days_ahead)
        return datetime.combine(next_date, event_time)

    return None