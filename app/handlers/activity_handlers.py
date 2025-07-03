import logging
import sqlite3
from datetime import datetime, time
from decimal import Decimal
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.keyboards import get_activities_keyboard, get_activity_details_keyboard, confirm_delete_keyboard
from app.states import ActivityCreationStates, ActivityEditStates
from app.database import db
from app.utils import is_admin, format_amount
from app.handlers.event_handlers import weekdays_map
from config import CURRENCY_SYMBOL

router = Router()
logger = logging.getLogger(__name__)

@router.message(Command("activity", ignore_case=True))
async def cmd_activity(message: Message):
    """Выводит список активностей с возможностью подписки/отписки."""
    user_id = message.from_user.id
    
    # ИЗМЕНЕНО: Логика отображения общей активности: показывается, только если есть события.
    all_activities = db.get_all_activities()
    general_activity_events = db.get_events_for_activity(1)
    
    activities_to_show = []
    for act in all_activities:
        if act['id'] == 1:
            # Показываем общую активность, только если у нее есть события
            if general_activity_events:
                activities_to_show.append(act)
        else:
            # Всегда показываем остальные активности
            activities_to_show.append(act)

    if not activities_to_show:
        await message.answer("На данный момент нет ни одной доступной активности.")
        return
        
    user_subscriptions = db.get_user_subscriptions(user_id)
    
    keyboard = await get_activities_keyboard(activities_to_show, user_subscriptions)
    await message.answer("Выберите активность для просмотра информации:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("activity_"))
async def process_activity_selection(callback: CallbackQuery):
    """Обрабатывает выбор активности из списка."""
    activity_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    activity = db.get_activity(activity_id)
    if not activity:
        await callback.answer("Активность не найдена.", show_alert=True)
        return

    is_subscribed = db.is_user_subscribed(user_id, activity_id)
    keyboard = await get_activity_details_keyboard(activity_id, is_subscribed)
    
    text = f"<b>{activity['name']}</b>\n\n{activity['description']}"
    
    events = db.get_events_for_activity(activity_id)
    if events:
        events_text_parts = ["\n\n<b>Связанные события:</b>"]
        for event in events:
            schedule_str = "Не определено"
            if event['event_type'] == 'single' and event['event_date']:
                event_date = event['event_date']
                if isinstance(event_date, str): event_date = datetime.fromisoformat(event_date)
                schedule_str = f"{event_date.strftime('%d.%m.%Y в %H:%M')}"
            elif event['event_type'] == 'recurring' and event['weekday'] is not None and event['event_time'] is not None:
                event_time = event['event_time']
                if isinstance(event_time, str): event_time = time.fromisoformat(event_time)
                schedule_str = f"Каждый {weekdays_map[event['weekday']]} в {event_time.strftime('%H:%M')}"
            
            event_name = event['name'] or activity['name']
            cost = format_amount(Decimal(str(event['cost'])))
            events_text_parts.append(f"- {event_name} ({schedule_str}, {cost} {CURRENCY_SYMBOL})")
        
        text += "\n".join(events_text_parts)
    
    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("subscribe_"))
async def process_subscribe(callback: CallbackQuery):
    """Обрабатывает подписку на активность."""
    activity_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    db.add_subscription(user_id, activity_id)
    logger.info(f"User {user_id} subscribed to activity {activity_id}")
    
    await callback.answer("✅ Вы успешно подписались!", show_alert=True)
    
    activity = db.get_activity(activity_id)
    is_subscribed = db.is_user_subscribed(user_id, activity_id)
    keyboard = await get_activity_details_keyboard(activity_id, is_subscribed)
    
    text = f"<b>{activity['name']}</b>\n\n{activity['description']}"
    events = db.get_events_for_activity(activity_id)
    if events:
        events_text_parts = ["\n\n<b>Связанные события:</b>"]
        for event in events:
            schedule_str = "Не определено"
            if event['event_type'] == 'single' and event['event_date']:
                event_date = event['event_date']
                if isinstance(event_date, str): event_date = datetime.fromisoformat(event_date)
                schedule_str = f"{event_date.strftime('%d.%m.%Y в %H:%M')}"
            elif event['event_type'] == 'recurring' and event['weekday'] is not None and event['event_time'] is not None:
                event_time = event['event_time']
                if isinstance(event_time, str): event_time = time.fromisoformat(event_time)
                schedule_str = f"Каждый {weekdays_map[event['weekday']]} в {event_time.strftime('%H:%M')}"
            
            event_name = event['name'] or activity['name']
            cost = format_amount(Decimal(str(event['cost'])))
            events_text_parts.append(f"- {event_name} ({schedule_str}, {cost} {CURRENCY_SYMBOL})")
        text += "\n".join(events_text_parts)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data.startswith("unsubscribe_"))
