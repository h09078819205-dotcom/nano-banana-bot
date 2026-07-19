import os
import logging
import asyncio
import base64
import json
import re
from io import BytesIO

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ─── CONFIG ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

OWNER_ID = 1343284628
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

# Available models
MODELS = {
    "nano2": {
        "name": "🍌 Nano Banana 2",
        "model": "gemini-2.5-gemini-2.5-flash-image",
        "desc": "Быстрая, качественная"
    },
    "imagen": {
        "name": "🖼 Imagen 4",
        "model": "imagen-4.0-generate-preview-06-06",
        "desc": "Максимальное качество фото"
    },
}

DEFAULT_MODEL = "nano2"

# Available quality levels
QUALITIES = {
    "1K": {"name": "1K (стандарт)", "size": "1K"},
    "2K": {"name": "2K (высокое)", "size": "2K"},
    "4K": {"name": "4K (максимум)", "size": "4K"},
}

DEFAULT_QUALITY = "1K"

# ─── LOGGING ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── STATE ────────────────────────────────────────────────────────────
user_models = {}
user_qualities = {}
active_generations = {}
cancelled_users = set()
retry_prompts = {}
processed_messages = set()
processed_callbacks = set()
authorized_users = {OWNER_ID}
ACCESS_CODE = "dino2025"


# ─── HELPERS ──────────────────────────────────────────────────────────

def get_user_model(user_id: int) -> dict:
    key = user_models.get(user_id, DEFAULT_MODEL)
    return MODELS[key]


def parse_prompts(text: str) -> list:
    parts = re.split(r'\(\d+\)\s*', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts

    parts = re.split(r'\d+\.\s+', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts

    return [text.strip()] if text.strip() else []


async def generate_image(prompt: str, model_id: str, user_id: int, quality: str = "1K") -> bytes | None:
    if user_id in cancelled_users:
        return None

    # Check if using Imagen model
    is_imagen = "imagen" in model_id

    if is_imagen:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:predict"
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        }
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": "16:9",
                "sampleCount": 1
            }
        }
    else:
        url = GEMINI_API_URL.format(model=model_id)
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        }
        payload = {
            "contents": [{
                "parts": [{"text": f"Generate an image: {prompt}"}]
            }],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"]
            }
        }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Gemini API error {resp.status}: {error_text}")
                    return None

                data = await resp.json()

                if is_imagen:
                    predictions = data.get("predictions", [])
                    if predictions:
                        b64 = predictions[0].get("bytesBase64Encoded", "")
                        if b64:
                            return base64.b64decode(b64)
                else:
                    candidates = data.get("candidates", [])
                    for candidate in candidates:
                        content = candidate.get("content", {})
                        parts = content.get("parts", [])
                        for part in parts:
                            if "inlineData" in part:
                                b64 = part["inlineData"].get("data", "")
                                if b64:
                                    return base64.b64decode(b64)

                logger.error(f"No image in response: {json.dumps(data)[:500]}")
                return None

    except asyncio.TimeoutError:
        logger.error("Gemini API timeout")
        return None
    except Exception as e:
        logger.error(f"Gemini API exception: {e}")
        return None


# ─── COMMAND HANDLERS ─────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id not in authorized_users:
        await update.message.reply_text("🔒 Введи код доступа для активации бота:")
        return

    await update.message.reply_text(
        "🦕 *Nano Banana Dino Generator*\n\n"
        "Отправь промпт — получи картинку!\n\n"
        "📝 Один промпт = одна картинка\n"
        "📄 Отправь .txt файл с промптами для пакетной генерации\n\n"
        "Команды:\n"
        "/models — выбрать модель\n"
        "/quality — выбрать качество (1K/2K/4K)\n"
        "/cancel — отменить генерацию\n"
        "/help — помощь",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🦕 *Как пользоваться:*\n\n"
        "1️⃣ Отправь текстовый промпт — бот сгенерирует картинку\n"
        "2️⃣ Отправь .txt файл с промптами в формате:\n"
        "   `(1) промпт один`\n"
        "   `(2) промпт два`\n"
        "   или\n"
        "   `1. промпт один`\n"
        "   `2. промпт два`\n\n"
        "3️⃣ Под каждой картинкой есть кнопка 🔄 для повторной генерации\n\n"
        "*Команды:*\n"
        "/models — выбрать модель генерации\n"
        "/quality — выбрать качество (1K/2K/4K)\n"
        "/cancel — отменить текущую генерацию\n"
        "/about — о боте",
        parse_mode="Markdown"
    )


