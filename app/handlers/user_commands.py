# 2025-07-24 18:30:00
import logging
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.database import db
from app.states import TransferStates
# ИЗМЕНЕНО: Добавлен импорт новой функции format_transactions_history
from app.utils import format_amount, get_user_balance, get_transaction_count, is_user_in_group, ensure_user_exists, format_transactions_history
from config import CURRENCY_SYMBOL

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("balance", "баланс", ignore_case=True))
async def cmd_balance(message: Message):
    """Обработчик команды /balance."""
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.is_bot)
    balance = await get_user_balance(message.from_user.id)
    tx_count = await get_transaction_count(message.from_user.id)
    await message.answer(
        f"💰 Ваш баланс: <b>{format_amount(balance)} {CURRENCY_SYMBOL}</b>\n"
        f"📊 Совершено транзакций: <b>{tx_count}</b>",
        parse_mode="HTML"
    )

@router.message(Command("send", ignore_case=True))
async def cmd_send(message: Message, state: FSMContext, bot: Bot):
    """Обработчик команды /send с диалогом для комментария."""
    logger.info(f"User {message.from_user.id} initiated /send command: {message.text}")
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.is_bot)
    args = message.text.split()
    
    if len(args) < 3:
        await message.reply("❌ Неверный формат. Используйте: `/send @username сумма [комментарий]`", parse_mode="Markdown")
        return

    recipient_username = args[1].lstrip('@').lower()
    
    if recipient_username == (message.from_user.username or '').lower():
        logger.warning(f"User {message.from_user.id} tried to send to themselves")
        await message.reply("❌ Нельзя отправить средства самому себе.")
        return

    try:
        amount = Decimal(args[2])
        if amount <= 0:
            raise ValueError("Сумма должна быть положительной.")
    except (InvalidOperation, ValueError) as e:
        logger.error(f"Invalid amount in /send from user {message.from_user.id}: {args[2]}")
        await message.reply(f"❌ Неверная сумма. Пожалуйста, укажите положительное число.")
        return

    if recipient_username == 'fund':
        recipient = db.get_user(telegram_id=0)
    else:
        recipient = db.get_user(username=recipient_username)

    if not recipient:
        logger.warning(f"Recipient @{recipient_username} not found in database")
        await message.reply(
            f"❌ Пользователь @{recipient_username} не найден в базе.\n"
            f"Он должен сначала запустить бота командой /start в личных сообщениях."
        )
        return
    
    if not await is_user_in_group(bot, recipient['telegram_id']):
        logger.warning(f"Recipient {recipient['telegram_id']} not in main group")
        await message.reply(f"❌ Пользователь @{recipient_username} не является участником основной группы.")
        return

    comment = ' '.join(args[3:]) if len(args) > 3 else None
    
    if not comment:
        await state.set_state(TransferStates.waiting_for_comment)
        await state.update_data(
            recipient_id=recipient['id'],
            recipient_telegram_id=recipient['telegram_id'],
            recipient_username=recipient_username,
            amount=str(amount)
        )
        await message.answer(
            f"💬 Вы переводите <b>{format_amount(amount)} {CURRENCY_SYMBOL}</b> пользователю @{recipient_username}.\n"
            "Напишите комментарий к переводу (например, за что переводятся средства, дата договоренности и т.д.):",
            parse_mode="HTML"
        )
    else:
        await process_transfer(message, recipient['id'], recipient['telegram_id'], recipient_username, amount, comment, bot)

@router.message(TransferStates.waiting_for_comment)
async def process_transfer_comment(message: Message, state: FSMContext, bot: Bot):
    """Обрабатывает полученный в диалоге комментарий и завершает перевод."""
    data = await state.get_data()
    await state.clear()
    
    recipient_id = data['recipient_id']
    recipient_telegram_id = data['recipient_telegram_id']
    recipient_username = data['recipient_username']
    amount = Decimal(data['amount'])
    comment = message.text

    await process_transfer(message, recipient_id, recipient_telegram_id, recipient_username, amount, comment, bot)