async def process_unsubscribe(callback: CallbackQuery):
    """Обрабатывает отписку от активности."""
    activity_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    
    db.remove_subscription(user_id, activity_id)
    logger.info(f"User {user_id} unsubscribed from activity {activity_id}")
    
    await callback.answer("✅ Вы успешно отписались!", show_alert=True)

    activity = db.get_activity(activity_id)
    is_subscribed = db.is_user_subscribed(user_id, activity_id)
    keyboard = await get_activity_details_keyboard(activity_id, is_subscribed)

    text = f"<b>{activity['name']}</b>\n\n{activity['description']}"
    events = db.get_events_for_activity(activity_id)
    if events:
        events_text_parts = ["\n\n<b>Связанные события:</b>"]
        for event in events:
            schedule_str = "Не определено"
            if event['event_type'] == 'single' and event['event_date']:
                event_date = event['event_date']
                if isinstance(event_date, str): event_date = datetime.fromisoformat(event_date)
                schedule_str = f"{event_date.strftime('%d.%m.%Y в %H:%M')}"
            elif event['event_type'] == 'recurring' and event['weekday'] is not None and event['event_time'] is not None:
                event_time = event['event_time']
                if isinstance(event_time, str): event_time = time.fromisoformat(event_time)
                schedule_str = f"Каждый {weekdays_map[event['weekday']]} в {event_time.strftime('%H:%M')}"
            
            event_name = event['name'] or activity['name']
            cost = format_amount(Decimal(str(event['cost'])))
            events_text_parts.append(f"- {event_name} ({schedule_str}, {cost} {CURRENCY_SYMBOL})")
        text += "\n".join(events_text_parts)

    await callback.message.edit_text(
        text,
        reply_markup=keyboard,
        parse_mode="HTML"
    )

@router.callback_query(F.data == "back_to_activities")
async def back_to_activities_list(callback: CallbackQuery):
    """Возвращает к списку активностей."""
    user_id = callback.from_user.id
    
    # ИЗМЕНЕНО: Логика возврата к списку идентична /activity: общая активность видна, только если есть события.
    all_activities = db.get_all_activities()
    general_activity_events = db.get_events_for_activity(1)
    activities_to_show = []
    for act in all_activities:
        if act['id'] == 1:
            if general_activity_events:
                activities_to_show.append(act)
        else:
            activities_to_show.append(act)

    if not activities_to_show:
        await callback.message.edit_text("На данный момент нет ни одной доступной активности.")
        await callback.answer()
        return

    user_subscriptions = db.get_user_subscriptions(user_id)
    
    keyboard = await get_activities_keyboard(activities_to_show, user_subscriptions)
    await callback.message.edit_text("Выберите активность для просмотра информации:", reply_markup=keyboard)
    await callback.answer()

# --- Admin commands for activities ---