async def cmd_about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = get_user_model(update.effective_user.id)
    quality = user_qualities.get(update.effective_user.id, DEFAULT_QUALITY)
    await update.message.reply_text(
        "🦕 *Nano Banana Dino Generator*\n\n"
        f"Текущая модель: {model['name']}\n"
        f"Качество: {quality}\n"
        f"Формат: 16:9\n"
        f"API: Google Gemini\n\n"
        "Создано для канала про динозавров 🦖",
        parse_mode="Markdown"
    )


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text("🔒 Бот не активирован.")
        return

    current = user_models.get(user_id, DEFAULT_MODEL)
    buttons = []
    for key, info in MODELS.items():
        check = " ✅" if key == current else ""
        buttons.append([InlineKeyboardButton(
            f"{info['name']}{check}",
            callback_data=f"model:{key}"
        )])

    await update.message.reply_text(
        "🎨 *Выбери модель генерации:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )


async def cmd_quality(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text("🔒 Бот не активирован.")
        return

    current = user_qualities.get(user_id, DEFAULT_QUALITY)
    buttons = []
    for key, info in QUALITIES.items():
        check = " ✅" if key == current else ""
        buttons.append([InlineKeyboardButton(
            f"{info['name']}{check}",
            callback_data=f"quality:{key}"
        )])

    await update.message.reply_text(
        "🖼 *Выбери качество изображения:*\n\n"
        "1K — быстрее\n2K — выше детализация\n4K — максимум (медленнее)",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cancelled_users.add(user_id)
    await update.message.reply_text("🛑 Генерация отменена.")


# ─── CALLBACK HANDLER ─────────────────────────────────────────────────

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    callback_id = f"{query.message.message_id}:{query.data}"

    if callback_id in processed_callbacks:
        await query.answer("⏳ Уже обработано")
        return
    processed_callbacks.add(callback_id)

    user_id = query.from_user.id
    if user_id not in authorized_users:
        await query.answer("🔒 Нет доступа")
        return

    data = query.data

    if data.startswith("model:"):
        model_key = data.split(":", 1)[1]
        if model_key in MODELS:
            user_models[user_id] = model_key
            model = MODELS[model_key]
            await query.answer(f"Выбрана: {model['name']}")
            await query.edit_message_text(f"✅ Модель: {model['name']}\n{model['desc']}")
        return

    if data.startswith("quality:"):
        quality_key = data.split(":", 1)[1]
        if quality_key in QUALITIES:
            user_qualities[user_id] = quality_key
            quality = QUALITIES[quality_key]
            await query.answer(f"Качество: {quality['name']}")
            await query.edit_message_text(f"✅ Качество: {quality['name']}")
        return

    if data.startswith("retry:"):
        msg_id = data.split(":", 1)[1]
        prompt = retry_prompts.get(msg_id)
        if not prompt:
            await query.answer("❌ Промпт не найден")
            return

        await query.answer("🔄 Повторная генерация...")
        model_config = get_user_model(user_id)
        quality = user_qualities.get(user_id, DEFAULT_QUALITY)
        status_msg = await query.message.reply_text(
            f"🎨 Генерирую повтор...\n📋 {model_config['name']} | {quality}"
        )

        image_data = await generate_image(prompt, model_config["model"], user_id, quality)
        if image_data:
            retry_key = str(status_msg.message_id)
            retry_prompts[retry_key] = prompt
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё раз", callback_data=f"retry:{retry_key}")
            ]])
            await query.message.reply_photo(photo=BytesIO(image_data), reply_markup=keyboard)
            await status_msg.delete()
        else:
            await status_msg.edit_text("❌ Ошибка генерации.")
        return

    if len(processed_callbacks) > 5000:
        processed_callbacks.clear()


