import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types
from google import genai

# Сервер-заглушка для Render Web Service
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ================= ДАННЫЕ =================
TELEGRAM_BOT_TOKEN = "8957041093:AAFLQX5djkK8vb4bK3SO_raTptkrbLZi5-E"
ADMIN_ID = 6517815185
GEMINI_KEY = "AQ.Ab8RN6KnkHB6EWKSXXBnmvxXw4nz0ISe0N2akIVsBf3g31xV0w"

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
        "Я AI-ассистент на базе Google Gemini Flash.\n"
        "Пишу тексты, решаю задачи, код, генерирую идеи.\n\n"
        f"🎁 Вам доступно {FREE_LIMIT} бесплатных запросов.\n"
        "Задайте вопрос прямо в чат!"
    )
    bot.send_message(uid, welcome_text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.message.chat.id

    if call.data == "check_balance":
        if uid in premium_users or uid == ADMIN_ID:
            status = "👑 У вас активен Безлимитный доступ!"
        else:
            used = user_usage.get(uid, 0)
            left = max(0, FREE_LIMIT - used)
            status = f"Осталось бесплатных запросов: {left} из {FREE_LIMIT}"
        bot.answer_callback_query(call.id)
        bot.send_message(uid, status)

    elif call.data == "buy_sub":
        text = (
            f"👑 Оформление безлимитного доступа\n\n"
            f"Стоимость: {SUB_PRICE} навсегда\n\n"
            f"Переведите сумму по реквизитам:\n{PAYMENT_REQUISITES}\n\n"
            "После перевода отправьте скриншот чека в этот чат."
        )
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text)

    elif call.data.startswith("grant_"):
        if call.from_user.id != ADMIN_ID:
            return
        target_id = int(call.data.split("_")[1])
        premium_users.add(target_id)
        bot.send_message(target_id, "🎉 Оплата подтверждена! Безлимит активирован.")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption="✅ Одобрено!")

    elif call.data.startswith("reject_"):
        if call.from_user.id != ADMIN_ID:
            return
        target_id = int(call.data.split("_")[1])
        bot.send_message(target_id, "❌ Чек отклонен.")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption="❌ Отклонено.")

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
        caption=f"💳 Новая оплата!\nОт: {username} ({uid})",
        reply_markup=kb
    )
    bot.send_message(uid, "⏳ Чек отправлен на проверку администратору.")

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
                f"⚠️ Ваши {FREE_LIMIT} бесплатных запроса исчерпаны.\nОформите безлимит для продолжения.",
                reply_markup=kb
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
        error_msg = str(e)
        bot.send_message(uid, f"⚠️ Детали ошибки API:\n{error_msg}")
        print(f"Ошибка API: {e}")

if __name__ == "__main__":
    print("Бот успешно запущен и готов к работе!")
    bot.infinity_polling()
