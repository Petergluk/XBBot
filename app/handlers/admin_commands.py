# XBalanseBot/app/handlers/admin_commands.py
import logging
from decimal import Decimal, InvalidOperation
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from app.database import db
from app.states import AdminEditStates
from app.utils import is_admin, format_amount, get_user_balance
from config import CURRENCY_SYMBOL, DEFAULT_WELCOME_MESSAGE_BOT, DEFAULT_WELCOME_MESSAGE_GROUP, DEFAULT_GIDE_TEXT, DEFAULT_TEST_COMMANDS_TEXT

router = Router()
logger = logging.getLogger(__name__)

# РЕФАКТОРИНГ: Middleware полностью переписан для упрощения и надежности.
# Теперь он не содержит списков исключений и просто проверяет права администратора.
@router.message.middleware()
@router.callback_query.middleware()
async def admin_middleware(handler, event, data):
    """
    Middleware, которое проверяет, что пользователь, вызвавший команду,
    является администратором. Этот middleware применяется ко всем обработчикам
    в данном роутере.
    """
    user_id = data['event_from_user'].id
    
    # Просто проверяем права администратора
    if not await is_admin(user_id):
        if isinstance(event, Message):
            await event.answer("❌ У вас нет прав для выполнения этой команды.")
        elif isinstance(event, CallbackQuery):
            await event.answer("❌ У вас нет прав для выполнения этой команды.", show_alert=True)
        return  # Прерываем обработку
        
    # Если проверка пройдена, передаем управление дальше
    return await handler(event, data)

@router.message(Command("gide", "гид", ignore_case=True))
async def cmd_gide(message: Message):
    """Выводит подробное руководство для администратора."""
    gide_text = DEFAULT_GIDE_TEXT.format(currency_symbol=CURRENCY_SYMBOL)
    await message.answer(gide_text, parse_mode="HTML")

# ДОБАВЛЕНО: Новая команда /test для вывода шпаргалки
@router.message(Command("test", ignore_case=True))
async def cmd_test(message: Message):
    """Выводит шпаргалку с командами для тестирования."""
    test_text = DEFAULT_TEST_COMMANDS_TEXT
    await message.answer(test_text, parse_mode="HTML")


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
    
    user = db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return

    with db.get_connection() as conn:
        conn.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (str(amount), user['id']))
        # ИЗМЕНЕНО: ID отправителя теперь 0 (фонд/система)
        conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (0, ?, ?, 'manual_add', ?)",
                     (user['id'], str(amount), comment))
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (user['id'],))
        conn.commit()
        db.handle_debt_repayment(user['id'])

    await message.answer(f"✅ Начислено {format_amount(amount)} {CURRENCY_SYMBOL} пользователю @{username}.")
    
    # ИСПРАВЛЕНО: Не отправляем уведомление фонду
    if username != 'community_fund':
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

    user = db.get_user(username=username)
    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return

    with db.get_connection() as conn:
        conn.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (str(amount), user['id']))
        # ИЗМЕНЕНО: ID получателя теперь 0 (фонд/система)
        conn.execute("INSERT INTO transactions (from_user_id, to_user_id, amount, type, comment) VALUES (?, 0, ?, 'manual_rem', ?)",
                     (user['id'], str(amount), comment))
        conn.execute("UPDATE users SET transaction_count = transaction_count + 1 WHERE id = ?", (user['id'],))
        conn.commit()

    await message.answer(f"✅ Списано {format_amount(amount)} {CURRENCY_SYMBOL} с пользователя @{username}.")
    
    if username != 'community_fund':
        try:
            await bot.send_message(user['telegram_id'], f"💰 С вашего счета было списано {format_amount(amount)} {CURRENCY_SYMBOL}. Комментарий: {comment}")
        except Exception as e:
            logger.warning(f"Не удалось уведомить пользователя {user['telegram_id']} о списании: {e}")

@router.message(Command("check", ignore_case=True))
async def cmd_check(message: Message):
    """Проверяет баланс и информацию о пользователе."""
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
    response_text = (
        f"👤 <b>Информация о @{user['username']}</b>\n"
        f"💰 Баланс: {format_amount(balance)} {CURRENCY_SYMBOL}\n"
        f"📊 Транзакций: {user['transaction_count']}\n"
        f"💳 Кредит использован: {'Да' if user['grace_credit_used'] else 'Нет'}\n"
        f"👮 Администратор: {'Да' if user['is_admin'] else 'Нет'}"
    )
    await message.answer(response_text, parse_mode="HTML")

# --- Управление приветствиями ---

