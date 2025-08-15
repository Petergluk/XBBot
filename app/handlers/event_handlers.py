# project_name/app/handlers/event_handlers.py
import logging
from datetime import datetime, time, timedelta
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
from app.utils import is_admin, format_amount, get_next_run_time
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
    
    sorted_events = []
    now = datetime.now()
    
    for event in events:
        next_run = get_next_run_time(
            event['event_type'],
            event['event_date'],
            event['weekday'],
            event['event_time'],
            event['last_run']
        )
        if next_run:
            sorted_events.append((next_run, event))
    
    sorted_events.sort(key=lambda x: x[0])
    
    week_ahead = now + timedelta(days=7)
    this_week_events = [(date, event) for date, event in sorted_events if date <= week_ahead]
    
    if not this_week_events:
        await message.answer("На ближайшую неделю событий не запланировано.")
        return
    
    keyboard = await get_events_keyboard([event for _, event in this_week_events])
    await message.answer("📅 <b>События на ближайшие 7 дней:</b>", reply_markup=keyboard, parse_mode="HTML")

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
        next_run = get_next_run_time(
            event['event_type'], event['event_date'], 
            event['weekday'], event['event_time'], event['last_run']
        )
        if next_run:
            schedule_str = f"📅 <b>Регулярность:</b> Каждый {weekdays_map[event['weekday']]}\n"
            schedule_str += f"📅 <b>Следующее:</b> {next_run.strftime('%d.%m.%Y в %H:%M')}"
        else:
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
    await cmd_event(callback.message)
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

async def proceed_to_cost_or_skip(message: Message, state: FSMContext):
    """
    Проверяет, относится ли событие к общей активности (ID=1) и выводит предупреждение.
    """
    data = await state.get_data()
    activity_id = data.get('activity_id')

    await state.set_state(EventCreationStates.waiting_for_cost)
    
    if activity_id == 1:
        await message.answer(
            "⚠️ <b>Внимание!</b> Это общее событие.\n"
            "Все пользователи будут автоматически участвовать со списанием средств!\n\n"
            "Введите стоимость участия (число). Введите 0 для бесплатного события.\n\n"
            "*Для отмены введите /cancel*",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            "Отлично. Теперь введите стоимость участия (число).\n\n"
            "*Для отмены введите /cancel*",
            parse_mode="Markdown"
        )

