# main.py

import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, CallbackQuery, ChatMemberUpdated
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import BOT_TOKEN, SUPER_ADMIN_ID
from app.database import db
from app.handlers import common, user_commands, admin_commands, activity_handlers, event_handlers
# РЕФАКТОРИНГ: Импортируем новые функции планировщика
from app.services import scheduler_jobs

# Улучшенная настройка логирования с записью в файл
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def logging_middleware(handler, event, data: dict):
    """Middleware для логирования всех входящих обновлений от пользователей."""
    user = data.get('event_from_user')
    
    if user:
        if isinstance(event, Message):
            logger.info(f"User {user.id} (@{user.username}) sent message: '{event.text}'")
        elif isinstance(event, CallbackQuery):
            logger.info(f"User {user.id} (@{user.username}) sent callback: '{event.data}'")
        elif isinstance(event, ChatMemberUpdated):
             logger.info(f"User {user.id} (@{user.username}) caused chat member update: {event.new_chat_member.status}")
    
    return await handler(event, data)


async def setup_super_admin():
    """Проверяет и назначает суперадмина из config.py при запуске бота."""
    logger.info("Checking for super admin setup...")
    if not SUPER_ADMIN_ID:
        logger.warning("SUPER_ADMIN_ID is not set in config.py. Skipping super admin setup.")
        return

    user = db.get_user(telegram_id=SUPER_ADMIN_ID)
    if not user:
        db.create_user(telegram_id=SUPER_ADMIN_ID, username=None, is_admin=True)
        logger.info(f"Super admin with ID {SUPER_ADMIN_ID} created.")
    elif not user['is_admin']:
        db.set_admin_status(telegram_id=SUPER_ADMIN_ID, is_admin=True)
        logger.info(f"Existing user {SUPER_ADMIN_ID} has been promoted to super admin.")
    else:
        logger.info(f"Super admin {SUPER_ADMIN_ID} is already configured.")

async def setup_scheduler(bot: Bot, scheduler: AsyncIOScheduler):
    """Инициализирует и 'заряжает' планировщик задачами при старте бота."""
    # 1. Добавляем постоянную задачу для демерреджа
    scheduler.add_job(scheduler_jobs.process_demurrage, CronTrigger(hour=0, minute=1), args=(bot,))
    
    # 2. Проходим по всем активным событиям в БД и планируем их
    all_events = db.get_all_events()
    for event_row in all_events:
        event = dict(event_row)
        await scheduler_jobs.schedule_event_jobs(event, bot, scheduler)
    
    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs.")


async def main():
    """Основная асинхронная функция для запуска всех компонентов бота."""
    logger.info("Starting bot initialization...")
    
    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    bot = Bot(
        token=BOT_TOKEN, 
        default=DefaultBotProperties(parse_mode="HTML")
    )
    
    # ИСПРАВЛЕНО: Планировщик добавляется как атрибут к уже созданному объекту Bot.
    # Конструктор Bot() не принимает произвольные аргументы и молча их игнорирует.
    # Это является первопричиной ошибки AttributeError.
    bot.scheduler = scheduler

    dp = Dispatcher(storage=MemoryStorage())

    # Регистрация middleware для логирования
    dp.message.outer_middleware(logging_middleware)
    dp.callback_query.outer_middleware(logging_middleware)
    dp.chat_member.outer_middleware(logging_middleware)
    
    dp.include_router(common.router)
    dp.include_router(admin_commands.router)
    dp.include_router(user_commands.router)
    dp.include_router(activity_handlers.router)
    dp.include_router(event_handlers.router)
    
    await setup_super_admin()
    
    # РЕФАКТОРИНГ: Запускаем новую функцию настройки планировщика
    await setup_scheduler(bot, scheduler)
    
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
        logger.info("Bot is starting...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        scheduler.shutdown()
        logger.info("Bot and scheduler stopped.")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot application stopped by user.")
    except Exception as e:
        logger.error(f"Fatal error during bot execution: {e}", exc_info=True)