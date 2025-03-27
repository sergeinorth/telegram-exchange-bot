import os
import logging
from telegram.ext import Application
from dotenv import load_dotenv
import json

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
logger = logging.getLogger(__name__)

logger.info(f"Прочитанный TELEGRAM_TOKEN: {TOKEN}")

# Инициализируем bot_config как глобальную переменную
bot_config = {}

def load_config():
    global bot_config
    default_config = {
        "owner_id": "669497764",
        "request_limit": 5,
        "subscription_check_interval": 300,
        "messages": {
            "welcome": "Здравствуйте, {name}! Добро пожаловать в бота для обмена валют.\nВыберите, что хотите обменять:",
            "amount_prompt": "Вы выбрали {operation}.\nКакую сумму хотите обменять? Укажите сумму:\n(Для USDT — в USDT, для рублей и рупий — в соответствующей валюте)",
            "invalid_amount": "Пожалуйста, укажите сумму числом.",
            "negative_amount": "Сумма должна быть больше 0. Пожалуйста, попробуйте ещё раз.",
            "location_prompt": "Вы получите {result:.2f} {currency}.\nКуда доставить деньги? Выберите локацию:",
            "fine_location_prompt": "Хотите указать точное место? Отправьте геолокацию или ссылку на Google Maps:",
            "request_accepted": "Ваша заявка принята.\nОперация: {operation}\nСумма: {amount}\nВыдать: {result:.2f} {currency}\nЛокация: {location}\nТочное место: {fine_location}\nОжидайте, с вами скоро свяжутся.",
            "admin_request": "Новая заявка\nОт: {user_link}\nОперация: {operation}\nСумма: {amount}\nВыдать: {result}\nЛокация: {location}\nТочное место: {fine_location}",
            "limit_exceeded": "Вы превысили лимит в {limit} заявок в сутки! Попробуйте завтра.",
            "limit_exceeded_owner": "Бро, ты превысил лимит в {limit} заявок в сутки! Попробуй завтра.",
            "no_pairs": "Извините, на данный момент нет доступных пар для обмена. Попробуйте позже."
        },
        "default_rates": {
            "Рубли (безнал) → Рупии (нал)": 0.85,
            "Рупии (нал) → Рубли (безнал)": 0.95,
            "USDT → Рупии (нал)": 90,
            "Рупии (нал) → USDT": 95,
            "USDT → Рубли (безнал)": 100,
            "Рубли (безнал) → USDT": 0.01
        },
        "default_pairs": [
            "Рубли (безнал) → Рупии (нал)",
            "Рупии (нал) → Рубли (безнал)",
            "USDT → Рупии (нал)",
            "Рупии (нал) → USDT",
            "USDT → Рубли (безнал)",
            "Рубли (безнал) → USDT"
        ],
        "default_active_pairs": [
            "Рубли (безнал) → Рупии (нал)",
            "Рупии (нал) → Рубли (безнал)",
            "USDT → Рупии (нал)",
            "Рупии (нал) → USDT",
            "USDT → Рубли (безнал)",
            "Рубли (безнал) → USDT"
        ],
        "default_locations": [
            "Пернем",
            "Керим",
            "Коргао",
            "Арамболь",
            "Мандрем",
            "Ашвем",
            "Морджим",
            "Сиолим"
        ],
        "default_active_locations": [
            "Пернем",
            "Керим",
            "Коргао",
            "Арамболь",
            "Мандрем",
            "Ашвем",
            "Морджим",
            "Сиолим"
        ],
        "currencies": {
            "Рубли (безнал) → Рупии (нал)": "рупий",
            "Рупии (нал) → Рубли (безнал)": "рублей",
            "USDT → Рупии (нал)": "рупий",
            "Рупии (нал) → USDT": "USDT",
            "USDT → Рубли (безнал)": "рублей",
            "Рубли (безнал) → USDT": "USDT"
        }
    }
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            bot_config.update(json.load(f))
        logger.info("Конфигурация загружена из config.json")
    except Exception as e:
        logger.error(f"Ошибка загрузки config.json: {e}, использую дефолтный конфиг")
        bot_config.update(default_config)

# Загружаем конфиг при импорте модуля
load_config()

application = Application.builder().token(TOKEN).build()