@router.message(EventCreationStates.waiting_for_date)
async def process_event_date(message: Message, state: FSMContext):
    """Получает дату и время для разового события."""
    # ИЗМЕНЕНО: Добавлена замена запятых на точки для гибкости ввода.
    date_text = message.text.replace(',', '.')
    try:
        event_date = datetime.strptime(date_text, "%d.%m.%Y %H:%M")
        # ИЗМЕНЕНО: Добавлена проверка, чтобы дата не была в прошлом.
        if event_date < datetime.now():
            await message.reply("❌ Нельзя создать событие в прошлом. Пожалуйста, введите будущую дату и время.")
            return
            
        await state.update_data(event_date=event_date, weekday=None, event_time=None)
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
            await message.answer(
                "Введите текст напоминания. Отправьте `.` для использования шаблона по умолчанию.\n\n"
                "<b>Доступные переменные:</b>\n"
                "<code>{event_name}</code> - название события\n"
                "<code>{event_description}</code> - описание события\n"
                "<code>{start_date}</code> - дата события (ДД.ММ.ГГГГ)\n"
                "<code>{start_time}</code> - время события (ЧЧ:ММ)\n"
                "<code>{cost}</code> - стоимость участия\n"
                "<code>{currency_symbol}</code> - символ валюты\n"
                "<code>{reminder_minutes}</code> - за сколько минут напоминание\n"
                "<code>{link}</code> - ссылка на событие\n\n"
                "Для отмены введите /cancel",
                parse_mode="HTML"
            )
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
    event = db.get_event(event_id)
    if not event:
        await callback.answer("Событие не найдено.", show_alert=True)
        return
    
    await state.update_data(event_id=event_id)
    
    event_name = event['name'] or event['activity_name']
    event_description = event['description'] or event['activity_description']
    
    info_parts = [
        f"<b>📝 Редактирование события</b>\n",
        f"<b>Название:</b> <code>{event_name}</code>",
        f"<b>Описание:</b> <code>{event_description[:50]}{'...' if len(event_description) > 50 else ''}</code>"
    ]
    
    if event['event_type'] == 'single' and event['event_date']:
        event_date = event['event_date']
        if isinstance(event_date, str): event_date = datetime.fromisoformat(event_date)
        info_parts.append(f"<b>Дата:</b> <code>{event_date.strftime('%d.%m.%Y %H:%M')}</code>")
    elif event['event_type'] == 'recurring':
        if event['weekday'] is not None:
            info_parts.append(f"<b>День недели:</b> <code>{weekdays_map[event['weekday']].capitalize()}</code>")
        if event['event_time'] is not None:
            event_time = event['event_time']
            if isinstance(event_time, str): event_time = time.fromisoformat(event_time)
            info_parts.append(f"<b>Время:</b> <code>{event_time.strftime('%H:%M')}</code>")
    
    info_parts.extend([
        f"<b>Стоимость:</b> <code>{format_amount(Decimal(str(event['cost'])))} {CURRENCY_SYMBOL}</code>",
        f"<b>Ссылка:</b> <code>{event['link']}</code>",
        f"<b>Напоминание:</b> <code>{'За ' + str(event['reminder_time']) + ' мин.' if event['reminder_time'] else 'Нет'}</code>",
        "\nЧто вы хотите изменить?"
    ])
    
    info_text = "\n".join(info_parts)
    
    keyboard = await get_event_edit_keyboard(event_id)
    await callback.message.edit_text(info_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data.startswith("edit_evt_name_"))
