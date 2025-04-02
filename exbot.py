# -*- coding: utf-8 -*-
import telegram
import signal
import sys
import logging
import asyncio
import sqlite3
import time
import pytz
import telegram.ext
import inspect

from telegram.error import NetworkError, TimedOut, TelegramError
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

async def send_message_with_retry(context, chat_id, text, parse_mode=None, reply_markup=None, retries=3, timeout=10):
    for attempt in range(retries):
        try:
            await asyncio.wait_for(
                context.bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, reply_markup=reply_markup),
                timeout=timeout
            )
            logger.info(f"Сообщение успешно отправлено в чат {chat_id} с попытки {attempt + 1}")
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Тайм-аут при отправке сообщения в чат {chat_id}, попытка {attempt + 1} из {retries}")
        except telegram.error.TimedOut as e:
            logger.warning(f"Telegram TimedOut в чат {chat_id}, попытка {attempt + 1} из {retries}: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения в чат {chat_id}: {str(e)}")
            break
        if attempt < retries - 1:
            await asyncio.sleep(2)  # Пауза между попытками
    logger.error(f"Не удалось отправить сообщение в чат {chat_id} после {retries} попыток")
    return False

def build_client_menu(user_id):
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    logger.debug(f"build_client_menu: active_order={active_order}, type={type(active_order)}, referrer_id={referrer_id}, in_admin_mode={in_admin_mode}")
    if active_order is None and request_count == 0 and referrer_id is None:
        logger.warning(f"Не удалось получить данные пользователя {user_id} в build_client_menu, используем owner_id")
        referrer_id = bot_config["owner_id"]
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)
    reply_keyboard = [admin_data['active_pairs'][i:i+2] for i in range(0, len(admin_data['active_pairs']), 2)]
    is_owner = str(user_id) == bot_config["owner_id"]
    has_admin_expiry = False
    if active_order:
        try:
            logger.debug(f"Перед json.loads: active_order={active_order}, type={type(active_order)}")
            active_order_dict = json.loads(active_order) if isinstance(active_order, str) else active_order
            logger.debug(f"После json.loads: active_order_dict={active_order_dict}, type={type(active_order_dict)}")
            has_admin_expiry = isinstance(active_order_dict, dict) and 'admin_expiry' in active_order_dict
            logger.debug(f"Проверка has_admin_expiry: isinstance={isinstance(active_order_dict, dict)}, 'admin_expiry' in dict={'admin_expiry' in active_order_dict if isinstance(active_order_dict, dict) else 'N/A'}, has_admin_expiry={has_admin_expiry}")
            if has_admin_expiry:
                expiry = datetime.strptime(active_order_dict['admin_expiry'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
                logger.debug(f"expiry={expiry}, now={datetime.now(pytz.UTC)}")
                if datetime.now(pytz.UTC) > expiry:
                    logger.debug(f"Подписка истекла, удаляем admin_expiry из active_order_dict")
                    active_order_dict.pop('admin_expiry', None)
                    save_user_data(user_id, active_order_dict, referrer_id, in_admin_mode)
                    has_admin_expiry = False
                    logger.info(f"Подписка истекла для user_id={user_id}, 'admin_expiry' удалён")
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Ошибка парсинга active_order в build_client_menu для user_id={user_id}: {str(e)}, active_order={active_order}")
            active_order_dict = {}
            has_admin_expiry = False
    logger.info(f"build_client_menu для user_id={user_id}: is_owner={is_owner}, has_admin_expiry={has_admin_expiry}, active_order={active_order}")
    if is_owner or has_admin_expiry:
        reply_keyboard.append(["Админка"])
    return ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=False, resize_keyboard=True)

def build_amount_menu():
    return ReplyKeyboardMarkup([["Назад"]], one_time_keyboard=True, resize_keyboard=True)

def build_location_menu(user_id):
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)  # Полная распаковка
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

    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    if active_order is None and request_count == 0 and referrer_id is None:
        logger.warning(f"Не удалось получить данные пользователя {user_id}, создаём новую запись")
        save_user_data(user_id, None, referrer_id=bot_config["owner_id"], in_admin_mode=0)
        active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)

    if not active_order and not referrer_id:
        args = context.args
        if args and args[0].startswith("ref_"):
            try:
                referrer_id = int(args[0].replace("ref_", ""))
                save_user_data(user_id, None, referrer_id=referrer_id, in_admin_mode=0)
                logger.info(f"Пользователь {user_id} привязан к админу {referrer_id} через рефералку")
                user_link = f"@{user.username}" if user.username else f"[Пользователь](tg://user?id={user_id})"
                await send_message_with_retry(
                    context,
                    chat_id=referrer_id,
                    text=f"Новый пользователь {user_link} запустил бота через твою реферальную ссылку!"
                )
            except ValueError:
                logger.warning(f"Некорректный реферальный ID: {args[0]}")
                referrer_id = bot_config["owner_id"]
                save_user_data(user_id, None, referrer_id=referrer_id, in_admin_mode=0)

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

    try:
        if active_order or referrer_id:
            await send_message_with_retry(
                context,
                chat_id=user_id,
                text=bot_config["messages"]["welcome"].format(name=user.first_name),
                reply_markup=reply_markup
            )
        else:
            save_user_data(user_id, None, referrer_id)
            await send_message_with_retry(
                context,
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
    
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    if in_admin_mode:
        logger.info(f"Пользователь {user_id} в админке, перенаправляем в админ-панель")
        from ex_admin import build_main_menu
        reply_markup = build_main_menu(user_id)
        await update.message.reply_text(
            "Ты в админке! Сначала выйди, чтобы работать в клиентском меню. Выбери раздел:",
            reply_markup=reply_markup
        )
        return ADMIN_STATE

    choice = update.message.text
    logger.info(f"Выбор пользователя: {choice}")

    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    # Отладка active_order
    logger.debug(f"active_order для user_id={user_id}: {active_order}, тип: {type(active_order)}")
    
    # Проверка прав доступа к админке
    is_owner = str(user_id) == bot_config["owner_id"]
    has_admin_expiry = False
    if active_order:
        try:
            active_order_dict = json.loads(active_order) if isinstance(active_order, str) else active_order
            has_admin_expiry = isinstance(active_order_dict, dict) and 'admin_expiry' in active_order_dict
        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"Ошибка парсинга active_order для user_id={user_id}: {str(e)}, active_order={active_order}")
            active_order_dict = {}
            has_admin_expiry = False

    if (is_owner or has_admin_expiry) and choice == "Админка":
        logger.info("Отправка кнопки для входа в админку")
        reply_markup = build_admin_entry_menu()
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text="Нажми ниже, чтобы войти в админ-панель:",
            reply_markup=reply_markup
        )
        return CHOOSING
    elif choice == "Назад":
        logger.info("Возврат к выбору операции")
        reply_markup = build_client_menu(user_id)
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text=bot_config["messages"]["welcome"].format(name=update.message.from_user.first_name),
            reply_markup=reply_markup
        )
        return CHOOSING
    elif choice not in admin_data['active_pairs']:
        logger.info("Некорректный выбор, просьба выбрать из предложенных")
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text="Пожалуйста, выберите одну из предложенных опций ниже."
        )
        return CHOOSING

    context.user_data.setdefault('user_data', {})
    context.user_data['user_data']['operation'] = choice
    logger.info("Отправка запроса суммы")
    await send_message_with_retry(
        context,
        chat_id=user_id,
        text=bot_config["messages"]["amount_prompt"].format(operation=choice),
        reply_markup=build_amount_menu()
    )
    logger.info("Завершение выбора операции")
    return AMOUNT

