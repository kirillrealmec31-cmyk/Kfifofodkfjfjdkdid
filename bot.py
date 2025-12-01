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

# ----------------- ВАШИ ВКЛЮЧЕННЫЕ ДАННЫЕ (НЕ ИЗМЕНЯТЬ) -----------------
BOT_TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [8180575933, 7569239259, 7825456486, 7605589697, 7983497123]
# --------------------------------------------------------------------------------

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
        InlineKeyboardButton("Предоставить на рассмотрение", callback_data="submit_request"),
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

WELCOME_TEXT = "Главное меню"


# ----------------- ХЕЛПЕРЫ -----------------
def user_display_name(user):
    if user is None:
        return "Unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    return f"{user.first_name or ''} {user.last_name or ''}".strip() or str(user.id)


# ----------------- ОБРАБОТЧИКИ -----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Поддержка как команды /start (message) так и колбэка "go_menu"
    target_chat = update.effective_chat.id if update.effective_chat else (update.message.chat_id if update.message else None)
    if target_chat:
        await context.bot.send_message(chat_id=target_chat, text=WELCOME_TEXT, reply_markup=KB_START)


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = query.from_user
    uid = user.id
    data = query.data

    # удаляем сообщение с кнопкой, откуда пришёл клик (чтобы не засорять чат)
    try:
        if query.message:
            await query.message.delete()
    except Exception:
        pass

    # главная навигация
    if data == "go_menu":
        await context.bot.send_message(chat_id=uid, text=WELCOME_TEXT, reply_markup=KB_START)
        return

    # начало заявки
    if data == "start_request":
        pending_reports[uid] = {"text": None, "files": []}
        await context.bot.send_message(chat_id=uid, text="Опишите вашу проблему или отправьте доказательства.", reply_markup=KB_SUBMIT)
        return

    # отправка заявки
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

        admin_message_text = (
            f"❗ ЗАПРОС ПОМОЩИ №{rid} ❗\n"
            f"{pr['text'] or '(без текста)'}\n\n"
            f"Запросил: {user_display_name(user)}"
        )

        for admin_id in ADMINS:
            try:
                sent = await context.bot.send_message(chat_id=admin_id, text=admin_message_text, reply_markup=KB_ADMIN_ACTIONS)
                data_store = load_data()
                data_store["requests"][str(rid)]["admin_messages"][str(admin_id)] = sent.message_id
                save_data(data_store)

                # отправляем медиа отдельно (по одному)
                for f in pr["files"]:
                    if f.get("type") == "photo":
                        await context.bot.send_photo(chat_id=admin_id, photo=f["file_id"])
                    elif f.get("type") == "video":
                        await context.bot.send_video(chat_id=admin_id, video=f["file_id"])
            except Exception:
                logger.exception(f"Не удалось отправить админ-уведомление админ {admin_id}")

        # удаляем локальную pending-заявку и возвращаем пользователя в главное меню
        pending_reports.pop(uid, None)
        await context.bot.send_message(chat_id=uid, text="Запрос отправлен администрации.", reply_markup=KB_START)
        return

    # отзыв (переходим в режим ожидания текста отзыва)
    if data == "write_thanks":
        context.user_data["awaiting_review"] = True
        await context.bot.send_message(chat_id=uid, text="Пожалуйста напишите отзыв о нашей работе.", reply_markup=KB_BACK_TO_MENU)
        return

    # действия админа
    if data in ("admin_accept", "admin_reject"):
        # чтобы понимать, с какой заявки — ищем request_id в data по message_id
        msg = query.message
        if msg is None:
            return

        # Найдём request_id по message_id
        found_rid = None
        data_store = load_data()
        for rid, rec in data_store["requests"].items():
            admin_msgs = rec.get("admin_messages", {})
            if str(query.from_user.id) in admin_msgs and admin_msgs[str(query.from_user.id)] == msg.message_id:
                found_rid = rid
                break
            # также попробуем более общую проверку на наличие message_id в admin_messages
            for adm_str, m_id in admin_msgs.items():
                if m_id == msg.message_id:
                    found_rid = rid
                    break
            if found_rid:
                break

        if not found_rid:
            # Если не нашли, просто уведомим админа (или игнорируем)
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
            # удалить сообщения админам
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
            # попросим причину у админа — пометим его в awaiting_rejection_reason
            awaiting_rejection_reason[uid] = {"request_id": rid}
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, напишите причину отказа", reply_markup=KB_BACK_TO_MENU)
            return


# ----------------- ОБРАБОТКА ТЕКСТОВ, ФОТО, ВИДЕО -----------------
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    text = (msg.text or "").strip()

    # 1) Если админ ранее нажимал "отказаться" — он должен написать причину
    if uid in awaiting_rejection_reason:
        info = awaiting_rejection_reason.pop(uid)
        rid = info.get("request_id")
        data_store = load_data()
        rec = data_store["requests"].get(str(rid))
        if not rec:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена.")
            return
        rec["status"] = "rejected"
        save_data(data_store)
        # удаляем сообщения заявки у админов
        for adm_str, msg_id in rec.get("admin_messages", {}).items():
            adm = int(adm_str)
            try:
                await context.bot.delete_message(chat_id=adm, message_id=msg_id)
            except Exception:
                pass
        # уведомляем пользователя — отправляем причину
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

    # 2) Если пользователь в процессе составления заявки — добавляем текст как доказательство
    # Но предварительно обработаем отзывы — они имеют приоритет
    if context.user_data.get("awaiting_review"):
        # Сначала подтвердим получение (как просили — "Доказательство принято!" первый отклик)
        await context.bot.send_message(chat_id=uid, text="Доказательство принято!")

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

        # уведомление админам
        for adm in ADMINS:
            try:
                await context.bot.send_message(chat_id=adm, text=f"Отзыв №{rid}\nТекст отзыва: {review_text}\nНаписал: {user_display_name(msg.from_user)}")
            except Exception:
                pass

        # благодарность пользователю и возврат в главное меню (без инлайн под благодарностью)
        await context.bot.send_message(chat_id=uid, text="Благодарим вас за отзыв, он был переслан администрации", reply_markup=KB_START)
        context.user_data["awaiting_review"] = False
        return

    # Теперь обработка добавления текста в заявку (если есть pending)
    if uid in pending_reports:
        if text:
            pending_reports[uid]["text"] = text
            await context.bot.send_message(chat_id=uid, text="Доказательство принято!", reply_markup=KB_SUBMIT)
        return

    # Иначе — ничего не делаем
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
    await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)


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
    await context.bot.send_message(chat_id=uid, text=f"Доказательство принято! Сейчас прикреплено: {len(pending_reports[uid]['files'])}", reply_markup=KB_SUBMIT)


# ----------------- ОСНОВНАЯ -----------------
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
