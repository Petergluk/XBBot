# XBalanseBot/app/handlers/admin_commands.py
# v1.5.4 - 2025-08-16
import logging
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from psycopg.rows import dict_row

from app.database import db
from app.states import AdminEditStates
from app.utils import is_admin, format_amount, get_user_balance, format_transactions_history
from config import CURRENCY_SYMBOL, DEFAULT_GIDE_TEXT, DEFAULT_TEST_COMMANDS_TEXT, DEFAULT_REMINDER_TEXT, DEFAULT_WELCOME_MESSAGE_GROUP, DEFAULT_WELCOME_MESSAGE_BOT

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
    """Отображает руководство для администратора."""
    gide_text = DEFAULT_GIDE_TEXT.format(currency_symbol=CURRENCY_SYMBOL)
    await message.answer(gide_text, parse_mode="HTML")

@router.message(Command("test", ignore_case=True))
async def cmd_test(message: Message):
    """Отображает набор тестовых команд."""
    test_text = DEFAULT_TEST_COMMANDS_TEXT
    await message.answer(test_text, parse_mode="HTML")

@router.message(Command("users", ignore_case=True))
async def cmd_users(message: Message):
    """Отображает список пользователей системы."""
    async with db.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT telegram_id, username, balance, transaction_count, is_admin, created_at FROM users WHERE telegram_id != 0 ORDER BY created_at DESC")
            users = await cur.fetchall()

    if not users:
        await message.answer("В системе пока нет пользователей.")
        return
        
    page_size = 20
    total_users = len(users)
    response_parts = [f"👥 <b>Всего пользователей: {total_users}</b>\n\n"]
    for i, user in enumerate(users[:page_size]):
        admin_mark = "👮" if user['is_admin'] else ""
        username_str = f"@{user['username']}" if user['username'] else f"ID:{user['telegram_id']}"
        created_date = user['created_at'].strftime('%d.%m.%Y')
        response_parts.append(f"{i+1}. {admin_mark}{username_str}\n   💰 {format_amount(user['balance'])} {CURRENCY_SYMBOL} | 📊 {user['transaction_count']} тр. | 📅 {created_date}\n")
    
    if total_users > page_size:
        response_parts.append(f"\n<i>Показаны первые {page_size} из {total_users} пользователей</i>")
        
    await message.answer("".join(response_parts), parse_mode="HTML")

@router.message(Command("add", ignore_case=True))
async def cmd_add(message: Message, bot: Bot):
    """Начисляет средства пользователю."""
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
    user = await db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
        
    async with db.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, user['id']))
            await conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (0, %s, %s, 'manual_add', %s)", (user['id'], amount, comment))
            await conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = %s", (user['id'],))
            
    await db.handle_debt_repayment(user['id'])
    
    await message.answer(f"✅ Начислено {format_amount(amount)} {CURRENCY_SYMBOL} пользователю @{username}.")
    if username != 'fund':
        try:
            await bot.send_message(user['telegram_id'], f"💰 Вам было начислено {format_amount(amount)} {CURRENCY_SYMBOL}. Комментарий: {comment}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']} о начислении: {e}")

@router.message(Command("rem", ignore_case=True))
async def cmd_rem(message: Message, bot: Bot):
    """Списывает средства с пользователя."""
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
    user = await db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
        
    async with db.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, user['id']))
            await conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (%s, 0, %s, 'manual_rem', %s)", (user['id'], amount, comment))
            await conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = %s", (user['id'],))
            
    await message.answer(f"✅ Списано {format_amount(amount)} {CURRENCY_SYMBOL} с пользователя @{username}.")
    if username != 'fund':
        try:
            await bot.send_message(user['telegram_id'], f"💰 С вашего счета было списано {format_amount(amount)} {CURRENCY_SYMBOL}. Комментарий: {comment}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']} о списании: {e}")

@router.message(Command("check", ignore_case=True))
async def cmd_check(message: Message):
    """Показывает детальную информацию о пользователе и его транзакциях."""
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /check @username")
        return
        
    username = args[1].lstrip('@').lower()
    user = await db.get_user(username=username)
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

    async with db.pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            date_limit = datetime.now() - timedelta(days=30)
            await cur.execute("""
                SELECT t.*, 
                       sender.username as sender_username,
                       recipient.username as recipient_username
                FROM transactions t
                LEFT JOIN users sender ON t.from_user_id = sender.id
                LEFT JOIN users recipient ON t.to_user_id = recipient.id
                WHERE (t.to_user_id = %s OR t.from_user_id = %s) AND t.created_at > %s
                ORDER BY t.created_at DESC
            """, (user['id'], user['id'], date_limit))
            all_txs = await cur.fetchall()

    if not all_txs:
        response_parts.append("\n<i>История транзакций за последний месяц пуста.</i>")
    else:
        response_parts.append("\n<b>📜 История за последние 30 дней:</b>")
        history_text = format_transactions_history(all_txs, user['id'])
        response_parts.append(history_text)

    await message.answer("".join(response_parts), parse_mode="HTML")


