import sqlite3
import re
import asyncio
import logging
from io import BytesIO
from openpyxl import Workbook
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler, ContextTypes
import json

# --- КОНФИГ ---
with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)
BOT_TOKEN = config["BOT_TOKEN"]

GROUP_ID = -1002653254890  # ID группы с гробовозками

ASK_LASTNAME, ASK_FIRSTNAME, ASK_PHONE, SHOW_CARS, COLLECT_DOCS = range(5)

#excel файлик.
async def export_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(str(update.effective_user.id)):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, lastname, firstname, phone, car FROM users")
    rows = cur.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("❌ Нет данных для экспорта.")
        return

    wb = Workbook()
    ws = wb.active
    ws.title = "Users"

    # Заголовки
    ws.append(["User ID", "Фамилия", "Имя", "Телефон", "Машина"])

    for row in rows:
        ws.append(row)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    await context.bot.send_document(
        chat_id=update.effective_user.id,
        document=output,
        filename="users.xlsx"
    )

# --- ЛОГИРОВАНИЕ ---
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

# Список гробовозок
CARS = ["Е-1", "Е-4", "Е-5", "Е-6", "Е-002", "Е-005",
        "Е-009","Е-016", "Е-27", "Е-28", "Е-29", "Е-30",
        "Е-44", "Е-45", "Е-48", "Е-50", "М-4", "R-2", "R-3", "R-4",
        "R-5","R-6", "R-7", "R-8", "R-9", "R-10", "R-11", "R-12", "R-14",
        "R-17","R-21", "R-22", "R-31", "R-33", "R-46", "R-47", "И-06", "И-3",
        "И-9"]

PAGE_SIZE = 12
COLUMNS = 3

# Топики для гробовозок
CAR_TOPICS = {
    "Е-1": 6, "Е-4": 9, "Е-5": 11, "Е-6": 13, "Е-002": 15,
    "Е-005": 17, "Е-009": 19, "Е-016": 21, "Е-27": 23,
    "Е-28": 25, "Е-29": 27, "Е-30": 29, "Е-44": 31, "Е-45": 33,
    "Е-48": 35, "Е-50": 37, "М-4": 39, "R-2": 41, "R-3": 43,
    "R-4": 45, "R-5": 47, "R-6": 49, "R-7": 51, "R-8": 53,
    "R-9": 55, "R-10": 57, "R-11": 59, "R-12": 61, "R-14": 63,
    "R-17": 65, "R-21": 67, "R-22": 69, "R-31": 71, "R-33": 73,
    "R-46": 75, "R-47": 77, "И-06": 79, "И-3": 81, "И-9": 83
}

# --- Инициализация БД ---
def init_db():
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY,
            lastname TEXT,
            firstname TEXT,
            phone TEXT,
            car TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            type TEXT,
            content TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS user_state (
            user_id TEXT PRIMARY KEY,
            state INTEGER,
            data TEXT
        )
    """)                 
    cur.execute("""                
        CREATE TABLE IF NOT EXISTS blacklist (
            user_id TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id TEXT PRIMARY KEY
    )
""")
    conn.commit()
    conn.close()

# --- Работа с БД ---
def get_user(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT user_id, lastname, firstname, phone, car FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        return {"user_id": row[0], "lastname": row[1], "firstname": row[2], "phone": row[3], "car": row[4]}
    return None

def add_user(user_id, lastname, firstname, phone):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO users (user_id, lastname, firstname, phone, car) VALUES (?, ?, ?, ?, ?)",
                (user_id, lastname, firstname, phone, None))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} зарегистрирован: {lastname} {firstname}, {phone}")

def update_user_car(user_id, car):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("UPDATE users SET car = ? WHERE user_id = ?", (car, user_id))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} выбрал машину: {car}")

def add_message(user_id, msg_type, content):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO messages (user_id, type, content) VALUES (?, ?, ?)", (user_id, msg_type, content))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} сохранил сообщение: {msg_type}")

def get_messages(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT type, content FROM messages WHERE user_id = ?", (user_id,))
    rows = cur.fetchall()
    conn.close()
    return rows

def clear_messages(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Сообщения пользователя {user_id} очищены")

def reset_user(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("UPDATE users SET car = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    cur.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} сброшен (машина и сообщения удалены)")

def add_to_blacklist(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO blacklist (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} добавлен в чёрный список")

def remove_from_blacklist(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Пользователь {user_id} удалён из чёрного списка")

def is_blacklisted(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blacklist WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result is not None

# --- Сохранение состояния пользователя ---
def save_user_state(user_id, state, data=None):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO user_state (user_id, state, data) VALUES (?, ?, ?)
    """, (user_id, state, json.dumps(data) if data else None))
    conn.commit()
    conn.close()

