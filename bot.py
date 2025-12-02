import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime

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
ADMINS = [8180575933, 7569239259, 7825456486, 7605589697, 7983497123, 5628438532]
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
        "user_review_cooldowns": {}
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_data():
    if not DATA_FILE.exists():
        return {
            "requests": {},
            "next_request_id": 1,
            "reviews": {},
            "next_review_id": 1,
            "user_review_cooldowns": {}
        }
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------- ПАМЯТЬ -----------------
pending_reports = {}               # user_id -> {"text": str or None, "files": [{file_id, type}]}
awaiting_rejection_reason = {}     # admin_id -> {"request_id": id}
# -----------------------------------------


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
# ------------------------------------------------


# ----------------- ХЕЛПЕРЫ -----------------
def user_display_name(user):
    if user is None:
        return "Unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)
# ------------------------------------------


# ----------------- ОБРАБОТЧИКИ -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_chat = update.effective_chat.id if update.effective_chat else update.message.chat_id
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
        pending_reports[uid] = {"text": None, "files": []}
        await context.bot.send_message(chat_id=uid, text=HELP_PROMPT_TEXT, reply_markup=KB_SUBMIT)
        return

    if data == "submit_request":
        pr = pending_reports.get(uid)
        if not pr:
            await context.bot.send_message(chat_id=uid, text="Вы ещё не начали заявку. Нажмите 'Запросить помощь'.", reply_markup=KB_START)
            return
        if not pr["text"] or len(pr["files"]) < 2:
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, опишите ситуацию и прикрепите не менее 2 фото/видео доказательств.", reply_markup=KB_SUBMIT)
            return

        data_store = load_data()
        rid = data_store["next_request_id"]
        data_store["next_request_id"] += 1

        data_store["requests"][str(rid)] = {
            "user_id": uid,
            "username": user.username or "",
            "text": pr["text"] or "",
            "files": pr["files"],
            "admin_messages": {},
            "status": "open",
            "created_at": datetime.utcnow().isoformat()
        }
        save_data(data_store)

        admin_message_text = f"❗ ЗАПРОС ПОМОЩИ №{rid} ❗\n{pr['text'] or '(без текста)'}\n\nЗапросил: {user_display_name(user)}"

        for admin_id in ADMINS:
            try:
                sent = await context.bot.send_message(chat_id=admin_id, text=admin_message_text, reply_markup=KB_ADMIN_ACTIONS)
                data_store = load_data()
                if str(rid) in data_store["requests"]:
                    data_store["requests"][str(rid)]["admin_messages"][str(admin_id)] = sent.message_id
                    save_data(data_store)
                for f in pr["files"]:
                    try:
                        if f.get("type") == "photo":
                            await context.bot.send_photo(chat_id=admin_id, photo=f["file_id"])
                        elif f.get("type") == "video":
                            await context.bot.send_video(chat_id=admin_id, video=f["file_id"])
                    except Exception:
                        pass
            except Exception:
                logger.exception(f"Не удалось отправить админ-уведомление админ {admin_id}")

        pending_reports.pop(uid, None)
        await context.bot.send_message(chat_id=uid, text="Ваша заявка была отправлена администрации, ожидайте")
        await context.bot.send_message(chat_id=uid, text=WELCOME_TEXT, reply_markup=KB_START)
        return

    if data == "write_thanks":
        context.user_data["awaiting_review"] = True
        await context.bot.send_message(chat_id=uid, text="Пожалуйста напишите отзыв о нашей работе.", reply_markup=KB_BACK_TO_MENU)
        return

    if data in ("admin_accept", "admin_reject"):
        msg = query.message
        if not msg:
            return
        found_rid = None
        data_store = load_data()
        for rid, rec in data_store["requests"].items():
            admin_msgs = rec.get("admin_messages", {})
            for adm_str, m_id in admin_msgs.items():
                if m_id == msg.message_id:
                    found_rid = rid
                    break
            if found_rid:
                break
        if not found_rid:
            try:
                await context.bot.send_message(chat_id=uid, text="Не удалось определить заявку.")
            except Exception:
                pass
            return

        rid = found_rid
        rec = data_store["requests"][rid]
        if rec.get("status") != "open":
            try:
                await context.bot.send_message(chat_id=uid, text="Заявка уже обработана.")
            except Exception:
                pass
            return

        if data == "admin_accept":
            rec["status"] = "accepted"
            save_data(data_store)
            requester_id = rec["user_id"]
            admin_name = user_display_name(query.from_user)
            try:
                await context.bot.send_message(chat_id=requester_id, text=f"Ваш запрос приняли. Свяжется админ {admin_name}.")
            except Exception:
                logger.exception("Не удалось уведомить заявителя об принятии.")

            # удаляем сообщения у всех админов кроме нажавшего
            for adm_id in ADMINS:
                if adm_id == query.from_user.id:
                    continue
                msg_id = rec.get("admin_messages", {}).get(str(adm_id))
                if msg_id:
                    try:
                        await context.bot.delete_message(chat_id=adm_id, message_id=msg_id)
                    except Exception:
                        pass

            # оставляем сообщение только нажавшему
            rec["admin_messages"] = {str(query.from_user.id): rec.get("admin_messages", {}).get(str(query.from_user.id))}
            data_store["requests"][rid] = rec
            save_data(data_store)
            return

        if data == "admin_reject":
            awaiting_rejection_reason[uid] = {"request_id": rid}
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, укажите причину отказа.", reply_markup=KB_BACK_TO_MENU)
            return
