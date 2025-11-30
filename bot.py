import asyncio
import time
import aiosqlite
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

# ---------------- CONFIG ----------------

TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [7569239259, 7825456486, 7983497123]
DB_PATH = "bot.db"

router = Router()

# ---------------- ANTI-SPAM ----------------

last_message = {}       # user_id: timestamp
last_request = {}       # user_id: timestamp (отправка заявки)

SPAM_DELAY = 3          # сек между сообщениями
REQUEST_DELAY = 60      # сек между отправками запросов помощи

async def antispam(msg: Message) -> bool:
    uid = msg.from_user.id
    now = time.time()

    if uid not in last_message:
        last_message[uid] = now
        return False

    if now - last_message[uid] < SPAM_DELAY:
        await msg.answer(f"⏱ Подождите {SPAM_DELAY} сек перед следующим сообщением.")
        return True

    last_message[uid] = now
    return False


# ---------------- DATABASE INIT ----------------

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT,
                status TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS request_photos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id INTEGER,
                file_id TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                username TEXT,
                text TEXT
            )
        """)

        await db.commit()

async def create_request(user_id, username, text):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO requests (user_id, username, text, status) VALUES (?, ?, ?, ?)",
            (user_id, username, text, "pending")
        )
        await db.commit()
        return cur.lastrowid

async def add_photo(request_id, file_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO request_photos (request_id, file_id) VALUES (?, ?)",
            (request_id, file_id)
        )
        await db.commit()

async def get_request_owner(request_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT user_id FROM requests WHERE id = ?", (request_id,)
        )
        row = await cur.fetchone()
        return row[0] if row else None

async def save_feedback(user_id, username, text):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO feedback (user_id, username, text) VALUES (?, ?, ?)",
            (user_id, username, text)
        )
        await db.commit()
        return cur.lastrowid


# ---------------- KEYBOARDS ----------------

def start_keyboard():
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Запросить помощь", callback_data="request_help"),
        InlineKeyboardButton(text="Написать спасибо", callback_data="send_feedback")
    )
    return kb.as_markup()

def provide_button():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="Предоставить на рассмотрение", callback_data="provide_request"))
    return kb.as_markup()

def admin_keyboard(request_id):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text="Принять запрос", callback_data=f"accept_{request_id}"),
        InlineKeyboardButton(text="Отказаться защищать", callback_data=f"reject_{request_id}")
    )
    return kb.as_markup()


# ---------------- FSM ----------------

class RequestForm(StatesGroup):
    waiting_text = State()
    waiting_photos = State()

class FeedbackForm(StatesGroup):
    waiting_text = State()

class RejectForm(StatesGroup):
    waiting_text = State()


# ---------------- HANDLERS ----------------

@router.message(F.text == "/start")
async def start_cmd(msg: Message):
    await msg.answer(
        "Добро пожаловать в бота защиты от @Nightfall_Retribution!\n\n"
        "Вы можете запросить защиту, если:\n"
        "- вам угрожают деаноном\n"
        "- вы не виноваты в конфликте\n"
        "- столкнулись с педофилией\n\n"
        "Выберите действие:",
        reply_markup=start_keyboard()
    )

# --- START REQUEST ---

@router.callback_query(F.data == "request_help")
async def request_help(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id

    # антиспам — частые запросы помощи
    now = time.time()
    if uid in last_request and now - last_request[uid] < REQUEST_DELAY:
        await cb.answer(
            f"Подождите {int(REQUEST_DELAY - (now - last_request[uid]))} секунд.",
            show_alert=True
        )
        return

    await cb.message.answer(
        "Опишите проблему и отправьте **минимум 2 скриншота** (по одному).\n"
        "После этого нажмите кнопку ниже.",
        reply_markup=provide_button()
    )
    await state.set_state(RequestForm.waiting_text)
    await cb.answer()

@router.message(RequestForm.waiting_text)
async def handle_text(msg: Message, state: FSMContext):
    if await antispam(msg):
        return

    await state.update_data(text=msg.text)
    await msg.answer("Отлично! Теперь отправьте минимум 2 скриншота.")
    await state.set_state(RequestForm.waiting_photos)

@router.message(RequestForm.waiting_photos, F.photo)
async def handle_photo(msg: Message, state: FSMContext):
    if await antispam(msg):
        return

    data = await state.get_data()
    photos = data.get("photos", [])
    photos.append(msg.photo[-1].file_id)
    await state.update_data(photos=photos)

    await msg.answer(f"Скриншот получен ({len(photos)}).")

@router.callback_query(F.data == "provide_request")
async def provide_request(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    if "photos" not in data or len(data["photos"]) < 2:
        await cb.answer("Минимум 2 скриншота!", show_alert=True)
        return

    user = cb.from_user

    last_request[user.id] = time.time()   # антиспам записи

    req_id = await create_request(user.id, user.username, data["text"])

    for ph in data["photos"]:
        await add_photo(req_id, ph)

    # отправить админам
    for admin in ADMINS:
        await cb.bot.send_message(
            admin,
            f"Запрос помощи №{req_id}\n\n"
            f"Текст: {data['text']}\n"
            f"От: @{user.username} (ID: {user.id})",
            reply_markup=admin_keyboard(req_id)
        )
        for ph in data["photos"]:
            await cb.bot.send_photo(admin, ph)

    await cb.message.answer("Ваш запрос отправлен!")
    await state.clear()
    await cb.answer()

# --- ADMIN ACCEPT ---

@router.callback_query(F.data.startswith("accept_"))
async def accept(cb: CallbackQuery):
    req_id = int(cb.data.split("_")[1])
    admin_id = cb.from_user.id

    # уведомление юзеру
    owner = await get_request_owner(req_id)
    if owner:
        try:
            await cb.bot.send_message(owner, f"✅ Ваш запрос №{req_id} был ПРИНЯТ администратором.")
        except:
            pass

    # убрать кнопки у остальных
    for adm in ADMINS:
        if adm != admin_id:
            try:
                await cb.bot.edit_message_reply_markup(adm, cb.message.message_id, reply_markup=None)
            except:
                pass

    await cb.answer("Запрос принят.")

# --- ADMIN REJECT ---

@router.callback_query(F.data.startswith("reject_"))
async def reject(cb: CallbackQuery, state: FSMContext):
    req_id = int(cb.data.split("_")[1])
    await state.update_data(req_id=req_id)

    await cb.message.answer("Введите текст отказа:")
    await state.set_state(RejectForm.waiting_text)
    await cb.answer()

@router.message(RejectForm.waiting_text)
async def reject_text(msg: Message, state: FSMContext):
    if await antispam(msg):
        return

    data = await state.get_data()
    req_id = data["req_id"]
    text = msg.text

    # уведомление юзеру
    owner = await get_request_owner(req_id)
    if owner:
        try:
            await msg.bot.send_message(owner, f"❌ Ваш запрос №{req_id} был отклонён.\nПричина:\n{text}")
        except:
            pass

    # удалить кнопки
    for adm in ADMINS:
        try:
            await msg.bot.edit_message_reply_markup(adm, msg.message_id - 1, reply_markup=None)
        except:
            pass

    await msg.answer("Отказ отправлен пользователю.")
    await state.clear()

# --- FEEDBACK ---

@router.callback_query(F.data == "send_feedback")
async def start_fb(cb: CallbackQuery, state: FSMContext):
    await cb.message.answer("Напишите отзыв:")
    await state.set_state(FeedbackForm.waiting_text)
    await cb.answer()

@router.message(FeedbackForm.waiting_text)
async def feedback(msg: Message, state: FSMContext):
    if await antispam(msg):
        return

    fb_id = await save_feedback(msg.from_user.id, msg.from_user.username, msg.text)

    for admin in ADMINS:
        await msg.bot.send_message(
            admin,
            f"Отзыв №{fb_id}\n\n"
            f"Текст: {msg.text}\n"
            f"Автор: @{msg.from_user.username}"
        )

    await msg.answer("Спасибо! Ваш отзыв отправлен.")
    await state.clear()


# ---------------- RUN BOT ----------------

async def main():
    await init_db()
    bot = Bot(TOKEN, parse_mode="HTML")
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
