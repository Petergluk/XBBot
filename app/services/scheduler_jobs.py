# XBalanseBot/app/services/scheduler_jobs.py
# v1.5.5 - 2025-08-17 (restore process_demurrage; explicit MSK in user messages)
import logging
from datetime import datetime, date
from decimal import Decimal
from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError
from psycopg.rows import dict_row
from zoneinfo import ZoneInfo

from app.database import db
from app.utils import format_amount, get_next_run_time
from config import CURRENCY_SYMBOL, DEFAULT_REMINDER_TEXT

logger = logging.getLogger(__name__)
MOSCOW_TZ = ZoneInfo("Europe/Moscow")
MSK_LABEL = "MSK"

async def schedule_event_jobs(event: dict, bot: Bot, scheduler: AsyncIOScheduler):
    """
    Планирует или перепланирует задачи для одного события (оплата и напоминание).
    """
    event_id = event['id']
    remove_event_jobs(event_id, scheduler)

    next_run = get_next_run_time(
        event_type=event['event_type'],
        event_date=event.get('event_date'),
        weekday=event.get('weekday'),
        event_time=event.get('event_time'),
        last_run=event.get('last_run')
    )

    if not next_run or next_run < datetime.now(next_run.tzinfo):
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
        from datetime import timedelta
        reminder_datetime = next_run - timedelta(minutes=event['reminder_time'])
        if reminder_datetime > datetime.now(reminder_datetime.tzinfo):
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
    event = await db.get_event(event_id)
    if not event or not event['is_active']:
        logger.warning(f"Job run_event_payment for event {event_id} skipped: event not found or inactive.")
        return
    
    logger.info(f"Running payment job for event {event_id} ('{event['name'] or event['activity_name']}')")
    
    await handle_payment_for_event(bot, event)

    await db.update_event(event_id, last_run=datetime.now(MOSCOW_TZ))

    if event['event_type'] == 'recurring':
        updated_event = await db.get_event(event_id)
        if updated_event:
            await schedule_event_jobs(updated_event, bot, scheduler)

async def run_event_reminder(event_id: int, bot: Bot):
    """Выполняется по расписанию. Отправляет напоминания."""
    event = await db.get_event(event_id)
    if not event or not event['is_active']:
        logger.warning(f"Job run_event_reminder for event {event_id} skipped: event not found or inactive.")
        return
        
    logger.info(f"Running reminder job for event {event_id} ('{event['name'] or event['activity_name']}')")
    
    await handle_reminders_for_event(bot, event)