# ─── MESSAGE HANDLERS ─────────────────────────────────────────────────

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if message_id in processed_messages:
        return
    processed_messages.add(message_id)

    if user_id not in authorized_users:
        if update.message.text.strip() == ACCESS_CODE:
            authorized_users.add(user_id)
            await update.message.reply_text("✅ Доступ активирован! Отправляй промпты.")
            if user_id != OWNER_ID:
                try:
                    user = update.effective_user
                    await context.bot.send_message(
                        OWNER_ID,
                        f"🆕 Новый пользователь: {user.first_name} "
                        f"(@{user.username or 'нет'}, ID: {user_id})"
                    )
                except Exception:
                    pass
            return
        else:
            await update.message.reply_text("🔒 Неверный код.")
            return

    text = update.message.text.strip()
    if not text:
        return

    prompts = parse_prompts(text)
    await process_prompts(update, context, prompts, user_id)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if message_id in processed_messages:
        return
    processed_messages.add(message_id)

    if user_id not in authorized_users:
        await update.message.reply_text("🔒 Бот не активирован.")
        return

    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("📄 Отправь .txt файл с промптами.")
        return

    file = await context.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("cp1251", errors="replace")

    prompts = parse_prompts(text)
    if not prompts:
        await update.message.reply_text("❌ Не удалось найти промпты.")
        return

    await process_prompts(update, context, prompts, user_id)


async def process_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE,
                          prompts: list, user_id: int):
    if user_id in cancelled_users:
        cancelled_users.discard(user_id)

    total = len(prompts)
    model_config = get_user_model(user_id)
    quality = user_qualities.get(user_id, DEFAULT_QUALITY)

    if total == 1:
        status_msg = await update.message.reply_text(
            f"🎨 Генерирую...\n📋 {model_config['name']} | {quality}"
        )
    else:
        status_msg = await update.message.reply_text(
            f"🎨 Генерирую {total} изображений...\n📋 {model_config['name']} | {quality}\n"
            f"Для отмены: /cancel"
        )

    success = 0
    errors_count = 0

    for i, prompt in enumerate(prompts):
        if user_id in cancelled_users:
            cancelled_users.discard(user_id)
            await status_msg.edit_text(f"🛑 Остановлено. Сгенерировано: {success}/{total}")
            return

        if total > 1:
            try:
                await status_msg.edit_text(
                    f"🎨 Генерирую {i+1}/{total}...\n📋 {model_config['name']} | {quality}"
                )
            except Exception:
                pass

        image_data = await generate_image(prompt, model_config["model"], user_id, quality)

        if image_data:
            retry_key = f"{update.message.message_id}_{i}"
            retry_prompts[retry_key] = prompt
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё раз", callback_data=f"retry:{retry_key}")
            ]])
            caption = f"({i+1})" if total > 1 else None
            await update.message.reply_photo(
                photo=BytesIO(image_data), caption=caption, reply_markup=keyboard
            )
            success += 1
        else:
            errors_count += 1
            await update.message.reply_text(f"❌ Ошибка генерации ({i+1})")

    if total > 1:
        await status_msg.edit_text(
            f"✅ Готово! Успешно: {success}/{total}"
            + (f", ошибок: {errors_count}" if errors_count else "")
        )
    else:
        try:
            await status_msg.delete()
        except Exception:
            pass

    if len(processed_messages) > 5000:
        processed_messages.clear()
    if len(retry_prompts) > 2000:
        retry_prompts.clear()


# ─── MAIN ─────────────────────────────────────────────────────────────

async def post_init(application):
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("help", "Помощь"),
        BotCommand("models", "Выбрать модель"),
        BotCommand("quality", "Качество 1K/2K/4K"),
        BotCommand("cancel", "Отменить генерацию"),
        BotCommand("about", "О боте"),
    ]
    await application.bot.set_my_commands(commands)


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not set!")
        return
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set!")
        return

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("quality", cmd_quality))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("🦕 Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
