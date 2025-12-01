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

# ----------------- ВАШИ ВКЛЮЧЕННЫЕ ДАННЫЕ (НИЧЕГО НЕ ИЗМЕНЯТЬ) -----------------
BOT_TOKEN = "8180575933:AAFECe4o9hDGf5mEDrNBJoNek9B9m8Ak-2I"
ADMINS = [8180575933, 7569239259, 7825456486, 7605589697, 7983497123]
# --------------------------------------------------------------------------------

DATA_FILE = Path("data.json")
LOG_FMT = "%(asctime)s - %(levelname)s - %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
logger = logging.getLogger(__name__)

# Инициализация файла данных
if not DATA_FILE.exists():
    DATA_FILE.write_text(json.dumps({
        "next_request_id": 1,
        "requests": {},         # request_id -> {user_id, username, text, files: [{file_id, type}], admin_messages: {admin_id: message_id}, status}
        "next_review_id": 1,
        "reviews": {},          # review_id -> {user_id, username, text, timestamp}
        "user_review_cooldowns": {}  # user_id -> iso timestamp of last review
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def load_data():
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def save_data(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# Вспомогательные клавиатуры и тексты (точно как в вашем ТЗ)
WELCOME_TEXT = "Добро пожаловать. Для управления, пожалуйста, воспользуйтесь кнопками ниже."

KB_START = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("*Запросить помощь*", callback_data="start_request"),
        InlineKeyboardButton("*Написать спасибо*", callback_data="write_thanks")
    ]]
)

REQUEST_INSTRUCTION = (
    "Здесь вы можете запросить помощь в случаях если:\n"
    "-Вы являетесь мирным пользователем и вам угрожают\n"
    "-Вы столкнулись с актом педофилии В ВАШУ СТОРОНУ \n"
    "-Вы сейчас в конфликте и не являетесь спровоцировавшим конфликт/виновным в конфликте \n"
    "-Вас обманули на сумму более 5$\n\n"
    "Пожалуйста, коротко и ясно опишите ситуацию и приложите не менее 2 фото/видео доказательств (ПО ОТДЕЛЬНОСТИ), после чего нажмите \"Запросить помощь\""
)

KB_SUBMIT = InlineKeyboardMarkup(
    [[InlineKeyboardButton("*Предоставить на рассмотрение*", callback_data="submit_request")]]
)

# Тексты для админов и пользователей — строго соответствуют ТЗ
# Админское уведомление формируется динамически: содержит ❗ ЗАПРОС ПОМОЩИ №{n}❗ и т.д.

# Кнопки для админов при запросе (точные тексты)
KB_ADMIN_ACTIONS = InlineKeyboardMarkup(
    [[
        InlineKeyboardButton("принять запрос.", callback_data="admin_accept"),
        InlineKeyboardButton("отказаться помогать.", callback_data="admin_reject")
    ]]
)

# Тексты для отзывов
ASK_REVIEW_TEXT = "Пожалуйста напишите отзыв о нашей работе."
THANK_REVIEW_USER = "Благодарим вас за уделённое время, ваш отзыв будет передан администрации"

# ----------------- Хранилище временных состояний (в оперативной памяти) -----------------
# pending_reports: user_id -> {"text": str, "files": [{"file_id", "type"}]}
pending_reports = {}
# when admin pressed "отказаться помогать.", we will expect a follow-up reason from that admin:
awaiting_rejection_reason = {}  # admin_id -> {"request_id": id, "admin_msg_id": message.id}
# when admin accepted, no extra input needed
# ---------------------------------------------------------------------------------------

# ----------------- ХЕЛПЕРЫ -----------------
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
    data = query.data
    user = query.from_user
    uid = user.id

    if data == "start_request":
        # Начало заполнения запроса
        pending_reports[uid] = {"text": "", "files": []}
        await context.bot.send_message(chat_id=uid, text=REQUEST_INSTRUCTION, reply_markup=KB_SUBMIT)
        return

    if data == "submit_request":
        # Подать на рассмотрение
        pr = pending_reports.get(uid)
        if not pr:
            await context.bot.send_message(chat_id=uid, text="Пожалуйста сначала нажмите *Запросить помощь*.", reply_markup=KB_START)
            return

        # Проверки: текст и минимум 2 файлов
        if not pr["text"] or len(pr["files"]) < 2:
            # Соблюдаем строгость текста — не меняем реплики кроме необходимых указаний
            await context.bot.send_message(chat_id=uid, text="Пожалуйста, коротко и ясно опишите ситуацию и приложите не менее 2 фото/видео доказательств (ПО ОТДЕЛЬНОСТИ), после чего нажмите \"Запросить помощь\"")
            return

        # Создаём запись заявки
        data = load_data()
        rid = data["next_request_id"]
        data["next_request_id"] += 1

        request_record = {
            "user_id": uid,
            "username": user.username or "",
            "text": pr["text"],
            "files": pr["files"],  # list of dicts {file_id, type}
            "admin_messages": {},  # admin_id -> message_id
            "status": "open",
            "created_at": datetime.utcnow().isoformat()
        }
        data["requests"][str(rid)] = request_record
        save_data(data)

        # Отправляем админам уведомления
        admin_message_text = (
            f"❗ ЗАПРОС ПОМОЩИ №{rid}❗\n"
            f"{pr['text']} (скриншоты вместе с запросом)\n"
            f"Запросил помощь: {user_display_name(user)}"
        )

        # Для каждого админа отправим сообщение и прикрепим файлы (по отдельности)
        for admin_id in ADMINS:
            try:
                sent = await context.bot.send_message(chat_id=admin_id, text=admin_message_text, reply_markup=KB_ADMIN_ACTIONS)
                # запомним message_id, чтобы потом можно было удалить
                data = load_data()
                data["requests"][str(rid)]["admin_messages"][str(admin_id)] = sent.message_id
                save_data(data)

                # отправим файлы отдельно после текста (скриншоты вместе с запросом)
                # Отправляем по одному файлу (как сказано: ПО ОТДЕЛЬНОСТИ)
                for f in pr["files"]:
                    if f["type"] == "photo":
                        await context.bot.send_photo(chat_id=admin_id, photo=f["file_id"])
                    elif f["type"] == "video":
                        await context.bot.send_video(chat_id=admin_id, video=f["file_id"])
            except Exception as e:
                logger.exception(f"Не удалось отправить админ-уведомление админ {admin_id}: {e}")

        # Сообщаем юзеру, что заявка отправлена (в ТЗ такого сообщения явно нет; чтобы не менять реплики, просто удаляем локальную заявку)
        # Оставим короткое подтверждение в том же стиле, но без фантазий:
        await context.bot.send_message(chat_id=uid, text="Запрос отправлен на рассмотрение.")
        # очищаем pending
        pending_reports.pop(uid, None)
        return

    if data == "write_thanks":
        # Просим написать отзыв
        await context.bot.send_message(chat_id=uid, text=ASK_REVIEW_TEXT)
        # пометим, что ожидаем отзыв от этого пользователя (простая флаг-схема)
        context.user_data["awaiting_review"] = True
        return

    # Ниже — действия админов по заявке
    if data in ("admin_accept", "admin_reject"):
        # Нужно понять, к какой заявке относится нажатие — ищем по message_id
        msg = query.message
        if msg is None or msg.text is None:
            await query.message.reply_text("Ошибка при обработке.")
            return

        # Найдём request_id по message_id отправленного админского сообщения
        found_rid = None
        data = load_data()
        for rid, rec in data["requests"].items():
            if "admin_messages" in rec:
                for adm_id, m_id in rec["admin_messages"].items():
                    if int(adm_id) == uid and m_id == msg.message_id:
                        found_rid = rid
                        break
            if found_rid:
                break

        if not found_rid:
            # возможно админ нажал кнопку уже после удаления
            await query.message.reply_text("Заявка не найдена или уже обработана.")
            return

        rid = found_rid
        rec = data["requests"][rid]
        if rec["status"] != "open":
            await query.message.reply_text("Заявка уже обработана.")
            return

        if data == {}:
            data = load_data()

        if query.data == "admin_accept":
            # пометим как принято
            rec["status"] = "accepted"
            save_data(data)
            # удаляем сообщение этой заявки у всех других админов
            admin_messages = rec.get("admin_messages", {})
            for adm_str, msg_id in admin_messages.items():
                adm = int(adm_str)
                if adm == uid:
                    continue
                try:
                    await context.bot.delete_message(chat_id=adm, message_id=msg_id)
                except Exception:
                    pass
            # уведомляем юзера
            requester_id = rec["user_id"]
            admin_name = user_display_name(query.from_user)
            try:
                await context.bot.send_message(chat_id=requester_id, text=f"ваш запрос помощи принял админ {admin_name} В скором времени он свяжется с вами.")
            except Exception:
                logger.exception("Не удалось уведомить заявителя об принятии.")
            # удаляем все админские кнопки у этого админа (редактируем исходное сообщение)
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            # удаляем остальные админские сообщения (включая у того, кто нажал) для чистоты
            for adm_str, msg_id in admin_messages.items():
                adm = int(adm_str)
                try:
                    await context.bot.delete_message(chat_id=adm, message_id=msg_id)
                except Exception:
                    pass
            # сохраним статус
            rec["admin_messages"] = {}
            data["requests"][rid] = rec
            save_data(data)
            return

        if query.data == "admin_reject":
            # У администрации: попросить причину отказа
            # Сохраним ожидание причины от этого админа
            awaiting_rejection_reason[uid] = {"request_id": rid, "admin_msg_id": msg.message_id}
            # Попросим админа написать причину (строго текст по ТЗ: "Пожалуйста, напишите причину отказа")
            try:
                await context.bot.send_message(chat_id=uid, text="Пожалуйста, напишите причину отказа")
            except Exception:
                pass
            return

    # ничего не подошло
    return


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    text = msg.text or ""

    # 1) Если админ ожидается причина отказа — обработаем
    if uid in awaiting_rejection_reason:
        info = awaiting_rejection_reason.pop(uid)
        rid = info["request_id"]
        data = load_data()
        rec = data["requests"].get(str(rid))
        if not rec:
            await context.bot.send_message(chat_id=uid, text="Заявка не найдена.")
            return
        # статус -> rejected
        rec["status"] = "rejected"
        save_data(data)
        # Удаляем сообщение заявки у всех админов
        for adm_str, msg_id in rec.get("admin_messages", {}).items():
            adm = int(adm_str)
            try:
                await context.bot.delete_message(chat_id=adm, message_id=msg_id)
            except Exception:
                pass
        # отправляем юзеру уведомление об отказе с указанной причиной
        reason = text
        requester_id = rec["user_id"]
        try:
            # Точный текст сообщения (с двумя пробелами как в ТЗ и новой строкой)
            await context.bot.send_message(chat_id=requester_id, text=f"Ваш запрос помощи был отклонен.  Причина: {reason}\nВы можете подать заявку снова через 24 часа")
        except Exception:
            logger.exception("Не удалось отправить сообщение об отказе пользователю.")
        # отметим admin_messages пустыми
        rec["admin_messages"] = {}
        data["requests"][str(rid)] = rec
        save_data(data)
        return

    # 2) Если пользователь отправляет текст как часть заявки — сохраним
    # Если у пользователя есть pending_reports -> считаем текст частью описания
    if uid in pending_reports:
        # Если сообщение содержит media (handled elsewhere) and text -> include text
        if msg.photo or msg.video:
            # обработка мультимедиа в отдельных handler'ах (ниже) — но на случай, пусть пропускаем
            pass
        if text:
            pending_reports[uid]["text"] = text
            await context.bot.send_message(chat_id=uid, text="Описание сохранено. Добавьте не менее 2 фото/видео (по отдельности), затем нажмите \"Предоставить на рассмотрение\"")
        return

    # 3) Обработка отзывов (кнопка *Написать спасибо*)
    if context.user_data.get("awaiting_review"):
        # проверяем кулдаун 1 день
        data = load_data()
        cooldowns = data.get("user_review_cooldowns", {})
        last_iso = cooldowns.get(str(uid))
        now = datetime.utcnow()
        if last_iso:
            last = datetime.fromisoformat(last_iso)
            if now < last + timedelta(days=1):
                # Кулдаун не прошел
                await context.bot.send_message(chat_id=uid, text="Вы можете оставить отзыв снова через 24 часа")
                context.user_data["awaiting_review"] = False
                return
        # Примем отзыв
        review_text = text or ""
        rid = data["next_review_id"]
        data["next_review_id"] += 1
        data["reviews"][str(rid)] = {
            "user_id": uid,
            "username": msg.from_user.username or "",
            "text": review_text,
            "timestamp": now.isoformat()
        }
        # обновим кулдаун
        data["user_review_cooldowns"][str(uid)] = now.isoformat()
        save_data(data)

        # уведомляем пользователя и админов
        await context.bot.send_message(chat_id=uid, text=THANK_REVIEW_USER)
        # уведомление админам (точный текст)
        admin_text = f"Отзыв №{rid}\nТекст отзыва: {review_text}\nНаписал: {user_display_name(msg.from_user)}"
        for adm in ADMINS:
            try:
                await context.bot.send_message(chat_id=adm, text=admin_text)
            except Exception:
                pass

        context.user_data["awaiting_review"] = False
        return

    # 4) Иначе ничего не делаем (чтобы не вмешиваться)
    return


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg is None or msg.from_user is None:
        return
    uid = msg.from_user.id
    if uid not in pending_reports:
        # фото вне заявки — игнорируем
        return
    # Получим file_id самого большого фото
    photo = msg.photo[-1]
    file_id = photo.file_id
    pending_reports[uid]["files"].append({"file_id": file_id, "type": "photo"})
    await context.bot.send_message(chat_id=uid, text=f"Принято фото. Сейчас прикреплено: {len(pending_reports[uid]['files'])} доказательства(ел).")

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
    await context.bot.send_message(chat_id=uid, text=f"Принято видео. Сейчас прикреплено: {len(pending_reports[uid]['files'])} доказательства(ел).")


# ----------------- Основная функция -----------------
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), message_handler))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
