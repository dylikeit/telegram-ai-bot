import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import requests
import telebot
from telebot import types

# Сервер-заглушка для прохождения проверки порта Render
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is online")

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
OPENROUTER_KEY = "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"

SUB_PRICE = "290 ₽"
PAYMENT_REQUISITES = "+7 (916) 200-08-63 (СБП / Альфа-Банк)"
TRIAL_DAYS = 7
# ==========================================

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

user_join_date = {}
premium_users = set()

def has_access(uid):
    if uid == ADMIN_ID or uid in premium_users:
        return True, "Безлимитная подписка"
    first_seen = user_join_date.get(uid)
    if not first_seen:
        user_join_date[uid] = datetime.now()
        return True, f"Пробный период: осталось {TRIAL_DAYS} дн."
    elapsed = datetime.now() - first_seen
    if elapsed < timedelta(days=TRIAL_DAYS):
        remaining_days = TRIAL_DAYS - elapsed.days
        return True, f"Пробный период: осталось {remaining_days} дн."
    return False, "Пробный период завершен"

def ask_openrouter(prompt):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "google/gemini-2.0-flash-exp:free",
        "messages": [{"role": "user", "content": prompt}]
    }
    r = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=60)
    data = r.json()
    if "choices" in data and len(data["choices"]) > 0:
        return data["choices"][0]["message"]["content"]
    elif "error" in data:
        return f"Ошибка API: {data['error'].get('message', str(data['error']))}"
    return "Пустой ответ."

@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.chat.id
    if uid not in user_join_date:
        user_join_date[uid] = datetime.now()

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton(f"👑 Купить навсегда ({SUB_PRICE})", callback_data="buy_sub"),
        types.InlineKeyboardButton("📊 Статус доступа", callback_data="check_status")
    )

    welcome_text = (
        f"👋 Привет, {message.from_user.first_name}!\n\n"
        "Я AI-ассистент на базе Google Gemini Flash.\n"
        "Пишу тексты, решаю задачи, код, генерирую идеи.\n\n"
        f"🎁 Для вас действует бесплатный безлимит на {TRIAL_DAYS} дней с момента старта!\n"
        "Задайте любой вопрос прямо в чат."
    )
    bot.send_message(uid, welcome_text, reply_markup=kb)

@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.message.chat.id

    if call.data == "check_status":
        allowed, status_desc = has_access(uid)
        bot.answer_callback_query(call.id)
        bot.send_message(uid, f"ℹ️ Статус вашего аккаунта:\n{status_desc}")

    elif call.data == "buy_sub":
        text = (
            f"👑 Оформление доступа навсегда\n\n"
            f"Стоимость: {SUB_PRICE}\n\n"
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
        bot.send_message(target_id, "🎉 Оплата подтверждена! Доступ навсегда активирован.")
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
        types.InlineKeyboardButton("✅ Выдать доступ", callback_data=f"grant_{uid}"),
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

    allowed, status_desc = has_access(uid)
    if not allowed:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton(f"Купить доступ ({SUB_PRICE})", callback_data="buy_sub"))
        bot.send_message(
            uid,
            f"⚠️ Ваша бесплатная неделя подошла к концу.\nОформите доступ навсегда для продолжения.",
            reply_markup=kb
        )
        return

    bot.send_chat_action(uid, "typing")

    try:
        reply_text = ask_openrouter(prompt)
        if len(reply_text) > 4000:
            for x in range(0, len(reply_text), 4000):
                bot.send_message(uid, reply_text[x:x+4000])
        else:
            bot.send_message(uid, reply_text)
    except Exception as e:
        bot.send_message(uid, f"⚠️ Ошибка: {str(e)}")

if __name__ == "__main__":
    print("Бот успешно запущен!")
    bot.infinity_polling()