@router.message(Command("edit_welcome_bot", ignore_case=True))
async def cmd_edit_welcome_bot(message: Message, state: FSMContext):
    """Начинает диалог редактирования приветствия в боте."""
    current_text = db.get_setting('welcome_message_bot', DEFAULT_WELCOME_MESSAGE_BOT)
    await state.set_state(AdminEditStates.waiting_for_welcome_text)
    await message.answer("Текущий текст приветствия в боте (использует HTML-разметку):")
    await message.answer(current_text)
    await message.answer("Пришлите новый текст приветствия. Используйте {username} и {bot_username} для подстановок.\n\nДля отмены введите /cancel")

@router.message(AdminEditStates.waiting_for_welcome_text)
async def process_welcome_text(message: Message, state: FSMContext):
    """Сохраняет новый текст приветствия для бота."""
    db.set_setting('welcome_message_bot', message.text)
    await state.clear()
    await message.answer("✅ Текст приветствия в боте обновлен.")

@router.message(Command("edit_welcome_group", ignore_case=True))
async def cmd_edit_welcome_group(message: Message, state: FSMContext):
    """Начинает диалог редактирования приветствия в группе."""
    current_text = db.get_setting('welcome_message_group', DEFAULT_WELCOME_MESSAGE_GROUP)
    await state.set_state(AdminEditStates.waiting_for_welcome_text_group)
    await message.answer("Текущий текст приветствия в группе (использует HTML-разметку):")
    await message.answer(current_text)
    await message.answer("Пришлите новый текст приветствия. Используйте {username} и {bot_username} для подстановок.\n\nДля отмены введите /cancel")

@router.message(AdminEditStates.waiting_for_welcome_text_group)
async def process_welcome_text_group(message: Message, state: FSMContext):
    """Сохраняет новый текст приветствия для группы."""
    db.set_setting('welcome_message_group', message.text)
    await state.clear()
    await message.answer("✅ Текст приветствия в группе обновлен.")

# --- Управление настройками ---

@router.message(Command("welcome_bonus", ignore_case=True))
async def cmd_welcome_bonus(message: Message, state: FSMContext):
    """Управляет welcome-бонусом."""
    args = message.text.split()
    
    if len(args) > 1:
        try:
            amount = Decimal(args[1])
            if amount < 0: raise ValueError()
            db.set_setting('welcome_bonus_amount', str(amount))
            if amount > 0:
                await message.answer(f"✅ Welcome-бонус установлен: <b>{format_amount(amount)} {CURRENCY_SYMBOL}</b>", parse_mode="HTML")
            else:
                await message.answer("✅ Welcome-бонус отключен.")
        except (InvalidOperation, ValueError):
            await message.reply("❌ Введите корректную сумму (0 для отключения).")
        return

    current_bonus = db.get_setting('welcome_bonus_amount', '0')
    await state.set_state(AdminEditStates.waiting_for_welcome_bonus)
    await message.reply(
        f"Текущий welcome-бонус: <b>{format_amount(Decimal(current_bonus))} {CURRENCY_SYMBOL}</b>\n\n"
        f"Введите новую сумму (0 для отключения).\n\nДля отмены введите /cancel",
        parse_mode="HTML"
    )

@router.message(AdminEditStates.waiting_for_welcome_bonus)
async def process_welcome_bonus(message: Message, state: FSMContext):
    """Обрабатывает новую сумму welcome-бонуса."""
    try:
        amount = Decimal(message.text)
        if amount < 0: raise ValueError()
        db.set_setting('welcome_bonus_amount', str(amount))
        await state.clear()
        if amount > 0:
            await message.answer(f"✅ Welcome-бонус установлен: <b>{format_amount(amount)} {CURRENCY_SYMBOL}</b>", parse_mode="HTML")
        else:
            await message.answer("✅ Welcome-бонус отключен.")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Неверное значение. Введите корректную неотрицательную сумму.")

@router.message(Command("demurrage_status", ignore_case=True))
async def cmd_demurrage_status(message: Message):
    """Показывает статус и ставку демерреджа."""
    enabled = db.get_setting('demurrage_enabled', '0') == '1'
    rate = Decimal(db.get_setting('demurrage_rate', '0.01')) * 100
    status_text = "✅ Включен" if enabled else "❌ Отключен"
    await message.answer(f"Статус демерреджа: {status_text}\nТекущая ставка: {format_amount(rate)}%")

@router.message(Command("demurrage_on", ignore_case=True))
async def cmd_demurrage_on(message: Message):
    """Включает демерредж."""
    db.set_setting('demurrage_enabled', '1')
    await message.answer("✅ Демерредж включен.")

@router.message(Command("demurrage_off", ignore_case=True))
async def cmd_demurrage_off(message: Message):
    """Выключает демерредж."""
    db.set_setting('demurrage_enabled', '0')
    await message.answer("❌ Демерредж отключен.")

