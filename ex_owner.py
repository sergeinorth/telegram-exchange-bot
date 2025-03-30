import asyncio
import logging
from datetime import datetime, timedelta
import pytz
import secrets
from utils import get_otp_data, save_otp_data, delete_otp, get_user_data, save_user_data
import json

logger = logging.getLogger(__name__)

OWNER_ID = '669497764'

def generate_otp(days):
    otp = secrets.token_hex(3).upper()
    expiry = datetime.now(pytz.UTC) + timedelta(days=days)
    return otp, expiry

async def activate_otp(update, context):
    logger.info("Получена команда /otp")
    user_id = update.message.from_user.id
    args = context.args

    if not args or len(args) != 1:
        await update.message.reply_text("Используй: /otp <код>")
        return

    otp_code = args[0].strip()
    otp_data = get_otp_data(otp_code)

    if not otp_data:
        await update.message.reply_text("Неверный или истёкший код OTP!")
        return

    user_id_otp, expiry, duration = otp_data
    expiry_date = datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
    logger.debug(f"OTP данные: user_id_otp={user_id_otp}, expiry={expiry}, duration={duration}")

    if datetime.now(pytz.UTC) > expiry_date:
        await update.message.reply_text("Срок действия этого OTP-кода истёк.")
        delete_otp(otp_code)
        return

    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    logger.debug(f"Текущие данные: active_order={active_order}, type={type(active_order)}, referrer_id={referrer_id}, in_admin_mode={in_admin_mode}")

    if not active_order:
        active_order_dict = {}
        logger.debug("active_order пустой, инициализируем пустой словарь")
    else:
        try:
            active_order_dict = json.loads(active_order)
            logger.debug(f"Распаршенный active_order_dict={active_order_dict}, type={type(active_order_dict)}")
            if not isinstance(active_order_dict, dict):  # Проверяем тип
                logger.error(f"active_order_dict не словарь после json.loads: {active_order_dict}, type={type(active_order_dict)}")
                active_order_dict = {}
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга active_order для user_id={user_id}: {str(e)}, active_order={active_order}")
            active_order_dict = {}
            logger.debug("После ошибки парсинга: active_order_dict={}, type={type(active_order_dict)}")

    logger.debug(f"Перед добавлением admin_expiry: active_order_dict={active_order_dict}, type={type(active_order_dict)}")
    if not isinstance(active_order_dict, dict):
        logger.error(f"active_order_dict не словарь перед добавлением admin_expiry: {active_order_dict}, type={type(active_order_dict)}")
        active_order_dict = {}
    try:
        active_order_dict['admin_expiry'] = expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    except TypeError as e:
        logger.error(f"TypeError при добавлении admin_expiry: {str(e)}, active_order_dict={active_order_dict}, type={type(active_order_dict)}")
        raise
    logger.debug(f"После добавления admin_expiry: active_order_dict={active_order_dict}, type={type(active_order_dict)}")

    referrer_id = user_id

    try:
        save_user_data(user_id, active_order_dict, referrer_id=referrer_id, in_admin_mode=1)
        logger.debug(f"Данные сохранены: user_id={user_id}, active_order_dict={active_order_dict}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных для user_id={user_id}: {str(e)}")
        await update.message.reply_text("Ошибка при сохранении данных. Попробуй снова или пиши в поддержку.")
        return

    # Проверяем, что данные сохранились
    active_order_check, _, referrer_id_check, in_admin_mode_check = get_user_data(user_id)
    logger.debug(f"Проверка после сохранения: active_order_check={active_order_check}, referrer_id_check={referrer_id_check}, in_admin_mode_check={in_admin_mode_check}")
    if not active_order_check:
        logger.error(f"После сохранения active_order пустой для user_id={user_id}")
        await update.message.reply_text("Ошибка при активации OTP. Попробуй снова или пиши в поддержку.")
        return

    try:
        active_order_check_dict = json.loads(active_order_check)
        logger.debug(f"Проверка active_order_check_dict={active_order_check_dict}, type={type(active_order_check_dict)}")
    except json.JSONDecodeError:
        logger.error(f"Ошибка парсинга active_order_check для user_id={user_id}: {active_order_check}")
        await update.message.reply_text("Ошибка проверки данных после активации. Попробуй снова или пиши в поддержку.")
        return

    if 'admin_expiry' not in active_order_check_dict or referrer_id_check != user_id or not in_admin_mode_check:
        logger.error(f"Ошибка в сохранении данных для user_id={user_id}: active_order={active_order_check}, referrer_id={referrer_id_check}, in_admin_mode={in_admin_mode_check}")
        await update.message.reply_text("Ошибка при настройке админки. Попробуй снова или пиши в поддержку.")
        return

    # Удаляем OTP после успешной активации
    delete_otp(otp_code)

    # Возвращаем меню клиента с кнопкой "Админка"
    from exbot import build_client_menu
    reply_markup = build_client_menu(user_id)
    await update.message.reply_text(
        f"Код активирован, бро! Ты теперь админ до {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}. "
        "Используй кнопку 'Админка' в главном меню!",
        reply_markup=reply_markup
    )

    # Уведомляем владельца
    user = update.message.from_user
    user_link = f"@{user.username}" if user.username else f"[пользователь](tg://user?id={user_id})"
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"Новый админ активирован: {user_link}\nСрок действия подписки: до {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode='Markdown'
    )

async def check_subscription(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)  # Исправлено
    if not active_order or 'admin_expiry' not in json.loads(active_order):
        text = "Бро, у тебя нет активной админской подписки!"
    else:
        expiry_str = json.loads(active_order)['admin_expiry']
        expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
        if datetime.now(pytz.UTC) > expiry:
            text = "Бро, твоя админская подписка истекла! Пора обновить код."
            active_order_dict = json.loads(active_order)
            active_order_dict.pop('admin_expiry', None)
            save_user_data(user_id, active_order_dict)
        else:
            remaining_time = expiry - datetime.now(pytz.UTC)
            days_left = remaining_time.days
            hours_left = remaining_time.seconds // 3600
            text = f"Бро, твоя подписка активна до {expiry_str}. Осталось {days_left} дн. и {hours_left} ч."

    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text)
