import asyncio
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo
)
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_FILE = Path("data.json")
ADMINS = [6931537294]

# ------------------ КЛАВИАТУРЫ ------------------
KB_START = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("Запросить помощь", callback_data="start_request"),
        InlineKeyboardButton("Написать спасибо", callback_data="write_thanks")
    ]]
)

KB_SUBMIT = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Предоставить на рассмотрение", callback_data="submit_request")]]
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

# ------------------ ПАМЯТЬ ------------------
pending_reports = {}
awaiting_rejection_reason = {}

# ------------------ ВСПОМОГАТЕЛЬНЫЕ ------------------
def load_data():
    if not DATA_FILE.exists():
        return {
            "requests": {},
            "next_request_id": 1,
            "reviews": {},
            "next_review_id": 1,
            "user_review_cooldowns": {}
        }
    return json.loads(DATA_FILE.read_text())


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, indent=4, ensure_ascii=False))


def user_display_name(user):
    return f"@{user.username}" if user.username else f"{user.first_name}"


# ------------------ СТАРТ ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Главное меню", reply_markup=KB_START)


# ------------------ ИНЛАЙН КНОПКИ ------------------
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    await query.answer()

    # УДАЛЯЕМ СТАРОЕ СООБЩЕНИЕ
    try:
        await query.message.delete()
    except Exception:
        pass

    if query.data == "go_menu":
        await context.bot.send_message(chat_id=uid, text="Главное меню", reply_markup=KB_START)
        return

    # --- Старт заявки помощи ---
    if query.data == "start_request":
        pending_reports[uid] = {"text": None, "files": []}
        await context.bot.send_message(
            chat_id=uid,
            text="Опишите вашу проблему или отправьте доказательства.",
            reply_markup=KB_SUBMIT
        )
        return

    # --- Сабмит заявки ---
    if query.data == "submit_request":
        rep = pending_reports.get(uid)
        if not rep:
            await context.bot.send_message(chat_id=uid, text="Вы ещё не начали заявку.", reply_markup=KB_START)
            return

        data = load_data()
        rid = data["next_request_id"]
        data["next_request_id"] += 1

        data["requests"][str(rid)] = {
            "user_id": uid,
            "username": "",
            "text": rep["text"] or "",
            "files": rep["files"],
            "timestamp": datetime.utcnow().isoformat(),
            "status": "pending",
            "admin_messages": {}
        }
        save_data(data)

        # Отправляем админу
        for adm in ADMINS:
            try:
                msg = await context.bot.send_message(
                    chat_id=adm,
                    text=f"НОВАЯ ЗАЯВКА №{rid}\nОт: {user_display_name(query.from_user)}\n\n{rep['text'] or '(без текста)'}",
                    reply_markup=KB_ADMIN_ACTIONS
                )
                data = load_data()
                data["requests"][str(rid)]["admin_messages"][str(adm)] = msg.message_id
                save_data(data)

                for f in rep["files"]:
                    if f["type"] == "photo":
                        await context.bot.send_photo(adm, f["file_id"])
                    else:
                        await context.bot.send_video(adm, f["file_id"])

            except Exception:
                logger.exception("ADMIN ERROR")

        del pending_reports[uid]

        # ВОЗВРАТ В МЕНЮ
        await context.bot.send_message(chat_id=uid, text="Заявка отправлена администрации.", reply_markup=KB_START)
        return

    # --- Пишем отзыв ---
    if query.data == "write_thanks":
        context.user_data["awaiting_review"] = True
        await context.bot.send_message(
            chat_id=uid,
            text="Пожалуйста напишите отзыв о нашей работе.",
            reply_markup=KB_BACK_TO_MENU
        )
        return

    # --- Админ: принять ---
    if query.data == "admin_accept":
        await query.message.edit_text("Вы приняли заявку.")
        return

    # --- Админ: отказать ---
    if query.data == "admin_reject":
        text = (
            "Пожалуйста укажите причину отказа.\n"
            "Она будет отправлена пользователю."
        )
        awaiting_rejection_reason[uid] = {
            "request_id": query.message.text.split("№")[1].split("\n")[0]
        }
        await context.bot.send_message(uid, text)
        return


# ------------------ ПОЛУЧЕНИЕ СООБЩЕНИЙ ------------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = msg.from_user.id
    text = msg.text or ""

    # ---------------- ОТЗЫВЫ — ОБРАБАТЫВАЕМ ПЕРВЫМИ ----------------
    if context.user_data.get("awaiting_review"):
        context.user_data["awaiting_review"] = False

        data = load_data()
        now = datetime.utcnow()

        cooldowns = data["user_review_cooldowns"]
        last = cooldowns.get(str(uid))

        if last and now < datetime.fromisoformat(last) + timedelta(days=1):
            await msg.reply_text(
                "Вы можете оставить отзыв снова через 24 часа.",
                reply_markup=KB_START
            )
            return

        # Сохраняем отзыв
        rid = data["next_review_id"]
        data["next_review_id"] += 1

        data["reviews"][str(rid)] = {
            "user_id": uid,
            "username": msg.from_user.username or "",
            "text": text,
            "timestamp": now.isoformat()
        }

        cooldowns[str(uid)] = now.isoformat()
        save_data(data)

        # Отправка админу
        for adm in ADMINS:
            try:
                await context.bot.send_message(
                    chat_id=adm,
                    text=f"ОТЗЫВ №{rid}\nОт: {user_display_name(msg.from_user)}\n\n{text}"
                )
            except Exception:
                pass

        # Спасибо пользователю
        await msg.reply_text(
            "Благодарим вас за отзыв, он был переслан администрации",
            reply_markup=KB_START
        )
        return

    # ---------------- ДОКАЗАТЕЛЬСТВА ДЛЯ ЗАЯВКИ ----------------
    if uid in pending_reports:
        pending_reports[uid]["text"] = text
        await msg.reply_text("Доказательство принято!")
        return


# ------------------ ДОКАЗАТЕЛЬСТВА ФОТО/ВИДЕО ------------------
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid in pending_reports:
        pending_reports[uid]["files"].append({"file_id": update.message.photo[-1].file_id, "type": "photo"})
        await update.message.reply_text("Доказательство принято!")


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    if uid in pending_reports:
        pending_reports[uid]["files"].append({"file_id": update.message.video.file_id, "type": "video"})
        await update.message.reply_text("Доказательство принято!")


# ------------------ ЗАПУСК ------------------
def main():
    app = ApplicationBuilder().token("YOUR_TOKEN").build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))

    app.run_polling()


if __name__ == "__main__":
    main()
