import json
import sqlite3
import logging
import pytz
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    CommandHandler
)
from ex_owner import generate_otp, check_subscription
from utils import get_user_data, save_otp_data, save_user_data, get_admin_data, save_admin_data

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния для ConversationHandler
ADMIN_STATE, ADD_PAIR, ADD_LOCATION, EDIT_RATES, SET_RATE, EDIT_PAIRS, EDIT_LOCATIONS, GENERATE_OTP, BROADCAST = range(9)

def build_admin_entry_menu():
    keyboard = [[InlineKeyboardButton("Войти в админку 🔐", callback_data='enter_admin')]]
    return InlineKeyboardMarkup(keyboard)

def build_main_menu(user_id):
    from exbot import bot_config  # Локальный импорт
    keyboard = [
        [InlineKeyboardButton("Курсы ⚙️", callback_data='edit_rates'),
         InlineKeyboardButton("Локации 🌍", callback_data='edit_locations')],
        [InlineKeyboardButton("Пары 💱", callback_data='edit_pairs'),
         InlineKeyboardButton("Установить курс", callback_data='set_rate')],
        [InlineKeyboardButton("Добавить/удалить локацию", callback_data='manage_location')],
        [InlineKeyboardButton("Добавить/удалить пару", callback_data='manage_pair')],
        [InlineKeyboardButton("Рассылка 📩", callback_data='broadcast')],
        [InlineKeyboardButton("Выход 🚪", callback_data='exit')]
    ]
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)  # Исправлено: распаковываем 4 значения
    if str(user_id) == bot_config["owner_id"]:
        keyboard.insert(0, [InlineKeyboardButton("Сгенерировать OTP", callback_data='generate_otp')])
        keyboard.insert(1, [InlineKeyboardButton("Проверить подписку 🔍", callback_data='check_subscription')])
        keyboard.insert(2, [InlineKeyboardButton("Моя реф. ссылка 🔗", callback_data='generate_ref_link')])
        keyboard.insert(3, [InlineKeyboardButton("Перезагрузить конфиг 🔄", callback_data='reload_config')])
    elif active_order and 'admin_expiry' in json.loads(active_order):
        keyboard.insert(0, [InlineKeyboardButton("Проверить подписку 🔍", callback_data='check_subscription')])
        keyboard.insert(1, [InlineKeyboardButton("Моя реф. ссылка 🔗", callback_data='generate_ref_link')])
    return InlineKeyboardMarkup(keyboard)

def build_locations_menu(admin_id):
    admin_data = get_admin_data(admin_id)
    keyboard = [
        [InlineKeyboardButton(f"{loc} ✅" if loc in admin_data['active_locations'] else f"{loc} ❌", callback_data=f"toggle_loc_{loc}")
         for loc in admin_data['locations'][i:i+2]]
        for i in range(0, len(admin_data['locations']), 2)
    ]
    keyboard.append([
        InlineKeyboardButton("Сбросить 🔄", callback_data='reset_locations'),
        InlineKeyboardButton("Сохранить ✅", callback_data='save_locations'),
        InlineKeyboardButton("Назад ⬅️", callback_data='back_to_main')
    ])
    return InlineKeyboardMarkup(keyboard)

def build_pairs_menu(admin_id):
    admin_data = get_admin_data(admin_id)
    active_count = len(admin_data['active_pairs'])
    total_count = len(admin_data['pairs'])
    keyboard = [
        [InlineKeyboardButton(f"{pair} ✅" if pair in admin_data['active_pairs'] else f"{pair} ❌", callback_data=f"toggle_pair_{pair}")]
        for pair in admin_data['pairs']
    ]
    keyboard.append([
        InlineKeyboardButton("Сбросить 🔄", callback_data='reset_pairs'),
        InlineKeyboardButton(f"Сохранить ({active_count}/{total_count}) ✅", callback_data='save_pairs'),
        InlineKeyboardButton("Назад ⬅️", callback_data='back_to_main')
    ])
    return InlineKeyboardMarkup(keyboard)

def build_rates_menu(admin_id):
    admin_data = get_admin_data(admin_id)
    keyboard = [
        [InlineKeyboardButton(f"{rate_key}: {rate_value:.2f}", callback_data=f"set_rate_{rate_key}")]
        for rate_key, rate_value in admin_data['rates'].items()
    ]
    keyboard.append([
        InlineKeyboardButton("Сохранить ✅", callback_data='save_rates'),
        InlineKeyboardButton("Назад ⬅️", callback_data='back_to_main')
    ])
    return InlineKeyboardMarkup(keyboard)

