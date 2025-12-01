# bot.py
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

# ----------------- ВАШИ ДАННЫЕ -----------------
BOT_TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [8180575933, 7569239259, 7825456486, 7605589697, 7983497123]
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


# ----------------- Хранилища -----------------
pending_reports = {}
awaiting_rejection_reason = {}
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
        InlineKeyboardButton("Предоставить на рассмотрение", callback_data="submit_request"),
        InlineKeyboardButton("Назад", callback_data="back_to_start")
    ]]
)

KB_ADMIN_ACTIONS = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("принять запрос.", callback_data="admin_accept"),
        InlineKeyboardButton("отказаться помогать.", callback_data="admin_reject")
    ]]
)

KB_BACK = InlineKeyboardMarkup(
    [[InlineKeyboardButton("Назад", callback_data="back_to_start")]]
)

WELCOME_TEXT = "Добро пожаловать. Для управления, пожалуйста, воспользуйтесь кнопками ниже."

REQUEST_INSTRUCTION = (
    "Здесь вы можете запросить помощь в случаях если:\n"
    "-Вы являетесь мирным пользователем и вам угрожают\n"
    "-Вы столкнулись с актом педофилии В ВАШУ СТОРОНУ \n"
    "-Вы сейчас в конфликте и не являетесь спровоцировавшим конфликт/виновным в конфликте \n"
    "-Вас обманули на сумму более 5$\n\n"
    "Пожалуйста, коротко и ясно опишите ситуацию и приложите не менее 2 фото/видео доказательств (ПО ОТДЕЛЬНОСТИ), после чего нажмите \"Предоставить на рассмотрение\""
)

ASK_REVIEW_TEXT = "Пожалуйста напишите отзыв о нашей работе."


# ----------------- ХЕЛПЕР -----------------
def user_display_name(user):
    if user.username:
        return f"@{user.username}"
    else:
        return f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)


# ----------------- ОБРАБОТЧИКИ -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user is None:
        return
    await context.bot.send_message(chat_id=update.effective_chat.id, text=WELCOME_TEXT, reply_markup=KB_START)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    uid = user.id
    data = query.data

    # ----------------- "Назад" -----------------
    if data == "back_to_start":
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(chat_id=uid, text=WELCOME_TEXT, reply_markup=KB_START)
        return

    # ----------------- Начало запроса -----------------
    if data == "start_request":
        pending_reports[uid] = {"text": "", "files": []}
        await context.bot.send_message(chat_id=uid, text=REQUEST_INSTRUCTION, reply_markup=KB_SUBMIT)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # ----------------- Отправка заявки -----------------
    if data == "submit_request":
        pr = pending_reports.get(uid)
        if not pr:
            await context.bot.send_message(chat_id=uid, text="Пожалуйста сначала нажмите 'Запросить помощь'.", reply_markup=KB_START)
            return

        if not pr["text"] or len(pr["files"]) < 2:
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, коротко и ясно опишите ситуацию и прикрепите не менее 2 фото/видео доказательств.", reply_markup=KB_SUBMIT)
            return

        data_store = load_data()
        rid = data_store["next_request_id"]
        data_store["next_request_id"] += 1

        request_record = {
            "user_id": uid,
            "username": user.username or "",
            "text": pr["text"],
            "files": pr["files"],
            "admin_messages": {},
            "status": "open",
            "created_at": datetime.utcnow().isoformat()
        }
        data_store["requests"][str(rid)] = request_record
        save_data(data_store)

        admin_message_text = (
            f"❗ ЗАПРОС ПОМОЩИ №{rid}❗\n"
            f"{pr['text']}\n"
            f"Запросил помощь: {user_display_name(user)}"
        )

        for admin_id in ADMINS:
            try:
                sent = await context.bot.send_message(chat_id=admin_id, text=admin_message_text, reply_markup=KB_ADMIN_ACTIONS)
                data_store = load_data()
                data_store["requests"][str(rid)]["admin_messages"][str(admin_id)] = sent.message_id
                save_data(data_store)

                for f in pr["files"]:
                    if f["type"] == "photo":
                        await context.bot.send_photo(chat_id=admin_id, photo=f["file_id"])
                    elif f["type"] == "video":
                        await context.bot.send_video(chat_id=admin_id, video=f["file_id"])
            except Exception as e:
                logger.exception(f"Не удалось отправить админ-уведомление админ {admin_id}: {e}")

        try:
            await query.message.delete()
        except Exception:
            pass

        # После отправки заявки — возвращаем в главное меню
        await context.bot.send_message(chat_id=uid, text="Запрос отправлен на рассмотрение.", reply_markup=KB_START)
        pending_reports.pop(uid, None)
        return

    # ----------------- Написать отзыв -----------------
    if data == "write_thanks":
        await context.bot.send_message(chat_id=uid, text=ASK_REVIEW_TEXT, reply_markup=KB_BACK)
        context.user_data["awaiting_review"] = True
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    # ----------------- Действия админов -----------------
    if data in ("admin_accept", "admin_reject"):
        msg = query.message
        if msg is None:
            return

        found_rid = None
        data_store = load_data()
        for rid, rec in data_store["requests"].items():
            if "admin_messages" in rec:
                for adm_id, m_id in rec["admin_messages"].items():
                    if int(adm_id) == uid and m_id == (msg.message_id if msg else 0):
                        found_rid = rid
                        break
            if found_rid:
                break

        if not found_rid:
            return

        rid = found_rid
        rec = data_store["requests"][rid]
        if rec["status"] != "open":
            return

        if data == "admin_accept":
            rec["status"] = "accepted"
            save_data(data_store)
            requester_id = rec["user_id"]
            admin_name = user_display_name(user)
            try:
                await context.bot.send_message(chat_id=requester_id, text=f"ваш запрос помощи принял админ {admin_name} В скором времени он свяжется с вами.")
            except Exception:
                logger.exception("Не удалось уведомить заявителя об принятии.")
            for adm_str, msg_id in rec.get("admin_messages", {}).items():
                adm = int(adm_str)
                try:
                    await context.bot.delete_message(chat_id=adm, message_id=msg_id)
                except Exception:
                    pass
            rec["admin_messages"] = {}
            data_store[rid] = rec
            save_data(data_store)
            return

        if data == "admin_reject":
            awaiting_rejection_reason[uid] = {"request_id": rid}
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, напишите причину отказа", reply_markup=KB_BACK)
            return


