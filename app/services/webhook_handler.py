# app/services/webhook_handler.py

import logging
import asyncio
from decimal import Decimal
from aiohttp import web
from aiogram import Bot

from app.database import db
from app.utils import format_amount, ensure_user_exists, is_user_in_group
# ИСПРАВЛЕНО: Импортируем секрет напрямую из конфига
from config import WEBHOOK_HOST, WEBHOOK_PORT, TRIBUTE_WEBHOOK_SECRET

logger = logging.getLogger(__name__)

async def handle_tribute_webhook(request: web.Request):
    """Обработка вебхука от Tribute для пополнения баланса."""
    bot = request.app['bot']
    
    # ИСПРАВЛЕНО: Проверка секрета из config.py, а не из настроек БД
    if request.headers.get('X-Tribute-Secret') != TRIBUTE_WEBHOOK_SECRET:
        logger.warning("Received webhook with invalid secret.")
        return web.Response(status=403) # Forbidden

    try:
        data = await request.json()
        logger.info(f"Received Tribute webhook: {data}")

        # Извлекаем нужные данные
        payer_info = data.get('payer', {})
        telegram_id = payer_info.get('telegram_id')
        username = payer_info.get('username') # ИСПРАВЛЕНО: Получаем username
        amount_rub = Decimal(str(data.get('amount')))
        
        if not telegram_id or not amount_rub:
            logger.error(f"Invalid data in webhook: {data}")
            return web.Response(status=400) # Bad Request

        # Проверяем, есть ли пользователь в группе
        if not await is_user_in_group(bot, telegram_id):
            logger.warning(f"User {telegram_id} from webhook is not in the main group.")
            # Можно отправить уведомление админу или просто игнорировать
            return web.Response(status=200, text="OK (user not in group)")

        # ИСПРАВЛЕНО: Передаем username для сохранения в БД
        await ensure_user_exists(telegram_id, username)
        
        # Получаем курс обмена
        exchange_rate = Decimal(db.get_setting('exchange_rate', '1.0'))
        top_up_amount = (amount_rub * exchange_rate).quantize(Decimal('0.0001'))

        # Начисляем средства
        with db.get_connection() as conn:
            user = conn.execute("SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            if not user:
                logger.error(f"User {telegram_id} not found in DB after ensure_user_exists call.")
                return web.Response(status=500)

            user_id = user['id']
            conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(top_up_amount), user_id))
            conn.execute(
                "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (0, ?, ?, 'top_up', ?)",
                (user_id, str(top_up_amount), f"Пополнение через Tribute на {amount_rub} RUB")
            )
            conn.commit()
            # ИСПРАВЛЕНО: Вызов правильного метода для сброса кредита
            db.handle_debt_repayment(user_id)

        # Отправляем уведомление пользователю
        try:
            await bot.send_message(
                telegram_id,
                f"✅ Ваш баланс пополнен на <b>{format_amount(top_up_amount)} Ӫ</b> "
                f"после оплаты {amount_rub} RUB через Tribute."
            )
        except Exception as e:
            logger.error(f"Failed to notify user {telegram_id} about top-up: {e}")

        return web.Response(status=200, text="OK")

    except Exception as e:
        logger.exception(f"Error processing Tribute webhook: {e}")
        return web.Response(status=500) # Internal Server Error


async def run_webhook_server(bot: Bot):
    """Запускает веб-сервер для приема вебхуков."""
    app = web.Application()
    app['bot'] = bot
    app.router.add_post('/webhook/tribute', handle_tribute_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
    
    logger.info(f"Starting aiohttp server for webhooks on {WEBHOOK_HOST}:{WEBHOOK_PORT}...")
    await site.start()
    
    # Бесконечный цикл, чтобы сервер не останавливался
    while True:
        await asyncio.sleep(3600)