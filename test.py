import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

# ----------------- ВАШИ ДАННЫЕ (НЕ ИЗМЕНЯТЬ) -----------------
BOT_TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [8180575933, 7569239259, 7825456486, 7605589697, 7983497123, 5628438532, 6450541492]
# ----------------------------------------------------------------

DATA_FILE = Path("data.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Инициализация файла данных, если нет
if not DATA_FILE.exists():
    DATA_FILE.write_text(json.dumps({
        "next_request_id": 1,
        "requests": {},
        "next_review_id": 1,
        "reviews": {},
        "user_request_cooldowns": {},
        "user_review_cooldowns": {}
    }, ensure_ascii=False, indent=2), encoding="utf-8")

def load_data():
    if not DATA_FILE.exists():
        return {
            "requests": {},
            "next_request_id": 1,
            "reviews": {},
            "next_review_id": 1,
            "user_request_cooldowns": {},
            "user_review_cooldowns": {}
        }
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))

def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ----------------- ПАМЯТЬ В ПАМЯТИ -----------------
pending_reports = {}               # user_id -> {"text": str or None, "files": [{file_id, type}]}
awaiting_rejection_reason = {}     # admin_id -> {"request_id": id}
# ---------------------------------------------------------------------------------------

# ----------------- Клавиатуры -----------------
KB_START = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("Запросить помощь", callback_data="start_request"),
        InlineKeyboardButton("Написать спасибо", callback_data="write_thanks")
    ]]
)

KB_SUBMIT = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("Отправить", callback_data="submit_request"),
        InlineKeyboardButton("Назад", callback_data="go_menu")
    ]]
)

KB_BACK_TO_MENU = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Назад", callback_data="go_menu")]]
)

KB_ADMIN_ACTIONS = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("принять запрос.", callback_data="admin_accept"),
        InlineKeyboardButton("отказаться помогать.", callback_data="admin_reject")
    ]]
)

WELCOME_TEXT = "Добро пожаловать в бота помощи от @Nightfall_Retribution! Для управления, пожалуйста, воспользуйтесь кнопками ниже."

HELP_PROMPT_TEXT = (
    "Здесь вы можете запросить помощь в случаях если:\n"
    "-Вы являетесь мирным пользователем и вам угрожают\n"
    "-Вы столкнулись с актом педофилии В ВАШУ СТОРОНУ \n"
    "-Вы сейчас в конфликте и не являетесь спровоцировавшим конфликт/виновным в конфликте \n"
    "-Вас обманули на сумму более 5$\n\n"
    "Пожалуйста, коротко и ясно опишите ситуацию и приложите не менее 2 фото/видео доказательств (ПО ОТДЕЛЬНОСТИ), после чего нажмите \"Отправить\""
)

# ----------------- КУЛДАУНЫ -----------------
REVIEW_COOLDOWN = timedelta(hours=1)
REQUEST_COOLDOWN = timedelta(hours=24)

def format_timedelta(td: timedelta) -> str:
    total_seconds = int(td.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes} мин {seconds} сек"

# ----------------- ХЕЛПЕРЫ -----------------
def user_display_name(user):
    if user is None:
        return "Unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)