@router.message(Command("create_act", ignore_case=True))
async def cmd_create_activity(message: Message, state: FSMContext):
    """Начинает процесс создания новой активности (только для админов)."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
    await state.set_state(ActivityCreationStates.waiting_for_name)
    await message.answer("Введите название новой активности:\n\n*Для отмены введите /cancel*", parse_mode="Markdown")

@router.message(ActivityCreationStates.waiting_for_name)
async def process_activity_name(message: Message, state: FSMContext):
    """Получает название активности и запрашивает описание."""
    await state.update_data(name=message.text)
    await state.set_state(ActivityCreationStates.waiting_for_description)
    await message.answer("Отлично! Теперь введите описание активности:\n\n*Для отмены введите /cancel*", parse_mode="Markdown")

@router.message(ActivityCreationStates.waiting_for_description)
async def process_activity_description(message: Message, state: FSMContext):
    """Получает описание и запрашивает дату окончания."""
    await state.update_data(description=message.text)
    await state.set_state(ActivityCreationStates.waiting_for_end_date)
    await message.answer("Теперь введите дату окончания активности в формате ДД.ММ.ГГГГ или напишите 'нет', если она бессрочная.\n\n*Для отмены введите /cancel*", parse_mode="Markdown")

@router.message(ActivityCreationStates.waiting_for_end_date)
async def process_activity_end_date(message: Message, state: FSMContext):
    """Получает дату, создает активность и завершает процесс."""
    end_date_str = message.text.lower()
    end_date = None
    if end_date_str != 'нет':
        try:
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y").date()
        except ValueError:
            await message.reply("❌ Неверный формат даты. Пожалуйста, введите дату в формате ДД.ММ.ГГГГ или 'нет'.\n\n*Для отмены введите /cancel*", parse_mode="Markdown")
            return
    
    data = await state.get_data()
    try:
        activity_id = db.create_activity(data['name'], data['description'], end_date)
        await state.clear()
        
        logger.info(f"Admin {message.from_user.id} created new activity {activity_id}: {data['name']}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Создать событие для этой активности", callback_data=f"create_event_for_{activity_id}")]
        ])
        await message.answer(f"✅ Новая активность '{data['name']}' успешно создана!", reply_markup=keyboard)
    except sqlite3.IntegrityError:
        await message.reply(f"❌ Активность с названием '{data['name']}' уже существует. Пожалуйста, выберите другое название или отмените операцию (/cancel).")
        await state.set_state(ActivityCreationStates.waiting_for_name)
        await message.answer("Введите название новой активности:")


@router.message(Command("edit_act", ignore_case=True))
async def cmd_edit_activity(message: Message):
    """Выводит список активностей для редактирования (только для админов)."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
    
    # ИЗМЕНЕНО: Системная активность (ID=1) теперь видна для редактирования.
    activities = db.get_all_activities()
    if not activities:
        await message.answer("Нет активностей для редактирования.")
        return
    
    keyboard = await get_activities_keyboard(activities, action="edit")
    await message.answer("Выберите активность для редактирования:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("edit_activity_"))
async def process_edit_activity_selection(callback: CallbackQuery, state: FSMContext):
    """Запрашивает, что именно нужно отредактировать в активности."""
    activity_id = int(callback.data.split("_")[2])
    activity = db.get_activity(activity_id)
    if not activity:
        await callback.answer("Активность не найдена.", show_alert=True)
        return

    await state.update_data(activity_id=activity_id)
    
    info_text = (
        f"<b>Редактирование активности:</b>\n"
        f"<b>Название:</b> {activity['name']}\n"
        f"<b>Описание:</b> {activity['description'][:100]}...\n"
        f"<b>Дата окончания:</b> {activity['end_date'] or 'Бессрочная'}\n\n"
        f"Что вы хотите изменить?"
    )
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Название", callback_data="edit_field_name")],
        [InlineKeyboardButton(text="Описание", callback_data="edit_field_desc")],
        [InlineKeyboardButton(text="Дату окончания", callback_data="edit_field_date")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_edit_list")]
    ])
    await callback.message.edit_text(info_text, reply_markup=keyboard, parse_mode="HTML")
    await callback.answer()

@router.callback_query(F.data == "back_to_edit_list")
async def back_to_edit_list(callback: CallbackQuery, state: FSMContext):
    """Возвращает к списку активностей для редактирования."""
    await state.clear()
    activities = db.get_all_activities()
    keyboard = await get_activities_keyboard(activities, action="edit")
    await callback.message.edit_text("Выберите активность для редактирования:", reply_markup=keyboard)
    await callback.answer()

