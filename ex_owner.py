import logging
from datetime import datetime, timedelta
import secrets
from utils import get_otp_data, save_otp_data, delete_otp, get_user_data, save_user_data
import json

logger = logging.getLogger(__name__)

OWNER_ID = '669497764'

def generate_otp(days):
    otp = secrets.token_hex(3).upper()
    expiry = datetime.now() + timedelta(days=days)
    return otp, expiry

async def activate_otp(update, context):
    logger.info("Получена команда /otp")
    user_id = update.message.from_user.id
    args = context.args  # Получаем аргументы команды, например ['F025A8']

    if not args or len(args) != 1:
        await update.message.reply_text("Используй: /otp <код>")
        return

    otp_code = args[0].strip()
    # Проверяем код в базе данных
    otp_data = get_otp_data(otp_code)

    if not otp_data:
        await update.message.reply_text("Неверный или истёкший код OTP!")
        return

    # Извлекаем данные: user_id, expiry, duration
    _, expiry, duration = otp_data
    expiry_date = datetime.strptime(expiry, '%Y-%m-%d %H:%M:%S')

    if datetime.now() > expiry_date:
        await update.message.reply_text("Срок действия этого OTP-кода истёк.")
        delete_otp(otp_code)
        return

    # Обновляем статус пользователя
    active_order, request_count, referrer_id = get_user_data(user_id)
    if not active_order:
        active_order = '{}'
    data = json.loads(active_order)
    # Устанавливаем admin_expiry равным expiry из OTP (срок действия с момента генерации)
    data['admin_expiry'] = expiry
    # Устанавливаем referrer_id равным user_id, чтобы пользователь стал "сам себе админом"
    referrer_id = user_id
    save_user_data(user_id, data, referrer_id)

    # Проверяем, что данные действительно сохранились
    active_order_check, _, referrer_id_check = get_user_data(user_id)
    logger.info(f"После сохранения active_order для user_id={user_id}: {active_order_check}, referrer_id: {referrer_id_check}")
    if not active_order_check or 'admin_expiry' not in json.loads(active_order_check):
        logger.error(f"Не удалось сохранить admin_expiry для user_id={user_id}")
        await update.message.reply_text("Произошла ошибка при активации OTP. Попробуй снова или обратись в поддержку.")
        return
    if referrer_id_check != user_id:
        logger.error(f"Не удалось установить referrer_id для user_id={user_id}")
        await update.message.reply_text("Произошла ошибка при настройке админки. Попробуй снова или обратись в поддержку.")
        return

    # Удаляем использованный OTP
    delete_otp(otp_code)

    # Импортируем build_client_menu внутри функции, чтобы избежать циклической зависимости
    from exbot import build_client_menu
    reply_markup = build_client_menu(user_id)
    await update.message.reply_text(
        f"Код активирован, бро! Ты теперь админ до {expiry}. "
        "Используй кнопку 'Админка' в главном меню!",
        reply_markup=reply_markup
    )

    # Уведомление владельцу с кликабельным юзернеймом или ссылкой
    user = update.message.from_user
    if user.username:
        user_link = f"@{user.username}"
    else:
        user_link = f"[пользователь](tg://user?id={user_id})"
    await context.bot.send_message(
        chat_id=OWNER_ID,
        text=f"Новый админ активирован: {user_link}\nСрок действия подписки: до {expiry}",
        parse_mode='Markdown'  # Включаем Markdown для поддержки ссылок
    )

async def check_subscription(update, context):
    user_id = update.message.from_user.id if update.message else update.callback_query.from_user.id
    active_order, _, _ = get_user_data(user_id)

    if not active_order or 'admin_expiry' not in json.loads(active_order):
        text = "Бро, у тебя нет активной админской подписки!"
    else:
        expiry_str = json.loads(active_order)['admin_expiry']
        expiry = datetime.strptime(expiry_str, '%Y-%m-%d %H:%M:%S')
        if datetime.now() > expiry:
            text = "Бро, твоя админская подписка истекла! Пора обновить код."
            active_order_dict = json.loads(active_order)
            active_order_dict.pop('admin_expiry', None)
            save_user_data(user_id, active_order_dict)
        else:
            remaining_time = expiry - datetime.now()
            days_left = remaining_time.days
            hours_left = remaining_time.seconds // 3600
            text = f"Бро, твоя подписка активна до {expiry_str}. Осталось {days_left} дн. и {hours_left} ч."

    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text)