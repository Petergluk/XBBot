# XBalanseBot/app/services/scheduler_jobs.py

import logging
from datetime import datetime, time, timedelta, date
from decimal import Decimal
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError

from app.database import db
from app.utils import format_amount, get_next_run_time
from config import CURRENCY_SYMBOL, DEFAULT_REMINDER_TEXT

logger = logging.getLogger(__name__)

async def schedule_event_jobs(event: dict, bot: Bot, scheduler: AsyncIOScheduler):
    """
    Планирует или перепланирует задачи для одного события (оплата и напоминание).
    """
    event_id = event['id']
    
    remove_event_jobs(event_id, scheduler)

    next_run = get_next_run_time(
        event_type=event['event_type'],
        event_date=event['event_date'],
        weekday=event['weekday'],
        event_time=event['event_time'],
        last_run=event['last_run']
    )

    if not next_run or next_run < datetime.now():
        logger.info(f"Event {event_id} has no valid next run time in the future. Not scheduling.")
        return

    scheduler.add_job(
        run_event_payment,
        'date',
        run_date=next_run,
        args=[event_id, bot, scheduler],
        id=f"event_payment_{event_id}"
    )
    logger.info(f"Scheduled payment for event {event_id} at {next_run}")

    if event['reminder_time'] and event['reminder_time'] > 0:
        reminder_datetime = next_run - timedelta(minutes=event['reminder_time'])
        if reminder_datetime > datetime.now():
            scheduler.add_job(
                run_event_reminder,
                'date',
                run_date=reminder_datetime,
                args=[event_id, bot],
                id=f"event_reminder_{event_id}"
            )
            logger.info(f"Scheduled reminder for event {event_id} at {reminder_datetime}")

def remove_event_jobs(event_id: int, scheduler: AsyncIOScheduler):
    """Удаляет задачи для события из планировщика."""
    for job_id in [f"event_payment_{event_id}", f"event_reminder_{event_id}"]:
        try:
            scheduler.remove_job(job_id)
            logger.info(f"Removed job {job_id} from scheduler.")
        except JobLookupError:
            pass

async def run_event_payment(event_id: int, bot: Bot, scheduler: AsyncIOScheduler):
    """
    Выполняется по расписанию. Обрабатывает списания и перепланирует событие.
    """
    event = db.get_event(event_id)
    if not event or not event['is_active']:
        logger.warning(f"Job run_event_payment for event {event_id} skipped: event not found or inactive.")
        return
    
    logger.info(f"Running payment job for event {event_id} ('{event['name'] or event['activity_name']}')")
    
    with db.get_connection() as conn:
        await handle_payment_for_event(bot, conn, event)

    db.update_event(event_id, last_run=datetime.now())

    if event['event_type'] == 'recurring':
        updated_event = db.get_event(event_id)
        await schedule_event_jobs(updated_event, bot, scheduler)

async def run_event_reminder(event_id: int, bot: Bot):
    """Выполняется по расписанию. Отправляет напоминания."""
    event = db.get_event(event_id)
    if not event or not event['is_active']:
        logger.warning(f"Job run_event_reminder for event {event_id} skipped: event not found or inactive.")
        return
        
    logger.info(f"Running reminder job for event {event_id} ('{event['name'] or event['activity_name']}')")
    
    with db.get_connection() as conn:
        await handle_reminders_for_event(bot, conn, event)

