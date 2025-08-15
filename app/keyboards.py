"""
Модуль для создания клавиатур (интерактивных кнопок) для бота.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, time
from app.utils import get_next_run_time

def confirm_delete_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для подтверждения удаления.

    Args:
        callback_data (str): Данные, которые будут отправлены при подтверждении.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками "Да, удалить" и "Отмена".
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=callback_data),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")
    )
    return builder.as_markup()

async def get_activities_keyboard(activities: list, user_subscriptions: list = None, action: str = "view") -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком активностей.

    Args:
        activities (list): Список словарей с активностями.
        user_subscriptions (list, optional): Список ID активностей, на которые подписан пользователь.
        action (str, optional): Действие ('view', 'edit', 'delete'). Определяет callback_data.

    Returns:
        InlineKeyboardMarkup: Клавиатура со списком активностей.
    """
    builder = InlineKeyboardBuilder()
    user_subscriptions_ids = {sub['activity_id'] for sub in user_subscriptions} if user_subscriptions else set()

    for activity in activities:
        # ИЗМЕНЕНО: Скрываем системную активность "Общие события" из списка на удаление, но разрешаем редактирование.
        if action == "delete" and activity['id'] == 1:
            continue

        is_subscribed = activity['id'] in user_subscriptions_ids
        
        text = f"✅ {activity['name']}" if is_subscribed and action == "view" else activity['name']
        
        if action == "view":
            callback_data = f"activity_{activity['id']}"
        elif action == "edit":
            callback_data = f"edit_activity_{activity['id']}"
        elif action == "delete":
            callback_data = f"delete_activity_{activity['id']}"
        else:
            callback_data = f"activity_{activity['id']}"

        builder.row(InlineKeyboardButton(text=text, callback_data=callback_data))
    
    return builder.as_markup()

async def get_activity_details_keyboard(activity_id: int, is_subscribed: bool) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для управления подпиской на конкретную активность.

    Args:
        activity_id (int): ID активности.
        is_subscribed (bool): Подписан ли пользователь на эту активность.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками подписки/отписки и возврата.
    """
    builder = InlineKeyboardBuilder()
    # РЕФАКТОРИНГ: Не даем отписаться от системной активности "Общие события"
    if activity_id != 1:
        if is_subscribed:
            builder.row(InlineKeyboardButton(text="❌ Отписаться", callback_data=f"unsubscribe_{activity_id}"))
        else:
            builder.row(InlineKeyboardButton(text="✅ Подписаться", callback_data=f"subscribe_{activity_id}"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_activities"))
    return builder.as_markup()

async def get_activities_keyboard_for_event(activities: list) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком активностей для выбора при создании события.

    Args:
        activities (list): Список активностей.

    Returns:
        InlineKeyboardMarkup: Клавиатура со списком активностей.
    """
    builder = InlineKeyboardBuilder()
    for activity in activities:
        # РЕФАКТОРИНГ: Кнопка "Общее событие" теперь ссылается на activity_id=1
        if activity['id'] == 1:
            builder.row(InlineKeyboardButton(text=f"{activity['name']} (для всех)", callback_data=f"select_activity_1"))
        else:
            builder.row(InlineKeyboardButton(text=activity['name'], callback_data=f"select_activity_{activity['id']}"))
    return builder.as_markup()

async def get_events_keyboard(events: list, action: str = "view") -> InlineKeyboardMarkup:
    """
    Создает клавиатуру со списком событий.

    Args:
        events (list): Список событий (может быть уже отсортирован).
        action (str, optional): Действие ('view', 'edit', 'delete').

    Returns:
        InlineKeyboardMarkup: Клавиатура со списком событий.
    """
    builder = InlineKeyboardBuilder()
    weekdays_map = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    
    for event_row in events:
        event = dict(event_row)
        
        # ДОБАВЛЕНО: Вычисление конкретной даты для всех событий
        next_run = get_next_run_time(
            event['event_type'],
            event.get('event_date'),
            event.get('weekday'),
            event.get('event_time'),
            event.get('last_run')
        )
        
        if next_run:
            date_str = next_run.strftime('%d.%m')
            weekday_str = weekdays_map[next_run.weekday()]
            time_str = next_run.strftime('%H:%M')
            schedule_str = f"{date_str} ({weekday_str}) {time_str}"
        else:
            # Fallback для событий без корректной даты
            schedule_str = "Дата не определена"

        event_display_name = event.get('name') or event.get('activity_name')
        # ДОБАВЛЕНО: Форматирование названия жирным
        text = f"{schedule_str} - {event_display_name}"
        
        event_id = event['id']
        if action == "view":
            callback_data = f"event_{event_id}"
        elif action == "edit":
            callback_data = f"edit_event_{event_id}"
        elif action == "delete":
            callback_data = f"delete_event_{event_id}"
        else:
            callback_data = f"event_{event_id}"
            
        builder.row(InlineKeyboardButton(text=text, callback_data=callback_data))
    return builder.as_markup()

async def get_event_details_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для деталей события.

    Args:
        event_id (int): ID события.

    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопкой "Назад".
    """
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_events"))
    return builder.as_markup()

async def get_event_edit_keyboard(event_id: int) -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для выбора поля для редактирования в событии.

    Args:
        event_id (int): ID редактируемого события.

    Returns:
        InlineKeyboardMarkup: Клавиатура с полями события.
    """
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="Название", callback_data=f"edit_evt_name_{event_id}"),
        InlineKeyboardButton(text="Описание", callback_data=f"edit_evt_description_{event_id}")
    )
    builder.row(
        InlineKeyboardButton(text="Расписание", callback_data=f"edit_evt_schedule_{event_id}"),
        InlineKeyboardButton(text="Стоимость", callback_data=f"edit_evt_cost_{event_id}")
    )
    builder.row(
        InlineKeyboardButton(text="Ссылку", callback_data=f"edit_evt_link_{event_id}"),
        InlineKeyboardButton(text="Напоминание", callback_data=f"edit_evt_reminder_{event_id}")
    )
    builder.row(InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_events"))
    return builder.as_markup()

def get_weekday_keyboard() -> InlineKeyboardMarkup:
    """
    Создает клавиатуру для выбора дня недели.

    Returns:
        InlineKeyboardMarkup: Клавиатура с днями недели.
    """
    builder = InlineKeyboardBuilder()
    weekdays = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5, "Вс": 6}
    buttons = [
        InlineKeyboardButton(text=day, callback_data=f"select_weekday_{idx}")
        for day, idx in weekdays.items()
    ]
    builder.row(*buttons)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete"))
    return builder.as_markup()
