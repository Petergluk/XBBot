# XBalanseBot/main.py
# 2025-07-24 22:45:00
import asyncio
import logging
import os
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Message, CallbackQuery, ChatMemberUpdated

# УДАЛЕНО: Неверный импорт, который вызывал ошибку ModuleNotFoundError
# from aiogram.client.session.httpx import HttpxSession

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from config import BOT_TOKEN, SUPER_ADMIN_ID
from app.database import db
from app.handlers import common, user_commands, admin_commands, activity_handlers, event_handlers
from app.services import scheduler_jobs

log_dir = "data"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'bot.log'), encoding='utf-8'),
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
    user = db.get_user(telegram_id=SUPER_ADMIN_ID)
    if not user:
        db.create_user(telegram_id=SUPER_ADMIN_ID, username=None, is_admin=True)
        logger.info(f"Super admin with ID {SUPER_ADMIN_ID} created.")
    elif not user['is_admin']:
        db.set_admin_status(telegram_id=SUPER_ADMIN_ID, is_admin=True)
        logger.info(f"Existing user {SUPER_ADMIN_ID} has been promoted to super admin.")

async def setup_scheduler(bot: Bot, scheduler: AsyncIOScheduler):
    scheduler.add_job(scheduler_jobs.process_demurrage, CronTrigger(hour=0, minute=1), args=(bot,))
    all_events = db.get_all_events()
    for event_row in all_events:
        await scheduler_jobs.schedule_event_jobs(dict(event_row), bot, scheduler)
    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs.")

async def main():
    logger.info("Starting bot initialization...")
    
    if not BOT_TOKEN:
        logger.critical("FATAL: BOT_TOKEN is not found! Bot cannot start.")
        return

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    
    # ИЗМЕНЕНО: Убран параметр session. Aiogram автоматически подхватит httpx,
    # так как он установлен, а aiohttp - нет (или несовместим).
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

    logger.warning("Bot is running in DEVELOPMENT mode (polling).")
    await setup_super_admin()
    await setup_scheduler(bot, scheduler)
    await bot.delete_webhook(drop_pending_updates=True)
    
    try:
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