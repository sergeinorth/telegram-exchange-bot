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
        await update.message.reply_text("Используйте: /otp <код>")
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

    # Получаем текущие данные пользователя
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    logger.debug(f"Текущие данные: active_order={active_order}, type={type(active_order)}, referrer_id={referrer_id}, in_admin_mode={in_admin_mode}")

    # Инициализируем пустой словарь или парсим существующий active_order
    try:
        if active_order and isinstance(active_order, str):
            active_order_dict = json.loads(active_order)
            if not isinstance(active_order_dict, dict):
                logger.warning(f"active_order не является словарем: {active_order_dict}, создаем новый")
                active_order_dict = {}
        else:
            active_order_dict = {}
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга active_order: {e}")
        active_order_dict = {}

    # Устанавливаем дату истечения прав админа
    active_order_dict['admin_expiry'] = expiry_date.strftime('%Y-%m-%d %H:%M:%S')
    
    # Устанавливаем referrer_id равным user_id и in_admin_mode=1
    referrer_id = user_id

    # Сохраняем данные с явным указанием всех параметров
    try:
        save_user_data(user_id, active_order_dict, referrer_id=referrer_id, in_admin_mode=1)
        logger.debug(f"Данные сохранены: user_id={user_id}, active_order_dict={active_order_dict}")
    except Exception as e:
        logger.error(f"Ошибка при сохранении данных: {str(e)}")
        await update.message.reply_text("Ошибка при сохранении данных. Попробуйте снова или обратитесь в поддержку.")
        return

    # Проверяем, что данные действительно сохранились
    active_order_check, _, referrer_id_check, in_admin_mode_check = get_user_data(user_id)
    logger.debug(f"Проверка после сохранения: active_order_check={active_order_check}, referrer_id_check={referrer_id_check}, in_admin_mode_check={in_admin_mode_check}")
    
    if not active_order_check:
        logger.error(f"После сохранения active_order пустой для user_id={user_id}")
        await update.message.reply_text("Ошибка при активации OTP. Попробуйте снова или обратитесь в поддержку.")
        return

    try:
        active_order_check_dict = json.loads(active_order_check) if isinstance(active_order_check, str) else active_order_check
        if not isinstance(active_order_check_dict, dict) or 'admin_expiry' not in active_order_check_dict:
            logger.error(f"Ошибка в проверке сохраненных данных: active_order_check_dict={active_order_check_dict}")
            await update.message.reply_text("Ошибка проверки данных после активации. Повторно активируйте OTP.")
            return
    except (json.JSONDecodeError, TypeError) as e:
        logger.error(f"Ошибка парсинга active_order_check: {e}")
        await update.message.reply_text("Ошибка проверки данных после активации. Попробуйте снова или обратитесь в поддержку.")
        return

    # Если все проверки прошли успешно, удаляем OTP и возвращаем успех
    delete_otp(otp_code)

    # Возвращаем меню клиента с кнопкой "Админка"
    from exbot import build_client_menu
    reply_markup = build_client_menu(user_id)
    await update.message.reply_text(
        f"Код успешно активирован! Вы получили права администратора до {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}. "
        "Используйте кнопку 'Админка' в главном меню для управления.",
        reply_markup=reply_markup
    )

    # Уведомляем владельца
    try:
        user = update.message.from_user
        user_link = f"@{user.username}" if user.username else f'<a href="tg://user?id={user_id}">Пользователь</a>'
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"Активирован новый администратор: {user_link}\nСрок действия прав: до {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления владельцу: {str(e)}")
        # Не прерываем выполнение, так как главная функция уже выполнена

async def check_subscription(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    active_order, request_count, referrer_id, in_admin_mode = get_user_data(user_id)
    if not active_order or 'admin_expiry' not in json.loads(active_order):
        text = "У вас нет активной административной подписки!"
    else:
        expiry_str = json.loads(active_order)['admin_expiry']
        expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=pytz.UTC)
        if datetime.now(pytz.UTC) > expiry:
            text = "Ваша административная подписка истекла! Обновите код для продолжения."
            active_order_dict = json.loads(active_order)
            active_order_dict.pop('admin_expiry', None)
            save_user_data(user_id, active_order_dict)
        else:
            remaining_time = expiry - datetime.now(pytz.UTC)
            days_left = remaining_time.days
            hours_left = remaining_time.seconds // 3600
            text = f"Ваша подписка активна до {expiry_str}. Осталось {days_left} дн. и {hours_left} ч."

    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text)
