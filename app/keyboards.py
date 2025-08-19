# XBalanseBot/app/keyboards.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.utils import get_next_run_time

MSK_LABEL = "MSK"

def confirm_delete_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=callback_data),
        InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete")
    )
    return builder.as_markup()

async def get_activities_keyboard(activities: list, user_subscriptions: list = None, action: str = "view") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    user_sub_ids = {sub['activity_id'] for sub in user_subscriptions} if user_subscriptions else set()

    for activity in activities:
        if action == "delete" and activity['id'] == 1:
            continue

        is_subscribed = activity['id'] in user_sub_ids
        text = f"✅ {activity['name']}" if is_subscribed and action == "view" else activity['name']

        if action == "view":
            cb = f"activity_{activity['id']}"
        elif action == "edit":
            cb = f"edit_activity_{activity['id']}"
        elif action == "delete":
            cb = f"delete_activity_{activity['id']}"
        else:
            cb = f"activity_{activity['id']}"

        builder.row(InlineKeyboardButton(text=text, callback_data=cb))

    return builder.as_markup()

async def get_activity_details_keyboard(activity_id: int, is_subscribed: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if activity_id != 1:
        if is_subscribed:
            builder.row(InlineKeyboardButton(text="❌ Отписаться", callback_data=f"unsubscribe_{activity_id}"))
        else:
            builder.row(InlineKeyboardButton(text="✅ Подписаться", callback_data=f"subscribe_{activity_id}"))
    builder.row(InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_activities"))
    return builder.as_markup()

async def get_activities_keyboard_for_event(activities: list) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for activity in activities:
        if activity['id'] == 1:
            builder.row(InlineKeyboardButton(text=f"{activity['name']} (для всех)", callback_data=f"select_activity_1"))
        else:
            builder.row(InlineKeyboardButton(text=activity['name'], callback_data=f"select_activity_{activity['id']}"))
    return builder.as_markup()

async def get_events_keyboard(events: list, action: str = "view") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    weekdays_map = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    for event_row in events:
        event = dict(event_row)
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
            schedule_str = f"{date_str} ({weekday_str}) {time_str} ({MSK_LABEL})"
        else:
            schedule_str = f"Дата не определена ({MSK_LABEL})"

        display_name = event.get('name') or event.get('activity_name')
        text = f"{schedule_str} - {display_name}"

        event_id = event['id']
        if action == "view":
            cb = f"event_{event_id}"
        elif action == "edit":
            cb = f"edit_event_{event_id}"
        elif action == "delete":
            cb = f"delete_event_{event_id}"
        else:
            cb = f"event_{event_id}"

        builder.row(InlineKeyboardButton(text=text, callback_data=cb))
    return builder.as_markup()

async def get_event_details_keyboard(event_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="⬅️ Назад к списку", callback_data="back_to_events"))
    return builder.as_markup()

async def get_event_edit_keyboard(event_id: int) -> InlineKeyboardMarkup:
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
    builder = InlineKeyboardBuilder()
    weekdays = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5, "Вс": 6}
    buttons = [InlineKeyboardButton(text=day, callback_data=f"select_weekday_{idx}") for day, idx in weekdays.items()]
    builder.row(*buttons)
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_delete"))
    return builder.as_markup()