# ----------------- Обработчики сообщений, фото и видео -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    text = msg.text or ""

    # Причина отказа админом
    if uid in awaiting_rejection_reason:
        info = awaiting_rejection_reason.pop(uid)
        rid = info["request_id"]
        data_store = load_data()
        rec = data_store["requests"].get(str(rid))
        if not rec:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена.")
            return
        rec["status"] = "rejected"
        for adm_str, msg_id in rec.get("admin_messages", {}).items():
            adm = int(adm_str)
            try:
                await context.bot.delete_message(chat_id=adm, message_id=msg_id)
            except Exception:
                pass
        reason = text
        requester_id = rec["user_id"]
        try:
            await context.bot.send_message(chat_id=requester_id, text=f"Ваш запрос помощи был отклонен.  Причина: {reason}\nВы можете подать заявку снова через 24 часа")
        except Exception:
            logger.exception("Не удалось отправить сообщение об отказе пользователю.")
        rec["admin_messages"] = {}
        data_store["requests"][str(rid)] = rec
        save_data(data_store)
        return

    # Добавление текста в заявку
    if uid in pending_reports:
        if text:
            pending_reports[uid]["text"] = text
            await context.bot.send_message(chat_id=uid, text="Доказательство принято!")
        return

    # Обработка отзывов
    if context.user_data.get("awaiting_review"):
        await context.bot.send_message(chat_id=uid, text="Доказательство принято!")
        review_text = text
        now = datetime.utcnow()
        rid = load_data()["next_review_id"]

        # Сохраняем отзыв в data.json
        data_store = load_data()
        data_store["next_review_id"] += 1
        data_store["reviews"][str(rid)] = {
            "user_id": uid,
            "username": msg.from_user.username or "",
            "text": review_text,
            "timestamp": now.isoformat()
        }
        data_store["user_review_cooldowns"][str(uid)] = now.isoformat()
        save_data(data_store)

        # Уведомляем пользователя и админов
        await context.bot.send_message(chat_id=uid, text="Благодарим вас за отзыв, он был переслан администрации", reply_markup=KB_START)
        for adm in ADMINS:
            try:
                await context.bot.send_message(chat_id=adm, text=f"Отзыв №{rid}\nТекст отзыва: {review_text}\nНаписал: {user_display_name(msg.from_user)}")
            except Exception:
                pass

        context.user_data["awaiting_review"] = False
        return


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    if uid not in pending_reports:
        return
    photo = msg.photo[-1]
    file_id = photo.file_id
    pending_reports[uid]["files"].append({"file_id": file_id, "type": "photo"})
    await context.bot.send_message(chat_id=uid, text="Доказательство принято!")


async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    if uid not in pending_reports:
        return
    video = msg.video
    file_id = video.file_id
    pending_reports[uid]["files"].append({"file_id": file_id, "type": "video"})
    await context.bot.send_message(chat_id=uid, text="Доказательство принято!")


# ----------------- Основная функция -----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
