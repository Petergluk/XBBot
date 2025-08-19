# XBalanseBot/app/utils.py
# v1.5.6 - 2025-08-17 (fix recurring same-day logic: return today if time hasn't passed)
import logging
from datetime import datetime, timedelta, time
from decimal import Decimal
from zoneinfo import ZoneInfo
from aiogram import Bot
from config import MAIN_GROUP_ID, CURRENCY_SYMBOL
from app.database import db

logger = logging.getLogger(__name__)

MOSCOW_TZ = ZoneInfo("Europe/Moscow")

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

def format_transactions_history(transactions: list, user_db_id: int) -> str:
    """
    Форматирует список транзакций в текстовый отчет по категориям.

    Args:
        transactions (list): Список транзакций (объекты sqlite3.Row).
        user_db_id (int): ID пользователя в БД, для которого строится отчет.

    Returns:
        str: Отформатированный HTML-текст истории.
    """
    response_parts = []
    top_ups, incoming, outgoing, system_debits = [], [], [], []

    for tx in transactions:
        if tx['to_user_id'] == user_db_id:
            if tx['type'] in ('manual_add', 'welcome_bonus', 'top_up'):
                top_ups.append(tx)
            elif tx['type'] in ('transfer', 'fund_payment'):
                incoming.append(tx)
        elif tx['from_user_id'] == user_db_id:
            if tx['type'] in ('transfer', 'event_fee'):
                outgoing.append(tx)
            elif tx['type'] in ('demurrage', 'manual_rem'):
                system_debits.append(tx)

    def format_tx_line(tx, sign, prefix="", peer_name=""):
        date_str = tx['created_at'].strftime('%d.%m %H:%M')
        amount_str = format_amount(Decimal(str(tx['amount'])))
        comment = f" ({tx['comment']})" if tx['comment'] else ""
        return f"  {sign} {amount_str} {prefix}{peer_name}{comment} - {date_str}\n"

    if top_ups:
        response_parts.append("\n\n💰 <b>Пополнения:</b>\n")
        for tx in top_ups:
            response_parts.append(format_tx_line(tx, "✅ +"))
    
    if incoming:
        response_parts.append("\n📥 <b>Входящие переводы:</b>\n")
        for tx in incoming:
            peer = f"@{tx['sender_username']}" if tx['sender_username'] else "Пользователь"
            response_parts.append(format_tx_line(tx, "➕", prefix="от ", peer_name=peer))

    if outgoing:
        response_parts.append("\n📤 <b>Исходящие переводы и платежи:</b>\n")
        for tx in outgoing:
            peer = f"@{tx['recipient_username']}" if tx['recipient_username'] != 'fund' else "Фонд"
            response_parts.append(format_tx_line(tx, "➖ -", peer_name=peer))

    if system_debits:
        response_parts.append("\n💸 <b>Системные списания:</b>\n")
        for tx in system_debits:
            peer = "Техническое списание" if tx['type'] == 'manual_rem' else "Демерредж"
            response_parts.append(format_tx_line(tx, "➖ -", peer_name=peer))
            
    return "".join(response_parts)

async def get_user_balance(telegram_id: int) -> Decimal:
    """Получает баланс пользователя."""
    user = await db.get_user(telegram_id=telegram_id)
    return Decimal(str(user['balance'])) if user else Decimal('0')

async def get_transaction_count(telegram_id: int) -> int:
    """Получает количество транзакций пользователя."""
    user = await db.get_user(telegram_id=telegram_id)
    return user['transaction_count'] if user else 0

async def is_admin(telegram_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    user = await db.get_user(telegram_id=telegram_id)
    return bool(user['is_admin']) if user else False

async def is_user_in_group(bot: Bot, telegram_id: int) -> bool:
    """
    Проверяет, состоит ли пользователь в основной группе.
    """
    if telegram_id == 0:
        return True
        
    try:
        member = await bot.get_chat_member(MAIN_GROUP_ID, telegram_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.warning(f"Could not check user {telegram_id} in group {MAIN_GROUP_ID}: {e}")
        return False

async def ensure_user_exists(telegram_id: int, username: str | None, is_bot: bool = False) -> bool:
    """
    Проверяет существование пользователя и создает его, если он отсутствует.
    Также обновляет username, если он появился или изменился.
    Игнорирует ботов.
    """
    if is_bot:
        logger.info(f"Ignored attempt to register a bot with id {telegram_id}")
        return False

    user = await db.get_user(telegram_id=telegram_id)
    
    if not user:
        await db.create_user(telegram_id, username)
        logger.info(f"New user created: {username or telegram_id}")
        return True
    
    if username and (not user['username'] or user['username'] != username.lower()):
        await db.update_user_username(telegram_id, username)
        logger.info(f"Username for user {telegram_id} updated to {username.lower()}")

    return False

def get_next_run_time(
    event_type: str, 
    event_date: datetime | None, 
    weekday: int | None, 
    event_time: time | None,
    last_run: datetime | None = None
) -> datetime | None:
    """
    Вычисляет следующую дату и время для события на основе его типа и расписания.
    Всегда возвращает aware datetime в Europe/Moscow.

    Правила:
    - single: если дата в будущем — вернуть её, иначе None.
    - recurring:
        * если нужный день в будущем — ближайший такой день;
        * если сегодня:
            - если текущее время < event_time — сегодня;
            - иначе — через 7 дней.
    """
    now = datetime.now(MOSCOW_TZ)

    if event_type == 'single':
        if event_date and event_date > now:
            return event_date
        return None

    if event_type == 'recurring' and weekday is not None and event_time is not None:
        today_weekday = now.weekday()
        days_ahead = weekday - today_weekday

        if days_ahead < 0:
            days_ahead += 7
        elif days_ahead == 0:
            # Сегодня нужный день: если текущее время уже прошло — переносим на следующую неделю
            if now.time() >= event_time:
                days_ahead = 7

        target_date = now.date() + timedelta(days=days_ahead)
        target_dt = datetime.combine(target_date, event_time).replace(tzinfo=MOSCOW_TZ)
        return target_dt

    return None