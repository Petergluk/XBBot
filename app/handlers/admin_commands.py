# 2025-07-24 18:30:00
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from app.database import db
from app.states import AdminEditStates, FundPaymentStates
# ИЗМЕНЕНО: Добавлен импорт новой функции format_transactions_history
from app.utils import is_admin, format_amount, get_user_balance, format_transactions_history
from config import CURRENCY_SYMBOL, DEFAULT_WELCOME_MESSAGE_BOT, DEFAULT_WELCOME_MESSAGE_GROUP, DEFAULT_GIDE_TEXT, DEFAULT_TEST_COMMANDS_TEXT, DEFAULT_REMINDER_TEXT

router = Router()
logger = logging.getLogger(__name__)

@router.message.middleware()
@router.callback_query.middleware()
async def admin_middleware(handler, event, data):
    """
    Middleware для проверки прав администратора на все команды в этом роутере.
    """
    user_id = data['event_from_user'].id
    if not await is_admin(user_id):
        if isinstance(event, Message):
            await event.answer("❌ У вас нет прав для выполнения этой команды.")
        elif isinstance(event, CallbackQuery):
            await event.answer("❌ У вас нет прав для выполнения этой команды.", show_alert=True)
        return
    return await handler(event, data)

@router.message(Command("gide", "гид", ignore_case=True))
async def cmd_gide(message: Message):
    """
    Отображает руководство для администратора.
    """
    gide_text = DEFAULT_GIDE_TEXT.format(currency_symbol=CURRENCY_SYMBOL)
    await message.answer(gide_text, parse_mode="HTML")

@router.message(Command("test", ignore_case=True))
async def cmd_test(message: Message):
    """
    Отображает набор тестовых команд.
    """
    test_text = DEFAULT_TEST_COMMANDS_TEXT
    await message.answer(test_text, parse_mode="HTML")

@router.message(Command("users", ignore_case=True))
async def cmd_users(message: Message):
    """
    Отображает список пользователей системы.
    """
    with db.get_connection() as conn:
        users = conn.execute("SELECT telegram_id, username, balance, transaction_count, is_admin, created_at FROM users WHERE telegram_id != 0 ORDER BY created_at DESC").fetchall()
    if not users:
        await message.answer("В системе пока нет пользователей.")
        return
    page_size = 20
    total_users = len(users)
    response_parts = [f"👥 <b>Всего пользователей: {total_users}</b>\n\n"]
    for i, user in enumerate(users[:page_size]):
        balance = Decimal(str(user['balance']))
        admin_mark = "👮" if user['is_admin'] else ""
        username_str = f"@{user['username']}" if user['username'] else f"ID:{user['telegram_id']}"
        created_date = user['created_at'].strftime('%d.%m.%Y')
        response_parts.append(f"{i+1}. {admin_mark}{username_str}\n   💰 {format_amount(balance)} {CURRENCY_SYMBOL} | 📊 {user['transaction_count']} тр. | 📅 {created_date}\n")
    if total_users > page_size:
        response_parts.append(f"\n<i>Показаны первые {page_size} из {total_users} пользователей</i>")
    await message.answer("".join(response_parts), parse_mode="HTML")

@router.message(Command("add", ignore_case=True))
async def cmd_add(message: Message, bot: Bot):
    """
    Начисляет средства пользователю.
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❌ Формат: /add @username сумма [комментарий]")
        return
    username = args[1].lstrip('@').lower()
    try:
        amount = Decimal(args[2])
        if amount <= 0: raise ValueError("Сумма должна быть положительной.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Сумма должна быть положительным числом.")
        return
    comment = ' '.join(args[3:]) if len(args) > 3 else "Ручное начисление"
    user = db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(amount), user['id']))
        conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (0, ?, ?, 'manual_add', ?)", (user['id'], str(amount), comment))
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (user['id'],))
        conn.commit()
        db.handle_debt_repayment(user['id'])
    await message.answer(f"✅ Начислено {format_amount(amount)} {CURRENCY_SYMBOL} пользователю @{username}.")
    if username != 'fund':
        try:
            await bot.send_message(user['telegram_id'], f"💰 Вам было начислено {format_amount(amount)} {CURRENCY_SYMBOL}. Комментарий: {comment}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']} о начислении: {e}")

@router.message(Command("rem", ignore_case=True))
async def cmd_rem(message: Message, bot: Bot):
    """
    Списывает средства с пользователя.
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❌ Формат: /rem @username сумма [комментарий]")
        return
    username = args[1].lstrip('@').lower()
    try:
        amount = Decimal(args[2])
        if amount <= 0: raise ValueError("Сумма должна быть положительной.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Сумма должна быть положительным числом.")
        return
    comment = ' '.join(args[3:]) if len(args) > 3 else "Ручное списание"
    user = db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (str(amount), user['id']))
        conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, 0, ?, 'manual_rem', ?)", (user['id'], str(amount), comment))
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (user['id'],))
        conn.commit()
    await message.answer(f"✅ Списано {format_amount(amount)} {CURRENCY_SYMBOL} с пользователя @{username}.")
    if username != 'fund':
        try:
            await bot.send_message(user['telegram_id'], f"💰 С вашего счета было списано {format_amount(amount)} {CURRENCY_SYMBOL}. Комментарий: {comment}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']} о списании: {e}")