async def process_transfer(message: Message, recipient_id: int, recipient_telegram_id: int, recipient_username: str, amount: Decimal, comment: str, bot: Bot):
    """Выполняет атомарную транзакцию перевода средств."""
    sender_id = message.from_user.id
    sender_username = message.from_user.username or f"user{sender_id}"

    sender_balance = await get_user_balance(sender_id)
    if sender_balance < amount:
        logger.warning(f"Insufficient balance for user {sender_id}: {sender_balance} < {amount}")
        await message.answer(f"❌ Недостаточно средств. Ваш баланс: <b>{format_amount(sender_balance)} {CURRENCY_SYMBOL}</b>", parse_mode="HTML")
        return

    with db.get_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")
            
            sender_db_id = cursor.execute("SELECT id FROM users WHERE telegram_id = ?", (sender_id,)).fetchone()['id']
            
            cursor.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (str(amount), sender_db_id))
            cursor.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(amount), recipient_id))
            
            cursor.execute(
                "INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, ?, ?, 'transfer', ?)",
                (sender_db_id, recipient_id, str(amount), comment)
            )
            
            cursor.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id IN (?, ?)", (sender_db_id, recipient_id))
            
            conn.commit()
            logger.info(f"Transfer successful: {sender_id} -> {recipient_telegram_id}, amount: {amount}")

            db.handle_debt_repayment(recipient_id)

        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction failed between users {sender_id} -> {recipient_telegram_id}: {e}", exc_info=True)
            await message.answer("❌ Произошла ошибка при выполнении перевода. Попробуйте позже.")
            return

    await message.answer(
        f"✅ Перевод выполнен!\n\n"
        f"<b>Получатель:</b> @{recipient_username}\n"
        f"<b>Сумма:</b> {format_amount(amount)} {CURRENCY_SYMBOL}\n"
        f"<b>Комментарий:</b> {comment}",
        parse_mode="HTML"
    )
    
    if recipient_telegram_id != 0:
        try:
            await bot.send_message(
                recipient_telegram_id,
                f"💸 Вам поступил перевод!\n\n"
                f"<b>Отправитель:</b> @{sender_username}\n"
                f"<b>Сумма:</b> {format_amount(amount)} {CURRENCY_SYMBOL}\n"
                f"<b>Комментарий:</b> {comment}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Could not send notification to recipient {recipient_telegram_id}: {e}")

@router.message(Command("history", ignore_case=True))
async def cmd_history(message: Message):
    """
    Обработчик команды /history.
    """
    await ensure_user_exists(message.from_user.id, message.from_user.username, message.from_user.is_bot)
    
    args = message.text.split()
    try:
        days = int(args[1]) if len(args) > 1 else 30
    except (ValueError, IndexError):
        days = 30

    user_id = message.from_user.id
    current_balance = await get_user_balance(user_id)
    
    with db.get_connection() as conn:
        user_db_id_row = conn.execute("SELECT id FROM users WHERE telegram_id = ?", (user_id,)).fetchone()
        if not user_db_id_row:
            await message.answer("Не удалось найти ваш профиль в системе.")
            return
        user_db_id = user_db_id_row['id']
        date_limit = datetime.now() - timedelta(days=days)
        
        all_txs = conn.execute("""
            SELECT t.*, 
                   sender.username as sender_username,
                   recipient.username as recipient_username
            FROM transactions t
            LEFT JOIN users sender ON t.from_user_id = sender.id
            LEFT JOIN users recipient ON t.to_user_id = recipient.id
            WHERE (t.to_user_id = ? OR t.from_user_id = ?) AND t.created_at > ?
            ORDER BY t.created_at DESC
        """, (user_db_id, user_db_id, date_limit)).fetchall()

    if not all_txs:
        await message.answer(f"За последние {days} дней транзакций не найдено.")
        return

    # ИЗМЕНЕНО: Логика форматирования вынесена в утилиту.
    response_parts = [f"📊 <b>История транзакций за последние {days} дней:</b>"]
    history_text = format_transactions_history(all_txs, user_db_id)
    response_parts.append(history_text)
    response_parts.append(f"\n💰 <b>Текущий баланс:</b> {format_amount(current_balance)} {CURRENCY_SYMBOL}")
    
    await message.answer("".join(response_parts), parse_mode="HTML")


@router.message(Command("gdp", "ввп", ignore_case=True))
async def cmd_gdp(message: Message):
    """Обработчик команды /gdp."""
    with db.get_connection() as conn:
        now = datetime.now()
        
        def get_turnover_and_count(days=None):
            query = "SELECT COALESCE(SUM(amount), 0) as turnover, COUNT(id) as tx_count FROM transactions WHERE type = 'transfer'"
            params = []
            if days:
                query += " AND created_at > ?"
                params.append(now - timedelta(days=days))
            return conn.execute(query, params).fetchone()
        
        turnover_7d_data = get_turnover_and_count(7)
        turnover_30d_data = get_turnover_and_count(30)
        turnover_all_data = get_turnover_and_count()
        
        total_supply = Decimal(str(conn.execute("SELECT COALESCE(SUM(balance), 0) as total FROM users").fetchone()['total']))
        fund_balance = Decimal(str(conn.execute("SELECT balance FROM users WHERE id = 0").fetchone()['balance']))
        
        response = f"""
📊 <b>Экономика сообщества:</b>

💱 <b>Оборот (переводы между пользователями):</b>
• За 7 дней: {format_amount(Decimal(str(turnover_7d_data['turnover'])))} {CURRENCY_SYMBOL} ({turnover_7d_data['tx_count']} транзакций)
• За 30 дней: {format_amount(Decimal(str(turnover_30d_data['turnover'])))} {CURRENCY_SYMBOL} ({turnover_30d_data['tx_count']} транзакций)
• За все время: {format_amount(Decimal(str(turnover_all_data['turnover'])))} {CURRENCY_SYMBOL} ({turnover_all_data['tx_count']} транзакций)

💰 <b>Денежная масса:</b>
• Всего в системе: {format_amount(total_supply)} {CURRENCY_SYMBOL}
• В фонде сообщества: {format_amount(fund_balance)} {CURRENCY_SYMBOL}
"""
        await message.answer(response, parse_mode="HTML")
