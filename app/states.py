"""
Модуль для определения состояний (FSM) бота.
Состояния используются для пошагового сбора информации от пользователя.
"""

from aiogram.fsm.state import State, StatesGroup

class TransferStates(StatesGroup):
    """Состояния для процесса перевода средств."""
    waiting_for_comment = State()

class AdminEditStates(StatesGroup):
    """Состояния для редактирования административных настроек."""
    waiting_for_welcome_text = State()
    waiting_for_welcome_text_group = State()
    waiting_for_demurrage_rate = State()
    # ИЗМЕНЕНО: Добавлено состояние для интервала демерреджа
    waiting_for_demurrage_interval = State()
    waiting_for_exchange_rate = State()
    waiting_for_welcome_bonus = State()
    waiting_for_reminder_text = State()

class FundPaymentStates(StatesGroup):
    """Состояния для процесса выплаты из фонда."""
    waiting_for_amount_and_comment = State()

class ActivityCreationStates(StatesGroup):
    """Состояния для процесса создания новой активности."""
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_end_date = State()

class ActivityEditStates(StatesGroup):
    """Состояния для процесса редактирования существующей активности."""
    waiting_for_new_name = State()
    waiting_for_new_description = State()
    waiting_for_new_end_date = State()

class EventCreationStates(StatesGroup):
    """Состояния для процесса создания нового события."""
    waiting_for_activity = State()
    waiting_for_event_name = State()
    waiting_for_event_description = State()
    waiting_for_type = State()
    waiting_for_date = State()
    waiting_for_weekday = State()
    waiting_for_time = State()
    waiting_for_cost = State()
    waiting_for_link = State()
    waiting_for_reminder_time = State()
    waiting_for_reminder_text = State()

class EventEditStates(StatesGroup):
    """Состояния для процесса редактирования существующего события."""
    waiting_for_field_choice = State()
    waiting_for_new_name = State()
    waiting_for_new_description = State()
    waiting_for_new_date = State()
    waiting_for_new_weekday = State()
    waiting_for_new_time = State()
    waiting_for_new_cost = State()
    waiting_for_new_link = State()
    waiting_for_new_reminder_time = State()
    waiting_for_new_reminder_text = State()