async def handle_payment_for_event(bot: Bot, event: dict):
    """Обрабатывает списания для конкретного наступившего события."""
    event_name = event['name'] or event['activity_name']
    fee = event['cost']
    if fee <= 0:
        logger.info(f"Event {event['id']} has zero cost, skipping payments.")
        return
        
    async with db.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            if event['activity_id'] == 1:
                await cur.execute("SELECT * FROM users WHERE telegram_id != 0")
            else:
                await cur.execute("""
                    SELECT u.* FROM users u
                    JOIN user_subscriptions us ON u.id = us.user_id
                    WHERE us.activity_id = %s
                """, (event['activity_id'],))
            subscribers = await cur.fetchall()

    if not subscribers:
        logger.info(f"No subscribers found for event {event['id']}, skipping payments.")
        return

    logger.info(f"Processing payments for {len(subscribers)} users for event '{event_name}'.")
    
    fund_user_id = 0
    
    async with db.pool.connection() as conn:
        async with conn.transaction():
            for user in subscribers:
                user_id = user['id']
                user_telegram_id = user['telegram_id']
                
                await conn.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (fee, user_id))
                await conn.execute(
                    "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (%s, %s, %s, 'event_fee', %s)",
                    (user_id, fund_user_id, fee, f"Оплата за событие: {event_name}")
                )
                await conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = %s", (user_id,))
                
                # Явно указываем пояс MSK в тексте
                start_time_str = ""
                next_run = get_next_run_time(event['event_type'], event.get('event_date'), event.get('weekday'), event.get('event_time'), event.get('last_run'))
                if next_run:
                    start_time_str = f"\n🕒 Начало: {next_run.strftime('%d.%m.%Y в %H:%M')} ({MSK_LABEL})"

                notification_text = (
                    f"▶️ <b>Начинается событие: «{event_name}»</b>\n"
                    f"🔗 Ссылка для подключения: {event['link']}{start_time_str}\n\n"
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
    
    logger.info(f"Successfully processed payments for event {event['id']}.")

async def handle_reminders_for_event(bot: Bot, event: dict):
    """Отправляет напоминания подписчикам события."""
    if not event['reminder_text']:
        return

    async with db.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            if event['activity_id'] == 1:
                await cur.execute("SELECT telegram_id FROM users WHERE telegram_id != 0")
            else:
                await cur.execute("""
                    SELECT u.telegram_id FROM users u
                    JOIN user_subscriptions us ON u.id = us.user_id
                    WHERE us.activity_id = %s
                """, (event['activity_id'],))
            subscribers = await cur.fetchall()

    if not subscribers:
        return

    logger.info(f"Sending reminders to {len(subscribers)} users for event '{event['name'] or event['activity_name']}'.")
    
    event_name = event['name'] or event['activity_name']
    event_description = event['description'] or event['activity_description']
    
    next_run = get_next_run_time(
        event['event_type'], event.get('event_date'), event.get('weekday'), event.get('event_time')
    )
    
    reminder_text = event['reminder_text']
    if reminder_text == ".":
        reminder_text = await db.get_setting('default_reminder_text', DEFAULT_REMINDER_TEXT)

    # Добавляем (MSK) в форматированные поля
    try:
        formatted_text = reminder_text.format(
            event_name=event_name,
            event_description=event_description,
            start_date=next_run.strftime('%d.%m.%Y') + f" ({MSK_LABEL})" if next_run else "N/A",
            start_time=next_run.strftime('%H:%M') if next_run else "N/A",
            cost=format_amount(event['cost']),
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
    Ежедневный процесс демерреджа.
    Планируется в main.setup_scheduler на 00:01 по MSK.
    """
    logger.info("Checking daily demurrage process...")
    
    is_enabled = await db.get_setting('demurrage_enabled', '0') == '1'
    if not is_enabled:
        logger.info("Demurrage is disabled. Skipping.")
        return

    try:
        interval_str = await db.get_setting('demurrage_interval_days', '1')
        interval = int(interval_str)
        last_run_str = await db.get_setting('demurrage_last_run', '1970-01-01')
        last_run_date = datetime.strptime(last_run_str, '%Y-%m-%d').date()
        
        days_since_last_run = (date.today() - last_run_date).days
        
        if days_since_last_run < interval:
            logger.info(f"Demurrage check: {days_since_last_run}/{interval} days passed. Skipping.")
            return
            
        logger.info("Demurrage interval passed. Starting process...")
        
        rate_str = await db.get_setting('demurrage_rate', '0.01')
        rate = Decimal(rate_str)
        if rate <= 0:
            logger.info(f"Demurrage rate is zero or negative ({rate}). Skipping.")
            return
    except (ValueError, TypeError) as e:
        logger.error(f"Could not get or parse demurrage settings: {e}")
        return

    try:
        async with db.pool.connection() as conn:
            async with conn.transaction():
                async with conn.cursor(row_factory=dict_row) as cur:
                    await cur.execute("SELECT id, balance FROM users WHERE balance > 0 AND telegram_id != 0")
                    users_to_tax = await cur.fetchall()

                if not users_to_tax:
                    logger.info("No users with positive balance found. Demurrage process finished.")
                    await db.set_setting('demurrage_last_run', date.today().isoformat())
                    return

                total_demurrage = Decimal('0')
                fund_user_id = 0
                for user in users_to_tax:
                    user_id = user['id']
                    balance = user['balance']
                    demurrage_amount = (balance * rate).quantize(Decimal('0.0001'))
                    if demurrage_amount <= 0: 
                        continue
                    
                    await conn.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (demurrage_amount, user_id))
                    await conn.execute(
                        "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (%s, %s, %s, 'demurrage', %s)",
                        (user_id, fund_user_id, demurrage_amount, f"Демерредж {rate*100}%")
                    )
                    total_demurrage += demurrage_amount
                
                if total_demurrage > 0:
                    await conn.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (total_demurrage, fund_user_id))
                
                await db.set_setting('demurrage_last_run', date.today().isoformat())
                
        logger.info(f"Demurrage successfully processed for {len(users_to_tax)} users. Total amount: {format_amount(total_demurrage)} {CURRENCY_SYMBOL}.")
    except Exception as e:
        logger.error(f"An error occurred during demurrage process. Transaction rolled back. Error: {e}", exc_info=True)