# -------------------------------------------------


# ----------------- ОБРАБОТКА ТЕКСТОВ -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.from_user:
        return
    uid = msg.from_user.id
    text = (msg.text or "").strip()

    # 1) Отказ админа
    if uid in awaiting_rejection_reason:
        info = awaiting_rejection_reason.pop(uid)
        rid = info.get("request_id")
        data_store = load_data()
        rec = data_store["requests"].get(str(rid))
        if not rec:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена.")
            return
        if not text:
            awaiting_rejection_reason[uid] = {"request_id": rid}
            await context.bot.send_message(chat_id=uid, text="Причина не может быть пустой, пожалуйста, отправьте текст.", reply_markup=KB_BACK_TO_MENU)
            return

        rec["status"] = "rejected"
        for adm_str, msg_id in rec.get("admin_messages", {}).items():
            try:
                await context.bot.delete_message(chat_id=int(adm_str), message_id=msg_id)
            except Exception:
                pass

        requester_id = rec["user_id"]
        try:
            await context.bot.send_message(chat_id=requester_id, text=f"Ваш запрос помощи был отклонен. Причина: {text}\nВы можете подать заявку снова через 24 часа")
        except Exception:
            logger.exception("Не удалось уведомить пользователя об отказе.")
        rec["admin_messages"] = {}
        data_store["requests"][str(rid)] = rec
        save_data(data_store)
        return

    # 2) Отзыв пользователя
    if context.user_data.get("awaiting_review"):
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

        # уведомление только реальным админам
        for adm in ADMINS:
            try:
                await context.bot.send_message(chat_id=adm, text=f"Отзыв №{rid}\nТекст: {review_text}\nАвтор: {user_display_name(msg.from_user)}")
            except Exception:
                pass

        await context.bot.send_message(chat_id=uid, text="Благодарим вас за отзыв, он был переслан администрации", reply_markup=KB_START)
        context.user_data["awaiting_review"] = False
        return

    # 3) Добавление текста в заявку
    if uid in pending_reports and text:
        if pending_reports[uid].get("text"):
            pending_reports[uid]["text"] += "\n\n---\n\n" + text
            await context.bot.send_message(chat_id=uid, text="Доказательство принято! Текст добавлен к существующему описанию.", reply_markup=KB_SUBMIT)
        else:
            pending_reports[uid]["text"] = text
            await context.bot.send_message(chat_id=uid, text="Доказательство принято!", reply_markup=KB_SUBMIT)
        return
# ---------------------------------------------


# ----------------- ОБРАБОТКА ФОТО -----------------
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


# ----------------- ОБРАБОТКА ВИДЕО -----------------
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