from pytils import numeral  # Добавляем импорт в начало файла

async def get_amount(update, context):
    user_id = update.message.from_user.id
    logger.debug(f"Получен ввод суммы от {user_id}: '{update.message.text}'")
    
    if update.message.text == "Назад":
        context.user_data.pop('user_data', None)
        reply_markup = build_client_menu(user_id)
        await update.message.reply_text(
            bot_config["messages"]["welcome"].format(name=update.message.from_user.first_name),
            reply_markup=reply_markup
        )
        logger.info(f"Возврат в CHOOSING для {user_id}")
        return CHOOSING

    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]
    admin_data = get_admin_data(admin_id)

    try:
        amount = float(update.message.text.strip())
        logger.debug(f"Сумма {amount} успешно преобразована для {user_id}")
        
        if amount <= 0:
            await update.message.reply_text(bot_config["messages"]["negative_amount"])
            logger.info(f"Отрицательная сумма, остаёмся в AMOUNT для {user_id}")
            return AMOUNT
        if amount > 1_000_000_000:
            await update.message.reply_text("Сумма слишком большая! Максимум — 1 миллиард.")
            logger.info(f"Сумма превышает лимит, остаёмся в AMOUNT для {user_id}")
            return AMOUNT
        
        context.user_data.setdefault('user_data', {})
        context.user_data['user_data']['amount'] = amount

        operation = context.user_data['user_data']['operation']
        rate = admin_data['rates'].get(operation, 1)
        result = amount * rate
        context.user_data['user_data']['result'] = result
        logger.debug(f"Результат расчёта для {operation}: {result}")

        # Форматируем результат: до миллиона — без изменений, миллионы — с точками
        formatted_result = str(int(result)) if int(result) < 1000000 else "{:,}".format(int(result)).replace(",", ".")
        currency = get_currency(operation, result)
        
        reply_markup = build_location_menu(user_id)
        logger.debug(f"Меню локаций построено для {user_id}")
        
        text = f"Вы получите {formatted_result} {currency}.\nКуда доставить деньги? Выберите локацию:"
        logger.debug(f"Текст сообщения: {text}")
        
        success = await send_message_with_retry(
            context,
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup
        )
        logger.debug(f"Результат отправки: {success}")
        
        if not success:
            logger.error(f"Не удалось отправить сообщение о локации для {user_id}")
            await update.message.reply_text("Ошибка при отправке сообщения. Попробуйте снова.")
            return AMOUNT
        
        logger.info(f"Успешно переходим в LOCATION для {user_id}")
        return LOCATION
    except ValueError as ve:
        logger.debug(f"ValueError в get_amount для {user_id}: {str(ve)}")
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text=bot_config["messages"]["invalid_amount"]
        )
        return AMOUNT
    except Exception as e:
        logger.error(f"Неожиданная ошибка в get_amount для {user_id}: {str(e)}")
        await update.message.reply_text("Произошла ошибка. Попробуйте снова.")
        return AMOUNT

