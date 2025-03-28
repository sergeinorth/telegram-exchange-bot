import telegram
import signal
import sys
import logging
import asyncio
import sqlite3
from telegram.ext import Application, CommandHandler, ConversationHandler, MessageHandler
from telegram.ext import filters
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton
from ex_admin import get_admin_handler, build_admin_entry_menu, ADMIN_STATE, ADD_LOCATION, ADD_PAIR
from ex_owner import activate_otp, check_subscription
from bot_config import application, bot_config
from utils import init_db, get_user_data, save_user_data, check_request_limit, log_request, get_admin_data
from datetime import datetime, timedelta
from pytils import numeral
import json
import os
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

# Загружаем токен из .env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Настройка логирования с ротацией
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # Устанавливаем уровень DEBUG
handler = RotatingFileHandler('bot.log', maxBytes=5*1024*1024, backupCount=3)  # 5 MB, 3 файла
handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
logger.addHandler(handler)
logging.getLogger("httpx").setLevel(logging.WARNING)

CHOOSING, AMOUNT, LOCATION, FINE_LOCATION = range(4)

def build_client_menu(user_id):
    active_order, request_count, referrer_id = get_user_data(user_id)
    if active_order is None and request_count == 0 and referrer_id is None:
        logger.warning(f"Не удалось получить данные пользователя {user_id} в build_client_menu, используем owner_id")
        referrer_id = bot_config["owner_id"]  # Дефолтный admin_id
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)
    reply_keyboard = [admin_data['active_pairs'][i:i+2] for i in range(0, len(admin_data['active_pairs']), 2)]
    if str(user_id) == bot_config["owner_id"] or (active_order and 'admin_expiry' in json.loads(active_order)):
        reply_keyboard.append(["Админка"])
    return ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=False, resize_keyboard=True)

def build_amount_menu():
    return ReplyKeyboardMarkup([["Назад"]], one_time_keyboard=True, resize_keyboard=True)

def build_location_menu(user_id):
    _, _, referrer_id = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)
    reply_keyboard = [admin_data['active_locations'][i:i+3] for i in range(0, len(admin_data['active_locations']), 3)]
    reply_keyboard.append(["Назад"])
    return ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)

def build_fine_location_menu():
    reply_keyboard = [[KeyboardButton("Отправить свою локацию", request_location=True), "Пропустить"], ["Назад"]]
    return ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True)