def load_user_state(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT state, data FROM user_state WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    if row:
        state, data = row
        return state, json.loads(data) if data else None
    return None, None

def clear_user_state(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM user_state WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    
    # --- Работа с администраторами ---
def add_admin(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Добавлен админ {user_id}")

def remove_admin(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Удалён админ {user_id}")

def is_admin(user_id):
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result is not None

def is_admin(user_id):
    user_id = str(user_id)
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    result = cur.fetchone()
    conn.close()
    return result is not None

def add_admin(user_id):
    user_id = str(user_id)
    conn = sqlite3.connect("users.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO admins (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()
    logging.info(f"Добавлен админ {user_id}")
    
# --- Регистрация ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    logging.info(f"Команда /start от {user_id}")

    # проверим, зарегистрирован ли уже
    if get_user(user_id):
        save_user_state(user_id, SHOW_CARS, {"page": 0})
        await update.message.reply_text("✅ Вы уже зарегистрированы. Выберите машину:")
        return await show_cars_page(update, context, page=0)

    # начинаем регистрацию
    save_user_state(user_id, ASK_LASTNAME)
    await update.message.reply_text("Добро пожаловать! 👋\nВведите вашу Фамилию (только русские буквы):")
    return ASK_LASTNAME


async def ask_lastname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = str(update.effective_user.id)
    logging.info(f"Пользователь {user_id} ввёл фамилию: {text}")

    if not re.fullmatch(r"[А-Яа-яЁё\-]{3,20}", text):
        await update.message.reply_text(
            "❌ Фамилия должна содержать только русские буквы и быть длиной от 3 до 20 символов.\nПопробуйте ещё раз:"
        )
        return ASK_LASTNAME

    context.user_data["lastname"] = text
    save_user_state(user_id, ASK_FIRSTNAME)  # сохраняем состояние

    await update.message.reply_text("Теперь введите Имя (только русские буквы):")
    return ASK_FIRSTNAME


async def ask_firstname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = str(update.effective_user.id)
    logging.info(f"Пользователь {user_id} ввёл имя: {text}")

    if not re.fullmatch(r"[А-Яа-яЁё\-]{2,20}", text):
        await update.message.reply_text(
            "❌ Имя должно содержать только русские буквы и быть длиной от 2 до 20 символов.\nПопробуйте ещё раз:"
        )
        return ASK_FIRSTNAME

    context.user_data["firstname"] = text
    save_user_state(user_id, ASK_PHONE)  # сохраняем состояние

    button = [[KeyboardButton("Отправить номер 📱", request_contact=True)]]
    await update.message.reply_text(
        "Введите номер телефона (только + и цифры):",
        reply_markup=ReplyKeyboardMarkup(button, one_time_keyboard=True, resize_keyboard=True)
    )
    return ASK_PHONE


async def ask_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if update.message.contact:
        phone = update.message.contact.phone_number
        logging.info(f"Пользователь {user_id} отправил контакт: {phone}")
    else:
        phone = update.message.text.strip()
        logging.info(f"Пользователь {user_id} ввёл телефон: {phone}")

    if not re.fullmatch(r"\+?\d{10,15}", phone):
        await update.message.reply_text("❌ Неверный формат номера. Введите только + и цифры:")
        return ASK_PHONE

    context.user_data["phone"] = phone

    # сохраняем пользователя в БД
    add_user(user_id, context.user_data["lastname"], context.user_data["firstname"], phone)

    # переводим на выбор машины
    save_user_state(user_id, SHOW_CARS, {"page": 0})

    await update.message.reply_text(
        "✅ Регистрация завершена!\nТеперь выберите машину:",
        reply_markup=ReplyKeyboardRemove()
    )
    return await show_cars_page(update, context, page=0)


# --- Пагинация Гробовозок ---
def get_cars_page(page: int):
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    return CARS[start:end]

def build_cars_keyboard(page: int):
    cars_page = get_cars_page(page)
    keyboard = []
    for i in range(0, len(cars_page), COLUMNS):
        row = [InlineKeyboardButton(car, callback_data=f"car:{car}") for car in cars_page[i:i+COLUMNS]]
        keyboard.append(row)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅ Назад", callback_data=f"page:{page-1}"))
    if (page + 1) * PAGE_SIZE < len(CARS):
        nav_buttons.append(InlineKeyboardButton("➡ Вперед", callback_data=f"page:{page+1}"))
    if nav_buttons:
        keyboard.append(nav_buttons)
    return InlineKeyboardMarkup(keyboard)

async def show_cars_page(update_or_query, context, page: int = 0):
    reply_markup = build_cars_keyboard(page)
    user_id = str(update_or_query.from_user.id) if hasattr(update_or_query, "from_user") else str(update_or_query.message.chat_id)
    save_user_state(user_id, SHOW_CARS, {"page": page})  # <-- сохраняем состояние

    text = "Выберите машину:🚗"
    try:
        if isinstance(update_or_query, Update) and update_or_query.message:
            await update_or_query.message.reply_text(text, reply_markup=reply_markup)
        else:
            await update_or_query.edit_message_text(text, reply_markup=reply_markup)
    except:
        await context.bot.send_message(chat_id=user_id, text=text, reply_markup=reply_markup)
    return SHOW_CARS

async def page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    logging.info(f"Пользователь {query.from_user.id} перелистнул на страницу {page}")
    await show_cars_page(query, context, page)

async def car_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    car = query.data.split("car:")[1]

    user_id = str(query.from_user.id)
    user = get_user(user_id)
    if not user:
        await query.message.reply_text("Сначала авторизуйтесь через /start")
        return SHOW_CARS

    update_user_car(user_id, car)
    save_user_state(user_id, COLLECT_DOCS)  # <-- пользователь теперь собирает сообщения

    keyboard = [
        [InlineKeyboardButton("📤 Отправить сообщения", callback_data="send_all")],
        [InlineKeyboardButton("⬅ Вернуться к выбору машины", callback_data="back_to_cars")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"✅ Вы выбрали: {car} 🚗\n\nТеперь можете писать сообщения и загружать документы/фото. "
        f"\n\n ⚠️ Важно!⚠️\nВ 📋ЦМР или ЗАЯВКЕ📝\nдолжна быть 🕹печать или 📆дата загрузки/выгрузки!"
        f"\n\nНажмите «📤 Отправить сообщения», чтобы отправить его менеджеру.📬",
        reply_markup=reply_markup
    )
    return COLLECT_DOCS

# --- Сбор сообщений ---
async def collect_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Сначала авторизуйтесь через /start")
        return COLLECT_DOCS

    if update.message.text:
        logging.info(f"Пользователь {user_id} написал текст: {update.message.text}")
    elif update.message.document:
        logging.info(f"Пользователь {user_id} загрузил документ: {update.message.document.file_name}")
    elif update.message.photo:
        logging.info(f"Пользователь {user_id} загрузил фото (ID: {update.message.photo[-1].file_id})")

    if update.message.media_group_id:
        album_id = update.message.media_group_id
        if "album_buffer" not in context.user_data:
            context.user_data["album_buffer"] = {}
        if album_id not in context.user_data["album_buffer"]:
            context.user_data["album_buffer"][album_id] = []

        if update.message.photo:
            context.user_data["album_buffer"][album_id].append(("photo", update.message.photo[-1].file_id))
        elif update.message.document:
            context.user_data["album_buffer"][album_id].append(("document", update.message.document.file_id))

        await asyncio.sleep(2)

        if context.user_data["album_buffer"].get(album_id):
            for msg_type, content in context.user_data["album_buffer"][album_id]:
                add_message(user_id, msg_type, content)
            del context.user_data["album_buffer"][album_id]
    else:
        if update.message.text:
            add_message(user_id, "text", update.message.text)
        elif update.message.document:
            add_message(user_id, "document", update.message.document.file_id)
        elif update.message.photo:
            add_message(user_id, "photo", update.message.photo[-1].file_id)
        else:
            await update.message.reply_text("❌ Этот тип сообщения не поддерживается.")
            return COLLECT_DOCS

    messages = get_messages(user_id)
    counts = {"text": 0, "document": 0, "photo": 0}
    for msg_type, _ in messages:
        if msg_type in counts:
            counts[msg_type] += 1

    keyboard = [
        [InlineKeyboardButton("📤 Отправить сообщения", callback_data="send_all")],
        [InlineKeyboardButton("⬅ Вернуться к выбору машины", callback_data="back_to_cars")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✨ Всего сохранено:\n"
        f"• 🆎 Текстов: {counts['text']}\n"
        f"• 📄 Документов: {counts['document']}\n"
        f"• 🖼 Фото: {counts['photo']}\n\n"
        "Когда будете готовы, нажмите кнопку ниже 👇",
        reply_markup=reply_markup
    )
    return COLLECT_DOCS

# --- Отправка всех сообщений ---
async def send_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    user = get_user(user_id)
    messages = get_messages(user_id)
    if not messages or not user:
        await query.edit_message_text("🚫 У вас нет сообщений или документов для отправки. Отправьте сообщение.")
        return COLLECT_DOCS

    await query.edit_message_text("📤 Отправляю сообщения...")

    car = user["car"]
    thread_id = CAR_TOPICS.get(car)

    # Отправка информации о пользователе
    text_intro = (
        f"👤 Пользователь: {user['lastname']} {user['firstname']}\n"
        f"📱 Телефон: {user['phone']}\n"
        f"🚗 Машина: {car}\n"
    )
    await context.bot.send_message(chat_id=GROUP_ID, text=text_intro, message_thread_id=thread_id)

    # Отправка всех сообщений с небольшой задержкой
    for msg_type, content in messages:
        if msg_type == "text":
            await context.bot.send_message(chat_id=GROUP_ID, text=content, message_thread_id=thread_id)
        elif msg_type == "photo":
            await context.bot.send_photo(chat_id=GROUP_ID, photo=content, message_thread_id=thread_id)
        elif msg_type == "document":
            await context.bot.send_document(chat_id=GROUP_ID, document=content, message_thread_id=thread_id)
        await asyncio.sleep(1.0)  # <-- задержка между отправками

    clear_messages(user_id)

    reply_markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("📤 Отправить сообщения", callback_data="send_all")],
        [InlineKeyboardButton("⬅ Вернуться к выбору машины", callback_data="back_to_cars")]
    ])

    await context.bot.send_message(
        chat_id=user_id,
        text="✅ Все сообщения успешно отправлены!\n\n"
             "Вы можете отправить ещё сообщения или вернуться к выбору машины.",
        reply_markup=reply_markup
    )

    return COLLECT_DOCS

# --- Вернуться к выбору машины ---
async def back_to_cars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)
    reset_user(user_id)
    clear_user_state(user_id)
    await show_cars_page(query, context, page=0)
    return SHOW_CARS

# --- Отмена ---
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.message.from_user.id)
    reset_user(user_id)
    clear_user_state(user_id)
    await update.message.reply_text("Вы вышли из авторизации ❌")
    return ConversationHandler.END

#для бана пользователей 
ADMINS = [8417849865, 5749455968]  # список админов

async def ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
    elif context.args:
        target_id = context.args[0]
    else:
        await update.message.reply_text("Использование: /ban <user_id> или ответьте на сообщение пользователя.")
        return

    add_to_blacklist(target_id)
    await update.message.reply_text(f"✅ Пользователь {target_id} забанен.")


async def unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
    elif context.args:
        target_id = context.args[0]
    else:
        await update.message.reply_text("Использование: /unban <user_id> или ответьте на сообщение пользователя.")
        return

    remove_from_blacklist(target_id)
    await update.message.reply_text(f"✅ Пользователь {target_id} разбанен.")

SUPER_ADMIN = 8417849865  # твой Telegram ID (главный админ)

async def addadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN:
        return

    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
    elif context.args:
        target_id = context.args[0]
    else:
        await update.message.reply_text(
            "Использование: /addadmin <user_id> или ответьте на сообщение пользователя."
        )
        return

    add_admin(target_id)
    await update.message.reply_text(f"✅ Пользователь {target_id} теперь админ.")

async def deladmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPER_ADMIN:
        return

    if update.message.reply_to_message:
        target_id = str(update.message.reply_to_message.from_user.id)
    elif context.args:
        target_id = context.args[0]
    else:
        await update.message.reply_text(
            "Использование: /deladmin <user_id> или ответьте на сообщение пользователя."
        )
        return

    remove_admin(target_id)
    await update.message.reply_text(f"❌ Пользователь {target_id} больше не админ.")

# --- Роутер состояний ---
async def state_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Определяем user_id
    user_id = str(update.effective_user.id if update.effective_user else update.callback_query.from_user.id)

    # 🚫 Проверяем blacklist
    if is_blacklisted(user_id):
        if update.message:
            await update.message.reply_text("🚫 У вас нет доступа к этому боту.")
        elif update.callback_query:
            await update.callback_query.answer("🚫 У вас нет доступа к этому боту.", show_alert=True)
        return

    # --- Обработка callback-кнопок ---
    if update.callback_query:
        data_cb = update.callback_query.data
        if data_cb.startswith("car:"):
            return await car_selected(update, context)
        elif data_cb.startswith("page:"):
            return await page_callback(update, context)
        elif data_cb == "send_all":
            return await send_all(update, context)
        elif data_cb == "back_to_cars":
            return await back_to_cars(update, context)

    # Загружаем состояние из БД
    state, data = load_user_state(user_id)

    # Если состояния нет → ждём /start
    if state is None:
        if update.message and update.message.text == "/start":
            return await start(update, context)
        return  # ничего не делаем, пока человек не введёт /start

    # --- Этапы регистрации ---
    if state == ASK_LASTNAME:
        return await ask_lastname(update, context)

    elif state == ASK_FIRSTNAME:
        return await ask_firstname(update, context)

    elif state == ASK_PHONE:
        return await ask_phone(update, context)

    # --- Работа после регистрации ---
    elif state == SHOW_CARS:
        page = data.get("page", 0) if data else 0
        # Сообщение → показываем список машин
        if update.message:
            return await show_cars_page(update, context, page=page)

    elif state == COLLECT_DOCS:
        if update.message:
            return await collect_messages(update, context)

    return

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(str(update.effective_user.id)):
        await update.message.reply_text("❌ У вас нет прав администратора.")
        return

    conn = sqlite3.connect("users.db")
    cur = conn.cursor()

    # Количество пользователей
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]

    # Сколько выбрали машину
    cur.execute("SELECT COUNT(*) FROM users WHERE car IS NOT NULL AND car != ''")
    with_car = cur.fetchone()[0]

    # Количество сообщений (если есть таблица messages)
    try:
        cur.execute("SELECT COUNT(*) FROM messages")
        total_msgs = cur.fetchone()[0]
    except sqlite3.OperationalError:
        total_msgs = 0  # если таблицы нет, ставим 0

    conn.close()

    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🚗 Выбрали машину: <b>{with_car}</b>\n"
        f"📩 Сообщений сохранено: <b>{total_msgs}</b>"
    )

    await update.message.reply_text(text, parse_mode="HTML")

# --- MAIN ---
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Один роутер на все апдейты
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("ban", ban))
    app.add_handler(CommandHandler("unban", unban))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("addadmin", addadmin))
    app.add_handler(CommandHandler("deladmin", deladmin))
    app.add_handler(CommandHandler("export_excel", export_excel))
    app.add_handler(MessageHandler(filters.ALL, state_router))
    app.add_handler(CallbackQueryHandler(state_router))
    app.run_polling()

if __name__ == "__main__":
    main()