# Новая функция для получения валюты с правильным склонением
def get_currency(operation, result):
    currency_raw = operation.split('→')[1].strip() if '→' in operation else "unknown"
    currency_base = currency_raw.split()[0].lower()
    currency_corrections = {
        "рубли": "рубль", "рупии": "рупия", "донги": "донг", "юани": "юань", "баты": "бат", "usdt": "USDT"
    }
    currency_root = currency_corrections.get(currency_base, currency_base)
    if currency_root == "usdt":
        return "USDT"
    else:
        if currency_root == "рубль":
            return numeral.choose_plural(int(result), ("рубль", "рубля", "рублей"))
        elif currency_root == "рупия":
            return numeral.choose_plural(int(result), ("рупия", "рупии", "рупий"))
        elif currency_root == "донг":
            return numeral.choose_plural(int(result), ("донг", "донга", "донгов"))
        elif currency_root == "юань":
            return numeral.choose_plural(int(result), ("юань", "юаня", "юаней"))
        elif currency_root == "бат":
            return numeral.choose_plural(int(result), ("бат", "бата", "батов"))
        else:
            return currency_root

async def get_location(update, context):
    user_id = update.message.from_user.id
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
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
    await send_message_with_retry(
        context,
        chat_id=user_id,
        text=bot_config["messages"]["fine_location_prompt"],
        reply_markup=reply_markup
    )
    return FINE_LOCATION