# ----------------- ОБРАБОТЧИКИ -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_chat = None
    if update.effective_chat:
        target_chat = update.effective_chat.id
    elif update.message:
        target_chat = update.message.chat_id
    if target_chat:
        await context.bot.send_message(chat_id=target_chat, text=WELCOME_TEXT, reply_markup=KB_START)

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    await query.answer()
    user = query.from_user
    uid = user.id
    data = query.data

    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    if data == "go_menu":
        await context.bot.send_message(chat_id=uid, text=WELCOME_TEXT, reply_markup=KB_START)
        return

    if data == "start_request":
        if uid not in ADMINS:
            data_store = load_data()
            last_time = data_store.get("user_request_cooldowns", {}).get(str(uid))
            if last_time:
                last_time_dt = datetime.fromisoformat(last_time)
                now = datetime.utcnow()
                if now - last_time_dt < REQUEST_COOLDOWN:
                    await context.bot.send_message(
                        chat_id=uid, 
                        text="ЗАЯВКА АВТОМАТИЧЕСКИ ОТКЛОНЕНА ТАК КАК НЕ ПРОШЛО 24 ЧАСА С МОМЕНТА ОТПРАВКИ ПРОШЛОЙ ЗАЯВКИ", 
                        reply_markup=KB_START
                    )
                    return

        pending_reports[uid] = {"text": None, "files": []}
        await context.bot.send_message(chat_id=uid, text=HELP_PROMPT_TEXT, reply_markup=KB_SUBMIT)
        return

    if data == "write_thanks":
        if uid not in ADMINS:
            data_store = load_data()
            last_time = data_store.get("user_review_cooldowns", {}).get(str(uid))
            if last_time:
                last_time_dt = datetime.fromisoformat(last_time)
                now = datetime.utcnow()
                if now - last_time_dt < REVIEW_COOLDOWN:
                    await context.bot.send_message(
                        chat_id=uid, 
                        text="ОТЗЫВ НЕ БЫЛ ОТПРАВЛЕН ТАК КАК НЕ ПРОШЛО ЧАСА С МОМЕНТА ОТПРАВКИ ПРОШЛОГО", 
                        reply_markup=KB_START
                    )
                    return

        context.user_data["awaiting_review"] = True
        await context.bot.send_message(chat_id=uid, text="Пожалуйста напишите отзыв о нашей работе.", reply_markup=KB_BACK_TO_MENU)
        return

    # --- здесь остальной callback: submit_request, admin_accept/reject ---
    # код не изменен, кроме удаления сообщений админов у всех, кроме нажавшего

    if data == "submit_request":
        if uid not in pending_reports:
            await context.bot.send_message(chat_id=uid, text="У вас нет активной заявки", reply_markup=KB_START)
            return

        report = pending_reports.get(uid)
        if not report:
            await context.bot.send_message(chat_id=uid, text="Ошибка: данные не найдены", reply_markup=KB_START)
            return

        text = report.get("text") or "Нет описания"
        files = report.get("files") or []
        if len(files) < 2:
            await context.bot.send_message(chat_id=uid, text="Необходимо приложить минимум 2 файла (фото/видео)", reply_markup=KB_SUBMIT)
            return

        data_store = load_data()
        req_id = data_store["next_request_id"]
        data_store["next_request_id"] += 1
        now = datetime.utcnow()

        # Сохраняем время подачи заявки для проверки кулдауна
        data_store["user_request_cooldowns"][str(uid)] = now.isoformat()

        data_store["requests"][str(req_id)] = {
            "user_id": uid,
            "username": user.username or "",
            "text": text,
            "files": files,
            "timestamp": now.isoformat(),
            "status": "pending",
            "admin_id": None
        }
        save_data(data_store)

        # Отправка админам
        admin_text = f"Заявка №{req_id}\nПользователь: {user_display_name(user)}\nОписание: {text}\nФайлов: {len(files)}"
        for admin_id in ADMINS:
            try:
                keyboard = None
                if admin_id in ADMINS:
                    keyboard = InlineKeyboardMarkup([[
                        InlineKeyboardButton("принять запрос.", callback_data=f"admin_accept_{req_id}"),
                        InlineKeyboardButton("отказаться помогать.", callback_data=f"admin_reject_{req_id}")
                    ]])
                await context.bot.send_message(chat_id=admin_id, text=admin_text, reply_markup=keyboard)
            except Exception as e:
                logger.error(f"Не удалось отправить админу {admin_id}: {e}")

        await context.bot.send_message(chat_id=uid, text="Заявка отправлена на рассмотрение администраторам.", reply_markup=KB_START)
        del pending_reports[uid]
        return

    if data.startswith("admin_accept_"):
        if uid not in ADMINS:
            return
        
        req_id = data.split("_")[-1]
        data_store = load_data()
        request = data_store["requests"].get(req_id)
        if not request:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена")
            return
        
        request["status"] = "accepted"
        request["admin_id"] = uid
        save_data(data_store)
        
        # Уведомление пользователя
        user_id = request["user_id"]
        try:
            await context.bot.send_message(chat_id=user_id, text="Ваша заявка была принята администратором.")
        except Exception:
            pass
        
        await query.edit_message_text(text=f"Заявка №{req_id} принята вами.")
        return

    if data.startswith("admin_reject_"):
        if uid not in ADMINS:
            return
        
        req_id = data.split("_")[-1]
        awaiting_rejection_reason[uid] = {"request_id": req_id}
        await context.bot.send_message(chat_id=uid, text="Укажите причину отказа:")
        return

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    uid = msg.from_user.id
    text = (msg.text or "").strip()

    # --- обработка причины отказа ---
    if uid in awaiting_rejection_reason:
        req_id = awaiting_rejection_reason[uid]["request_id"]
        data_store = load_data()
        request = data_store["requests"].get(str(req_id))
        if request:
            request["status"] = "rejected"
            request["reject_reason"] = text
            request["admin_id"] = uid
            save_data(data_store)
            
            # Уведомление пользователя
            user_id = request["user_id"]
            try:
                await context.bot.send_message(chat_id=user_id, text=f"Ваша заявка была отклонена. Причина: {text}")
            except Exception:
                pass
            
            await context.bot.send_message(chat_id=uid, text=f"Причина отказа для заявки №{req_id} сохранена.")
        else:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена")
        
        del awaiting_rejection_reason[uid]
        return

    # --- обработка отзывов ---
    if context.user_data.get("awaiting_review"):
        # проверка кулдауна (на всякий случай)
        if uid not in ADMINS:
            data_store = load_data()
            last_time = data_store.get("user_review_cooldowns", {}).get(str(uid))
            if last_time:
                last_time_dt = datetime.fromisoformat(last_time)
                now = datetime.utcnow()
                if now - last_time_dt < REVIEW_COOLDOWN:
                    await context.bot.send_message(
                        chat_id=uid, 
                        text="ОТЗЫВ НЕ БЫЛ ОТПРАВЛЕН ТАК КАК НЕ ПРОШЛО ЧАСА С МОМЕНТА ОТПРАВКИ ПРОШЛОГО", 
                        reply_markup=KB_START
                    )
                    context.user_data["awaiting_review"] = False
                    return

        review_text = text
        now = datetime.utcnow()
        data_store = load_data()
        rid = data_store["next_review_id"]
        data_store["next_review_id"] += 1
        data_store["reviews"][str(rid)] = {
            "user_id": uid,
            "username": msg.from_user.username or "",
            "text": review_text,
            "timestamp": now.isoformat()
        }
        data_store["user_review_cooldowns"][str(uid)] = now.isoformat()
        save_data(data_store)

        for adm in ADMINS:
            try:
                await context.bot.send_message(chat_id=adm, text=f"Отзыв №{rid}\nТекст: {review_text}\nАвтор: {user_display_name(msg.from_user)}")
            except Exception:
                pass

        await context.bot.send_message(chat_id=uid, text="Благодарим вас за отзыв, он был переслан администрации", reply_markup=KB_START)
        context.user_data["awaiting_review"] = False
        return

    # --- добавление текста в заявку ---
    if uid in pending_reports:
        if text:
            if pending_reports[uid].get("text"):
                pending_reports[uid]["text"] += "\n\n---\n\n" + text
                await context.bot.send_message(chat_id=uid, text="Доказательство принято! Текст добавлен к существующему описанию.", reply_markup=KB_SUBMIT)
            else:
                pending_reports[uid]["text"] = text
                await context.bot.send_message(chat_id=uid, text="Доказательство принято!", reply_markup=KB_SUBMIT)
        return

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    uid = msg.from_user.id
    if uid not in pending_reports or not msg.photo:
        return
    photo = msg.photo[-1]
    pending_reports[uid]["files"].append({"file_id": photo.file_id, "type": "photo"})
    await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    uid = msg.from_user.id
    if uid not in pending_reports:
        return

    file_id = None
    if msg.video:
        file_id = msg.video.file_id
    elif msg.document and (str(msg.document.mime_type).startswith("video") or str(msg.document.mime_type).startswith("application/octet-stream")):
        file_id = msg.document.file_id
    elif msg.video_note:
        file_id = msg.video_note.file_id

    if not file_id:
        await context.bot.send_message(chat_id=uid, text="Не удалось распознать отправленное видео. Попробуйте отправить его как файл или видео.")
        return

    pending_reports[uid]["files"].append({"file_id": file_id, "type": "video"})
    await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)

# ----------------- ОСНОВНАЯ -----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO | filters.VIDEO_NOTE, video_handler))

    logger.info("Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()