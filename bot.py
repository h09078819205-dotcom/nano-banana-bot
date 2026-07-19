import os
import logging
import asyncio
import re
import urllib.parse
from io import BytesIO

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ─── CONFIG ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OWNER_ID = 1343284628

# Pollinations.ai - free, no key needed
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"

MODELS = {
    "flux": {
        "name": "\U0001f31f Flux",
        "model": "flux",
        "desc": "High quality, default"
    },
    "flux-realism": {
        "name": "\U0001f4f7 Flux Realism",
        "model": "flux-realism",
        "desc": "Photorealistic style"
    },
    "turbo": {
        "name": "\u26a1 Turbo",
        "model": "turbo",
        "desc": "Fastest generation"
    },
}
DEFAULT_MODEL = "flux"

# ─── LOGGING ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── STATE ────────────────────────────────────────────────────────────
user_models = {}
cancelled_users = set()
retry_prompts = {}
processed_messages = set()
processed_callbacks = set()
authorized_users = {OWNER_ID}
ACCESS_CODE = "dino2025"


# ─── HELPERS ──────────────────────────────────────────────────────────

def get_user_model(user_id):
    key = user_models.get(user_id, DEFAULT_MODEL)
    return MODELS[key]


def parse_prompts(text):
    parts = re.split(r'\(\d+\)\s*', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts
    parts = re.split(r'\d+\.\s+', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts
    return [text.strip()] if text.strip() else []


async def generate_image(prompt, model_name, user_id):
    if user_id in cancelled_users:
        return None

    encoded_prompt = urllib.parse.quote(prompt)
    url = POLLINATIONS_URL.format(prompt=encoded_prompt)
    params = {
        "width": 1920,
        "height": 1080,
        "model": model_name,
        "nologo": "true",
        "safe": "false",
    }

    param_str = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{param_str}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(full_url, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status != 200:
                    logger.error(f"Pollinations error {resp.status}")
                    return None

                content_type = resp.headers.get("Content-Type", "")
                if "image" not in content_type:
                    error_text = await resp.text()
                    logger.error(f"Not an image response: {error_text[:300]}")
                    return None

                image_data = await resp.read()
                if len(image_data) < 1000:
                    logger.error(f"Image too small: {len(image_data)} bytes")
                    return None

                return image_data

    except asyncio.TimeoutError:
        logger.error("Pollinations timeout")
        return None
    except Exception as e:
        logger.error(f"Pollinations exception: {e}")
        return None


# ─── COMMAND HANDLERS ─────────────────────────────────────────────────

async def cmd_start(update, context):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text(
            "\U0001f512 \u0412\u0432\u0435\u0434\u0438 \u043a\u043e\u0434 \u0434\u043e\u0441\u0442\u0443\u043f\u0430 \u0434\u043b\u044f \u0430\u043a\u0442\u0438\u0432\u0430\u0446\u0438\u0438 \u0431\u043e\u0442\u0430:"
        )
        return

    model = get_user_model(user_id)
    await update.message.reply_text(
        "\U0001f995 *Dino Image Generator*\n\n"
        "\u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043f\u0440\u043e\u043c\u043f\u0442 \u2014 \u043f\u043e\u043b\u0443\u0447\u0438 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0443!\n\n"
        "\U0001f4dd \u041e\u0434\u0438\u043d \u043f\u0440\u043e\u043c\u043f\u0442 = \u043e\u0434\u043d\u0430 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0430\n"
        "\U0001f4c4 \u041e\u0442\u043f\u0440\u0430\u0432\u044c .txt \u0444\u0430\u0439\u043b \u0441 \u043f\u0440\u043e\u043c\u043f\u0442\u0430\u043c\u0438 \u0434\u043b\u044f \u043f\u0430\u043a\u0435\u0442\u043d\u043e\u0439 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438\n\n"
        f"\U0001f3a8 \u041c\u043e\u0434\u0435\u043b\u044c: {model['name']}\n"
        "\U0001f5bc \u0424\u043e\u0440\u043c\u0430\u0442: 16:9 (1920x1080)\n\n"
        "\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n"
        "/models \u2014 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043c\u043e\u0434\u0435\u043b\u044c\n"
        "/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044e\n"
        "/help \u2014 \u043f\u043e\u043c\u043e\u0449\u044c\n"
        "/about \u2014 \u043e \u0431\u043e\u0442\u0435",
        parse_mode="Markdown"
    )


async def cmd_help(update, context):
    await update.message.reply_text(
        "\U0001f995 *\u041a\u0430\u043a \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u044c\u0441\u044f:*\n\n"
        "1\ufe0f\u20e3 \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u0442\u0435\u043a\u0441\u0442\u043e\u0432\u044b\u0439 \u043f\u0440\u043e\u043c\u043f\u0442 \u2014 \u0431\u043e\u0442 \u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u0435\u0442 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0443\n"
        "2\ufe0f\u20e3 \u041e\u0442\u043f\u0440\u0430\u0432\u044c .txt \u0444\u0430\u0439\u043b \u0441 \u043f\u0440\u043e\u043c\u043f\u0442\u0430\u043c\u0438 \u0432 \u0444\u043e\u0440\u043c\u0430\u0442\u0435:\n"
        "   `(1) \u043f\u0440\u043e\u043c\u043f\u0442`\n"
        "   `(2) \u043f\u0440\u043e\u043c\u043f\u0442`\n"
        "   \u0438\u043b\u0438\n"
        "   `1. \u043f\u0440\u043e\u043c\u043f\u0442`\n"
        "   `2. \u043f\u0440\u043e\u043c\u043f\u0442`\n\n"
        "3\ufe0f\u20e3 \u041f\u043e\u0434 \u043a\u0430\u0436\u0434\u043e\u0439 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u043e\u0439 \u0435\u0441\u0442\u044c \u043a\u043d\u043e\u043f\u043a\u0430 \U0001f504 \u0434\u043b\u044f \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0439 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438\n\n"
        "*\u041a\u043e\u043c\u0430\u043d\u0434\u044b:*\n"
        "/models \u2014 \u0432\u044b\u0431\u0440\u0430\u0442\u044c \u043c\u043e\u0434\u0435\u043b\u044c\n"
        "/cancel \u2014 \u043e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044e\n"
        "/support \u2014 \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430\n"
        "/about \u2014 \u043e \u0431\u043e\u0442\u0435",
        parse_mode="Markdown"
    )


async def cmd_about(update, context):
    model = get_user_model(update.effective_user.id)
    await update.message.reply_text(
        "\U0001f995 *Dino Image Generator*\n\n"
        f"\u041c\u043e\u0434\u0435\u043b\u044c: {model['name']}\n"
        "\u0424\u043e\u0440\u043c\u0430\u0442: 16:9 (1920x1080)\n"
        "API: Pollinations.ai (Flux)\n\n"
        "\u0421\u043e\u0437\u0434\u0430\u043d\u043e \u0434\u043b\u044f \u043a\u0430\u043d\u0430\u043b\u0430 \u043f\u0440\u043e \u0434\u0438\u043d\u043e\u0437\u0430\u0432\u0440\u043e\u0432 \U0001f996",
        parse_mode="Markdown"
    )


async def cmd_support(update, context):
    await update.message.reply_text(
        "\U0001f4e9 \u041f\u043e \u0432\u043e\u043f\u0440\u043e\u0441\u0430\u043c \u043f\u0438\u0448\u0438 \u0432\u043b\u0430\u0434\u0435\u043b\u044c\u0446\u0443 \u0431\u043e\u0442\u0430."
    )


async def cmd_models(update, context):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text("\U0001f512 \u0411\u043e\u0442 \u043d\u0435 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043a\u043e\u0434 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.")
        return

    current = user_models.get(user_id, DEFAULT_MODEL)
    buttons = []
    for key, info in MODELS.items():
        check = " \u2705" if key == current else ""
        buttons.append([InlineKeyboardButton(
            f"{info['name']}{check}",
            callback_data=f"model:{key}"
        )])

    await update.message.reply_text(
        "\U0001f3a8 *\u0412\u044b\u0431\u0435\u0440\u0438 \u043c\u043e\u0434\u0435\u043b\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438:*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )


async def cmd_cancel(update, context):
    user_id = update.effective_user.id
    cancelled_users.add(user_id)
    await update.message.reply_text("\U0001f6d1 \u0413\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f \u043e\u0442\u043c\u0435\u043d\u0435\u043d\u0430.")


# ─── CALLBACK HANDLER ─────────────────────────────────────────────────

async def callback_handler(update, context):
    query = update.callback_query
    callback_id = f"{query.message.message_id}:{query.data}"

    if callback_id in processed_callbacks:
        await query.answer("\u23f3 \u0423\u0436\u0435 \u043e\u0431\u0440\u0430\u0431\u043e\u0442\u0430\u043d\u043e")
        return
    processed_callbacks.add(callback_id)

    user_id = query.from_user.id
    if user_id not in authorized_users:
        await query.answer("\U0001f512 \u041d\u0435\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u0430")
        return

    data = query.data

    # Model selection
    if data.startswith("model:"):
        model_key = data.split(":", 1)[1]
        if model_key in MODELS:
            user_models[user_id] = model_key
            model = MODELS[model_key]
            await query.answer(f"\u0412\u044b\u0431\u0440\u0430\u043d\u0430: {model['name']}")
            await query.edit_message_text(
                f"\u2705 \u041c\u043e\u0434\u0435\u043b\u044c: {model['name']}\n{model['desc']}"
            )
        return

    # Retry generation
    if data.startswith("retry:"):
        msg_id = data.split(":", 1)[1]
        prompt = retry_prompts.get(msg_id)
        if not prompt:
            await query.answer("\u274c \u041f\u0440\u043e\u043c\u043f\u0442 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d")
            return

        await query.answer("\U0001f504 \u041f\u043e\u0432\u0442\u043e\u0440\u043d\u0430\u044f \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044f...")
        model_config = get_user_model(user_id)
        status_msg = await query.message.reply_text(
            f"\U0001f3a8 \u0413\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e \u043f\u043e\u0432\u0442\u043e\u0440...\n\U0001f4cb \u041c\u043e\u0434\u0435\u043b\u044c: {model_config['name']}"
        )

        image_data = await generate_image(prompt, model_config["model"], user_id)
        if image_data:
            retry_key = str(status_msg.message_id)
            retry_prompts[retry_key] = prompt

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\U0001f504 \u0415\u0449\u0451 \u0440\u0430\u0437", callback_data=f"retry:{retry_key}")
            ]])

            await query.message.reply_photo(
                photo=BytesIO(image_data),
                reply_markup=keyboard
            )
            await status_msg.delete()
        else:
            await status_msg.edit_text("\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u0435\u0449\u0451 \u0440\u0430\u0437.")
        return

    if len(processed_callbacks) > 5000:
        processed_callbacks.clear()


# ─── MESSAGE HANDLERS ─────────────────────────────────────────────────

async def handle_text(update, context):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if message_id in processed_messages:
        return
    processed_messages.add(message_id)

    # Access code check
    if user_id not in authorized_users:
        if update.message.text.strip() == ACCESS_CODE:
            authorized_users.add(user_id)
            await update.message.reply_text("\u2705 \u0414\u043e\u0441\u0442\u0443\u043f \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d! \u041e\u0442\u043f\u0440\u0430\u0432\u043b\u044f\u0439 \u043f\u0440\u043e\u043c\u043f\u0442\u044b.")
            if user_id != OWNER_ID:
                try:
                    user = update.effective_user
                    await context.bot.send_message(
                        OWNER_ID,
                        f"\U0001f195 \u041d\u043e\u0432\u044b\u0439 \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044c: {user.first_name} "
                        f"(@{user.username or '\u043d\u0435\u0442'}, ID: {user_id})"
                    )
                except Exception:
                    pass
            return
        else:
            await update.message.reply_text("\U0001f512 \u041d\u0435\u0432\u0435\u0440\u043d\u044b\u0439 \u043a\u043e\u0434. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 \u0435\u0449\u0451 \u0440\u0430\u0437.")
            return

    text = update.message.text.strip()
    if not text:
        return

    prompts = parse_prompts(text)
    await process_prompts(update, context, prompts, user_id)


async def handle_document(update, context):
    user_id = update.effective_user.id
    message_id = update.message.message_id

    if message_id in processed_messages:
        return
    processed_messages.add(message_id)

    if user_id not in authorized_users:
        await update.message.reply_text("\U0001f512 \u0411\u043e\u0442 \u043d\u0435 \u0430\u043a\u0442\u0438\u0432\u0438\u0440\u043e\u0432\u0430\u043d. \u041e\u0442\u043f\u0440\u0430\u0432\u044c \u043a\u043e\u0434 \u0434\u043e\u0441\u0442\u0443\u043f\u0430.")
        return

    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        await update.message.reply_text("\U0001f4c4 \u041e\u0442\u043f\u0440\u0430\u0432\u044c .txt \u0444\u0430\u0439\u043b \u0441 \u043f\u0440\u043e\u043c\u043f\u0442\u0430\u043c\u0438.")
        return

    file = await context.bot.get_file(doc.file_id)
    file_bytes = await file.download_as_bytearray()

    try:
        text = file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = file_bytes.decode("cp1251", errors="replace")

    prompts = parse_prompts(text)
    if not prompts:
        await update.message.reply_text("\u274c \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u043d\u0430\u0439\u0442\u0438 \u043f\u0440\u043e\u043c\u043f\u0442\u044b \u0432 \u0444\u0430\u0439\u043b\u0435.")
        return

    await process_prompts(update, context, prompts, user_id)


async def process_prompts(update, context, prompts, user_id):
    if user_id in cancelled_users:
        cancelled_users.discard(user_id)

    total = len(prompts)
    model_config = get_user_model(user_id)

    if total == 1:
        status_msg = await update.message.reply_text(
            f"\U0001f3a8 \u0413\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e...\n\U0001f4cb \u041c\u043e\u0434\u0435\u043b\u044c: {model_config['name']}"
        )
    else:
        eta_minutes = (total * 15) // 60
        eta_seconds = (total * 15) % 60
        status_msg = await update.message.reply_text(
            f"\U0001f3a8 \u0413\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e {total} \u0438\u0437\u043e\u0431\u0440\u0430\u0436\u0435\u043d\u0438\u0439...\n"
            f"\U0001f4cb \u041c\u043e\u0434\u0435\u043b\u044c: {model_config['name']}\n"
            f"\u23f1 \u041f\u0440\u0438\u043c\u0435\u0440\u043d\u043e: {eta_minutes}\u043c {eta_seconds}\u0441\n"
            f"\u0414\u043b\u044f \u043e\u0442\u043c\u0435\u043d\u044b: /cancel"
        )

    success = 0
    errors_count = 0

    for i, prompt in enumerate(prompts):
        if user_id in cancelled_users:
            cancelled_users.discard(user_id)
            await status_msg.edit_text(
                f"\U0001f6d1 \u041e\u0441\u0442\u0430\u043d\u043e\u0432\u043b\u0435\u043d\u043e. \u0421\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u043e: {success}/{total}"
            )
            return

        if total > 1 and i % 5 == 0:
            try:
                await status_msg.edit_text(
                    f"\U0001f3a8 \u0413\u0435\u043d\u0435\u0440\u0438\u0440\u0443\u044e {i+1}/{total}...\n"
                    f"\U0001f4cb \u041c\u043e\u0434\u0435\u043b\u044c: {model_config['name']}\n"
                    f"\u2705 \u0413\u043e\u0442\u043e\u0432\u043e: {success} | \u274c \u041e\u0448\u0438\u0431\u043e\u043a: {errors_count}"
                )
            except Exception:
                pass

        image_data = await generate_image(prompt, model_config["model"], user_id)

        if image_data:
            retry_key = f"{update.message.message_id}_{i}"
            retry_prompts[retry_key] = prompt

            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("\U0001f504 \u0415\u0449\u0451 \u0440\u0430\u0437", callback_data=f"retry:{retry_key}")
            ]])

            caption = f"({i+1})" if total > 1 else None
            try:
                await update.message.reply_photo(
                    photo=BytesIO(image_data),
                    caption=caption,
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Failed to send photo: {e}")
                errors_count += 1
                continue
            success += 1
        else:
            errors_count += 1
            if errors_count <= 3 or errors_count % 10 == 0:
                await update.message.reply_text(f"\u274c \u041e\u0448\u0438\u0431\u043a\u0430 \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438 ({i+1})")

    if total > 1:
        await status_msg.edit_text(
            f"\U0001f389 \u0412\u0441\u0435 \u043a\u0430\u0440\u0442\u0438\u043d\u043a\u0438 \u0433\u043e\u0442\u043e\u0432\u044b!\n"
            f"\u2705 \u0423\u0441\u043f\u0435\u0448\u043d\u043e: {success}/{total}"
            + (f"\n\u274c \u041e\u0448\u0438\u0431\u043e\u043a: {errors_count}" if errors_count else "")
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
        BotCommand("start", "\u0417\u0430\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u0431\u043e\u0442\u0430"),
        BotCommand("help", "\u041f\u043e\u043c\u043e\u0449\u044c"),
        BotCommand("models", "\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u043c\u043e\u0434\u0435\u043b\u044c"),
        BotCommand("cancel", "\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u044e"),
        BotCommand("support", "\u041f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0430"),
        BotCommand("about", "\u041e \u0431\u043e\u0442\u0435"),
    ]
    await application.bot.set_my_commands(commands)


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not set!")
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
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("support", cmd_support))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))

    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
