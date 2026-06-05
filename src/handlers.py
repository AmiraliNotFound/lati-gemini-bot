import logging
import asyncio
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
from google import genai
from google.genai import types

from src import config
from src import database

logger = logging.getLogger(__name__)

# Initialize Google GenAI client lazily to bind correctly to the running event loop
_ai_client = None

def get_ai_client():
    global _ai_client
    if _ai_client is None:
        if not config.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY is not configured.")
        _ai_client = genai.Client(api_key=config.GEMINI_API_KEY).aio
    return _ai_client

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cheeky entry point command greeting using dynamic user identification."""
    if update.message:
        sender_name = update.message.from_user.first_name or "رفیق"
        is_private = update.message.chat.type == "private"
        
        reply_markup = None
        text = f"بنال {sender_name} کارت چیه؟ تگ کن یا ریپلای بزن جوابتو بدم 🗿🤙"
        
        if is_private:
            keyboard = [
                [InlineKeyboardButton("🔗 سورس ربات (گیت‌هاب)", url="https://github.com/AmiraliNotFound/lati-gemini-bot")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
        await update.message.reply_text(
            text, 
            reply_markup=reply_markup,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enables authenticated users to query or update system settings securely via private chat."""
    if not update.message:
        return
        
    username = update.effective_user.username
    is_private = update.effective_chat.type == "private"
    
    # Authenticate admin
    if not is_private or not username or username.lower() not in config.ALLOWED_ADMINS:
        logger.warning(f"Unauthorized admin attempt by user: {username} in chat {update.effective_chat.id}")
        return

    args = context.args
    if not args:
        help_text = (
            "⚙️ *Admin Configuration Dashboard*\n\n"
            f"• *Model ID:* `{config.runtime_config['MODEL_ID']}`\n"
            f"• *Context Window:* `{config.runtime_config['CONTEXT_LIMIT']}` messages\n"
            f"• *API Timeout:* `{config.runtime_config['TIMEOUT']}` seconds\n\n"
            f"• *System Instruction Persona:* \n`{config.runtime_config['SYSTEM_INSTRUCTION']}`\n\n"
            "✍️ *Modification Parameters:*\n"
            "▫️ `/admin set_model <model-string>`\n"
            "▫️ `/admin set_limit <integer>`\n"
            "▫️ `/admin set_timeout <float>`\n"
            "▫️ `/admin set_chance <float>` (Random roast probability: 0.0 to 1.0)\n"
            "▫️ `/admin set_instruction <prompt-text>`\n\n"
            "✨ *Specials Management:*\n"
            "▫️ `/admin add_special <username> <custom-instruction>`\n"
            "▫️ `/admin remove_special <username>`\n"
            "▫️ `/admin list_special`\n\n"
            "📊 *Utility Commands:*\n"
            "▫️ `/admin stats` - Get database and user statistics\n"
            "▫️ `/admin broadcast <message>` - Send a message to all active chats"
        )
        reply_markup = None
        if config.WEBAPP_URL:
            keyboard = [[InlineKeyboardButton("🚀 Open Admin Dashboard", web_app=WebAppInfo(url=config.WEBAPP_URL))]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
        await update.message.reply_text(help_text, parse_mode="Markdown", reply_markup=reply_markup)
        return

    action = args[0].lower()
    value = " ".join(args[1:])

    # Admin utility: Stats
    if action == "stats":
        stats = await database.get_db_stats(config.DB_FILE)
        stats_text = (
            "📊 *System Statistics Dashboard*\n\n"
            f"• *Total Unique Chats:* `{stats.get('total_chats', 0)}`\n"
            f"• *Total Logged Messages:* `{stats.get('total_messages', 0)}`\n"
            f"• *Database File Size:* `{stats.get('db_size_kb', 0)} KB`"
        )
        await update.message.reply_text(stats_text, parse_mode="Markdown")
        return

    # Admin utility: Broadcast
    if action == "broadcast":
        if not value:
            await update.message.reply_text("❌ خطا: پیام برودکست خالی است.")
            return
        chat_ids = await database.get_all_chat_ids(config.DB_FILE)
        success_count = 0
        fail_count = 0
        status_msg = await update.message.reply_text(f"⏳ در حال ارسال پیام به {len(chat_ids)} چت...")
        
        for cid in chat_ids:
            try:
                await context.bot.send_message(chat_id=cid, text=value)
                success_count += 1
                await asyncio.sleep(0.05)  # Rate limiting protection
            except Exception as e:
                logger.error(f"Failed to send broadcast to chat {cid}: {e}")
                fail_count += 1
                
        await status_msg.edit_text(
            f"📢 *نتایج ارسال پیام همگانی:*\n\n"
            f"✅ موفق: `{success_count}`\n"
            f"❌ ناموفق: `{fail_count}`",
            parse_mode="Markdown"
        )
        return

    # Admin utility: List Specials
    if action == "list_special":
        specials = await database.get_special_users(config.DB_FILE)
        if not specials:
            await update.message.reply_text("ℹ️ هیچ کاربر ویژه‌ای ثبت نشده است.")
            return
        lines = ["✨ *فهرست کاربران ویژه و دستورالعمل‌های اختصاصی:*\n"]
        for idx, (uname, instr) in enumerate(specials, 1):
            lines.append(f"{idx}. `@{uname}`: `{instr}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Admin utility: Remove Special
    if action == "remove_special":
        if len(args) < 2:
            await update.message.reply_text("❌ خطا: یوزرنیم کاربر ویژه را وارد نکردی.")
            return
        special_username = args[1].lstrip("@")
        await database.remove_special_user(config.DB_FILE, special_username)
        await update.message.reply_text(f"✅ کاربر ویژه `@{special_username}` حذف شد.", parse_mode="Markdown")
        return

    # Admin utility: Add Special
    if action == "add_special":
        if len(args) < 3:
            await update.message.reply_text("❌ خطا: باید یوزرنیم و دستورالعمل اختصاصی را وارد کنی.\nمثال: `/admin add_special username بسیار مهربان و باادب باش`")
            return
        special_username = args[1].lstrip("@")
        special_instruction = " ".join(args[2:])
        await database.add_special_user(config.DB_FILE, special_username, special_instruction)
        await update.message.reply_text(f"✅ کاربر ویژه `@{special_username}` با دستورالعمل اختصاصی اضافه/ویرایش شد.", parse_mode="Markdown")
        return

    if not value:
        await update.message.reply_text("❌ مقدار جدید رو برای ویرایش تنظیمات ارسال نکردی.")
        return

    # Setting parameters
    if action == "set_model":
        await database.save_config_key(config.DB_FILE, "MODEL_ID", value)
        await update.message.reply_text(f"✅ مدل پردازشی تغییر کرد به: `{value}`", parse_mode="Markdown")
        
    elif action == "set_limit":
        if not value.isdigit():
            await update.message.reply_text("❌ خطا: محدودیت شمارش کانتکست گفتگو باید عدد صحیح مثبت باشه.")
            return
        await database.save_config_key(config.DB_FILE, "CONTEXT_LIMIT", value)
        await update.message.reply_text(f"✅ پنجره کانتکست تغییر کرد به: `{value}` پیام", parse_mode="Markdown")
        
    elif action == "set_timeout":
        try:
            float(value)
        except ValueError:
            await update.message.reply_text("❌ خطا: بازه زمانی تایم‌اوت باید یک عدد معتبر باشد.")
            return
        await database.save_config_key(config.DB_FILE, "TIMEOUT", value)
        await update.message.reply_text(f"✅ آستانه زمانی قطع اتصال تغییر کرد به: `{value}` ثانیه", parse_mode="Markdown")
        
    elif action == "set_chance":
        try:
            val = float(value)
            if not (0.0 <= val <= 1.0): raise ValueError
        except ValueError:
            await update.message.reply_text("❌ خطا: شانس تیکه‌اندازی باید عددی بین 0.0 و 1.0 باشد.")
            return
        await database.save_config_key(config.DB_FILE, "RANDOM_ROAST_CHANCE", value)
        await update.message.reply_text(f"✅ شانس تیکه‌اندازی تصادفی روی `{value}` تنظیم شد.", parse_mode="Markdown")
        
    elif action == "set_instruction":
        await database.save_config_key(config.DB_FILE, "SYSTEM_INSTRUCTION", value)
        await update.message.reply_text("✅ دستورالعمل پرسونای سیستم با موفقیت به‌روزرسانی شد.")
    else:
        await update.message.reply_text("❌ دستور نامعتبر است. از راهنمای پنل استفاده کن.")

async def tldr_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Summarizes the drama and main topics of the chat history in Persian slang."""
    if not update.message:
        return
        
    chat_id = update.message.chat_id
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    
    # Get up to 150 messages for a robust TL;DR
    history = await database.get_chat_history(config.DB_FILE, chat_id, limit=150)
    if not history:
        await update.message.reply_text("هیچ دیتایی تو این چت ثبت نشده که بخوام خلاصه‌اش کنم! بنالید تا ببینم چخبره 🗿")
        return
        
    transcript_lines = []
    for role, name, text in history:
        if role == "user":
            transcript_lines.append(f"{name}: {text}")
            
    chat_transcript = "\n".join(transcript_lines)
    
    prompt = (
        f"CHAT HISTORICAL TRANSCRIPT:\n"
        f"{chat_transcript}\n\n"
        f"TASK:\n"
        f"Summarize the drama, gossip, and main topics of this group chat history in 3 or 4 bullet points. "
        f"Keep your tone entirely Persian, completely informal, lative/Tehrani slang, and playfully roast the participants based on their messages. "
        f"Do NOT be polite or bookish. Output ONLY the summary without any prefix."
    )
    
    try:
        client = get_ai_client()
        model_id = config.runtime_config.get("MODEL_ID", "gemini-2.5-flash")
        timeout_threshold = float(config.runtime_config.get("TIMEOUT", 12.0))
        
        response = await asyncio.wait_for(
            client.models.generate_content(
                model=model_id,
                contents=[prompt]
            ),
            timeout=timeout_threshold
        )
        await update.message.reply_text(response.text if response.text else "نتونستم بخونمش، یه جای کار میلنگه.")
    except Exception as e:
        logger.error(f"TLDR generation failed: {e}")
        await update.message.reply_text("مغزم ارور داد از بس حرف مفت زدین... دفعه بعد 🚶‍♂️")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes message history pipelines, handles text/media targets, and fires requests to GenAI."""
    if not update.message:
        return

    # 1. Enforce Blocks
    user_id = update.message.from_user.id
    if await database.is_blocked(config.DB_FILE, user_id):
        return # Ignore blocked user

    if await database.is_blocked(config.DB_FILE, chat_id):
        if chat_id < 0: # It's a group
            try:
                await context.bot.leave_chat(chat_id)
            except Exception as e:
                logger.error(f"Failed to leave blocked chat {chat_id}: {e}")
        return

    # Extract user inputs: caption for photos, text for text messages
    user_text = update.message.text or update.message.caption or ""
    sender_name = update.message.from_user.first_name or "User"
    sender_username = update.message.from_user.username
    chat_id = update.message.chat_id
    bot_username = context.bot.username

    # Format the input label for the database log file
    log_text = user_text
    if update.message.photo:
        log_text = f"[عکس] {user_text}".strip()
    elif update.message.voice:
        log_text = f"[پیام صوتی] {user_text}".strip()

    # 1. Store incoming message context asynchronously
    await database.store_message(config.DB_FILE, chat_id, "user", sender_name, log_text)

    # 2. Trigger verification
    is_tagged = bot_username and f"@{bot_username}" in user_text
    is_reply_to_bot = (
        update.message.reply_to_message and 
        update.message.reply_to_message.from_user.id == context.bot.id
    )
    is_private = update.message.chat.type == "private"
    
    random_chance = float(config.runtime_config.get("RANDOM_ROAST_CHANCE", 0.02))
    triggered_randomly = False
    
    if not (is_private or is_tagged or is_reply_to_bot):
        # Roll the dice for an unprovoked roast if it's a group chat message
        if random.random() < random_chance and user_text:
            triggered_randomly = True
        else:
            return

    # Signal typing interface status
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Read active configurations from DB-synchronized memory cache
    context_limit = int(config.runtime_config["CONTEXT_LIMIT"])
    timeout_threshold = float(config.runtime_config["TIMEOUT"])
    model_id = config.runtime_config["MODEL_ID"]
    
    # Check if sender is a special user with a custom instruction
    special_instruction = None
    if sender_username:
        special_instruction = await database.get_special_user_instruction(config.DB_FILE, sender_username)
        
    system_instruction = special_instruction if special_instruction else config.runtime_config["SYSTEM_INSTRUCTION"]

    # 3. Pull historical sequence slices (Invert chronologically)
    history = await database.get_chat_history(config.DB_FILE, chat_id, context_limit)
    
    # 4. Prepare Multimodal Payload Contents
    contents = []

    # Build transcript logs
    transcript_lines = []
    for role, name, text in history:
        if role == "user":
            transcript_lines.append(f"{name}: {text}")
        else:
            transcript_lines.append(f"Bot ({name}): {text}")

    chat_transcript = "\n".join(transcript_lines)

    prompt_payload = (
        f"CONTEXT CLUE:\n"
        f"You are inside a group chat environment. You are currently having a conversation with a user named '{sender_name}'.\n\n"
        f"CHAT HISTORICAL TRANSCRIPT:\n"
        f"{chat_transcript}\n\n"
        f"TASK:\n"
        f"Formulate a direct reply to what '{sender_name}' stated or sent, strictly adhering to your system persona instruction."
    )
    contents.append(prompt_payload)

    # Download and process attachments if any
    try:
        # Photo Processing
        if update.message.photo:
            # Download highest resolution photo to memory buffer
            photo_file = await context.bot.get_file(update.message.photo[-1].file_id)
            photo_bytes = await photo_file.download_as_bytearray()
            contents.append(types.Part.from_bytes(data=bytes(photo_bytes), mime_type="image/jpeg"))

        # Voice Processing
        elif update.message.voice:
            # Download voice note (.ogg file) to memory buffer
            voice_file = await context.bot.get_file(update.message.voice.file_id)
            voice_bytes = await voice_file.download_as_bytearray()
            contents.append(types.Part.from_bytes(data=bytes(voice_bytes), mime_type="audio/ogg"))

    except Exception as media_err:
        logger.error(f"Error handling media download: {media_err}")
        # Proceed with text transcript prompt even if downloading media fails

    try:
        # 5. Fire Async Client generate_content requests (instantiated lazily)
        client = get_ai_client()
        response = await asyncio.wait_for(
            client.models.generate_content(
                model=model_id,
                contents=contents,
                config=types.GenerateContentConfig(system_instruction=system_instruction)
            ),
            timeout=timeout_threshold
        )
        
        bot_response = response.text if response.text else "🗿 بنال ببینم چی میگی..."
        
        # 6. Reply and log to DB
        await update.message.reply_text(bot_response)
        await database.store_message(db_path=config.DB_FILE, chat_id=chat_id, role="model", sender_name=bot_username or "Bot", text=bot_response)

    except asyncio.TimeoutError:
        logger.error("GenAI pipeline processing exceeded standard time boundaries.")
        await update.message.reply_text("سرعت اینترنت خودت داغونه یا گوگل ریده؟ طول کشید، دوباره بگو 🥱")
    except Exception as e:
        logger.error(f"Execution handling failure: {e}")
        await update.message.reply_text(f"سیستم ریپ زد {sender_name}، یه بار دیگه بگو 🚶‍♂️")