async def add_location(update, context):
    user_id = update.message.from_user.id
    admin_id = user_id  # Админ редактирует свои данные
    admin_data = get_admin_data(admin_id)

    logger.info(f"Начало обработки ввода новой локации для admin_id={admin_id}")
    if not update.message:
        logger.error("Обновление не содержит сообщения!")
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Произошла ошибка. Попробуй снова.")
        return ADMIN_STATE
    new_location = update.message.text.strip()
    logger.info(f"Получен текст: '{new_location}'")
    if not new_location:
        logger.info("Локация пустая, запрашиваем повторно")
        await update.message.reply_text("Локация не может быть пустой! Введи название, например, Гоа.")
        return ADD_LOCATION
    if new_location in admin_data['active_locations']:
        logger.info(f"Локация '{new_location}' уже существует")
        await update.message.reply_text("Эта локация уже существует!")
        return ADMIN_STATE
    admin_data['locations'].append(new_location)
    admin_data['active_locations'].append(new_location)
    save_admin_data(admin_id, admin_data)
    logger.info(f"Локация '{new_location}' добавлена")
    await update.message.reply_text(f"Локация '{new_location}' добавлена!", reply_markup=build_main_menu(user_id))
    return ADMIN_STATE

async def add_pair(update, context):
    user_id = update.message.from_user.id
    admin_id = user_id  # Админ редактирует свои данные
    admin_data = get_admin_data(admin_id)

    logger.info(f"Обработка ввода новой пары для admin_id={admin_id}")
    new_pair_input = update.message.text.strip()
    if not new_pair_input:
        await update.message.reply_text("Пара не может быть пустой! Введи название, например, Рубли - Доллары.")
        return ADD_PAIR
    
    new_pair = new_pair_input.replace('-', '→').replace('  ', ' ').strip()
    if new_pair in admin_data['active_pairs']:
        await update.message.reply_text("Эта пара уже существует!")
        return ADMIN_STATE
    
    try:
        admin_data['pairs'].append(new_pair)
        admin_data['active_pairs'].append(new_pair)
        admin_data['rates'][new_pair] = 1.0
        save_admin_data(admin_id, admin_data)
        logger.info(f"Пара '{new_pair}' добавлена для admin_id={admin_id}")
        await update.message.reply_text(
            f"Пара '{new_pair}' добавлена!",
            reply_markup=build_main_menu(user_id)
        )
    except Exception as e:
        logger.error(f"Ошибка при добавлении пары '{new_pair}' для admin_id={admin_id}: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка при добавлении пары. Попробуй снова или обратись в поддержку.",
            reply_markup=build_main_menu(user_id)
        )
    
    return ADMIN_STATE

