# XBalanseBot/main.py
# v1.8.0 - 2025-08-20 (Render.com deployment ready)
import asyncio
import logging
import os
import sys
import subprocess
from datetime import datetime
from dotenv import load_dotenv

# Загрузка .env в самом начале скрипта для локальной разработки
load_dotenv()

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# Set asyncio policy for Windows compatibility with psycopg3
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from config import BOT_TOKEN, SUPER_ADMIN_ID, DEV_MODE, WEBHOOK_HOST
from app.database import db
from app.handlers import common, user_commands, admin_commands, activity_handlers, event_handlers
from app.services import scheduler_jobs
# ИЗМЕНЕНИЕ: Импортируем функцию для запуска веб-сервера
from app.services.webhook_handler import run_webhook_server

# Настройка логирования
log_dir = "data/logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_filename = os.path.join(log_dir, f"bot_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def logging_middleware(handler, event, data: dict):
    user = data.get('event_from_user')
    if user:
        if isinstance(event, Message): logger.info(f"User {user.id} (@{user.username}) sent message: '{event.text}'")
        elif isinstance(event, CallbackQuery): logger.info(f"User {user.id} (@{user.username}) sent callback: '{event.data}'")
        elif isinstance(event, ChatMemberUpdated): logger.info(f"User {user.id} (@{user.username}) caused chat member update: {event.new_chat_member.status}")
    return await handler(event, data)

async def setup_super_admin():
    logger.info("Checking for super admin setup...")
    if not SUPER_ADMIN_ID: return
    user = await db.get_user(telegram_id=SUPER_ADMIN_ID)
    if not user:
        await db.create_user(telegram_id=SUPER_ADMIN_ID, username=None, is_admin=True)
        logger.info(f"Super admin with ID {SUPER_ADMIN_ID} created.")
    elif not user['is_admin']:
        await db.set_admin_status(telegram_id=SUPER_ADMIN_ID, is_admin=True)
        logger.info(f"Existing user {SUPER_ADMIN_ID} has been promoted to super admin.")

async def setup_scheduler(bot: Bot, scheduler: AsyncIOScheduler):
    scheduler.add_job(scheduler_jobs.process_demurrage, CronTrigger(hour=0, minute=1), args=(bot,))
    all_events = await db.get_all_events()
    for event in all_events:
        await scheduler_jobs.schedule_event_jobs(event, bot, scheduler)
    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs.")

# --- НОВАЯ ВЕРСИЯ ФУНКЦИИ MAIN, ГОТОВАЯ К ДЕПЛОЮ ---
async def main():
    logger.info("Starting bot initialization...")
    
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not found! Bot cannot start.")
        return

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    bot.scheduler = scheduler
    
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.outer_middleware(logging_middleware)
    dp.callback_query.outer_middleware(logging_middleware)
    dp.chat_member.outer_middleware(logging_middleware)
    
    dp.include_router(common.router)
    dp.include_router(admin_commands.router)
    dp.include_router(user_commands.router)
    dp.include_router(activity_handlers.router)
    dp.include_router(event_handlers.router)

    try:
        await db.initialize()
        await setup_super_admin()
        await setup_scheduler(bot, scheduler)
        
        # Удаляем старый вебхук, чтобы избежать конфликтов
        await bot.delete_webhook(drop_pending_updates=True)
        
        if DEV_MODE:
            # --- РЕЖИМ ДЛЯ ЛОКАЛЬНОЙ РАЗРАБОТКИ ---
            logger.info("Bot is running in DEVELOPMENT mode (polling).")
            await dp.start_polling(bot)
        else:
            # --- РЕЖИМ ДЛЯ СЕРВЕРА (RENDER.COM) ---
            if not WEBHOOK_HOST:
                logger.critical("FATAL: WEBHOOK_HOST is not set for production mode!")
                return
                
            logger.info("Bot is running in PRODUCTION mode (webhook).")
            webhook_url = f"https://{WEBHOOK_HOST}/webhook/telegram"
            await bot.set_webhook(webhook_url)
            logger.info(f"Webhook set to: {webhook_url}")
            
            # Запускаем веб-сервер, который будет принимать обновления от Telegram и Tribute
            await run_webhook_server(bot, dp)
            
    finally:
        if scheduler.running:
            scheduler.shutdown()
            logger.info("Scheduler stopped.")
            
        await db.close()
        await bot.session.close()
        logger.info("Bot session and database pool closed.")
        
        # ИЗМЕНЕНИЕ: Остановка Docker-контейнера происходит только в режиме разработки
        if DEV_MODE:
            logger.info("Stopping docker-compose services...")
            try:
                subprocess.run(["docker-compose", "down"], check=True, capture_output=True)
                logger.info("Docker services stopped successfully.")
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logger.error(f"Failed to run 'docker-compose down': {e}")


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot application stopped by user.")
    except Exception as e:
        logger.error(f"Fatal error during bot execution: {e}", exc_info=True)