@router.message(Command("pay_from_fund", ignore_case=True))
async def cmd_pay_from_fund(message: Message, bot: Bot):
    """Выплачивает средства из фонда сообщества пользователю."""
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
    recipient = await db.get_user(username=username)
    if not recipient:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
        
    fund = await db.get_user(telegram_id=0)
    if fund['balance'] < amount:
        await message.reply(f"❌ Недостаточно средств в фонде. Доступно: {format_amount(fund['balance'])} {CURRENCY_SYMBOL}")
        return
        
    async with db.pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("UPDATE users SET balance = balance - %s WHERE id = %s", (amount, fund['id']))
            await conn.execute("UPDATE users SET balance = balance + %s WHERE id = %s", (amount, recipient['id']))
            await conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (%s, %s, %s, 'fund_payment', %s)", (fund['id'], recipient['id'], amount, comment))
            await conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = %s", (recipient['id'],))

    await db.handle_debt_repayment(recipient['id'])
    
    logger.info(f"Admin {message.from_user.id} paid {amount} from fund to user {recipient['telegram_id']}")
    await message.answer(f"✅ Выплачено {format_amount(amount)} {CURRENCY_SYMBOL} из фонда пользователю @{username}.")
    
    try:
        await bot.send_message(recipient['telegram_id'], f"💰 Вам поступила выплата из фонда сообщества в размере {format_amount(amount)} {CURRENCY_SYMBOL}.\nКомментарий: {comment}")
    except Exception as e:
        logger.warning(f"Не удалось уведомить пользователя {recipient['telegram_id']} о выплате из фонда: {e}")


# --- НОВЫЙ БЛОК: СИСТЕМНЫЕ НАСТРОЙКИ И УПРАВЛЕНИЕ АДМИНАМИ ---

@router.message(Command("make_admin", ignore_case=True))
async def cmd_make_admin(message: Message):
    """Назначает пользователя администратором."""
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /make_admin @username")
        return
    username = args[1].lstrip('@').lower()
    user = await db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
    if user['is_admin']:
        await message.reply(f"✅ Пользователь @{username} уже является администратором.")
        return
    await db.set_admin_status(user['telegram_id'], is_admin=True)
    await message.answer(f"✅ Пользователь @{username} назначен администратором.")

@router.message(Command("remove_admin", ignore_case=True))
async def cmd_remove_admin(message: Message):
    """Снимает с пользователя права администратора."""
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /remove_admin @username")
        return
    username = args[1].lstrip('@').lower()
    user = await db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return
    if not user['is_admin']:
        await message.reply(f"✅ Пользователь @{username} не является администратором.")
        return
    await db.set_admin_status(user['telegram_id'], is_admin=False)
    await message.answer(f"✅ С пользователя @{username} сняты права администратора.")

@router.message(Command("edit_welcome_bot", ignore_case=True))
async def cmd_edit_welcome_bot(message: Message, state: FSMContext):
    """Начинает диалог редактирования приветствия в боте."""
    current_text = await db.get_setting('welcome_message_bot', DEFAULT_WELCOME_MESSAGE_BOT)
    await state.set_state(AdminEditStates.waiting_for_welcome_text)
    await message.answer(f"Текущий текст приветствия в ЛС:\n\n{current_text}\n\nОтправьте новый текст. Для отмены введите /cancel", parse_mode=None)

@router.message(AdminEditStates.waiting_for_welcome_text)
async def process_new_welcome_text(message: Message, state: FSMContext):
    """Сохраняет новый текст приветствия."""
    await db.set_setting('welcome_message_bot', message.html_text)
    await state.clear()
    await message.answer("✅ Текст приветствия в боте обновлен.")

@router.message(Command("edit_welcome_group", ignore_case=True))
async def cmd_edit_welcome_group(message: Message, state: FSMContext):
    """Начинает диалог редактирования приветствия в группе."""
    current_text = await db.get_setting('welcome_message_group', DEFAULT_WELCOME_MESSAGE_GROUP)
    await state.set_state(AdminEditStates.waiting_for_welcome_text_group)
    await message.answer(f"Текущий текст приветствия в группе:\n\n{current_text}\n\nОтправьте новый текст. Для отмены введите /cancel", parse_mode=None)