async def get_fine_location(update, context):
    user_id = update.message.from_user.id
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    admin_id = referrer_id if referrer_id else bot_config["owner_id"]

    if not context.user_data.get('user_data'):
        logger.warning(f"Нет user_data для user_id={user_id}, возвращаем в главное меню")
        reply_markup = build_client_menu(user_id)
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text="Что-то пошло не так. Выбери операцию заново:",
            reply_markup=reply_markup
        )
        return CHOOSING

    operation = context.user_data['user_data']['operation']
    amount = context.user_data['user_data']['amount']
    result = context.user_data['user_data']['result']
    location = context.user_data['user_data']['location']

    if not check_request_limit(user_id):
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text=bot_config["messages"]["limit_exceeded"].format(limit=bot_config["request_limit"])
        )
        reply_markup = build_client_menu(user_id)
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text="Выберите операцию:",
            reply_markup=reply_markup
        )
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
        reply_markup = build_location_menu(user_id)
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text=f"Вы получите {int(result)} {get_currency(operation, result)}. Куда доставить деньги? Выберите локацию:",
            reply_markup=reply_markup
        )
        return LOCATION
    else:
        await send_message_with_retry(
            context,
            chat_id=user_id,
            text="Пожалуйста, отправьте геолокацию, ссылку на Google Maps или нажмите 'Пропустить'."
        )
        return FINE_LOCATION

    # Формируем заявку
    active_order_dict = {
        'operation': operation,
        'amount': amount,
        'result': result,
        'location': location,
        'fine_location': fine_location
    }
    if active_order:
        try:
            current_order = json.loads(active_order)
            if 'admin_expiry' in current_order:
                active_order_dict['admin_expiry'] = current_order['admin_expiry']
        except json.JSONDecodeError:
            logger.error(f"Ошибка парсинга active_order для user_id={user_id}: {active_order}")

    save_user_data(user_id, active_order_dict, referrer_id=referrer_id, in_admin_mode=in_admin_mode)  # Передаём словарь
    log_request(user_id)

    # Форматируем числа: до миллиона — без изменений, миллионы — с точками
    formatted_amount = str(int(amount)) if int(amount) < 1000000 else "{:,}".format(int(amount)).replace(",", ".")
    formatted_result = str(int(result)) if int(result) < 1000000 else "{:,}".format(int(result)).replace(",", ".")
    currency = get_currency(operation, result)

    # Отправляем подтверждение пользователю
    reply_markup = build_client_menu(user_id)
    user_message = bot_config["messages"]["request_accepted"].format(
        operation=operation,
        amount=formatted_amount,
        result=formatted_result,
        currency=currency,
        location=location,
        fine_location=fine_location
    )
    await send_message_with_retry(
        context,
        chat_id=user_id,
        text=user_message,
        reply_markup=reply_markup
    )

    # Определяем, кому отправлять уведомление
    if in_admin_mode and referrer_id == user_id:  # Админ тестирует бота
        admin_chat_id = user_id
    else:
        admin_chat_id = referrer_id if referrer_id else bot_config["owner_id"]

    # Функция экранирования для MarkdownV2
    def escape_md(text):
        special_chars = r'_*[]()~`>#+-=|{}.!'
        for char in special_chars:
            text = text.replace(char, f"\\{char}")
        return text

    # Формируем user_link без лишнего экранирования ссылки
    user = update.message.from_user
    if user.username:
        user_link = f"@{user.username}"  # В HTML экранирование не нужно для @username
    else:
        user_link = f'<a href="tg://user?id={user_id}">Пользователь</a>'

    # Форматируем сообщение в HTML
    admin_message = bot_config["messages"]["admin_request"].format(
        user_link=user_link,
        operation=operation,
        amount=formatted_amount,
        result=f"{formatted_result} {currency}",
        location=location,
        fine_location=fine_location
    )
    logger.debug(f"Текст уведомления админу (HTML): {admin_message}")

    success = await send_message_with_retry(
        context,
        chat_id=admin_chat_id,
        text=admin_message,
        parse_mode='HTML'  # Меняем на HTML
    )
    if success:
        logger.info(f"Уведомление отправлено в чат {admin_chat_id}")
    else:
        logger.error(f"Не удалось отправить уведомление админу в чат {admin_chat_id}")

    # Очищаем временные данные
    context.user_data.pop('user_data', None)
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
    from bot_config import bot_config
    stop_event = asyncio.Event()
    application.stop_event = stop_event

    while not stop_event.is_set():
        try:
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute('SELECT user_id, active_order FROM users WHERE active_order IS NOT NULL')
            users = c.fetchall()
            conn.close()

            current_time = datetime.now(pytz.UTC)
            for user_id, active_order in users:
                active_order_dict = json.loads(active_order)
                if 'admin_expiry' in active_order_dict:
                    expiry = datetime.strptime(active_order_dict['admin_expiry'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
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
        
        for _ in range(bot_config["subscription_check_interval"]):
            if stop_event.is_set():
                break
            await asyncio.sleep(1)

    logger.info("Фоновая задача check_subscriptions завершена")

async def main():
    init_db()  # Конфиг уже загружен в bot_config.py

    # Инициализируем приложение
    await application.initialize()

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
            application.stop_event.set()
        asyncio.get_event_loop().create_task(application.stop())
        asyncio.get_event_loop().create_task(application.shutdown())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Запускаем фоновую задачу
    async def post_init(application):
        asyncio.create_task(check_subscriptions(application))
        logger.info("Фоновая задача check_subscriptions запущена")

    application.post_init = post_init

    logger.info("Запуск бота в режиме polling...")
    max_retries = 10
    base_delay = 5
    max_delay = 300
    retry_count = 0

    while retry_count < max_retries:
        try:
            await application.start()  # Явно стартуем приложение
            await application.updater.start_polling(
                allowed_updates=["message", "callback_query"],
                drop_pending_updates=True
            )
            # Ожидаем завершения (например, по сигналу)
            await application.stop_event.wait()
            await application.updater.stop()
            await application.stop()
            break  # Успешное завершение
        except (NetworkError, TimedOut, TelegramError) as e:
            retry_count += 1
            logger.error(f"Ошибка сети: {str(e)}. Попытка {retry_count}/{max_retries}")
            delay = min(base_delay * (2 ** (retry_count - 1)), max_delay)
            logger.info(f"Ожидание {delay} секунд перед перезапуском...")
            await asyncio.sleep(delay)
            # Останавливаем и переинициализируем только при необходимости
            try:
                await application.updater.stop()
                await application.stop()
            except Exception as stop_error:
                logger.warning(f"Ошибка при остановке: {str(stop_error)}")
            await application.initialize()  # Переинициализируем
        except Exception as e:
            logger.error(f"Критическая ошибка: {str(e)}. Остановка бота.")
            await application.updater.stop()
            await application.stop()
            break

    # Финальная остановка
    try:
        await application.shutdown()
        logger.info("Бот завершил работу")
    except Exception as shutdown_error:
        logger.error(f"Ошибка при завершении работы: {str(shutdown_error)}")

if __name__ == "__main__":
    asyncio.run(main())
