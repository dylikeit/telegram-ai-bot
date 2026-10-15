import os
import telebot
from telebot import types
from google import genai

# ================= ДАННЫЕ =================
TELEGRAM_BOT_TOKEN = "8763527964:AAFIzQt_vZLLUS5KS6VbzcliFa4OMfyy05o"
ADMIN_ID = 6517815185
GEMINI_KEY = "AQ.Ab8RN6LHS_DBkCn9uZ5lRy7VOSbCdcfmXu82juEw7n7cvcgUXA"

SUB_PRICE = "290 ₽"
PAYMENT_REQUISITES = "+7 (916) 200-08-63 (СБП / Альфа-Банк)"
FREE_LIMIT = 3
# ==========================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
ai_client = genai.Client(api_key=GEMINI_KEY)

user_usage = {}
premium_users = set()

@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.chat.id
    if uid not in user_usage:
        user_usage[uid] = 0

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"👑 Купить безлимит ({SUB_PRICE})", callback_data="buy_sub"),
        types.InlineKeyboardButton("📊 Мой баланс", callback_data="check_balance")
    )

    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я AI-ассистент на базе Google Gemini.\n"
        "Решаю задачи, пишу сочинения, программный код, тексты и посты.\n\n"
        f"🎁 Вам доступно **{FREE_LIMIT} бесплатных запросов**.\n"
        "Напишите любой вопрос прямо в чат!"
    )
    bot.send_message(uid, welcome_text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.message.chat.id

    if call.data == "check_balance":
        if uid in premium_users or uid == ADMIN_ID:
            status = "👑 У вас активен **Безлимитный доступ**!"
        else:
            used = user_usage.get(uid, 0)
            left = max(0, FREE_LIMIT - used)
            status = f"Осталось запросов: **{left} из {FREE_LIMIT}**"
        
        bot.answer_callback_query(call.id)
        bot.send_message(uid, status, parse_mode="Markdown")

    elif call.data == "buy_sub":
        text = (
            f"👑 **Оформление безлимитного доступа**\n\n"
            f"Стоимость: **{SUB_PRICE} навсегда**\n\n"
            f"Переведите сумму по реквизитам:\n`{PAYMENT_REQUISITES}`\n\n"
            "После оплаты **пришлите скриншот чека** прямо сюда в чат. Бот моментально передаст его на подтверждение администратору."
        )
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="Markdown")

# Прием чека от пользователя
@bot.message_handler(content_types=['photo'])
def handle_receipt(message):
    uid = message.chat.id
    username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {uid}"

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ Выдать безлимит", callback_data=f"grant_{uid}"),
        types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
    )

    bot.send_photo(
        ADMIN_ID,
        message.photo[-1].file_id,
        caption=f"💳 **Новая оплата подписки!**\nОт: {username} (`{uid}`)",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    bot.send_message(uid, "⏳ Чек отправлен на проверку. Безлимит откроется в течение нескольких минут!")

# Управление подписками из чата админа
@bot.callback_query_handler(func=lambda call: call.data.startswith(('grant_', 'reject_')))
def admin_sub_action(call):
    if call.from_user.id != ADMIN_ID:
        return

    action, target_id = call.data.split('_')
    target_id = int(target_id)

    if action == "grant":
        premium_users.add(target_id)
        bot.send_message(target_id, "🎉 **Оплата подтверждена!** Вам активирован постоянный безлимитный доступ к нейросети.")
        bot.edit_message_caption(
            call.message.chat.id,
            call.message.message_id,
            caption=call.message.caption + "\n\n✅ Одобрено! Безлимит активирован."
        )
    elif action == "reject":
        bot.send_message(target_id, "❌ Чек отклонен. Проверьте платеж или свяжитесь с поддержкой.")
        bot.edit_message_caption(
            call.message.chat.id,
            call.message.message_id,
            caption=call.message.caption + "\n\n❌ Отклонено."
        )

# Ответ нейросети
@bot.message_handler(func=lambda message: True)
def handle_ai_query(message):
    uid = message.chat.id
    prompt = message.text.strip()

    if uid not in premium_users and uid != ADMIN_ID:
        used = user_usage.get(uid, 0)
        if used >= FREE_LIMIT:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton(f"Купить безлимит ({SUB_PRICE})", callback_data="buy_sub"))
            bot.send_message(
                uid,
                "⚠️ **Ваши 3 бесплатных запроса закончились.**\n\n"
                "Оформите безлимитный доступ, чтобы продолжить пользоваться AI без ограничений.",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return

    bot.send_chat_action(uid, "typing")

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        reply_text = response.text or "Пустой ответ."

        if len(reply_text) > 4000:
            for x in range(0, len(reply_text), 4000):
                bot.send_message(uid, reply_text[x:x+4000])
        else:
            bot.send_message(uid, reply_text)

        if uid not in premium_users and uid != ADMIN_ID:
            user_usage[uid] = user_usage.get(uid, 0) + 1

    except Exception as e:
        bot.send_message(uid, "Произошла ошибка при генерации. Попробуйте еще раз.")
        print(f"Ошибка API: {e}")

if __name__ == "__main__":
    print("Бот успешно запущен и готов к работе!")
    bot.infinity_polling()