async def admin_callback(update, context):
    query = update.callback_query
    if not query:
        logger.error("Нет callback_query в update!")
        await update.message.reply_text("Ошибка обработки команды. Попробуй снова.")
        return ConversationHandler.END

    try:
        await query.answer()
        logger.info(f"Callback обработан: user_id={query.from_user.id}, choice={query.data}")
    except Exception as e:
        logger.error(f"Ошибка при ответе на callback для user_id={query.from_user.id}: {str(e)}")
        return ConversationHandler.END

    choice = query.data
    user_id = query.from_user.id
    admin_id = user_id
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    logger.info(f"Состояние: user_id={user_id}, in_admin_mode={in_admin_mode}, choice={choice}")

    from exbot import bot_config
    owner_id = bot_config["owner_id"]

    # Проверка: если не в админке и не пытаемся войти, блокируем действия админки
    if choice != 'enter_admin' and not in_admin_mode:
        await query.message.reply_text("Сначала войди в админку через 'Войти в админку'!")
        return ConversationHandler.END

    if choice == 'enter_admin':
        if str(user_id) != owner_id:
            if not active_order:
                await query.message.reply_text("Бро, это только для админов!")
                return ConversationHandler.END
            try:
                active_order_dict = json.loads(active_order) if isinstance(active_order, str) else {}
                if not isinstance(active_order_dict, dict) or 'admin_expiry' not in active_order_dict:
                    await query.message.reply_text("Бро, это только для админов!")
                    return ConversationHandler.END
                expiry = datetime.strptime(active_order_dict['admin_expiry'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
                if datetime.now(pytz.UTC) > expiry:
                    active_order_dict.pop('admin_expiry', None)
                    save_user_data(user_id, active_order_dict, referrer_id, in_admin_mode=0)
                    await query.message.reply_text("Бро, твоя админская подписка истекла!")
                    return ConversationHandler.END
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Ошибка парсинга active_order для user_id={user_id}: {str(e)}, active_order={active_order}")
                await query.message.reply_text("Ошибка данных. Попробуй снова или пиши в поддержку.")
                return ConversationHandler.END
        # Устанавливаем in_admin_mode=1 при входе
        active_order_dict = json.loads(active_order) if active_order and isinstance(active_order, str) else {}
        save_user_data(user_id, active_order_dict, referrer_id, in_admin_mode=1)
        # Проверяем, что данные обновились
        _, _, _, in_admin_mode_check = get_user_data(user_id)
        logger.debug(f"После сохранения: in_admin_mode={in_admin_mode_check}")
        if not in_admin_mode_check:
            logger.error(f"Не удалось установить in_admin_mode=1 для user_id={user_id}")
            await query.message.reply_text("Ошибка входа в админку. Попробуй снова или пиши в поддержку.")
            return ConversationHandler.END
        await query.message.reply_text(
            "Переключаемся в админку...",
            reply_markup=ReplyKeyboardRemove()
        )
        reply_markup = build_main_menu(user_id)
        await query.message.reply_text(
            "Админ-панель: выбери раздел",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return ADMIN_STATE

    elif choice == 'exit':
        active_order_dict = json.loads(active_order) if active_order and isinstance(active_order, str) else {}
        save_user_data(user_id, active_order_dict, referrer_id, in_admin_mode=0)
        from exbot import build_client_menu
        reply_markup = build_client_menu(user_id)
        await query.message.reply_text(
            bot_config["messages"]["welcome"].format(name=query.from_user.first_name),
            reply_markup=reply_markup
        )
        await query.message.delete()
        return ConversationHandler.END

        # Остальной код остаётся без изменений

    elif choice == 'manage_location':
        keyboard = [
            [InlineKeyboardButton("Добавить", callback_data='add_location')],
            [InlineKeyboardButton("Удалить", callback_data='remove_location')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Что ты хочешь сделать с локациями?", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE

    elif choice == 'manage_pair':
        keyboard = [
            [InlineKeyboardButton("Добавить", callback_data='add_pair')],
            [InlineKeyboardButton("Удалить", callback_data='remove_pair')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Что ты хочешь сделать с парами?", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE

    elif choice == 'add_location':
        await query.edit_message_text("Введите новую локацию (например, 'Гоа'):")
        return ADD_LOCATION

    elif choice == 'add_pair':
        await query.edit_message_text("Введите новую валютную пару (например, 'Рубли → Доллары'):")
        return ADD_PAIR

    elif choice == 'edit_rates':
        return await edit_rates_handler(update, context, admin_id)

    elif choice == 'edit_pairs':
        return await edit_pairs_handler(update, context, admin_id)

    elif choice == 'edit_locations':
        return await edit_locations_handler(update, context, admin_id)

    elif choice == 'set_rate':
        admin_data = get_admin_data(admin_id)
        rates_text = "\n".join([f"*{k}*: {v:.2f}" for k, v in admin_data['rates'].items()])
        reply_markup = build_rates_menu(admin_id)
        await query.edit_message_text(
            f"📊 *Текущие курсы:*\n{rates_text}\nВыбери пару для редактирования:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return SET_RATE

    elif choice == 'generate_otp':
        if str(user_id) != owner_id:
            await query.edit_message_text("Только владелец может генерировать OTP!")
            return ADMIN_STATE
        keyboard = [
            [InlineKeyboardButton("7 дней", callback_data='generate_otp_7')],
            [InlineKeyboardButton("30 дней", callback_data='generate_otp_30')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выбери срок действия OTP:", reply_markup=reply_markup, parse_mode='Markdown')
        return GENERATE_OTP

    elif choice == 'generate_otp_7':
        if str(user_id) == owner_id:
            otp, expiry = generate_otp(7)
            save_otp_data(otp, None, expiry.strftime('%Y-%m-%d %H:%M:%S'), 7)
            await query.edit_message_text(
                f"Сгенерирован OTP\nСрок действия: 7 дней\nКод: `/otp {otp}`\nСкопируй и отправь новому админу.",
                reply_markup=build_main_menu(user_id),
                parse_mode='Markdown'
            )
        return ADMIN_STATE

    elif choice == 'generate_otp_30':
        if str(user_id) == owner_id:
            otp, expiry = generate_otp(30)
            save_otp_data(otp, None, expiry.strftime('%Y-%m-%d %H:%M:%S'), 30)
            await query.edit_message_text(
                f"Сгенерирован OTP\nСрок действия: 30 дней\nКод: `/otp {otp}`\nСкопируй и отправь новому админу.",
                reply_markup=build_main_menu(user_id),
                parse_mode='Markdown'
            )
        return ADMIN_STATE

    elif choice == 'check_subscription':
        await check_subscription(update, context)
        await query.message.reply_text("Вернулся в админку!", reply_markup=build_main_menu(user_id))
        return ADMIN_STATE

    elif choice == 'generate_ref_link':
        ref_link = f"https://t.me/goa_exchangeBot?start=ref_{user_id}"
        await query.edit_message_text(
            f'<a href="{ref_link}">Мой бот обменник</a>\nСкопируй и отправь друзьям!',
            reply_markup=build_main_menu(user_id),
            parse_mode='HTML',
            disable_web_page_preview=True
        )
        return ADMIN_STATE

    elif choice == 'broadcast':
        await query.edit_message_text("Введи текст для рассылки своим клиентам:")
        return BROADCAST

    elif choice == 'reload_config':
        from bot_config import load_config
        try:
            load_config()
            await query.edit_message_text("Конфигурация успешно перезагружена!", reply_markup=build_main_menu(user_id))
            logger.info(f"Конфигурация перезагружена пользователем {user_id}")
        except Exception as e:
            logger.error(f"Ошибка при перезагрузке конфигурации: {str(e)}")
            await query.edit_message_text(f"Ошибка при перезагрузке: {str(e)}", reply_markup=build_main_menu(user_id))
        return ADMIN_STATE

    elif choice == 'back_to_main':
        reply_markup = build_main_menu(user_id)
        await query.edit_message_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE

    elif choice.startswith('delete_pair_'):
        admin_data = get_admin_data(admin_id)
        pair_to_delete = choice.replace('delete_pair_', '')
        if pair_to_delete in admin_data['pairs']:
            admin_data['pairs'].remove(pair_to_delete)
            if pair_to_delete in admin_data['active_pairs']:
                admin_data['active_pairs'].remove(pair_to_delete)
            if pair_to_delete in admin_data['rates']:
                del admin_data['rates'][pair_to_delete]
            save_admin_data(admin_id, admin_data)
            await query.edit_message_text(f"Пара '{pair_to_delete}' удалена!", reply_markup=build_main_menu(user_id))
        else:
            await query.edit_message_text("Пара не найдена!", reply_markup=build_main_menu(user_id))
        return ADMIN_STATE

    elif choice.startswith('delete_location_'):
        admin_data = get_admin_data(admin_id)
        location_to_delete = choice.replace('delete_location_', '')
        if location_to_delete in admin_data['locations']:
            admin_data['locations'].remove(location_to_delete)
            if location_to_delete in admin_data['active_locations']:
                admin_data['active_locations'].remove(location_to_delete)
            save_admin_data(admin_id, admin_data)
            await query.edit_message_text(f"Локация '{location_to_delete}' удалена!", reply_markup=build_main_menu(user_id))
        else:
            await query.edit_message_text("Локация не найдена!", reply_markup=build_main_menu(user_id))
        return ADMIN_STATE

    elif choice == 'remove_pair':
        admin_data = get_admin_data(admin_id)
        if not admin_data['pairs']:
            await query.edit_message_text("Нет пар для удаления!", reply_markup=build_main_menu(user_id))
            return ADMIN_STATE
        keyboard = [
            [InlineKeyboardButton(pair, callback_data=f'delete_pair_{pair}') for pair in admin_data['pairs'][i:i+2]]
            for i in range(0, len(admin_data['pairs']), 2)
        ]
        keyboard.append([InlineKeyboardButton("Назад ⬅️", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выбери пару для удаления:", reply_markup=reply_markup, parse_mode='Markdown')
        return EDIT_PAIRS

    elif choice == 'remove_location':
        admin_data = get_admin_data(admin_id)
        if not admin_data['locations']:
            await query.edit_message_text("Нет локаций для удаления!", reply_markup=build_main_menu(user_id))
            return ADMIN_STATE
        keyboard = [
            [InlineKeyboardButton(loc, callback_data=f'delete_location_{loc}') for loc in admin_data['locations'][i:i+2]]
            for i in range(0, len(admin_data['locations']), 2)
        ]
        keyboard.append([InlineKeyboardButton("Назад ⬅️", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выбери локацию для удаления:", reply_markup=reply_markup, parse_mode='Markdown')
        return EDIT_LOCATIONS

    logger.warning(f"Неизвестный choice: {choice}")
    return ADMIN_STATE

async def edit_rates_handler(update, context, admin_id):
    query = update.callback_query
    try:
        await query.answer()
        logger.info(f"edit_rates_handler: Ответ на callback-запрос отправлен для user_id={admin_id}")
    except Exception as e:
        logger.error(f"Ошибка при ответе на callback-запрос в edit_rates_handler для user_id={admin_id}: {str(e)}")
        return ConversationHandler.END

    try:
        admin_data = get_admin_data(admin_id)
        logger.info(f"admin_data['rates'] для user_id={admin_id}: {admin_data['rates']}")
        rates_text = "\n".join([f"*{k}*: {v:.2f}" for k, v in admin_data['rates'].items()])
        reply_markup = build_rates_menu(admin_id)
        await query.message.reply_text(
            f"📊 *Текущие курсы:*\n{rates_text}\nВыбери пару для редактирования:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        logger.info(f"Сообщение с курсами отправлено для user_id={admin_id}")
        return EDIT_RATES
    except Exception as e:
        logger.error(f"Ошибка в edit_rates_handler для user_id={admin_id}: {str(e)}")
        try:
            await query.message.reply_text(
                "Произошла ошибка при загрузке курсов. Попробуй снова.",
                reply_markup=build_main_menu(admin_id)
            )
        except Exception as reply_error:
            logger.error(f"Ошибка при отправке сообщения об ошибке для user_id={admin_id}: {str(reply_error)}")
        return ADMIN_STATE

async def rates_callback(update, context):
    query = update.callback_query
    await query.answer()
    choice = query.data
    admin_id = query.from_user.id
    logger.info(f"rates_callback: choice={choice}, user_id={admin_id}")

    if choice.startswith('set_rate_'):
        rate_key = choice.replace('set_rate_', '')
        logger.info(f"Выбрана пара для редактирования: {rate_key}")
        admin_data = get_admin_data(admin_id)
        logger.info(f"Доступные пары в rates: {list(admin_data['rates'].keys())}")
        if rate_key not in admin_data['rates']:
            logger.error(f"Пара {rate_key} не найдена в rates!")
            await query.message.reply_text(
                f"Ошибка: пара *{rate_key}* не найдена. Попробуй снова.",
                reply_markup=build_rates_menu(admin_id),
                parse_mode='Markdown'
            )
            return EDIT_RATES
        try:
            context.user_data['editing_rate'] = rate_key
            logger.info(f"Сохранён editing_rate: {context.user_data['editing_rate']}")
            await query.message.reply_text(
                f"Введи новый курс для пары *{rate_key}* (например, 0.85):",
                parse_mode='Markdown'
            )
            logger.info(f"Сообщение о вводе курса отправлено для {rate_key}")
            return SET_RATE
        except Exception as e:
            logger.error(f"Ошибка при выборе пары {rate_key}: {str(e)}")
            await query.message.reply_text(
                "Произошла ошибка при выборе пары. Попробуй снова.",
                reply_markup=build_main_menu(admin_id)
            )
            return ADMIN_STATE
    elif choice == 'save_rates':
        logger.info("Сохранение курсов")
        await query.message.reply_text("✅ Курсы сохранены!")
        reply_markup = build_main_menu(admin_id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    elif choice == 'back_to_main':
        logger.info("Возврат в главное меню")
        reply_markup = build_main_menu(admin_id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    logger.warning(f"Неизвестный choice в rates_callback: {choice}")
    return EDIT_RATES

async def set_rate(update, context):
    user_id = update.message.from_user.id
    admin_id = user_id
    admin_data = get_admin_data(admin_id)

    logger.info(f"Начало обработки ввода курса для user_id={user_id}, editing_rate={context.user_data.get('editing_rate')}")

    try:
        new_rate_input = update.message.text.strip()
        logger.info(f"Получен ввод: '{new_rate_input}'")
        new_rate = float(new_rate_input)
        if new_rate <= 0:
            logger.info("Введён некорректный курс (<= 0)")
            await update.message.reply_text("Бро, курс должен быть больше 0! Попробуй ещё раз.")
            return SET_RATE
        
        rate_key = context.user_data.get('editing_rate')
        if rate_key:
            admin_data['rates'][rate_key] = new_rate
            save_admin_data(admin_id, admin_data)
            logger.info(f"Курс для '{rate_key}' обновлён: {new_rate}")
            rates_text = "\n".join([f"*{k}*: {v:.2f}" for k, v in admin_data['rates'].items()])
            reply_markup = build_rates_menu(admin_id)
            await update.message.reply_text(
                f"✅ Курс для *{rate_key}* обновлён!\n📊 *Текущие курсы:*\n{rates_text}\nВыбери пару для редактирования:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            context.user_data.pop('editing_rate', None)
            return EDIT_RATES
        else:
            logger.error("Не выбрана пара для редактирования")
            await update.message.reply_text(
                "Ошибка: не выбрана пара для редактирования. Попробуй снова.",
                reply_markup=build_rates_menu(admin_id)
            )
            return EDIT_RATES
    except ValueError:
        logger.info("Введено некорректное значение курса")
        await update.message.reply_text(
            "Бро, введи число (например, 0.85)! Попробуй ещё раз.",
            reply_markup=build_rates_menu(admin_id)
        )
        return SET_RATE
    except Exception as e:
        logger.error(f"Ошибка в set_rate: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка при установке курса. Попробуй снова.",
            reply_markup=build_main_menu(user_id)
        )
        return ADMIN_STATE

async def edit_pairs_handler(update, context, admin_id):
    query = update.callback_query
    await query.answer()
    admin_data = get_admin_data(admin_id)
    active_pairs = [str(pair) for pair in admin_data['active_pairs']]
    pairs_text = ", ".join(active_pairs) if active_pairs else "Пока не выбрано"
    reply_markup = build_pairs_menu(admin_id)
    await query.message.reply_text(
        f"💱 Активные пары: {pairs_text}\nВыбери или обнови:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return EDIT_PAIRS

async def pairs_callback(update, context):
    query = update.callback_query
    await query.answer()
    choice = query.data
    admin_id = query.from_user.id
    admin_data = get_admin_data(admin_id)

    if choice == 'reset_pairs':
        admin_data['active_pairs'] = []
        save_admin_data(admin_id, admin_data)
        active_pairs = [str(pair) for pair in admin_data['active_pairs']]
        pairs_text = "Пока не выбрано"
        reply_markup = build_pairs_menu(admin_id)
        await query.message.edit_text(
            f"💱 Активные пары: {pairs_text}\nВыбери или обнови:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return EDIT_PAIRS
    elif choice.startswith('toggle_pair_'):
        pair = choice.replace('toggle_pair_', '')
        if pair in admin_data['active_pairs']:
            admin_data['active_pairs'].remove(pair)
        else:
            admin_data['active_pairs'].append(pair)
        save_admin_data(admin_id, admin_data)
        active_pairs = [str(pair) for pair in admin_data['active_pairs']]
        pairs_text = ", ".join(active_pairs) if active_pairs else "Пока не выбрано"
        reply_markup = build_pairs_menu(admin_id)
        await query.message.edit_text(
            f"💱 Активные пары: {pairs_text}\nВыбери или обнови:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return EDIT_PAIRS
    elif choice == 'save_pairs':
        active_pairs = [str(pair) for pair in admin_data['active_pairs']]
        await query.message.reply_text(f"✅ Пары сохранены: {', '.join(active_pairs) if active_pairs else 'Пока не выбрано'}")
        reply_markup = build_main_menu(query.from_user.id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    elif choice == 'back_to_main':
        reply_markup = build_main_menu(query.from_user.id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    return EDIT_PAIRS

async def edit_locations_handler(update, context, admin_id):
    query = update.callback_query
    await query.answer()
    admin_data = get_admin_data(admin_id)
    active_locations = [str(loc) for loc in admin_data['active_locations']]
    locations_text = ", ".join(active_locations) if active_locations else "Пока не выбрано"
    reply_markup = build_locations_menu(admin_id)
    await query.message.reply_text(
        f"🌍 Активные локации: {locations_text}\nВыбери или обнови:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return EDIT_LOCATIONS

async def locations_callback(update, context):
    query = update.callback_query
    await query.answer()
    choice = query.data
    admin_id = query.from_user.id
    admin_data = get_admin_data(admin_id)

    if choice == 'reset_locations':
        admin_data['active_locations'] = []
        save_admin_data(admin_id, admin_data)
        active_locations = [str(loc) for loc in admin_data['active_locations']]
        locations_text = "Пока не выбрано"
        reply_markup = build_locations_menu(admin_id)
        await query.message.edit_text(
            f"🌍 Активные локации: {locations_text}\nВыбери или обнови:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return EDIT_LOCATIONS
    elif choice.startswith('toggle_loc_'):
        location = choice.replace('toggle_loc_', '')
        if location in admin_data['active_locations']:
            admin_data['active_locations'].remove(location)
        else:
            admin_data['active_locations'].append(location)
        save_admin_data(admin_id, admin_data)
        active_locations = [str(loc) for loc in admin_data['active_locations']]
        locations_text = ", ".join(active_locations) if active_locations else "Пока не выбрано"
        reply_markup = build_locations_menu(admin_id)
        await query.message.edit_text(
            f"🌍 Активные локации: {locations_text}\nВыбери или обнови:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        return EDIT_LOCATIONS
    elif choice == 'save_locations':
        active_locations = [str(loc) for loc in admin_data['active_locations']]
        await query.message.reply_text(f"✅ Локации сохранены: {', '.join(active_locations)}")
        reply_markup = build_main_menu(query.from_user.id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    elif choice == 'back_to_main':
        reply_markup = build_main_menu(query.from_user.id)
        await query.message.reply_text("Админ-панель: выбери раздел", reply_markup=reply_markup, parse_mode='Markdown')
        return ADMIN_STATE
    return EDIT_LOCATIONS

async def broadcast_message(update, context):
    user_id = update.message.from_user.id  # ID админа
    message_text = update.message.text.strip()

    if not message_text:
        await update.message.reply_text("Текст рассылки не может быть пустым! Введи сообщение ещё раз:")
        return BROADCAST

    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute('SELECT user_id FROM users WHERE referrer_id = ?', (user_id,))
    clients = c.fetchall()
    conn.close()

    if not clients:
        await update.message.reply_text("У тебя пока нет клиентов для рассылки.", reply_markup=build_main_menu(user_id))
        return ADMIN_STATE

    sent_count = 0
    for client in clients:
        client_id = client[0]
        try:
            await context.bot.send_message(chat_id=client_id, text=message_text)
            sent_count += 1
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение клиенту {client_id}: {str(e)}")

    await update.message.reply_text(
        f"Рассылка завершена! Сообщение отправлено {sent_count} клиентам.",
        reply_markup=build_main_menu(user_id)
    )
    return ADMIN_STATE

def get_admin_handler(cancel_func):
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_callback, pattern='^enter_admin$'),
            CallbackQueryHandler(admin_callback),  # Добавляем обработку всех callback'ов
        ],
        states={
            ADMIN_STATE: [
                CallbackQueryHandler(admin_callback)
            ],
            ADD_LOCATION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_location)
            ],
            ADD_PAIR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_pair)
            ],
            EDIT_RATES: [
                CallbackQueryHandler(rates_callback, pattern='^(save_rates|back_to_main)')
            ],
            SET_RATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, set_rate),
                CallbackQueryHandler(rates_callback, pattern='^(set_rate_|save_rates|back_to_main)')
            ],
            EDIT_PAIRS: [
                CallbackQueryHandler(pairs_callback, pattern='^(reset_pairs|toggle_pair_|save_pairs|back_to_main)'),
                CallbackQueryHandler(admin_callback, pattern='^(remove_pair|delete_pair_)')
            ],
            EDIT_LOCATIONS: [
                CallbackQueryHandler(locations_callback, pattern='^(reset_locations|toggle_loc_|save_locations|back_to_main)'),
                CallbackQueryHandler(admin_callback, pattern='^(remove_location|delete_location_)')
            ],
            GENERATE_OTP: [
                CallbackQueryHandler(admin_callback, pattern='^(generate_otp_7|generate_otp_30)')
            ],
            BROADCAST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_message)
            ]
        },
        fallbacks=[CommandHandler('cancel', cancel_func)],
        per_message=False  # Оставляем только этот параметр
    )
