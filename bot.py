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

# ----------------- ВАШИ ВКЛЮЧЕННЫЕ ДАННЫЕ (НЕ ИЗМЕНЯТЬ) -----------------
BOT_TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [7569239259, 7605589697, 7983497123, 5628438532, 7825456486]
# --------------------------------------------------------------------------------

DATA_FILE = Path("data.json")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

if not DATA_FILE.exists():
    DATA_FILE.write_text(json.dumps({
        "next_request_id": 1,
        "requests": {},
        "next_review_id": 1,
        "reviews": {},
        "user_review_cooldowns": {}
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_data():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


pending_reports = {}
awaiting_rejection_reason = {}

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
    "-Вы столкнулись с актом педофилии В ВАШУ СТОРОНУ\n"
    "-Вы сейчас в конфликте и не являетесь виновным\n"
    "-Вас обманули на сумму более 5$\n\n"
    "Пожалуйста, коротко опишите ситуацию и приложите не менее 2 фото/видео, затем нажмите «Отправить»."
)


def user_display_name(user):
    if not user:
        return "Unknown"
    if user.username:
        return f"@{user.username}"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = update.effective_chat.id
    await context.bot.send_message(chat_id=target, text=WELCOME_TEXT, reply_markup=KB_START)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    try:
        await query.message.delete()
    except:
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
        if not pr or not pr["text"] or len(pr["files"]) < 2:
            await context.bot.send_message(chat_id=uid, text="Опишите ситуацию и прикрепите минимум 2 доказательства.", reply_markup=KB_SUBMIT)
            return

        data_store = load_data()
        rid = data_store["next_request_id"]
        data_store["next_request_id"] += 1

        data_store["requests"][str(rid)] = {
            "user_id": uid,
            "username": query.from_user.username or "",
            "text": pr["text"],
            "files": pr["files"],
            "admin_messages": {},
            "status": "open",
            "created_at": datetime.utcnow().isoformat()
        }
        save_data(data_store)

        msg_txt = f"❗ ЗАПРОС ПОМОЩИ №{rid} ❗\n{pr['text']}\n\nЗапросил: {user_display_name(query.from_user)}"

        for admin_id in ADMINS:
            try:
                sent = await context.bot.send_message(chat_id=admin_id, text=msg_txt, reply_markup=KB_ADMIN_ACTIONS)

                data_store = load_data()
                data_store["requests"][str(rid)]["admin_messages"][str(admin_id)] = sent.message_id
                save_data(data_store)

                for f in pr["files"]:
                    if f["type"] == "photo":
                        await context.bot.send_photo(chat_id=admin_id, photo=f["file_id"])
                    else:
                        await context.bot.send_video(chat_id=admin_id, video=f["file_id"])

            except Exception:
                logger.exception("Ошибка отправки админу")

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
        data_store = load_data()

        found_id = None
        for rid, rec in data_store["requests"].items():
            if str(uid) in rec["admin_messages"] and rec["admin_messages"][str(uid)] == msg.message_id:
                found_id = rid
                break

        if not found_id:
            await context.bot.send_message(chat_id=uid, text="Не удалось определить заявку.")
            return

        rec = data_store["requests"][found_id]

        if data == "admin_accept":
            rec["status"] = "accepted"
            save_data(data_store)

            await context.bot.send_message(chat_id=rec["user_id"], text=f"Ваш запрос приняли. С вами свяжется админ {user_display_name(query.from_user)}.")

            for a, mid in rec["admin_messages"].items():
                if int(a) != uid:
                    try:
                        await context.bot.delete_message(chat_id=int(a), message_id=mid)
                    except:
                        pass

            rec["admin_messages"] = {str(uid): rec["admin_messages"][str(uid)]}
            save_data(data_store)
            return

        if data == "admin_reject":
            awaiting_rejection_reason[uid] = {"request_id": found_id}
            await context.bot.send_message(chat_id=uid, text="Пожалуйста введите причину отказа.", reply_markup=KB_BACK_TO_MENU)
            return


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    text = (msg.text or "").strip()

    if uid in awaiting_rejection_reason:
        info = awaiting_rejection_reason.pop(uid)
        rid = info["request_id"]

        data_store = load_data()
        rec = data_store["requests"].get(str(rid))

        if not rec:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена.")
            return

        if not text:
            awaiting_rejection_reason[uid] = {"request_id": rid}
            await context.bot.send_message(chat_id=uid, text="Причина не может быть пустой.", reply_markup=KB_BACK_TO_MENU)
            return

        rec["status"] = "rejected"
        for a, mid in rec["admin_messages"].items():
            try:
                await context.bot.delete_message(chat_id=int(a), message_id=mid)
            except:
                pass

        await context.bot.send_message(chat_id=rec["user_id"], text=f"Ваш запрос помощи был отклонен. Причина: {text}\nВы можете подать заявку снова через 24 часа")

        rec["admin_messages"] = {}
        save_data(data_store)
        return

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

        for adm in ADMINS:
            await context.bot.send_message(chat_id=adm, text=f"Отзыв №{rid}\nТекст: {review_text}\nАвтор: {user_display_name(msg.from_user)}")

        await context.bot.send_message(chat_id=uid, text="Благодарим вас за отзыв, он был переслан администрации")
        await context.bot.send_message(chat_id=uid, text=WELCOME_TEXT, reply_markup=KB_START)

        context.user_data["awaiting_review"] = False
        return

    if uid in pending_reports:
        if text:
            if pending_reports[uid]["text"]:
                pending_reports[uid]["text"] += "\n\n---\n\n" + text
            else:
                pending_reports[uid]["text"] = text

            await context.bot.send_message(chat_id=uid, text="Доказательство принято!", reply_markup=KB_SUBMIT)
        return


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    if uid not in pending_reports:
        return
    photo = msg.photo[-1]
    pending_reports[uid]["files"].append({"file_id": photo.file_id, "type": "photo"})
    await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    if uid not in pending_reports:
        return

    file_id = None
    if msg.video:
        file_id = msg.video.file_id
    elif msg.document and msg.document.mime_type.startswith("video"):
        file_id = msg.document.file_id
    elif msg.video_note:
        file_id = msg.video_note.file_id

    if file_id:
        pending_reports[uid]["files"].append({"file_id": file_id, "type": "video"})
        await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)
    else:
        await context.bot.send_message(chat_id=uid, text="Не удалось распознать видео.")


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