async def start(update, context):
    init_db()
    logger.info("База данных инициализирована")
    user = update.message.from_user
    user_id = user.id

    active_order, request_count, referrer_id = get_user_data(user_id)
    if active_order is None and request_count == 0 and referrer_id is None:
        logger.warning(f"Не удалось получить данные пользователя {user_id}, возможно, проблема с базой")

    if not active_order and not referrer_id:
        args = context.args
        if args and args[0].startswith("ref_"):
            try:
                referrer_id = int(args[0].replace("ref_", ""))
                save_user_data(user_id, None, referrer_id=referrer_id)
                logger.info(f"Пользователь {user_id} привязан к админу {referrer_id} через рефералку")
                user_link = f"@{user.username}" if user.username else f"[Пользователь](tg://user?id={user_id})"
                await context.bot.send_message(
                    chat_id=referrer_id,
                    text=f"Новый пользователь {user_link} запустил бота через твою реферальную ссылку!"
                )
            except ValueError:
                logger.warning(f"Некорректный реферальный ID: {args[0]}")

    if active_order:
        context.user_data['in_admin_mode'] = 'admin_expiry' in json.loads(active_order)
    else:
        context.user_data['in_admin_mode'] = False

    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    if not admin_data['active_pairs']:
        await update.message.reply_text(bot_config["messages"]["no_pairs"])
        return ConversationHandler.END

    reply_markup = build_client_menu(user_id)
    logger.info("Отправка приветственного сообщения")

    async def send_message_with_retry(chat_id, text, parse_mode=None, reply_markup=None, retries=5):
        for attempt in range(retries):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
                logger.info(f"Сообщение успешно отправлено в чат {chat_id}")
                return True
            except telegram.error.TimedOut as e:
                logger.warning(f"Попытка {attempt + 1} из {retries} не удалась: {str(e)}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(5)
        return False

    try:
        if active_order or referrer_id:
            await send_message_with_retry(
                chat_id=user_id,
                text=bot_config["messages"]["welcome"].format(name=user.first_name),
                reply_markup=reply_markup
            )
        else:
            save_user_data(user_id, None, referrer_id)
            await send_message_with_retry(
                chat_id=user_id,
                text=bot_config["messages"]["welcome"].format(name=user.first_name),
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Ошибка в start: {str(e)}")
        raise

    logger.info("Завершение функции start")
    return CHOOSING

async def choose_operation(update, context):
    logger.info("Начало обработки выбора операции")
    user_id = update.message.from_user.id
    
    if context.user_data.get('in_admin_mode', False):
        logger.info(f"Пользователь {user_id} в админке, передаём управление")
        return ADMIN_STATE

    choice = update.message.text
    logger.info(f"Выбор пользователя: {choice}")

    _, _, referrer_id = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    async def send_message_with_retry(chat_id, text, parse_mode=None, reply_markup=None, retries=5):
        for attempt in range(retries):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
                logger.info(f"Сообщение успешно отправлено в чат {chat_id}")
                return True
            except telegram.error.TimedOut as e:
                logger.warning(f"Попытка {attempt + 1} из {retries} не удалась: {str(e)}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(5)
        return False

    if (str(user_id) == bot_config["owner_id"] or (get_user_data(user_id)[0] and 'admin_expiry' in json.loads(get_user_data(user_id)[0]))) and choice == "Админка":
        logger.info("Отправка кнопки для входа в админку")
        reply_markup = build_admin_entry_menu()
        await send_message_with_retry(
            chat_id=user_id,
            text="Нажми ниже, чтобы войти в админ-панель:",
            reply_markup=reply_markup
        )
        return CHOOSING
    elif choice == "Назад":
        logger.info("Возврат к выбору операции")
        reply_markup = build_client_menu(user_id)
        await send_message_with_retry(
            chat_id=user_id,
            text=bot_config["messages"]["welcome"].format(name=update.message.from_user.first_name),
            reply_markup=reply_markup
        )
        return CHOOSING
    elif choice not in admin_data['active_pairs']:
        logger.info("Некорректный выбор, просьба выбрать из предложенных")
        await send_message_with_retry(
            chat_id=user_id,
            text="Пожалуйста, выберите одну из предложенных опций ниже."
        )
        return CHOOSING

    context.user_data.setdefault('user_data', {})  # Инициализируем, если нет
    context.user_data['user_data']['operation'] = choice
    logger.info("Отправка запроса суммы")
    await send_message_with_retry(
        chat_id=user_id,
        text=bot_config["messages"]["amount_prompt"].format(operation=choice),
        reply_markup=build_amount_menu()
    )
    logger.info("Завершение выбора операции")
    return AMOUNT

from pytils import numeral  # Добавляем импорт в начало файла

async def get_amount(update, context):
    user_id = update.message.from_user.id
    logger.debug(f"Получен ввод суммы от {user_id}: {update.message.text}")
    if update.message.text == "Назад":
        reply_markup = build_client_menu(user_id)
        await update.message.reply_text(
            bot_config["messages"]["welcome"].format(name=update.message.from_user.first_name),
            reply_markup=reply_markup
        )
        return CHOOSING

    _, _, referrer_id = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    try:
        amount = float(update.message.text)
        logger.debug(f"Сумма {amount} успешно преобразована для {user_id}")
        if amount <= 0:
            await update.message.reply_text(bot_config["messages"]["negative_amount"])
            return AMOUNT
        if amount > 1_000_000_000:
            await update.message.reply_text("Сумма слишком большая! Максимум — 1 миллиард.")
            return AMOUNT
        context.user_data.setdefault('user_data', {})  # Инициализируем, если нет
        context.user_data['user_data']['amount'] = amount

        operation = context.user_data['user_data']['operation']
        rate = admin_data['rates'].get(operation, 1)
        result = amount * rate
        context.user_data['user_data']['result'] = result
        logger.debug(f"Результат расчёта для {operation}: {result}")

        formatted_result = "{:,}".format(int(result)).replace(",", ".")
        if '→' in operation:
            currency_raw = operation.split('→')[1].strip()
        elif '-' in operation:
            currency_raw = operation.split('-')[1].strip()
        else:
            currency_raw = "unknown"
        currency_base = currency_raw.split()[0].lower()
        currency_corrections = {
            "рубли": "рубль",
            "рупии": "рупия",
            "донги": "донг",
            "юани": "юань",
            "баты": "бат",
            "usdt": "USDT"
        }
        currency_root = currency_corrections.get(currency_base, currency_base)
        if currency_root in ["usdt"]:
            currency = currency_root.upper()
        else:
            currency = numeral.choose_plural(int(result), (currency_root, currency_root + 'а', currency_root + 'ов'))

        reply_markup = build_location_menu(user_id)
        await update.message.reply_text(
            f"Вы получите {formatted_result} {currency}.\nКуда доставить деньги? Выберите локацию:",
            reply_markup=reply_markup
        )
        logger.debug(f"Сообщение о выборе локации отправлено пользователю {user_id}")
        return LOCATION
    except ValueError:
        await update.message.reply_text(bot_config["messages"]["invalid_amount"])
        return AMOUNT

async def get_location(update, context):
    user_id = update.message.from_user.id
    _, _, referrer_id = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    if update.message.text == "Назад":
        reply_markup = build_amount_menu()
        await update.message.reply_text(
            bot_config["messages"]["amount_prompt"].format(operation=context.user_data['user_data']['operation']),
            reply_markup=reply_markup
        )
        return AMOUNT
    location = update.message.text
    if location not in admin_data['active_locations']:
        await update.message.reply_text("Пожалуйста, выберите локацию из предложенных ниже.")
        return LOCATION

    context.user_data['user_data']['location'] = location
    reply_markup = build_fine_location_menu()
    await update.message.reply_text(
        bot_config["messages"]["fine_location_prompt"],
        reply_markup=reply_markup
    )
    return FINE_LOCATION

async def get_fine_location(update, context):
    user_id = update.message.from_user.id
    operation = context.user_data['user_data']['operation']
    amount = context.user_data['user_data']['amount']
    result = context.user_data['user_data']['result']
    location = context.user_data['user_data']['location']

    if not check_request_limit(user_id):
        message_text = (
            bot_config["messages"]["limit_exceeded"].format(limit=bot_config["request_limit"])
            if str(user_id) != bot_config["owner_id"]
            else bot_config["messages"]["limit_exceeded_owner"].format(limit=bot_config["request_limit"])
        )
        await update.message.reply_text(message_text)
        reply_markup = build_client_menu(user_id)
        await update.message.reply_text("Выберите операцию:", reply_markup=reply_markup)
        return CHOOSING

    if update.message.location:
        latitude = update.message.location.latitude
        longitude = update.message.location.longitude
        fine_location = f"Геолокация: https://maps.google.com/?q={latitude},{longitude}"
    elif update.message.text and "maps.google.com" in update.message.text:
        fine_location = update.message.text
    elif update.message.text == "Пропустить":
        fine_location = "Не указано"
    elif update.message.text == "Назад":
        currency = bot_config["currencies"].get(context.user_data['user_data']['operation'], "unknown")
        reply_markup = build_location_menu(user_id)
        await update.message.reply_text(
            bot_config["messages"]["location_prompt"].format(result=context.user_data['user_data']['result'], currency=currency),
            reply_markup=reply_markup
        )
        return LOCATION
    else:
        await update.message.reply_text(
            "Пожалуйста, отправьте геолокацию, ссылку на Google Maps или нажмите 'Пропустить'."
        )
        return FINE_LOCATION

    context.user_data['user_data']['fine_location'] = fine_location
    active_order = {
        'operation': operation,
        'amount': amount,
        'result': result,
        'location': location,
        'fine_location': fine_location
    }
    save_user_data(user_id, active_order)
    log_request(user_id)

    async def send_message_with_retry(chat_id, text, parse_mode=None, reply_markup=None, retries=5):
        for attempt in range(retries):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
                logger.info(f"Сообщение успешно отправлено в чат {chat_id}")
                return True
            except telegram.error.TimedOut as e:
                logger.warning(f"Попытка {attempt + 1} из {retries} не удалась: {str(e)}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(5)
        return False

    try:
        # Форматируем сумму с разделителями и без копеек
        formatted_result = "{:,}".format(int(result)).replace(",", ".")
        
        # Определяем валюту
        currency_raw = operation.split('→')[1].strip() if '→' in operation else "unknown"
        currency_base = currency_raw.split()[0].lower()
        currency_corrections = {
            "рубли": "рубль",
            "рупии": "рупия",
            "донги": "донг",
            "юани": "юань",
            "баты": "бат",
            "usdt": "USDT"
        }
        currency_root = currency_corrections.get(currency_base, currency_base)
        if currency_root in ["usdt"]:
            currency = currency_root.upper()
        else:
            currency = numeral.choose_plural(int(result), (currency_root, currency_root + 'а', currency_root + 'ов'))

        reply_markup = build_client_menu(user_id)
        await send_message_with_retry(
            chat_id=user_id,
            text=bot_config["messages"]["request_accepted"].format(
                operation=operation,
                amount=amount,
                result=formatted_result,  # Используем отформатированную сумму
                currency=currency,       # Используем правильную валюту
                location=location,
                fine_location=fine_location
            ),
            reply_markup=reply_markup
        )

        active_order_data, _, referrer_id = get_user_data(user_id)
        admin_chat_id = str(referrer_id) if referrer_id else bot_config["owner_id"]

        def escape_md(text):
            special_chars = r'_*[]()~`>#+-=|{}.!'
            for char in special_chars:
                text = text.replace(char, f"\\{char}")
            return text

        user = update.message.from_user
        if user.username:
            user_link = f"@{escape_md(user.username)}"
        else:
            user_link = f"[Пользователь](tg://user?id={user_id})"

        admin_message = bot_config["messages"]["admin_request"].format(
            user_link=user_link,
            operation=escape_md(operation),
            amount=escape_md(str(amount)),
            result=escape_md(f"{formatted_result} {currency}"),  # Форматируем для админа
            location=escape_md(location),
            fine_location=escape_md(fine_location)
        )

        await send_message_with_retry(
            chat_id=admin_chat_id,
            text=admin_message,
            parse_mode='MarkdownV2'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке заявки: {str(e)}")
        await send_message_with_retry(
            chat_id=user_id,
            text="Извините, произошла ошибка при обработке заявки. Попробуйте снова или обратитесь в поддержку."
        )
        await send_message_with_retry(
            chat_id=bot_config["owner_id"],
            text=f"Ошибка при отправке заявки: {str(e)}"
        )

    return CHOOSING

async def cancel(update, context):
    await update.message.reply_text("Процесс обмена отменён. Для начала нового обмена выберите операцию.", reply_markup=build_client_menu(update.message.from_user.id))
    return ConversationHandler.END

async def error_handler(update, context):
    logger.error(f"Произошла ошибка: {context.error}")
    await context.bot.send_message(
        bot_config["owner_id"],
        f"Ошибка бота: {str(context.error)}\nПользователь: {update.message.from_user.id if update and update.message else 'Неизвестно'}"
    )
    if update and update.message:
        reply_markup = build_client_menu(update.message.from_user.id)
        await update.message.reply_text(
            f"Извините, произошла ошибка: {str(context.error)}. Пожалуйста, попробуйте снова или обратитесь в поддержку.",
            reply_markup=reply_markup
        )

async def reload_config(update, context):
    user_id = update.message.from_user.id
    if str(user_id) != bot_config["owner_id"]:
        await update.message.reply_text("Эта команда только для владельца!")
        return
    try:
        from bot_config import load_config  # Исправляем импорт
        load_config()  # Перезагружаем конфиг
        logger.info(f"Конфигурация перезагружена пользователем {user_id}")
        await update.message.reply_text("Конфигурация успешно перезагружена!")
    except Exception as e:
        logger.error(f"Ошибка при перезагрузке конфигурации пользователем {user_id}: {str(e)}")
        await update.message.reply_text(f"Ошибка при перезагрузке конфигурации: {str(e)}")

async def check_subscriptions(application):
    """Фоновая задача для проверки подписок и отправки уведомлений"""
    from bot_config import bot_config
    stop_event = asyncio.Event()  # Создаём событие для остановки

    # Сохраняем stop_event в application, чтобы можно было получить его при остановке
    application.stop_event = stop_event

    while not stop_event.is_set():  # Цикл работает, пока не установлен stop_event
        try:
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute('SELECT user_id, active_order FROM users WHERE active_order IS NOT NULL')
            users = c.fetchall()
            conn.close()

            current_time = datetime.now()
            for user_id, active_order in users:
                active_order_dict = json.loads(active_order)
                if 'admin_expiry' in active_order_dict:
                    expiry = datetime.strptime(active_order_dict['admin_expiry'], '%Y-%m-%d %H:%M:%S')
                    time_left = expiry - current_time
                    if timedelta(hours=23) < time_left <= timedelta(hours=24):
                        try:
                            await application.bot.send_message(
                                chat_id=user_id,
                                text="Ваша подписка заканчивается через 24 часа. Свяжитесь со своим менеджером для продления подписки."
                            )
                            logger.info(f"Отправлено уведомление об окончании подписки пользователю {user_id}")
                        except Exception as e:
                            logger.error(f"Не удалось отправить уведомление пользователю {user_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка в check_subscriptions: {str(e)}")
        
        # Ждём интервал, но проверяем stop_event каждую секунду
        for _ in range(bot_config["subscription_check_interval"]):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)

    logger.info("Фоновая задача check_subscriptions завершена")

def main():
    init_db()  # load_config() уже вызван в bot_config.py

    client_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, start)
        ],
        states={
            CHOOSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, choose_operation)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_amount)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_location)],
            FINE_LOCATION: [MessageHandler(filters.TEXT | filters.LOCATION, get_fine_location)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    admin_handler = get_admin_handler(cancel)

    application.add_handler(admin_handler)
    application.add_handler(client_handler)
    application.add_handler(CommandHandler('otp', activate_otp))
    application.add_handler(CommandHandler('reload_config', reload_config))
    application.add_error_handler(error_handler)

    def signal_handler(sig, frame):
        logger.info("Получен сигнал завершения, останавливаем бота...")
        if hasattr(application, 'stop_event'):
            application.stop_event.set()  # Устанавливаем stop_event
        application.stop_running()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Добавляем post_init для запуска фоновой задачи
    async def post_init(application):
        asyncio.ensure_future(check_subscriptions(application))
        logger.info("Фоновая задача check_subscriptions запущена")

    application.post_init = post_init

    logger.info("Запуск бота в режиме polling...")
    while True:
        try:
            application.run_polling(allowed_updates=["message", "callback_query"])
        except Exception as e:
            logger.error(f"Error: {e}. Restarting polling...")
            time.sleep(5)  # Пауза перед перезапуском

if __name__ == "__main__":
    main()