@router.callback_query(F.data.startswith("edit_field_"))
async def process_edit_field(callback: CallbackQuery, state: FSMContext):
    """Запрашивает новое значение для выбранного поля."""
    field = callback.data.split("_")[2]
    data = await state.get_data()
    activity_id = data.get('activity_id')
    
    if not activity_id:
        await callback.answer("Ошибка: ID активности не найден. Пожалуйста, начните заново.", show_alert=True)
        await state.clear()
        return

    activity = db.get_activity(activity_id)
    if not activity:
        await callback.answer("Ошибка: Активность не найдена.", show_alert=True)
        await state.clear()
        return

    prompts = {
        "name": (f"Текущее название: `{activity['name']}`\nВведите новое:", ActivityEditStates.waiting_for_new_name),
        "desc": (f"Текущее описание: `{activity['description']}`\nВведите новое:", ActivityEditStates.waiting_for_new_description),
        "date": (f"Текущая дата окончания: `{activity['end_date'] or 'нет'}`\nВведите новую (ДД.ММ.ГГГГ или 'нет'):", ActivityEditStates.waiting_for_new_end_date)
    }
    
    prompt_text, new_state = prompts[field]
    await state.set_state(new_state)
    await callback.message.edit_text(f"{prompt_text}\n\n*Для отмены введите /cancel*", parse_mode="Markdown")
    await callback.answer()

@router.message(ActivityEditStates.waiting_for_new_name)
async def update_activity_name(message: Message, state: FSMContext):
    data = await state.get_data()
    db.update_activity(data['activity_id'], name=message.text)
    await message.answer("✅ Название активности обновлено.")
    await state.clear()

@router.message(ActivityEditStates.waiting_for_new_description)
async def update_activity_description(message: Message, state: FSMContext):
    data = await state.get_data()
    db.update_activity(data['activity_id'], description=message.text)
    await message.answer("✅ Описание активности обновлено.")
    await state.clear()

@router.message(ActivityEditStates.waiting_for_new_end_date)
async def update_activity_end_date(message: Message, state: FSMContext):
    data = await state.get_data()
    end_date_str = message.text.lower()
    end_date = None
    if end_date_str != 'нет':
        try:
            end_date = datetime.strptime(end_date_str, "%d.%m.%Y").date()
        except ValueError:
            await message.reply("❌ Неверный формат. Введите дату в формате ДД.ММ.ГГГГ или 'нет'.\n\n*Для отмены введите /cancel*", parse_mode="Markdown")
            return
            
    if 'activity_id' in data:
        db.update_activity(data['activity_id'], end_date=end_date)
        await message.answer("✅ Дата окончания активности обновлена.")
    else:
        await message.answer("❌ Произошла ошибка. Не удалось найти ID активности для обновления.")
        
    await state.clear()

@router.message(Command("delete_act", ignore_case=True))
async def cmd_delete_activity(message: Message):
    """Выводит список активностей для удаления (только для админов)."""
    if not await is_admin(message.from_user.id):
        await message.reply("❌ У вас нет прав для выполнения этой команды.")
        return
    # ИЗМЕНЕНО: Фильтрация системной активности (ID=1) сохранена.
    activities = [act for act in db.get_all_activities() if act['id'] != 1]
    if not activities:
        await message.answer("Нет активностей для удаления.")
        return
    
    keyboard = await get_activities_keyboard(activities, action="delete")
    await message.answer("Выберите активность для удаления:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("delete_activity_"))
async def process_delete_confirmation(callback: CallbackQuery):
    """Запрашивает подтверждение удаления."""
    activity_id = int(callback.data.split("_")[2])
    # ИЗМЕНЕНО: Проверка на удаление системной активности сохранена.
    if activity_id == 1:
        await callback.answer("Эту активность нельзя удалить.", show_alert=True)
        return

    activity = db.get_activity(activity_id)
    if not activity:
        await callback.answer("Активность не найдена.", show_alert=True)
        return
    
    await callback.message.edit_text(
        f"Вы уверены, что хотите удалить активность '{activity['name']}'?\n"
        "<b>Это действие необратимо и удалит все связанные подписки и события!</b>",
        reply_markup=confirm_delete_keyboard(f"confirm_delete_activity_{activity_id}"),
        parse_mode="HTML"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("confirm_delete_activity_"))
async def process_delete_activity(callback: CallbackQuery):
    """Окончательно удаляет активность."""
    activity_id = int(callback.data.split("_")[3])
    activity = db.get_activity(activity_id)
    if not activity:
        await callback.answer("Активность уже удалена.", show_alert=True)
        return
        
    db.delete_activity(activity_id)
    logger.warning(f"Admin {callback.from_user.id} deleted activity {activity_id}: {activity['name']}")
    await callback.message.edit_text(f"✅ Активность '{activity['name']}' была удалена.")
    await callback.answer()
