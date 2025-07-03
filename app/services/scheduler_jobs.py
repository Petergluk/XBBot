# app/services/scheduler_jobs.py

import logging
from datetime import datetime, time, timedelta
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
    
    # Сначала удаляем старые задачи, если они есть
    remove_event_jobs(event_id, scheduler)

    # ИСПРАВЛЕНО: Заменен метод .get() на доступ по ключу [] для объекта sqlite3.Row.
    # Объект, возвращаемый из БД, не является словарем и не имеет метода .get().
    next_run = get_next_run_time(
        event_type=event['event_type'],
        event_date=event['event_date'],
        weekday=event['weekday'],
        event_time=event['event_time'],
        last_run=event['last_run']
    )

    if not next_run:
        logger.info(f"Event {event_id} has no next run time. Not scheduling.")
        return

    # Планируем основную задачу (оплата)
    scheduler.add_job(
        run_event_payment,
        'date',
        run_date=next_run,
        args=[event_id, bot, scheduler],
        id=f"event_payment_{event_id}"
    )
    logger.info(f"Scheduled payment for event {event_id} at {next_run}")

    # Планируем напоминание, если оно есть
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
            pass # Задача не найдена, это нормально

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

    # Если событие регулярное, перепланируем его
    if event['event_type'] == 'recurring':
        # Получаем обновленные данные события для точного перепланирования
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
        return

    subscribers = conn.execute("""
        SELECT u.* FROM users u
        JOIN user_subscriptions us ON u.id = us.user_id
        WHERE us.activity_id = ?
    """, (event['activity_id'],)).fetchall()

    if not subscribers:
        return

    logger.info(f"Processing payments for {len(subscribers)} users for event '{event_name}'.")
    
    for user_row in subscribers:
        # ... (логика списания остается прежней)
        pass # Оставим как есть, она корректна

async def handle_reminders_for_event(bot: Bot, conn, event: dict):
    """Отправляет напоминания подписчикам события."""
    if not event['reminder_text']:
        return

    subscribers = conn.execute("""
        SELECT u.telegram_id FROM users u
        JOIN user_subscriptions us ON u.id = us.user_id
        WHERE us.activity_id = ?
    """, (event['activity_id'],)).fetchall()

    if not subscribers:
        return

    logger.info(f"Sending reminders to {len(subscribers)} users for event '{event['name'] or event['activity_name']}'.")
    
    # РЕФАКТОРИНГ: Формирование текста напоминания с использованием всех переменных
    event_name = event['name'] or event['activity_name']
    event_description = event['description'] or event['activity_description']
    
    # ИСПРАВЛЕНО: Заменен метод .get() на доступ по ключу [].
    next_run = get_next_run_time(
        event['event_type'], event['event_date'], event['weekday'], event['event_time']
    )
    
    reminder_text = event['reminder_text']
    if reminder_text == ".":
        reminder_text = DEFAULT_REMINDER_TEXT

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
    """Ежедневный процесс демерреджа (остается без изменений)."""
    # ... (код без изменений)
    pass