async def handle_payment_for_event(bot: Bot, conn, event: dict):
    """Обрабатывает списания для конкретного наступившего события."""
    event_name = event['name'] or event['activity_name']
    fee = Decimal(str(event['cost']))
    if fee <= 0:
        logger.info(f"Event {event['id']} has zero cost, skipping payments.")
        return

    if event['activity_id'] == 1:
        subscribers = conn.execute("SELECT * FROM users WHERE telegram_id != 0").fetchall()
    else:
        subscribers = conn.execute("""
            SELECT u.* FROM users u
            JOIN user_subscriptions us ON u.id = us.user_id
            WHERE us.activity_id = ?
        """, (event['activity_id'],)).fetchall()

    if not subscribers:
        logger.info(f"No subscribers found for event {event['id']}, skipping payments.")
        return

    logger.info(f"Processing payments for {len(subscribers)} users for event '{event_name}'.")
    
    fund_user_id = 0
    
    for user in subscribers:
        user_id = user['id']
        user_telegram_id = user['telegram_id']
        
        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (str(fee), user_id))
        conn.execute(
            "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, ?, ?, 'event_fee', ?)",
            (user_id, fund_user_id, str(fee), f"Оплата за событие: {event_name}")
        )
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (user_id,))
        
        notification_text = (
            f"▶️ <b>Начинается событие: «{event_name}»</b>\n\n"
            f"🔗 Ссылка для подключения: {event['link']}\n\n"
            f"С вашего счета списано {format_amount(fee)} {CURRENCY_SYMBOL} за участие."
        )
        try:
            await bot.send_message(
                user_telegram_id,
                notification_text,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Failed to send payment notification to user {user_telegram_id}: {e}")
    
    conn.commit()
    logger.info(f"Successfully processed payments for event {event['id']}.")

async def handle_reminders_for_event(bot: Bot, conn, event: dict):
    """Отправляет напоминания подписчикам события."""
    if not event['reminder_text']:
        return

    if event['activity_id'] == 1:
        subscribers = conn.execute("SELECT telegram_id FROM users WHERE telegram_id != 0").fetchall()
    else:
        subscribers = conn.execute("""
            SELECT u.telegram_id FROM users u
            JOIN user_subscriptions us ON u.id = us.user_id
            WHERE us.activity_id = ?
        """, (event['activity_id'],)).fetchall()

    if not subscribers:
        return

    logger.info(f"Sending reminders to {len(subscribers)} users for event '{event['name'] or event['activity_name']}'.")
    
    event_name = event['name'] or event['activity_name']
    event_description = event['description'] or event['activity_description']
    
    next_run = get_next_run_time(
        event['event_type'], event['event_date'], event['weekday'], event['event_time']
    )
    
    reminder_text = event['reminder_text']
    if reminder_text == ".":
        reminder_text = db.get_setting('default_reminder_text', DEFAULT_REMINDER_TEXT)

    try:
        formatted_text = reminder_text.format(
            event_name=event_name,
            event_description=event_description,
            start_date=next_run.strftime('%d.%m.%Y') if next_run else "N/A",
            start_time=next_run.strftime('%H:%M') if next_run else "N/A",
            cost=format_amount(Decimal(str(event['cost']))),
            currency_symbol=CURRENCY_SYMBOL,
            reminder_minutes=event['reminder_time'],
            link=event['link'] or ''
        )
    except KeyError as e:
        logger.error(f"Invalid placeholder in reminder text for event {event['id']}: {e}")
        formatted_text = f"Скоро начнется событие {event_name}"

    for user_row in subscribers:
        try:
            await bot.send_message(user_row['telegram_id'], formatted_text, parse_mode="HTML")
        except Exception as e:
            logger.warning(f"Failed to send reminder to {user_row['telegram_id']} for event {event['id']}: {e}")

async def process_demurrage(bot: Bot):
    """
    Процесс демерреджа с учетом настраиваемого интервала.
    """
    logger.info("Checking daily demurrage process...")
    
    is_enabled = db.get_setting('demurrage_enabled', '0') == '1'
    if not is_enabled:
        logger.info("Demurrage is disabled. Skipping.")
        return

    try:
        interval = int(db.get_setting('demurrage_interval_days', '1'))
        last_run_str = db.get_setting('demurrage_last_run', '1970-01-01')
        last_run_date = datetime.strptime(last_run_str, '%Y-%m-%d').date()
        
        days_since_last_run = (date.today() - last_run_date).days
        
        if days_since_last_run < interval:
            logger.info(f"Demurrage check: {days_since_last_run}/{interval} days passed. Skipping.")
            return
            
        logger.info("Demurrage interval passed. Starting process...")
        
        rate_str = db.get_setting('demurrage_rate', '0.01')
        rate = Decimal(rate_str)
        if rate <= 0:
            logger.info(f"Demurrage rate is zero or negative ({rate}). Skipping.")
            return
    except Exception as e:
        logger.error(f"Could not get or parse demurrage settings: {e}")
        return

    with db.get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            users_to_tax = cursor.execute("SELECT id, balance FROM users WHERE balance > 0 AND telegram_id != 0").fetchall()
            if not users_to_tax:
                logger.info("No users with positive balance found. Demurrage process finished.")
                # ИСПРАВЛЕНО: Обновляем дату последнего запуска даже если нет пользователей, чтобы избежать повторных проверок в тот же день.
                cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('demurrage_last_run', date.today().isoformat()))
                conn.commit()
                return

            total_demurrage = Decimal('0')
            fund_user_id = 0
            for user in users_to_tax:
                user_id = user['id']
                balance = Decimal(str(user['balance']))
                demurrage_amount = (balance * rate).quantize(Decimal('0.0001'))
                if demurrage_amount <= 0: continue
                new_balance = balance - demurrage_amount
                cursor.execute("UPDATE users SET balance = ? WHERE id = ?", (str(new_balance), user_id))
                cursor.execute(
                    "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, ?, ?, 'demurrage', ?)",
                    (user_id, fund_user_id, str(demurrage_amount), f"Демерредж {rate*100}%")
                )
                total_demurrage += demurrage_amount
            if total_demurrage > 0:
                cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(total_demurrage), fund_user_id))
            
            # ИСПРАВЛЕНО: Обновление даты последнего запуска перенесено внутрь существующей транзакции,
            # чтобы избежать ошибки 'database is locked'.
            cursor.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", ('demurrage_last_run', date.today().isoformat()))
            
            conn.commit()
            logger.info(f"Demurrage successfully processed for {len(users_to_tax)} users. Total amount: {format_amount(total_demurrage)} {CURRENCY_SYMBOL}.")
        except Exception as e:
            conn.rollback()
            logger.error(f"An error occurred during demurrage process. Transaction rolled back. Error: {e}", exc_info=True)