@router.message(Command("set_demurrage", ignore_case=True))
async def cmd_set_demurrage(message: Message, state: FSMContext):
    """Устанавливает ставку демерреджа."""
    args = message.text.split()
    if len(args) > 1:
        try:
            percent = Decimal(args[1])
            if not (0 <= percent <= 100): raise ValueError()
            db.set_setting('demurrage_rate', str(percent / 100))
            await message.answer(f"✅ Ставка демерреджа установлена: {format_amount(percent)}%")
        except (InvalidOperation, ValueError):
            await message.reply("❌ Укажите процент от 0 до 100.")
        return
    
    await state.set_state(AdminEditStates.waiting_for_demurrage_rate)
    rate = Decimal(db.get_setting('demurrage_rate', '0.01')) * 100
    await message.answer(f"Текущая ставка: {format_amount(rate)}%.\nВведите новый процент (например, 5 или 0.5).\n\nДля отмены введите /cancel")

@router.message(AdminEditStates.waiting_for_demurrage_rate)
async def process_demurrage_rate(message: Message, state: FSMContext):
    """Обрабатывает новую ставку демерреджа."""
    try:
        percent = Decimal(message.text)
        if not (0 <= percent <= 100): raise ValueError()
        db.set_setting('demurrage_rate', str(percent / 100))
        await state.clear()
        await message.answer(f"✅ Ставка демерреджа установлена: {format_amount(percent)}%")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Неверное значение. Введите процент от 0 до 100.")

@router.message(Command("set_exchange", ignore_case=True))
async def cmd_set_exchange(message: Message, state: FSMContext):
    """Устанавливает курс обмена RUB к Ӫ."""
    args = message.text.split()
    if len(args) > 1:
        try:
            rate = Decimal(args[1])
            if rate <= 0: raise ValueError()
            db.set_setting('exchange_rate', str(rate))
            await message.answer(f"✅ Курс обмена установлен: 1 RUB = {format_amount(rate)} {CURRENCY_SYMBOL}")
        except (InvalidOperation, ValueError):
            await message.reply("❌ Укажите положительное число.")
        return
    
    await state.set_state(AdminEditStates.waiting_for_exchange_rate)
    current_rate = Decimal(db.get_setting('exchange_rate', '1.0'))
    await message.answer(f"Текущий курс: 1 RUB = {format_amount(current_rate)} {CURRENCY_SYMBOL}.\nВведите новый курс.\n\nДля отмены введите /cancel")

@router.message(AdminEditStates.waiting_for_exchange_rate)
async def process_exchange_rate(message: Message, state: FSMContext):
    """Обрабатывает новый курс обмена."""
    try:
        rate = Decimal(message.text)
        if rate <= 0: raise ValueError()
        db.set_setting('exchange_rate', str(rate))
        await state.clear()
        await message.answer(f"✅ Курс обмена установлен: 1 RUB = {format_amount(rate)} {CURRENCY_SYMBOL}")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Неверное значение. Введите корректную неотрицательную сумму.")

# --- Управление администраторами ---
# ДОБАВЛЕНО: Восстановлены команды для управления правами администраторов.

@router.message(Command("make_admin", ignore_case=True))
async def cmd_make_admin(message: Message):
    """
    Назначает пользователя администратором.

    Args:
        message (Message): Объект сообщения.
    """
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /make_admin @username")
        return

    username = args[1].lstrip('@').lower()
    user = db.get_user(username=username)

    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return

    if user['is_admin']:
        await message.reply(f"✅ Пользователь @{username} уже является администратором.")
        return

    db.set_admin_status(user['telegram_id'], is_admin=True)
    logger.info(f"Admin {message.from_user.id} promoted {user['telegram_id']} (@{username}) to admin.")
    await message.answer(f"✅ Пользователь @{username} успешно назначен администратором.")

@router.message(Command("remove_admin", ignore_case=True))
async def cmd_remove_admin(message: Message):
    """
    Снимает с пользователя права администратора.

    Args:
        message (Message): Объект сообщения.
    """
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❌ Формат: /remove_admin @username")
        return

    username = args[1].lstrip('@').lower()
    user = db.get_user(username=username)

    if not user:
        await message.reply(f"❌ Пользователь @{username} не найден.")
        return

    if not user['is_admin']:
        await message.reply(f"✅ Пользователь @{username} не является администратором.")
        return

    # Защита от снятия прав с самого себя
    if user['telegram_id'] == message.from_user.id:
        await message.reply("❌ Вы не можете снять права администратора с самого себя.")
        return

    db.set_admin_status(user['telegram_id'], is_admin=False)
    logger.warning(f"Admin {message.from_user.id} demoted {user['telegram_id']} (@{username}) from admin.")
    await message.answer(f"✅ С пользователя @{username} сняты права администратора.")