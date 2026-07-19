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

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OWNER_ID = 1343284628

MODELS = {
    "nano2": {
        "name": "\U0001f34c Nano Banana 2",
        "model": "gemini-3.1-flash-image",
        "desc": "Fast, high quality"
    },
    "nano": {
        "name": "\u26a1 Nano Banana",
        "model": "gemini-2.5-flash-image",
        "desc": "Speed optimized"
    },
}
DEFAULT_MODEL = "nano2"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

user_models = {}
cancelled_users = set()
retry_prompts = {}
processed_messages = set()
processed_callbacks = set()
authorized_users = {OWNER_ID}
ACCESS_CODE = "dino2025"


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


async def generate_image(prompt, model_id, user_id):
    if user_id in cancelled_users:
        return None

    url = f"https://generativelanguage.googleapis.com/v1/models/{model_id}:generateContent?key={GEMINI_API_KEY}"

    headers = {"Content-Type": "application/json"}

    payload = {
        "contents": [{"parts": [{"text": prompt}]}]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"Gemini API error {resp.status}: {error_text[:500]}")
                    return None

                data = await resp.json()
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


async def cmd_start(update, context):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        await update.message.reply_text("\U0001f512 Enter access code:")
        return
    await update.message.reply_text(
        "\U0001f995 *Nano Banana Dino Generator*\n\n"
        "Send a prompt to generate an image!\n\n"
        "/models - choose model\n"
        "/cancel - cancel generation\n"
        "/help - help",
        parse_mode="Markdown"
    )


async def cmd_help(update, context):
    await update.message.reply_text(
        "\U0001f995 *How to use:*\n\n"
        "1. Send a text prompt\n"
        "2. Send a .txt file with numbered prompts\n"
        "3. Use \U0001f504 button to regenerate\n\n"
        "/models - choose model\n"
        "/cancel - cancel generation\n"
        "/about - about bot",
        parse_mode="Markdown"
    )


async def cmd_about(update, context):
    model = get_user_model(update.effective_user.id)
    await update.message.reply_text(
        f"\U0001f995 *Nano Banana Dino Generator*\n\nModel: {model['name']}\nAPI: Google Gemini",
        parse_mode="Markdown"
    )


async def cmd_models(update, context):
    user_id = update.effective_user.id
    if user_id not in authorized_users:
        return
    current = user_models.get(user_id, DEFAULT_MODEL)
    buttons = []
    for key, info in MODELS.items():
        check = " \u2705" if key == current else ""
        buttons.append([InlineKeyboardButton(f"{info['name']}{check}", callback_data=f"model:{key}")])
    await update.message.reply_text("\U0001f3a8 *Choose model:*", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")


async def cmd_cancel(update, context):
    cancelled_users.add(update.effective_user.id)
    await update.message.reply_text("\U0001f6d1 Cancelled.")


async def callback_handler(update, context):
    query = update.callback_query
    cb_id = f"{query.message.message_id}:{query.data}"
    if cb_id in processed_callbacks:
        await query.answer()
        return
    processed_callbacks.add(cb_id)
    user_id = query.from_user.id
    if user_id not in authorized_users:
        await query.answer("\U0001f512")
        return
    data = query.data

    if data.startswith("model:"):
        mk = data.split(":", 1)[1]
        if mk in MODELS:
            user_models[user_id] = mk
            m = MODELS[mk]
            await query.answer(f"{m['name']}")
            await query.edit_message_text(f"\u2705 {m['name']}\n{m['desc']}")
        return

    if data.startswith("retry:"):
        mid = data.split(":", 1)[1]
        prompt = retry_prompts.get(mid)
        if not prompt:
            await query.answer("\u274c")
            return
        await query.answer("\U0001f504")
        mc = get_user_model(user_id)
        sm = await query.message.reply_text(f"\U0001f3a8 Generating...\n{mc['name']}")
        img = await generate_image(prompt, mc["model"], user_id)
        if img:
            rk = str(sm.message_id)
            retry_prompts[rk] = prompt
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f504 Again", callback_data=f"retry:{rk}")]])
            await query.message.reply_photo(photo=BytesIO(img), reply_markup=kb)
            await sm.delete()
        else:
            await sm.edit_text("\u274c Error")
        return

    if len(processed_callbacks) > 5000:
        processed_callbacks.clear()


async def handle_text(update, context):
    user_id = update.effective_user.id
    mid = update.message.message_id
    if mid in processed_messages:
        return
    processed_messages.add(mid)

    if user_id not in authorized_users:
        if update.message.text.strip() == ACCESS_CODE:
            authorized_users.add(user_id)
            await update.message.reply_text("\u2705 Access granted!")
            if user_id != OWNER_ID:
                try:
                    u = update.effective_user
                    await context.bot.send_message(OWNER_ID, f"New user: {u.first_name} (@{u.username or '-'}, {user_id})")
                except Exception:
                    pass
            return
        await update.message.reply_text("\U0001f512 Wrong code.")
        return

    text = update.message.text.strip()
    if not text:
        return
    await process_prompts(update, context, parse_prompts(text), user_id)


async def handle_document(update, context):
    user_id = update.effective_user.id
    mid = update.message.message_id
    if mid in processed_messages:
        return
    processed_messages.add(mid)
    if user_id not in authorized_users:
        return

    doc = update.message.document
    if not doc.file_name.endswith(".txt"):
        return

    f = await context.bot.get_file(doc.file_id)
    fb = await f.download_as_bytearray()
    try:
        text = fb.decode("utf-8")
    except UnicodeDecodeError:
        text = fb.decode("cp1251", errors="replace")

    prompts = parse_prompts(text)
    if prompts:
        await process_prompts(update, context, prompts, user_id)


async def process_prompts(update, context, prompts, user_id):
    if user_id in cancelled_users:
        cancelled_users.discard(user_id)
    total = len(prompts)
    mc = get_user_model(user_id)

    sm = await update.message.reply_text(
        f"\U0001f3a8 {'Generating...' if total == 1 else f'Generating {total} images...'}\n{mc['name']}"
        + ("\n/cancel to stop" if total > 1 else "")
    )

    ok = 0
    for i, prompt in enumerate(prompts):
        if user_id in cancelled_users:
            cancelled_users.discard(user_id)
            await sm.edit_text(f"\U0001f6d1 Stopped. Done: {ok}/{total}")
            return
        if total > 1:
            try:
                await sm.edit_text(f"\U0001f3a8 {i+1}/{total}...\n{mc['name']}")
            except Exception:
                pass

        img = await generate_image(prompt, mc["model"], user_id)
        if img:
            rk = f"{update.message.message_id}_{i}"
            retry_prompts[rk] = prompt
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f504 Again", callback_data=f"retry:{rk}")]])
            await update.message.reply_photo(photo=BytesIO(img), caption=f"({i+1})" if total > 1 else None, reply_markup=kb)
            ok += 1
        else:
            await update.message.reply_text(f"\u274c Error ({i+1})")

    if total > 1:
        await sm.edit_text(f"\u2705 Done! {ok}/{total}")
    else:
        try:
            await sm.delete()
        except Exception:
            pass

    if len(processed_messages) > 5000:
        processed_messages.clear()
    if len(retry_prompts) > 2000:
        retry_prompts.clear()


async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start", "Start"), BotCommand("help", "Help"),
        BotCommand("models", "Models"), BotCommand("cancel", "Cancel"),
        BotCommand("about", "About"),
    ])


def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not set!")
        return
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY not set!")
        return
    app = Application.builder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    logger.info("Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