@router.message(Command("check", ignore_case=True))
async def cmd_check(message: Message):
    """
    Обработчик команды /check.
    Показывает детальную информацию о пользователе и его транзакциях.
    """
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /check @username")
        return
    username = args[1].lstrip('@').lower()
    user = db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return

    balance = await get_user_balance(user['telegram_id'])
    response_parts = [
        f"👤 <b>Информация о @{user['username']}</b>\n",
        f"💰 Баланс: {format_amount(balance)} {CURRENCY_SYMBOL}\n",
        f"📊 Транзакций: {user['transaction_count']}\n",
        f"💳 Кредит использован: {'Да' if user['grace_credit_used'] else 'Нет'}\n",
        f"👮 Администратор: {'Да' if user['is_admin'] else 'Нет'}\n",
        f"📅 Дата регистрации: {user['created_at'].strftime('%d.%m.%Y')}\n"
    ]

    with db.get_connection() as conn:
        user_db_id = user['id']
        date_limit = datetime.now() - timedelta(days=30)
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
        response_parts.append("\n<i>История транзакций за последний месяц пуста.</i>")
    else:
        response_parts.append("\n<b>📜 История за последние 30 дней:</b>")
        # ИЗМЕНЕНО: Логика форматирования вынесена в утилиту.
        history_text = format_transactions_history(all_txs, user['id'])
        response_parts.append(history_text)

    await message.answer("".join(response_parts), parse_mode="HTML")


@router.message(Command("pay_from_fund", ignore_case=True))
async def cmd_pay_from_fund(message: Message, bot: Bot):
    """
    Выплачивает средства из фонда сообщества пользователю.
    """
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❌ Формат: /pay_from_fund @username сумма [комментарий]")
        return
    username = args[1].lstrip('@').lower()
    try:
        amount = Decimal(args[2])
        if amount <= 0: raise ValueError("Сумма должна быть положительной.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Сумма должна быть положительным числом.")
        return
    comment = ' '.join(args[3:]) if len(args) > 3 else "Выплата из фонда сообщества"
    recipient = db.get_user(username=username)
    if not recipient:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
    fund = db.get_user(telegram_id=0)
    fund_balance = Decimal(str(fund['balance']))
    if fund_balance < amount:
        await message.reply(f"❌ Недостаточно средств в фонде. Доступно: {format_amount(fund_balance)} {CURRENCY_SYMBOL}")
        return
    with db.get_connection() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (str(amount), fund['id']))
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(amount), recipient['id']))
        conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, ?, ?, 'fund_payment', ?)", (fund['id'], recipient['id'], str(amount), comment))
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (recipient['id'],))
        conn.commit()
        db.handle_debt_repayment(recipient['id'])
    logger.info(f"Admin {message.from_user.id} paid {amount} from fund to user {recipient['telegram_id']}")
    await message.answer(f"✅ Выплачено {format_amount(amount)} {CURRENCY_SYMBOL} из фонда пользователю @{username}.")
    try:
        await bot.send_message(recipient['telegram_id'], f"💰 Вам поступила выплата из фонда сообщества в размере {format_amount(amount)} {CURRENCY_SYMBOL}.\nКомментарий: {comment}")
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {recipient['telegram_id']} о выплате из фонда: {e}")

# ... (остальные функции без изменений)