@router.message(AdminEditStates.waiting_for_welcome_text_group)
async def process_new_welcome_text_group(message: Message, state: FSMContext):
    """Сохраняет новый текст приветствия для группы."""
    await db.set_setting('welcome_message_group', message.html_text)
    await state.clear()
    await message.answer("✅ Текст приветствия в группе обновлен.")

@router.message(Command("edit_reminder", ignore_case=True))
async def cmd_edit_reminder(message: Message, state: FSMContext):
    """Начинает диалог редактирования шаблона напоминания."""
    current_text = await db.get_setting('default_reminder_text', DEFAULT_REMINDER_TEXT)
    await state.set_state(AdminEditStates.waiting_for_reminder_text)
    await message.answer(f"Текущий шаблон напоминания:\n\n<code>{current_text}</code>\n\nОтправьте новый текст. Для отмены введите /cancel", parse_mode='HTML')

@router.message(AdminEditStates.waiting_for_reminder_text)
async def process_new_reminder_text(message: Message, state: FSMContext):
    """Сохраняет новый шаблон напоминания."""
    await db.set_setting('default_reminder_text', message.html_text)
    await state.clear()
    await message.answer("✅ Шаблон напоминания обновлен.")

@router.message(Command("welcome_bonus", ignore_case=True))
async def cmd_set_welcome_bonus(message: Message):
    """Устанавливает сумму welcome-бонуса."""
    args = message.text.split()
    if len(args) < 2:
        current_bonus = await db.get_setting('welcome_bonus_amount', '0')
        await message.reply(f"Текущий welcome-бонус: {current_bonus} {CURRENCY_SYMBOL}.\nФормат: /welcome_bonus [сумма]")
        return
    try:
        amount = Decimal(args[1])
        if amount < 0: raise ValueError
        await db.set_setting('welcome_bonus_amount', str(amount))
        await message.answer(f"✅ Welcome-бонус установлен в размере {format_amount(amount)} {CURRENCY_SYMBOL}.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Сумма должна быть неотрицательным числом.")

@router.message(Command("demurrage_on", ignore_case=True))
async def cmd_demurrage_on(message: Message):
    await db.set_setting('demurrage_enabled', '1')
    await message.answer("✅ Демерредж включен.")

@router.message(Command("demurrage_off", ignore_case=True))
async def cmd_demurrage_off(message: Message):
    await db.set_setting('demurrage_enabled', '0')
    await message.answer("✅ Демерредж выключен.")

@router.message(Command("demurrage_status", ignore_case=True))
async def cmd_demurrage_status(message: Message):
    is_enabled = await db.get_setting('demurrage_enabled', '0') == '1'
    rate = Decimal(await db.get_setting('demurrage_rate', '0.01')) * 100
    interval = await db.get_setting('demurrage_interval_days', '1')
    last_run = await db.get_setting('demurrage_last_run', '1970-01-01')
    status = "Включен ✅" if is_enabled else "Выключен ❌"
    await message.answer(
        f"<b>Статус демерреджа:</b>\n\n"
        f"Состояние: <b>{status}</b>\n"
        f"Ставка: <b>{rate:.2f}%</b>\n"
        f"Интервал: <b>каждые {interval} дней</b>\n"
        f"Последний запуск: <b>{last_run}</b>"
    )

@router.message(Command("set_demurrage", ignore_case=True))
async def cmd_set_demurrage(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /set_demurrage [процент]")
        return
    try:
        percent = Decimal(args[1])
        if not (0 <= percent <= 100): raise ValueError
        rate = percent / 100
        await db.set_setting('demurrage_rate', str(rate))
        await message.answer(f"✅ Ставка демерреджа установлена на {percent:.2f}%.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Процент должен быть числом от 0 до 100.")

@router.message(Command("set_exchange", ignore_case=True))
async def cmd_set_exchange(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply(f"❌ Формат: /set_exchange [курс]\n(Сколько {CURRENCY_SYMBOL} давать за 1 RUB)")
        return
    try:
        rate = Decimal(args[1])
        if rate < 0: raise ValueError
        await db.set_setting('exchange_rate', str(rate))
        await message.answer(f"✅ Курс обмена установлен: 1 RUB = {format_amount(rate)} {CURRENCY_SYMBOL}.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Курс должен быть неотрицательным числом.")
