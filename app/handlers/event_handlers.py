import logging
from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.database import db
from app.keyboards import (
    get_events_keyboard, get_event_details_keyboard, confirm_delete_keyboard, 
    get_activities_keyboard_for_event, get_event_edit_keyboard, get_weekday_keyboard
)
from app.states import EventCreationStates, EventEditStates
from app.utils import is_admin, format_amount
from config import CURRENCY_SYMBOL, DEFAULT_REMINDER_TEXT
from app.services.scheduler_jobs import schedule_event_jobs, remove_event_jobs

router = Router()
logger = logging.getLogger(__name__)
weekdays_map = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
weekdays_short_map = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

@router.message(Command("event", ignore_case=True))
async def cmd_event(message: Message):
    """Выводит список ближайших событий."""
    events = db.get_all_events()
    if not events:
        await message.answer("В ближайшее время событий не запланировано.")
        return
    
    keyboard = await get_events_keyboard(events)
    await message.answer("Ближайшие события:", reply_markup=keyboard)

@router.callback_query(F.data.regexp(r"^event_\d+$"))
async def process_event_selection(callback: CallbackQuery):
    """Показывает детали выбранного события."""
    event_id = int(callback.data.split("_")[1])
    event = db.get_event(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    
    event_name = event['name'] or event['activity_name']
    event_description = event['description'] or event['activity_description']
    
    schedule_str = "Не определено"
    if event['event_type'] == 'single' and event['event_date']:
        event_date = event['event_date']
        if isinstance(event_date, str): event_date = datetime.fromisoformat(event_date)
        schedule_str = f"📅 <b>Дата:</b> {event_date.strftime('%d.%m.%Y в %H:%M')}"
    elif event['event_type'] == 'recurring' and event['weekday'] is not None and event['event_time'] is not None:
        event_time = event['event_time']
        if isinstance(event_time, str): event_time = time.fromisoformat(event_time)
        schedule_str = f"📅 <b>Регулярность:</b> Каждый {weekdays_map[event['weekday']]} в {event_time.strftime('%H:%M')}"

    text = (
        f"<b>{event_name}</b>\n\n"
        f"<i>{event_description}</i>\n\n"
        f"{schedule_str}\n"
        f"💰 <b>Стоимость:</b> {format_amount(Decimal(str(event['cost'])))} {CURRENCY_SYMBOL}\n"
        f"🔗 Ссылка на событие будет отправлена подписчикам в личные сообщения."
    )
    
    keyboard = await get_event_details_keyboard(event_id)
    await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML", disable_web_page_preview=True)
    await callback.answer()

@router.callback_query(F.data == "back_to_events")
async def back_to_events_list(callback: CallbackQuery):
    """Возвращает к списку событий."""
    events = db.get_all_events()
    if not events:
        await callback.message.edit_text("В ближайшее время событий не запланировано.")
        await callback.answer()
        return
        
    keyboard = await get_events_keyboard(events)
    await callback.message.edit_text("Ближайшие события:", reply_markup=keyboard)
    await callback.answer()

# --- Admin commands for events ---

@router.message(Command("create_event", ignore_case=True))
async def cmd_create_event(message: Message, state: FSMContext):
    """Начинает процесс создания нового события."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
        
    activities = db.get_all_activities()
    keyboard = await get_activities_keyboard_for_event(activities)
    await state.set_state(EventCreationStates.waiting_for_activity)
    await message.answer("К какой активности относится событие?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("create_event_for_"))
async def start_event_creation_from_activity(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс создания события для конкретной, уже выбранной активности."""
    if not await is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет прав для выполнения этой команды.", show_alert=True)
        return

    activity_id = int(callback.data.split("_")[3])
    await state.update_data(activity_id=activity_id)
    await state.set_state(EventCreationStates.waiting_for_event_name)
    await callback.message.edit_text(
        "Введите название для события. Отправьте `.` или `нет`, чтобы использовать название активности.\n\n"
        "*Для отмены введите /cancel*",
        parse_mode="Markdown"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("select_activity_"), EventCreationStates.waiting_for_activity)
async def process_event_activity(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор активности из списка при создании события."""
    activity_id = int(callback.data.split("_")[2])
    await state.update_data(activity_id=activity_id)
    await state.set_state(EventCreationStates.waiting_for_event_name)
    await callback.message.edit_text(
        "Введите название для события. Отправьте `.` или `нет`, чтобы использовать название активности.\n\n"
        "*Для отмены введите /cancel*",
        parse_mode="Markdown"
    )
    await callback.answer()

@router.message(EventCreationStates.waiting_for_event_name)
async def process_event_name(message: Message, state: FSMContext):
    """Получает название для события и запрашивает описание."""
    event_name = message.text
    if event_name.strip() in ['.', 'нет']:
        event_name = None

    await state.update_data(name=event_name)
    await state.set_state(EventCreationStates.waiting_for_event_description)
    await message.answer(
        "Отлично! Теперь введите описание для события. Отправьте `.` или `нет`, чтобы использовать описание активности.\n\n"
        "*Для отмены введите /cancel*",
        parse_mode="Markdown"
    )

@router.message(EventCreationStates.waiting_for_event_description)
async def process_event_description(message: Message, state: FSMContext):
    """Получает описание для события и запрашивает его тип."""
    event_description = message.text
    if event_description.strip() in ['.', 'нет']:
        event_description = None

    await state.update_data(description=event_description)
    await state.set_state(EventCreationStates.waiting_for_type)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Разовое", callback_data="event_type_single")],
        [InlineKeyboardButton(text="Регулярное", callback_data="event_type_recurring")]
    ])
    await message.answer("Выберите тип события:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("event_type_"), EventCreationStates.waiting_for_type)
async def process_event_type(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа события (разовое/регулярное)."""
    event_type = callback.data.split("_")[2]
    await state.update_data(event_type=event_type)
    
    if event_type == "single":
        await state.set_state(EventCreationStates.waiting_for_date)
        await callback.message.edit_text("Введите дату и время события в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n\nДля отмены введите /cancel", parse_mode="HTML")
    else:
        await state.set_state(EventCreationStates.waiting_for_weekday)
        keyboard = get_weekday_keyboard()
        await callback.message.edit_text("Выберите день недели для регулярного события:", reply_markup=keyboard)
    await callback.answer()

# ИЗМЕНЕНО: Новая вспомогательная функция для обработки шага с ценой
async def proceed_to_cost_or_skip(message: Message, state: FSMContext):
    """
    Проверяет, относится ли событие к общей активности (ID=1).
    Если да, устанавливает цену 0 и пропускает шаг. В противном случае, запрашивает цену.

    Args:
        message (Message): Объект сообщения для ответа пользователю.
        state (FSMContext): Контекст состояния FSM.
    """
    data = await state.get_data()
    activity_id = data.get('activity_id')

    if activity_id == 1:
        # Для общих событий (ID=1) цена всегда 0, пропускаем шаг
        await state.update_data(cost='0')
        await state.set_state(EventCreationStates.waiting_for_link)
        await message.answer(
            "Событие для общей активности создается бесплатным.\n\n"
            "Теперь введите ссылку на событие (например, на чат или видеоконференцию).\n\n"
            "*Для отмены введите /cancel*",
            parse_mode="Markdown"
        )
        logger.info("Event creation for general activity (ID=1), skipping cost step.")
    else:
        # Для всех остальных активностей запрашиваем цену
        await state.set_state(EventCreationStates.waiting_for_cost)
        await message.answer(
            "Отлично. Теперь введите стоимость участия (число).\n\n"
            "*Для отмены введите /cancel*",
            parse_mode="Markdown"
        )

@router.message(EventCreationStates.waiting_for_date)
async def process_event_date(message: Message, state: FSMContext):
    """Получает дату и время для разового события."""
    try:
        event_date = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        await state.update_data(event_date=event_date, weekday=None, event_time=None)
        # ИЗМЕНЕНО: Вызов новой функции для определения следующего шага
        await proceed_to_cost_or_skip(message, state)
    except ValueError:
        await message.reply("❌ Неверный формат. Введите дату и время в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>.", parse_mode="HTML")

@router.callback_query(F.data.startswith("select_weekday_"), EventCreationStates.waiting_for_weekday)
async def process_event_weekday(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор дня недели для регулярного события."""
    weekday = int(callback.data.split("_")[2])
    await state.update_data(weekday=weekday)
    await state.set_state(EventCreationStates.waiting_for_time)
    await callback.message.edit_text(f"Вы выбрали: <b>{weekdays_map[weekday].capitalize()}</b>.\nТеперь введите время в формате <b>ЧЧ:ММ</b>.", parse_mode="HTML")
    await callback.answer()

@router.message(EventCreationStates.waiting_for_time)
async def process_event_time(message: Message, state: FSMContext):
    """Получает время для регулярного события."""
    try:
        event_time = datetime.strptime(message.text, "%H:%M").time()
        await state.update_data(event_time=event_time, event_date=None)
        # ИЗМЕНЕНО: Отправляем подтверждение времени отдельным сообщением перед вызовом следующего шага
        await message.answer(f"Время установлено: <b>{event_time.strftime('%H:%M')}</b>.", parse_mode="HTML")
        await proceed_to_cost_or_skip(message, state)
    except ValueError:
        await message.reply("❌ Неверный формат. Введите время в формате <b>ЧЧ:ММ</b>.", parse_mode="HTML")

@router.message(EventCreationStates.waiting_for_cost)
async def process_event_cost(message: Message, state: FSMContext):
    """Получает стоимость участия в событии."""
    try:
        cost = Decimal(message.text)
        if cost < 0: raise ValueError()
        await state.update_data(cost=str(cost))
        await state.set_state(EventCreationStates.waiting_for_link)
        await message.answer("Теперь введите ссылку на событие (например, на чат или видеоконференцию).\n\nДля отмены введите /cancel")
    except (InvalidOperation, ValueError):
        await message.reply("❌ Введите корректное неотрицательное число.")

@router.message(EventCreationStates.waiting_for_link)
async def process_event_link(message: Message, state: FSMContext):
    """Получает ссылку на событие."""
    await state.update_data(link=message.text)
    await state.set_state(EventCreationStates.waiting_for_reminder_time)
    await message.answer("За сколько минут до начала отправлять напоминание? Введите число (0 - не отправлять).\n\nДля отмены введите /cancel")

@router.message(EventCreationStates.waiting_for_reminder_time)
async def process_event_reminder_time(message: Message, state: FSMContext):
    """Получает время для отправки напоминания."""
    try:
        reminder_time = int(message.text)
        if reminder_time < 0: raise ValueError()
        await state.update_data(reminder_time=reminder_time)
        if reminder_time > 0:
            await state.set_state(EventCreationStates.waiting_for_reminder_text)
            await message.answer("Введите текст напоминания. Используйте переменные, как в шаблоне. Отправьте `.` для использования шаблона по умолчанию.\n\nДля отмены введите /cancel")
        else:
            await state.update_data(reminder_text=None)
            await create_event_from_state(message, state)
    except ValueError:
        await message.reply("❌ Введите целое неотрицательное число.")

@router.message(EventCreationStates.waiting_for_reminder_text)
async def process_event_reminder_text(message: Message, state: FSMContext):
    """Получает текст напоминания и создает событие."""
    await state.update_data(reminder_text=message.text)
    await create_event_from_state(message, state)

async def create_event_from_state(message: Message, state: FSMContext):
    """
    Вспомогательная функция для создания события из данных FSM,
    сохранения в БД и планирования задач.
    """
    data = await state.get_data()
    bot = message.bot
    scheduler = bot.scheduler

    event_data = {
        'activity_id': data.get('activity_id'),
        'name': data.get('name'),
        'description': data.get('description'),
        'event_type': data['event_type'],
        'cost': data['cost'],
        'link': data['link'],
        'reminder_time': data['reminder_time'],
        'reminder_text': data.get('reminder_text'),
        'created_by': message.from_user.id,
        'event_date': data.get('event_date'),
        'weekday': data.get('weekday'),
        'event_time': data.get('event_time')
    }
    
    event_id = db.create_event(**event_data)
    await schedule_event_jobs(db.get_event(event_id), bot, scheduler)
    
    await state.clear()
    logger.info(f"Admin {message.from_user.id} created new event {event_id}.")
    await message.answer(f"✅ Событие успешно создано и запланировано (ID: {event_id}).")

@router.message(Command("edit_event", ignore_case=True))
async def cmd_edit_event(message: Message, state: FSMContext):
    """Начинает процесс редактирования события."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
    
    events = db.get_all_events()
    if not events:
        await message.answer("Нет событий для редактирования.")
        return
        
    keyboard = await get_events_keyboard(events, action="edit")
    await message.answer("Выберите событие для редактирования:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_event_"))
async def process_edit_event_selection(callback: CallbackQuery, state: FSMContext):
    """Показывает меню редактирования для выбранного события."""
    event_id = int(callback.data.split("_")[2])
    await state.update_data(event_id=event_id)
    
    keyboard = await get_event_edit_keyboard(event_id)
    await callback.message.edit_text("Что вы хотите изменить в событии?", reply_markup=keyboard)
    await callback.answer()

@router.message(Command("delete_event", ignore_case=True))
async def cmd_delete_event(message: Message, state: FSMContext):
    """Начинает процесс удаления события."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
    
    events = db.get_all_events()
    if not events:
        await message.answer("Нет событий для удаления.")
        return
        
    keyboard = await get_events_keyboard(events, action="delete")
    await message.answer("Выберите событие для удаления:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("delete_event_"))
async def process_delete_confirmation(callback: CallbackQuery):
    """Запрашивает подтверждение удаления события."""
    event_id = int(callback.data.split("_")[2])
    event = db.get_event(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    
    event_name = event['name'] or event['activity_name']
    await callback.message.edit_text(
        f"Вы уверены, что хотите удалить событие '{event_name}'?\n"
        "<b>Это действие необратимо!</b>",
        reply_markup=confirm_delete_keyboard(f"confirm_delete_event_{event_id}"),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_delete_event_"))
async def process_delete_activity(callback: CallbackQuery):
    """Окончательно удаляет событие."""
    event_id = int(callback.data.split("_")[3])
    event = db.get_event(event_id)
    if not event:
        await callback.answer("Событие уже удалено.", show_alert=True)
        return
        
    scheduler = callback.bot.scheduler
    remove_event_jobs(event_id, scheduler)
    
    db.delete_event(event_id)
    
    logger.warning(f"Admin {callback.from_user.id} deleted event {event_id}: {event['name']}")
    await callback.message.edit_text(f"✅ Событие '{event['name'] or event['activity_name']}' было удалено.")
    await callback.answer()