async def process_edit_event_name(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новое название события."""
    event_id = int(callback.data.split("_")[3])
    await state.update_data(event_id=event_id)
    await state.set_state(EventEditStates.waiting_for_new_name)
    
    event = db.get_event(event_id)
    current_name = event['name'] or event['activity_name']
    
    await callback.message.edit_text(
        f"Текущее название: <code>{current_name}</code>\n\n"
        "Введите новое название или `.` чтобы использовать название активности.\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_name)
async def update_event_name(message: Message, state: FSMContext):
    """Обновляет название события."""
    data = await state.get_data()
    event_id = data['event_id']
    
    new_name = message.text if message.text != '.' else None
    db.update_event(event_id, name=new_name)
    
    await message.answer("✅ Название события обновлено.")
    await state.clear()

@router.callback_query(F.data.startswith("edit_evt_description_"))
async def process_edit_event_description(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новое описание события."""
    event_id = int(callback.data.split("_")[3])
    await state.update_data(event_id=event_id)
    await state.set_state(EventEditStates.waiting_for_new_description)
    
    event = db.get_event(event_id)
    current_desc = event['description'] or event['activity_description']
    
    await callback.message.edit_text(
        f"Текущее описание: <code>{current_desc}</code>\n\n"
        "Введите новое описание или `.` чтобы использовать описание активности.\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_description)
async def update_event_description(message: Message, state: FSMContext):
    """Обновляет описание события."""
    data = await state.get_data()
    event_id = data['event_id']
    
    new_desc = message.text if message.text != '.' else None
    db.update_event(event_id, description=new_desc)
    
    await message.answer("✅ Описание события обновлено.")
    await state.clear()

@router.callback_query(F.data.startswith("edit_evt_schedule_"))
async def process_edit_event_schedule(callback: CallbackQuery, state: FSMContext):
    """Начинает процесс изменения расписания события."""
    event_id = int(callback.data.split("_")[3])
    event = db.get_event(event_id)
    
    await state.update_data(event_id=event_id)
    
    if event['event_type'] == 'single':
        await state.set_state(EventEditStates.waiting_for_new_date)
        await callback.message.edit_text(
            "Введите новую дату и время в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n\n"
            "Для отмены введите /cancel",
            parse_mode="HTML"
        )
    else:
        await state.set_state(EventEditStates.waiting_for_new_weekday)
        keyboard = get_weekday_keyboard()
        await callback.message.edit_text("Выберите новый день недели:", reply_markup=keyboard)
    
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_date)
async def update_event_date(message: Message, state: FSMContext):
    """Обновляет дату разового события."""
    try:
        new_date = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        data = await state.get_data()
        event_id = data['event_id']
        
        db.update_event(event_id, event_date=new_date)
        
        bot = message.bot
        scheduler = bot.scheduler
        remove_event_jobs(event_id, scheduler)
        await schedule_event_jobs(db.get_event(event_id), bot, scheduler)
        
        await message.answer("✅ Дата события обновлена и перепланирована.")
        await state.clear()
    except ValueError:
        await message.reply("❌ Неверный формат. Введите дату и время в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>.", parse_mode="HTML")

@router.callback_query(F.data.startswith("select_weekday_"), EventEditStates.waiting_for_new_weekday)
async def update_event_weekday(callback: CallbackQuery, state: FSMContext):
    """Обновляет день недели для регулярного события."""
    weekday = int(callback.data.split("_")[2])
    data = await state.get_data()
    event_id = data['event_id']
    
    await state.update_data(weekday=weekday)
    await state.set_state(EventEditStates.waiting_for_new_time)
    
    await callback.message.edit_text(
        f"Выбран: <b>{weekdays_map[weekday].capitalize()}</b>.\n"
        "Теперь введите новое время в формате <b>ЧЧ:ММ</b>.",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_time)
async def update_event_time(message: Message, state: FSMContext):
    """Обновляет время для регулярного события."""
    try:
        new_time = datetime.strptime(message.text, "%H:%M").time()
        data = await state.get_data()
        event_id = data['event_id']
        weekday = data['weekday']
        
        db.update_event(event_id, weekday=weekday, event_time=new_time)
        
        bot = message.bot
        scheduler = bot.scheduler
        remove_event_jobs(event_id, scheduler)
        await schedule_event_jobs(db.get_event(event_id), bot, scheduler)
        
        await message.answer("✅ Расписание события обновлено и перепланировано.")
        await state.clear()
    except ValueError:
        await message.reply("❌ Неверный формат. Введите время в формате <b>ЧЧ:ММ</b>.", parse_mode="HTML")

@router.callback_query(F.data.startswith("edit_evt_cost_"))
async def process_edit_event_cost(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новую стоимость события."""
    event_id = int(callback.data.split("_")[3])
    await state.update_data(event_id=event_id)
    await state.set_state(EventEditStates.waiting_for_new_cost)
    
    event = db.get_event(event_id)
    current_cost = format_amount(Decimal(str(event['cost'])))
    
    text = f"Текущая стоимость: <code>{current_cost} {CURRENCY_SYMBOL}</code>\n\n"
    
    if event['activity_id'] == 1:
        text += "⚠️ <b>Внимание!</b> Это общее событие. Изменение стоимости затронет всех пользователей!\n\n"
    
    text += "Введите новую стоимость (число).\n\nДля отмены введите /cancel"
    
    await callback.message.edit_text(text, parse_mode="HTML")
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_cost)
async def update_event_cost(message: Message, state: FSMContext):
    """Обновляет стоимость события."""
    try:
        new_cost = Decimal(message.text)
        if new_cost < 0: raise ValueError()
        
        data = await state.get_data()
        event_id = data['event_id']
        
        db.update_event(event_id, cost=str(new_cost))
        
        await message.answer("✅ Стоимость события обновлена.")
        await state.clear()
    except (InvalidOperation, ValueError):
        await message.reply("❌ Введите корректное неотрицательное число.")

@router.callback_query(F.data.startswith("edit_evt_link_"))
async def process_edit_event_link(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новую ссылку на событие."""
    event_id = int(callback.data.split("_")[3])
    await state.update_data(event_id=event_id)
    await state.set_state(EventEditStates.waiting_for_new_link)
    
    event = db.get_event(event_id)
    
    await callback.message.edit_text(
        f"Текущая ссылка: <code>{event['link']}</code>\n\n"
        "Введите новую ссылку.\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_link)
async def update_event_link(message: Message, state: FSMContext):
    """Обновляет ссылку на событие."""
    data = await state.get_data()
    event_id = data['event_id']
    
    db.update_event(event_id, link=message.text)
    
    await message.answer("✅ Ссылка на событие обновлена.")
    await state.clear()

@router.callback_query(F.data.startswith("edit_evt_reminder_"))
async def process_edit_event_reminder(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новые параметры напоминания."""
    event_id = int(callback.data.split("_")[3])
    await state.update_data(event_id=event_id)
    await state.set_state(EventEditStates.waiting_for_new_reminder_time)
    
    event = db.get_event(event_id)
    current_time = event['reminder_time'] or 0
    
    await callback.message.edit_text(
        f"Текущее время напоминания: <code>{'За ' + str(current_time) + ' мин.' if current_time else 'Нет'}</code>\n\n"
        "Введите за сколько минут до события отправлять напоминание (0 - отключить).\n\n"
        "Для отмены введите /cancel",
        parse_mode="HTML"
    )
    await callback.answer()

@router.message(EventEditStates.waiting_for_new_reminder_time)
async def update_event_reminder_time(message: Message, state: FSMContext):
    """Обновляет время напоминания."""
    try:
        new_time = int(message.text)
        if new_time < 0: raise ValueError()
        
        data = await state.get_data()
        event_id = data['event_id']
        
        await state.update_data(reminder_time=new_time)
        
        if new_time > 0:
            await state.set_state(EventEditStates.waiting_for_new_reminder_text)
            await message.answer(
                "Введите новый текст напоминания или `.` для шаблона по умолчанию.\n\n"
                "<b>Доступные переменные:</b>\n"
                "<code>{event_name}</code> - название события\n"
                "<code>{event_description}</code> - описание события\n"
                "<code>{start_date}</code> - дата события (ДД.ММ.ГГГГ)\n"
                "<code>{start_time}</code> - время события (ЧЧ:ММ)\n"
                "<code>{cost}</code> - стоимость участия\n"
                "<code>{currency_symbol}</code> - символ валюты\n"
                "<code>{reminder_minutes}</code> - за сколько минут напоминание\n"
                "<code>{link}</code> - ссылка на событие\n\n"
                "Для отмены введите /cancel",
                parse_mode="HTML"
            )
        else:
            db.update_event(event_id, reminder_time=0, reminder_text=None)
            
            bot = message.bot
            scheduler = bot.scheduler
            try:
                scheduler.remove_job(f"event_reminder_{event_id}")
            except:
                pass
            
            await message.answer("✅ Напоминание отключено.")
            await state.clear()
    except ValueError:
        await message.reply("❌ Введите целое неотрицательное число.")

@router.message(EventEditStates.waiting_for_new_reminder_text)
async def update_event_reminder_text(message: Message, state: FSMContext):
    """Обновляет текст напоминания."""
    data = await state.get_data()
    event_id = data['event_id']
    reminder_time = data['reminder_time']
    reminder_text = message.text if message.text != '.' else DEFAULT_REMINDER_TEXT
    
    db.update_event(event_id, reminder_time=reminder_time, reminder_text=reminder_text)
    
    bot = message.bot
    scheduler = bot.scheduler
    event = db.get_event(event_id)
    
    remove_event_jobs(event_id, scheduler)
    await schedule_event_jobs(event, bot, scheduler)
    
    await message.answer("✅ Параметры напоминания обновлены.")
    await state.clear()

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