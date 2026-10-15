import os
import time
import base64
import sqlite3
import urllib.parse
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta
import requests
import telebot
from telebot import types

# ================= СЕРВЕР ДЛЯ RENDER =================
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Ultra AI Bot is online")

    def log_message(self, format, *args):
        return

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_server, daemon=True).start()

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_BOT_TOKEN = "8957041093:AAE_Q0u6DUvDptWHNZAuV60HzlH1zQUrdIQ"
ADMIN_ID = 6517815185
GROQ_API_KEY = "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"

SUB_PRICE = "290 ₽"
PAYMENT_REQUISITES = "+7 (916) 200-08-63 (СБП / Альфа-Банк)"
TRIAL_DAYS = 7
REF_BONUS_DAYS = 3
# ================================================

# Принудительный сброс старых вебхуков и чужих сессий
try:
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=True", timeout=10)
    time.sleep(1)
except Exception:
    pass

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ================= БАЗА ДАННЫХ SQLITE =================
conn = sqlite3.connect("users.db", check_same_thread=False)
db_lock = threading.Lock()

with db_lock:
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            uid INTEGER PRIMARY KEY,
            username TEXT,
            join_date TEXT,
            bonus_days INTEGER DEFAULT 0,
            is_vip INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'general',
            requests_count INTEGER DEFAULT 0,
            referred_by INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS business_conns (
            conn_id TEXT PRIMARY KEY,
            owner_id INTEGER
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS msg_cache (
            conn_id TEXT,
            chat_id INTEGER,
            msg_id INTEGER,
            owner_id INTEGER,
            sender_id INTEGER,
            sender_name TEXT,
            content_type TEXT,
            text_content TEXT,
            file_id TEXT,
            PRIMARY KEY (conn_id, chat_id, msg_id)
        )
    """)
    conn.commit()

def get_user(uid, username=""):
    with db_lock:
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE uid = ?", (uid,))
        row = c.fetchone()
        if not row:
            now_str = datetime.now().isoformat()
            c.execute(
                "INSERT INTO users (uid, username, join_date, bonus_days, is_vip, mode, requests_count, referred_by) VALUES (?, ?, ?, 0, 0, 'general', 0, 0)",
                (uid, username, now_str)
            )
            conn.commit()
            c.execute("SELECT * FROM users WHERE uid = ?", (uid,))
            row = c.fetchone()
        return {
            "uid": row[0],
            "username": row[1],
            "join_date": datetime.fromisoformat(row[2]),
            "bonus_days": row[3],
            "is_vip": bool(row[4]),
            "mode": row[5],
            "requests_count": row[6],
            "referred_by": row[7]
        }

def update_user_field(uid, field, value):
    with db_lock:
        c = conn.cursor()
        c.execute(f"UPDATE users SET {field} = ? WHERE uid = ?", (value, uid))
        conn.commit()

def increment_requests(uid):
    with db_lock:
        c = conn.cursor()
        c.execute("UPDATE users SET requests_count = requests_count + 1 WHERE uid = ?", (uid,))
        conn.commit()

def get_stats():
    with db_lock:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(is_vip), SUM(requests_count) FROM users")
        total, vips, reqs = c.fetchone()
        c.execute("SELECT COUNT(*) FROM business_conns")
        b_conns = c.fetchone()[0]
        return total or 0, vips or 0, reqs or 0, b_conns or 0

def get_all_uids():
    with db_lock:
        c = conn.cursor()
        c.execute("SELECT uid FROM users")
        return [r[0] for r in c.fetchall()]

def save_business_conn(conn_id, owner_id):
    with db_lock:
        c = conn.cursor()
        c.execute("INSERT OR REPLACE INTO business_conns (conn_id, owner_id) VALUES (?, ?)", (conn_id, owner_id))
        conn.commit()

def get_business_owner(conn_id):
    with db_lock:
        c = conn.cursor()
        c.execute("SELECT owner_id FROM business_conns WHERE conn_id = ?", (conn_id,))
        row = c.fetchone()
        return row[0] if row else None

def cache_business_msg(conn_id, chat_id, msg_id, owner_id, sender_id, sender_name, content_type, text_content, file_id):
    with db_lock:
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO msg_cache
            (conn_id, chat_id, msg_id, owner_id, sender_id, sender_name, content_type, text_content, file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (conn_id, chat_id, msg_id, owner_id, sender_id, sender_name, content_type, text_content, file_id))
        conn.commit()

def get_cached_msg(conn_id, chat_id, msg_id):
    with db_lock:
        c = conn.cursor()
        c.execute("SELECT owner_id, sender_id, sender_name, content_type, text_content, file_id FROM msg_cache WHERE conn_id = ? AND chat_id = ? AND msg_id = ?", (conn_id, chat_id, msg_id))
        row = c.fetchone()
        if not row:
            return None
        return {
            "owner_id": row[0],
            "sender_id": row[1],
            "sender_name": row[2],
            "content_type": row[3],
            "text_content": row[4],
            "file_id": row[5]
        }

# ================= ПАМЯТЬ И СОСТОЯНИЯ =================
user_histories = {}
last_user_prompt = {}
waiting_for_image = set()
waiting_for_receipt = set()
waiting_for_broadcast = set()

ROLES = {
    "general": ("🧠 Универсальный", "Ты — элитный AI-ассистент. Отвечай глубоко, структурированно, четко и на отличном русском языке."),
    "coder": ("💻 Senior Кодер", "Ты — Principal Software Engineer. Пиши идеальный, чистый, безопасный код с объяснением архитектуры."),
    "copywriter": ("✍️ Маркетолог", "Ты — гений маркетинга и копирайтинга. Пиши продающие тексты, прогревы, сценарии Reels/Shorts и заголовки."),
    "tutor": ("🎓 Репетитор", "Ты — лучший преподаватель. Решай задачи любой сложности по шагам, понятно объясняй правила и пиши сочинения."),
    "business": ("💼 Бизнес & Право", "Ты — бизнес-консультант и финансист. Помогай со стратегией, переговорами, документами и идеями дохода."),
    "translator": ("🇬🇧 Полиглот", "Ты — профессиональный переводчик. Переводи тексты с учетом сленга и контекста, объясняй грамматику."),
    "psychologist": ("🧘 Психолог", "Ты — чуткий, мудрый психолог. Выслушивай, поддерживай без осуждения и помогай разобраться в ситуации."),
    "bro": ("🔥 Дерзкий бро", "Ты — харизматичный, остроумный лучший друг. Общайся живо, на 'ты', с отличным юмором, но всегда по делу.")
}

# ================= ПРОВЕРКА ДОСТУПА =================
def check_access(uid, username=""):
    u = get_user(uid, username)
    if uid == ADMIN_ID or u["is_vip"]:
        return True, "👑 <b>Безлимитный VIP-доступ (Навсегда)</b>"

    total_allowed_days = TRIAL_DAYS + u["bonus_days"]
    deadline = u["join_date"] + timedelta(days=total_allowed_days)
    now = datetime.now()

    if now < deadline:
        rem = deadline - now
        days = rem.days
        hours = rem.seconds // 3600
        return True, f"🎁 <b>Бесплатный доступ:</b> еще {days} дн. {hours} ч."

    return False, "🔒 <b>Бесплатный период истёк</b>"

# ================= ЗАПРОСЫ К GROQ =================
def ask_groq(uid, prompt, custom_instruction=None):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }

    u = get_user(uid)
    mode_key = u["mode"] if u["mode"] in ROLES else "general"
    sys_prompt = ROLES[mode_key][1]

    history = user_histories.get(uid, [])
    user_content = f"{custom_instruction}: {prompt}" if custom_instruction else prompt
    history.append({"role": "user", "content": user_content})

    if len(history) > 8:
        history = history[-8:]
    user_histories[uid] = history

    messages = [{"role": "system", "content": sys_prompt}] + history

    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": messages,
        "temperature": 0.65,
        "max_tokens": 3000
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        data = r.json()
        if "choices" in data and len(data["choices"]) > 0:
            answer = data["choices"][0]["message"]["content"]
            history.append({"role": "assistant", "content": answer})
            user_histories[uid] = history
            increment_requests(uid)
            return answer
        elif "error" in data:
            return f"⚠️ Ошибка API: {data['error'].get('message', 'Попробуйте снова')}"
        return "⚠️ Пустой ответ от нейросети."
    except Exception as e:
        return f"⚠️ Ошибка соединения: {str(e)}"

def analyze_image_groq(uid, image_bytes, caption=""):
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    b64_img = base64.b64encode(image_bytes).decode("utf-8")
    user_query = caption if caption else "Внимательно изучи это изображение. Если на нем задача, тест или вопрос — реши его подробно по шагам. Если текст — переведи или объясни суть. Отвечай на русском языке."

    payload = {
        "model": "llama-3.2-11b-vision-preview",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_query},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                ]
            }
        ],
        "temperature": 0.5,
        "max_tokens": 2500
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        data = r.json()
        if "choices" in data and len(data["choices"]) > 0:
            increment_requests(uid)
            return data["choices"][0]["message"]["content"]
        elif "error" in data:
            return f"⚠️ Ошибка Vision API: {data['error'].get('message', 'Ошибка')}"
        return "⚠️ Не удалось распознать изображение."
    except Exception as e:
        return f"⚠️ Ошибка анализа фото: {e}"

def transcribe_voice(file_bytes):
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": ("voice.ogg", file_bytes, "audio/ogg")}
    data = {"model": "whisper-large-v3-turbo", "language": "ru"}
    r = requests.post(url, headers=headers, files=files, data=data, timeout=30)
    res = r.json()
    return res.get("text", "")

# ================= КЛАВИАТУРЫ =================
def main_menu_kb():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🕵️‍♂️ Анти-Удаление СМС", callback_data="menu_antidelete"),
        types.InlineKeyboardButton("🎭 Роли ИИ (8 шт)", callback_data="menu_roles"),
        types.InlineKeyboardButton("🎨 Создать арт", callback_data="menu_image"),
        types.InlineKeyboardButton("👁 Решение по фото", callback_data="menu_vision_info"),
        types.InlineKeyboardButton("👤 Мой профиль", callback_data="menu_profile"),
        types.InlineKeyboardButton("🎁 +3 дня за друга", callback_data="menu_ref"),
        types.InlineKeyboardButton("🧹 Новый диалог", callback_data="clear_chat"),
        types.InlineKeyboardButton(f"👑 VIP Навсегда ({SUB_PRICE})", callback_data="buy_sub")
    )
    return kb

def answer_actions_kb():
    kb = types.InlineKeyboardMarkup(row_width=3)
    kb.add(
        types.InlineKeyboardButton("✂️ Короче", callback_data="act_shorter"),
        types.InlineKeyboardButton("🔍 Подробнее", callback_data="act_longer"),
        types.InlineKeyboardButton("🔄 Ещё вариант", callback_data="act_regen")
    )
    kb.add(types.InlineKeyboardButton("🏠 В главное меню", callback_data="back_main"))
    return kb

# ================= МОДУЛЬ АНТИ-УДАЛЕНИЯ =================
if hasattr(bot, "business_connection_handler"):
    @bot.business_connection_handler(func=lambda connection: True)
    def on_business_connection(connection):
        conn_id = connection.id
        owner_id = connection.user.id
        save_business_conn(conn_id, owner_id)
        if connection.is_enabled:
            bot.send_message(
                owner_id,
                "🕵️‍♂️ <b>Модуль «Анти-Удаление» успешно подключён!</b>\n\n"
                "Теперь, если любой собеседник в личных чатах удалит или изменит сообщение (текст, фото, ГС, кружочек), я мгновенно пришлю вам копию сюда!",
                parse_mode="HTML"
            )

if hasattr(bot, "business_message_handler"):
    @bot.business_message_handler(content_types=['text', 'photo', 'voice', 'video', 'video_note', 'document'])
    def on_business_msg(message):
        conn_id = getattr(message, "business_connection_id", None)
        if not conn_id:
            return
        owner_id = get_business_owner(conn_id) or 0
        sender_id = message.from_user.id
        sender_name = message.from_user.first_name or "Собеседник"
        if message.from_user.username:
            sender_name += f" (@{message.from_user.username})"

        ctype = message.content_type
        text_content = message.text or message.caption or ""
        file_id = ""
        if ctype == "photo":
            file_id = message.photo[-1].file_id
        elif ctype == "voice":
            file_id = message.voice.file_id
        elif ctype == "video":
            file_id = message.video.file_id
        elif ctype == "video_note":
            file_id = message.video_note.file_id
        elif ctype == "document":
            file_id = message.document.file_id

        cache_business_msg(conn_id, message.chat.id, message.message_id, owner_id, sender_id, sender_name, ctype, text_content, file_id)

if hasattr(bot, "edited_business_message_handler"):
    @bot.edited_business_message_handler(content_types=['text', 'photo', 'video', 'document'])
    def on_edited_business_msg(message):
        conn_id = getattr(message, "business_connection_id", None)
        if not conn_id:
            return
        old = get_cached_msg(conn_id, message.chat.id, message.message_id)
        owner_id = get_business_owner(conn_id) or (old["owner_id"] if old else None)
        if not owner_id or message.from_user.id == owner_id:
            return

        new_text = message.text or message.caption or ""
        old_text = old["text_content"] if old else "<i>(не сохранено)</i>"
        sender_name = message.from_user.first_name or "Собеседник"
        if message.from_user.username:
            sender_name += f" (@{message.from_user.username})"

        if old_text != new_text:
            alert = (
                f"✏️ <b>Собеседник изменил сообщение!</b>\n"
                f"👤 <b>От:</b> {sender_name}\n\n"
                f"❌ <b>Было:</b>\n{old_text}\n\n"
                f"✅ <b>Стало:</b>\n{new_text}"
            )
            try:
                bot.send_message(owner_id, alert, parse_mode="HTML")
            except Exception:
                pass
            cache_business_msg(conn_id, message.chat.id, message.message_id, owner_id, message.from_user.id, sender_name, message.content_type, new_text, old["file_id"] if old else "")

if hasattr(bot, "deleted_business_messages_handler"):
    @bot.deleted_business_messages_handler(func=lambda deleted: True)
    def on_deleted_business_msgs(deleted):
        conn_id = deleted.business_connection_id
        chat_id = deleted.chat.id
        owner_id = get_business_owner(conn_id)

        for msg_id in deleted.message_ids:
            cached = get_cached_msg(conn_id, chat_id, msg_id)
            if not cached:
                continue
            target_owner = owner_id or cached["owner_id"]
            if not target_owner or cached["sender_id"] == target_owner:
                continue

            sender_name = cached["sender_name"]
            ctype = cached["content_type"]
            text_c = cached["text_content"]
            fid = cached["file_id"]

            header = (
                f"🗑 <b>Перехвачено удалённое сообщение!</b>\n"
                f"👤 <b>От:</b> {sender_name}\n"
            )
            if text_c:
                header += f"\n💬 <b>Текст:</b>\n{text_c}"

            try:
                if ctype == "text" or not fid:
                    bot.send_message(target_owner, header, parse_mode="HTML")
                elif ctype == "photo":
                    bot.send_photo(target_owner, fid, caption=header, parse_mode="HTML")
                elif ctype == "voice":
                    bot.send_voice(target_owner, fid, caption=header, parse_mode="HTML")
                elif ctype == "video":
                    bot.send_video(target_owner, fid, caption=header, parse_mode="HTML")
                elif ctype == "video_note":
                    bot.send_message(target_owner, header + "\n⬇️ <i>Удалённый видеокружок:</i>", parse_mode="HTML")
                    bot.send_video_note(target_owner, fid)
                elif ctype == "document":
                    bot.send_document(target_owner, fid, caption=header, parse_mode="HTML")
            except Exception:
                pass

# ================= КОМАНДЫ =================
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.chat.id
    username = message.from_user.username or ""
    args = message.text.split()

    with db_lock:
        c = conn.cursor()
        c.execute("SELECT uid FROM users WHERE uid = ?", (uid,))
        is_new = c.fetchone() is None

    u = get_user(uid, username)

    if is_new and len(args) > 1 and args[1].isdigit():
        ref_id = int(args[1])
        if ref_id != uid:
            ref_user = get_user(ref_id)
            new_bonus = ref_user["bonus_days"] + REF_BONUS_DAYS
            update_user_field(ref_id, "bonus_days", new_bonus)
            update_user_field(uid, "referred_by", ref_id)
            try:
                bot.send_message(
                    ref_id,
                    f"🎉 По вашей ссылке зашел друг!\nВам начислено <b>+{REF_BONUS_DAYS} дня</b> бесплатного безлимита!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

    _, status_text = check_access(uid, username)
    role_title = ROLES.get(u["mode"], ROLES["general"])[0]

    welcome = (
        f"⚡️ <b>Привет, {message.from_user.first_name}!</b>\n"
        f"Это <b>Dylikeit AI Ultra</b> — твой супер-комбайн в Telegram.\n\n"
        f"{status_text}\n"
        f"🎭 Активная роль: <b>{role_title}</b>\n\n"
        f"🔥 <b>Эксклюзивные функции:</b>\n"
        f"• 🕵️‍♂️ <b>Анти-Удаление:</b> перехват удалённых и изменённых сообщений, фото, ГС и кружков\n"
        f"• 👁 <b>AI Vision:</b> скиньте фото задачи или скриншот — бот решит его\n"
        f"• 📄 <b>Чтение файлов и кода</b>\n"
        f"• 🎙 <b>Голосовые и 🎨 генерация артов</b>\n\n"
        f"👇 <b>Напишите запрос или выберите функцию:</b>"
    )
    bot.send_message(uid, welcome, parse_mode="HTML", reply_markup=main_menu_kb())

@bot.message_handler(commands=['admin'])
def cmd_admin(message):
    if message.chat.id != ADMIN_ID:
        return
    total, vips, reqs, b_conns = get_stats()
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📢 Сделать рассылку всем", callback_data="admin_broadcast"))
    text = (
        f"👑 <b>Панель Администратора</b>\n\n"
        f"👥 Всего пользователей: <b>{total}</b>\n"
        f"💎 VIP-пользователей: <b>{vips}</b>\n"
        f"🕵️‍♂️ Подключений Анти-Удаления: <b>{b_conns}</b>\n"
        f"⚡️ Всего запросов к ИИ: <b>{reqs}</b>\n\n"
        f"Выдать VIP вручную:\n<code>/vip ID_пользователя</code>"
    )
    bot.send_message(ADMIN_ID, text, parse_mode="HTML", reply_markup=kb)

@bot.message_handler(commands=['vip'])
def cmd_grant_vip(message):
    if message.chat.id != ADMIN_ID:
        return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        bot.send_message(ADMIN_ID, "Формат: `/vip 123456789`", parse_mode="Markdown")
        return
    target = int(parts[1])
    get_user(target)
    update_user_field(target, "is_vip", 1)
    bot.send_message(ADMIN_ID, f"✅ Пользователю {target} выдан VIP навсегда!")
    try:
        bot.send_message(target, "🎉 <b>Вам активирован пожизненный VIP-доступ!</b>", parse_mode="HTML")
    except Exception:
        pass

@bot.message_handler(commands=['img'])
def cmd_image(message):
    uid = message.chat.id
    allowed, _ = check_access(uid)
    if not allowed:
        bot.send_message(uid, "🔒 Срок бесплатного доступа истек. Оформите VIP для генерации картинок.")
        return
    prompt = message.text.replace("/img", "").strip()
    if not prompt:
        waiting_for_image.add(uid)
        bot.send_message(uid, "🎨 <b>Напишите, что нужно нарисовать:</b>", parse_mode="HTML")
        return
    generate_and_send_image(uid, prompt)

def generate_and_send_image(uid, prompt):
    wait_msg = bot.send_message(uid, "🎨 <i>Генерирую арт, подождите 5–10 секунд...</i>", parse_mode="HTML")
    bot.send_chat_action(uid, "upload_photo")
    try:
        en_prompt = ask_groq(uid, f"Переведи этот запрос для генерации изображения на английский язык (выведи только сам перевод без кавычек): {prompt}")
        encoded = urllib.parse.quote(en_prompt.strip())
        img_url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true&seed={int(time.time())}"
        r = requests.get(img_url, timeout=45)
        if r.status_code == 200:
            bot.delete_message(uid, wait_msg.message_id)
            bot.send_photo(uid, r.content, caption=f"🎨 <b>Запрос:</b> {prompt}", parse_mode="HTML", reply_markup=main_menu_kb())
            increment_requests(uid)
        else:
            bot.edit_message_text("⚠️ Сервер генерации перегружен, попробуйте другое описание.", uid, wait_msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"⚠️ Ошибка генерации: {e}", uid, wait_msg.message_id)

# ================= КНОПКИ =================
@bot.callback_query_handler(func=lambda call: True)
def callbacks(call):
    uid = call.message.chat.id
    u = get_user(uid)

    if call.data == "back_main":
        bot.answer_callback_query(call.id)
        _, status_text = check_access(uid)
        role_title = ROLES.get(u["mode"], ROLES["general"])[0]
        bot.send_message(
            uid,
            f"🏠 <b>Главное меню</b>\n{status_text}\n🎭 Режим: <b>{role_title}</b>",
            parse_mode="HTML",
            reply_markup=main_menu_kb()
        )

    elif call.data == "menu_antidelete":
        bot_info = bot.get_me()
        text = (
            f"🕵️‍♂️ <b>Как включить перехват удалённых сообщений:</b>\n\n"
            f"1. Откройте <b>Настройки Telegram</b> → <b>Telegram для бизнеса</b>.\n"
            f"2. Перейдите в <b>«Чат-боты»</b>.\n"
            f"3. Вставьте юзернейм бота: <code>@{bot_info.username}</code> и нажмите <b>Добавить</b>.\n\n"
            f"✅ <b>Готово!</b> Теперь при удалении или изменении сообщений в личке вы получите копию!"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 В главное меню", callback_data="back_main"))
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

    elif call.data == "menu_vision_info":
        text = (
            "👁 <b>Решение задач и анализ по фото (AI Vision)</b>\n\n"
            "Просто отправьте боту любую картинку или скриншот с подписью или без!"
        )
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 В главное меню", callback_data="back_main"))
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

    elif call.data == "menu_roles":
        kb = types.InlineKeyboardMarkup(row_width=2)
        buttons = [
            types.InlineKeyboardButton(title, callback_data=f"role_{key}")
            for key, (title, _) in ROLES.items()
        ]
        kb.add(*buttons)
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back_main"))
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🎭 <b>Выберите специализацию ИИ:</b>", parse_mode="HTML", reply_markup=kb)

    elif call.data.startswith("role_"):
        new_role = call.data.replace("role_", "")
        if new_role in ROLES:
            update_user_field(uid, "mode", new_role)
            user_histories[uid] = []
            title = ROLES[new_role][0]
            bot.answer_callback_query(call.id, text=f"Включен: {title}")
            bot.send_message(uid, f"✅ Активирован режим: <b>{title}</b>", parse_mode="HTML", reply_markup=main_menu_kb())

    elif call.data == "menu_image":
        waiting_for_image.add(uid)
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "🎨 <b>Опишите картинку, которую хотите создать:</b>", parse_mode="HTML")

    elif call.data == "menu_profile":
        _, status_text = check_access(uid)
        role_title = ROLES.get(u["mode"], ROLES["general"])[0]
        text = (
            f"👤 <b>Ваш профиль</b>\n\n"
            f"🆔 ID: <code>{uid}</code>\n"
            f"{status_text}\n"
            f"🎭 Роль: <b>{role_title}</b>\n"
            f"📊 Запросов: <b>{u['requests_count']}</b>\n"
            f"🎁 Бонусов: <b>{u['bonus_days']} дн.</b>"
        )
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu_kb())

    elif call.data == "menu_ref":
        bot_info = bot.get_me()
        ref_link = f"https://t.me/{bot_info.username}?start={uid}"
        text = (
            f"🎁 <b>Бесплатный безлимит за друзей!</b>\n\n"
            f"За каждого друга вы получаете <b>+{REF_BONUS_DAYS} дня</b>!\n\n"
            f"🔗 <b>Ваша ссылка:</b>\n<code>{ref_link}</code>"
        )
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="HTML", reply_markup=main_menu_kb())

    elif call.data == "clear_chat":
        user_histories[uid] = []
        bot.answer_callback_query(call.id, text="Очищено!")
        bot.send_message(uid, "🧹 <b>Память диалога очищена!</b>", parse_mode="HTML")

    elif call.data in ["act_shorter", "act_longer", "act_regen"]:
        allowed, _ = check_access(uid)
        if not allowed:
            bot.answer_callback_query(call.id, text="Доступ истек")
            return
        bot.answer_callback_query(call.id, text="Генерирую...")
        bot.send_chat_action(uid, "typing")
        last_p = last_user_prompt.get(uid, "Продолжи")
        if call.data == "act_shorter":
            ans = ask_groq(uid, last_p, "Сократи ответ до сути по пунктам")
        elif call.data == "act_longer":
            ans = ask_groq(uid, last_p, "Распиши ответ подробно с примерами")
        else:
            ans = ask_groq(uid, last_p, "Перепиши ответ иначе")
        send_safe_markdown(uid, ans)

    elif call.data == "buy_sub":
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(
            types.InlineKeyboardButton("📸 Я оплатил — отправить чек", callback_data="send_receipt_mode"),
            types.InlineKeyboardButton("🔙 Назад", callback_data="back_main")
        )
        text = (
            f"👑 <b>VIP-Безлимит Навсегда</b>\n\n"
            f"• Полный безлимит на ИИ, фото, файлы и арты\n"
            f"• Модуль «Анти-Удаление»\n\n"
            f"💳 Стоимость: <b>{SUB_PRICE}</b>\n"
            f"Реквизиты:\n<code>{PAYMENT_REQUISITES}</code>\n\n"
            f"После перевода нажмите кнопку ниже:"
        )
        bot.answer_callback_query(call.id)
        bot.send_message(uid, text, parse_mode="HTML", reply_markup=kb)

    elif call.data == "send_receipt_mode":
        waiting_for_receipt.add(uid)
        bot.answer_callback_query(call.id)
        bot.send_message(uid, "📸 <b>Отправьте скриншот чека в этот чат:</b>", parse_mode="HTML")

    elif call.data == "admin_broadcast" and uid == ADMIN_ID:
        waiting_for_broadcast.add(ADMIN_ID)
        bot.answer_callback_query(call.id)
        bot.send_message(ADMIN_ID, "📢 Введите текст рассылки:")

    elif call.data.startswith("grant_"):
        if call.from_user.id != ADMIN_ID:
            return
        target_id = int(call.data.split("_")[1])
        get_user(target_id)
        update_user_field(target_id, "is_vip", 1)
        bot.send_message(target_id, "🎉 <b>VIP-доступ активирован!</b>", parse_mode="HTML")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption="✅ VIP выдан!")

    elif call.data.startswith("reject_"):
        if call.from_user.id != ADMIN_ID:
            return
        target_id = int(call.data.split("_")[1])
        bot.send_message(target_id, "❌ Чек отклонён.")
        bot.edit_message_caption(call.message.chat.id, call.message.message_id, caption="❌ Отклонено.")

# ================= ОБРАБОТКА МЕДИА =================
@bot.message_handler(content_types=['voice'])
def handle_voice(message):
    uid = message.chat.id
    allowed, _ = check_access(uid, message.from_user.username or "")
    if not allowed:
        bot.send_message(uid, "🔒 Доступ истек.")
        return

    bot.send_chat_action(uid, "typing")
    try:
        file_info = bot.get_file(message.voice.file_id)
        downloaded = bot.download_file(file_info.file_path)
        text = transcribe_voice(downloaded)
        if not text:
            bot.send_message(uid, "⚠️ Не удалось разобрать голос.")
            return
        bot.send_message(uid, f"🎙 <i>Распознано:</i> «{text}»", parse_mode="HTML")
        last_user_prompt[uid] = text
        reply = ask_groq(uid, text)
        send_safe_markdown(uid, reply)
    except Exception as e:
        bot.send_message(uid, f"⚠️ Ошибка: {e}")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.chat.id
    caption = (message.caption or "").strip()

    if uid in waiting_for_receipt or any(w in caption.lower() for w in ["чек", "оплат", "перевод", "сбп"]):
        waiting_for_receipt.discard(uid)
        username = f"@{message.from_user.username}" if message.from_user.username else f"ID: {uid}"
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("✅ Выдать VIP", callback_data=f"grant_{uid}"),
            types.InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_{uid}")
        )
        bot.send_photo(
            ADMIN_ID,
            message.photo[-1].file_id,
            caption=f"💳 <b>Новый чек!</b>\nОт: {username} (<code>{uid}</code>)",
            parse_mode="HTML",
            reply_markup=kb
        )
        bot.send_message(uid, "⏳ Чек отправлен на проверку!")
        return

    allowed, _ = check_access(uid, message.from_user.username or "")
    if not allowed:
        bot.send_message(uid, "🔒 Доступ истек.")
        return

    wait_msg = bot.send_message(uid, "👁 <i>Анализирую фото...</i>", parse_mode="HTML")
    bot.send_chat_action(uid, "typing")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        img_bytes = bot.download_file(file_info.file_path)
        ans = analyze_image_groq(uid, img_bytes, caption)
        bot.delete_message(uid, wait_msg.message_id)
        last_user_prompt[uid] = caption or "Анализ фото"
        send_safe_markdown(uid, ans)
    except Exception as e:
        bot.edit_message_text(f"⚠️ Ошибка фото: {e}", uid, wait_msg.message_id)

@bot.message_handler(content_types=['document'])
def handle_document(message):
    uid = message.chat.id
    allowed, _ = check_access(uid, message.from_user.username or "")
    if not allowed:
        bot.send_message(uid, "🔒 Доступ истек.")
        return

    fname = message.document.file_name or "file"
    if message.document.file_size and message.document.file_size > 2 * 1024 * 1024:
        bot.send_message(uid, "⚠️ Файл слишком большой.")
        return

    bot.send_chat_action(uid, "typing")
    try:
        file_info = bot.get_file(message.document.file_id)
        raw_bytes = bot.download_file(file_info.file_path)
        content = raw_bytes.decode("utf-8", errors="ignore")[:10000]
        if not content.strip():
            bot.send_message(uid, "⚠️ Пустой файл.")
            return
        user_task = message.caption or "Проанализируй этот файл."
        prompt = f"Файл `{fname}`:\n```\n{content}\n```\n\nЗадача: {user_task}"
        last_user_prompt[uid] = prompt
        reply = ask_groq(uid, prompt)
        send_safe_markdown(uid, reply)
    except Exception as e:
        bot.send_message(uid, f"⚠️ Ошибка чтения файла: {e}")

# ================= ТЕКСТ =================
def send_safe_markdown(uid, text):
    if len(text) > 4000:
        for x in range(0, len(text), 4000):
            bot.send_message(uid, text[x:x+4000], reply_markup=answer_actions_kb() if x + 4000 >= len(text) else None)
    else:
        try:
            bot.send_message(uid, text, parse_mode="Markdown", reply_markup=answer_actions_kb())
        except Exception:
            bot.send_message(uid, text, reply_markup=answer_actions_kb())

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    uid = message.chat.id
    prompt = message.text.strip()

    if uid == ADMIN_ID and uid in waiting_for_broadcast:
        waiting_for_broadcast.remove(ADMIN_ID)
        uids = get_all_uids()
        sent = 0
        for target in uids:
            try:
                bot.send_message(target, prompt)
                sent += 1
            except Exception:
                pass
        bot.send_message(ADMIN_ID, f"✅ Рассылка завершена: {sent}/{len(uids)}.")
        return

    if uid in waiting_for_image:
        waiting_for_image.remove(uid)
        generate_and_send_image(uid, prompt)
        return

    allowed, _ = check_access(uid, message.from_user.username or "")
    if not allowed:
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton(f"👑 Купить VIP ({SUB_PRICE})", callback_data="buy_sub"),
            types.InlineKeyboardButton("🎁 +3 дня за друга", callback_data="menu_ref")
        )
        bot.send_message(
            uid,
            "🔒 <b>Бесплатный период завершился.</b> Оформите VIP:",
            parse_mode="HTML",
            reply_markup=kb
        )
        return

    bot.send_chat_action(uid, "typing")
    last_user_prompt[uid] = prompt
    reply = ask_groq(uid, prompt)
    send_safe_markdown(uid, reply)

if __name__ == "__main__":
    print("Бот успешно запущен на Llama 3.3 70B!")
    bot.infinity_polling(
        skip_pending=True,
        allowed_updates=[
            "message", "edited_message", "callback_query",
            "business_connection", "business_message",
            "edited_business_message", "deleted_business_messages"
        ]
    )
