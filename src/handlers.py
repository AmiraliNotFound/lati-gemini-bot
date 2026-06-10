import logging
import asyncio
import random
import re
import os
import uuid
import traceback
import subprocess

try:
    import yt_dlp
except ImportError:
    yt_dlp = None

try:
    from gtts import gTTS
except ImportError:
    gTTS = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes
from google import genai
from google.genai import types

from src import config
from src import database

logger = logging.getLogger(__name__)

# Global rate limiting dictionary. Format: {(chat_id, user_id): [timestamps]}
_user_cooldowns = {}
COOLDOWN_WINDOW = 60 # seconds
MAX_REQUESTS_IN_WINDOW = 4

def generate_tts_sync(text: str, filepath: str):
    if not gTTS:
        raise ImportError("gTTS package is not installed")
    tts = gTTS(text=text, lang='fa')
    tts.save(filepath)

def convert_mp3_to_ogg(mp3_path: str, ogg_path: str):
    subprocess.run(
        ["ffmpeg", "-y", "-i", mp3_path, "-acodec", "libopus", ogg_path],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

async def generate_voice_reply(text: str) -> str:
    """
    Generates a Persian TTS voice reply and converts it to OGG format.
    Returns the file path of the OGG file, or None if it fails.
    """
    if not gTTS:
        logger.warning("gTTS not installed, skipping voice generation.")
        return None
    mp3_filename = f"tts_{uuid.uuid4().hex}.mp3"
    ogg_filename = f"tts_{uuid.uuid4().hex}.ogg"
    try:
        await asyncio.to_thread(generate_tts_sync, text, mp3_filename)
        await asyncio.to_thread(convert_mp3_to_ogg, mp3_filename, ogg_filename)
        return ogg_filename
    except Exception as e:
        logger.error(f"Failed to generate TTS voice reply: {e}")
        return None
    finally:
        if os.path.exists(mp3_filename):
            try:
                os.remove(mp3_filename)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temp tts mp3: {cleanup_err}")


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

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Provides a list of bot capabilities."""
    if update.message:
        help_text = (
            "🤖 **راهنمای ربات لاتی جمنای**\n\n"
            "من یه ربات هوشمندم که می‌تونم متن بخونم، عکس ببینم، و ویس گوش بدم. فقط کافیه تو گروه روم ریپلای کنی یا اسممو بیاری تا جوابتو بدم.\n\n"
            "📌 **دستورات من:**\n"
            "🔹 /start : بیدار کردن من\n"
            "🔹 /help : همین پیامی که داری می‌خونی\n"
            "🔹 /tldr : خلاصه‌سازی پیام‌های گروه (فقط تو گروه‌ها کار میکنه)\n\n"
            "🎥 **دانلودر هوشمند:**\n"
            "اگه لینک **اینستاگرام** یا **یوتوب** بفرستی، ویدیو رو مستقیم برات همینجا دانلود می‌کنم و می‌فرستم!"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")

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
            "▫️ `/admin add_special <username/name> <custom-instruction>`\n"
            "▫️ `/admin remove_special <username/name>`\n"
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
            display_name = uname if (" " in uname or uname.startswith("@")) else f"@{uname}"
            lines.append(f"{idx}. `{display_name}`: `{instr}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Admin utility: Remove Special
    if action == "remove_special":
        if not value:
            await update.message.reply_text("❌ خطا: یوزرنیم یا نام کاربر ویژه را وارد نکردی.")
            return
        special_username = value.strip()
        if (special_username.startswith('"') and special_username.endswith('"')) or (special_username.startswith("'") and special_username.endswith("'")):
            special_username = special_username[1:-1].strip()
        special_username = special_username.lstrip("@")
        await database.remove_special_user(config.DB_FILE, special_username)
        await update.message.reply_text(f"✅ کاربر ویژه `{special_username}` حذف شد.", parse_mode="Markdown")
        return

    # Admin utility: Add Special
    if action == "add_special":
        if not value:
            await update.message.reply_text("❌ خطا: باید نام کاربری/نام و دستورالعمل اختصاصی را وارد کنی.\nمثال: `/admin add_special username بسیار مهربان و باادب باش`\nیا برای نام‌های با فاصله: `/admin add_special \"John Doe\" بسیار مهربان باش`")
            return
        
        # Check if username is wrapped in quotes
        special_username = None
        special_instruction = None
        
        if value.startswith('"'):
            end_idx = value.find('"', 1)
            if end_idx != -1:
                special_username = value[1:end_idx].strip()
                special_instruction = value[end_idx+1:].strip()
        elif value.startswith("'"):
            end_idx = value.find("'", 1)
            if end_idx != -1:
                special_username = value[1:end_idx].strip()
                special_instruction = value[end_idx+1:].strip()
                
        if not special_username or not special_instruction:
            if len(args) < 3:
                await update.message.reply_text("❌ خطا: باید نام کاربری/نام و دستورالعمل اختصاصی را وارد کنی.\nمثال: `/admin add_special username بسیار مهربان و باادب باش`")
                return
            special_username = args[1].lstrip("@")
            special_instruction = " ".join(args[2:])
        else:
            special_username = special_username.lstrip("@")
            
        await database.add_special_user(config.DB_FILE, special_username, special_instruction)
        await update.message.reply_text(f"✅ کاربر ویژه `{special_username}` با دستورالعمل اختصاصی اضافه/ویرایش شد.", parse_mode="Markdown")
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
        error_msg = str(e)
        stack = traceback.format_exc()
        logger.error(f"GenAI error: {e}")
        await database.log_error(config.DB_FILE, "GENAI_ERROR", error_msg, stack)
        await update.message.reply_text("مغزم ارور داد از بس حرف مفت زدین... دفعه بعد 🚶‍♂️")

async def cobalt_fallback_download(url: str, output_path: str) -> bool:
    import aiohttp
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://cobalt.tools",
        "Referer": "https://cobalt.tools/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    payload = {"url": url}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://api.cobalt.tools/api/json", json=payload, headers=headers, timeout=15) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("status") in ["stream", "redirect"] and data.get("url"):
                        video_url = data.get("url")
                        async with session.get(video_url, timeout=60) as v_resp:
                            if v_resp.status == 200:
                                with open(output_path, "wb") as f:
                                    f.write(await v_resp.read())
                                return True
    except Exception as e:
        logger.error(f"Cobalt fallback failed: {e}")
    return False

def sync_download_video(url: str, output_path: str):
    if not yt_dlp:
        raise ImportError("yt_dlp is not installed")
        
    # Strip tracking parameters from Instagram/X URLs to bypass basic blocks
    if "instagram.com" in url or "x.com" in url or "twitter.com" in url:
        url = url.split("?")[0]
        
    ydl_opts = {
        'outtmpl': output_path,
        'format': 'best[ext=mp4]/best',
        'noplaylist': True,
        'quiet': True,
        'max_filesize': 50000000,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    }
    
    # Use cookies if provided by admin to bypass Instagram login walls
    cookies_data_path = os.path.join(os.path.dirname(config.DB_FILE), "cookies.txt")
    cookies_root_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
    
    if os.path.exists(cookies_data_path):
        ydl_opts['cookiefile'] = cookies_data_path
    elif os.path.exists(cookies_root_path):
        ydl_opts['cookiefile'] = cookies_root_path
        
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

async def download_and_send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    chat_id = update.message.chat_id
    status_msg = await update.message.reply_text("⏳ دارم ویدیو رو میکشم بیرون... وایسا...")
    
    filename = f"video_{uuid.uuid4().hex}.mp4"
    try:
        try:
            await asyncio.to_thread(sync_download_video, url, filename)
            if os.path.exists(filename):
                with open(filename, 'rb') as video:
                    await context.bot.send_video(chat_id=chat_id, video=video, reply_to_message_id=update.message.message_id)
                await status_msg.delete()
                return
            else:
                await status_msg.edit_text("❌ نتونستم دانلودش کنم، شاید حجمش زیاده یا پرایوته.")
        except ImportError:
            await status_msg.edit_text("❌ قابلیت دانلود فعال نیست! ادمین باید ربات رو دوباره Build کنه.")
        except Exception as e:
            error_msg = str(e)
            stack = traceback.format_exc()
            logger.error(f"yt-dlp error: {e}")
            await database.log_error(config.DB_FILE, "YT_DLP_ERROR", error_msg, stack)
            
            # Try Cobalt API fallback
            success = await cobalt_fallback_download(url, filename)
            if success and os.path.exists(filename):
                with open(filename, 'rb') as video:
                    await context.bot.send_video(chat_id=chat_id, video=video, reply_to_message_id=update.message.message_id)
                await status_msg.delete()
                return
    
            # If everything fails, Instagram is completely blocking the IP without cookies.
            if "instagram.com" in url:
                await status_msg.edit_text("❌ اینستاگرام گیر داده! ادمین باید کوکی ست کنه.")
            else:
                await status_msg.edit_text("❌ نتونستم دانلودش کنم، یوتوب/اینستا گیر داده.")
    finally:
        if os.path.exists(filename):
            try:
                os.remove(filename)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temp video file {filename}: {cleanup_err}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes message history pipelines, handles text/media targets, and fires requests to GenAI."""
    if not update.message:
        return

    chat_id = update.message.chat.id

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

    chat_name = update.message.chat.title or update.message.chat.first_name or str(chat_id)
    await database.save_chat_metadata(config.DB_FILE, chat_id, chat_name)

    # Extract user inputs: caption for photos, text for text messages
    user_text = update.message.text or update.message.caption or ""
    sender_name = update.message.from_user.first_name or "User"
    sender_username = update.message.from_user.username
    chat_id = update.message.chat_id
    bot_username = context.bot.username

    # Detect Instagram/YouTube links and auto-download in background
    url_match = re.search(r"(https?://(?:www\.)?(?:instagram\.com|youtube\.com|youtu\.be|x\.com|twitter\.com)[^\s]+)", user_text)
    if url_match:
        await download_and_send_video(update, context, url_match.group(1))
        return

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

    # 2.5 Rate Limiting Check (Admins are exempt)
    is_admin = sender_username and sender_username.lower() in config.ALLOWED_ADMINS
    if not is_admin:
        now = asyncio.get_event_loop().time()
        cooldown_key = (chat_id, user_id)
        if cooldown_key not in _user_cooldowns:
            _user_cooldowns[cooldown_key] = []
        
        # Prune old timestamps
        _user_cooldowns[cooldown_key] = [t for t in _user_cooldowns[cooldown_key] if now - t < COOLDOWN_WINDOW]
        
        if len(_user_cooldowns[cooldown_key]) >= MAX_REQUESTS_IN_WINDOW:
            cooldown_responses = [
                "چته رگباری پیام می‌فرستی؟ سر آوردی؟ یه دقیقه خفه شو بتونم نفس بکشم 🗿",
                "نفس بکش بچه! تند تند ننویس کیبورد گوشیت داغ کرد. چند لحظه دیگه بنال 🥱",
                "اسپم نکن دیگه ضایع! دو دقیقه بشین سر جات بعداً بیا فک بزن 🤫",
                "آروم باش چه خبرته؟ منم یه حدی دارم، یه دقیقه دندون رو جگر بذار 🚶‍♂️"
            ]
            await update.message.reply_text(random.choice(cooldown_responses))
            return
            
        _user_cooldowns[cooldown_key].append(now)

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
        
    if not special_instruction and update.message.from_user:
        # Fallback to account full name
        full_name = update.message.from_user.full_name
        if full_name:
            special_instruction = await database.get_special_user_instruction(config.DB_FILE, full_name)
        # Fallback to account first name
        if not special_instruction:
            first_name = update.message.from_user.first_name
            if first_name and first_name != full_name:
                special_instruction = await database.get_special_user_instruction(config.DB_FILE, first_name)
        
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
        f"Formulate a direct reply to what '{sender_name}' stated or sent, strictly adhering to your system persona instruction.\n"
        f"CRITICAL RULE: DO NOT prefix your response with 'Bot:' or your name. Just output the raw message content."
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
        
        # Fallback strip prefix if Gemini disobeys
        bot_response = re.sub(r"^Bot\s*\([^)]+\):\s*", "", bot_response).strip()
        
        # 6. Reply and log to DB
        is_voice_request = bool(update.message.voice)
        sent_voice = False
        
        if is_voice_request:
            await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
            voice_file = await generate_voice_reply(bot_response)
            if voice_file and os.path.exists(voice_file):
                try:
                    with open(voice_file, 'rb') as vf:
                        await update.message.reply_voice(voice=vf)
                    sent_voice = True
                except Exception as voice_send_err:
                    logger.error(f"Failed to send voice reply: {voice_send_err}")
                finally:
                    if os.path.exists(voice_file):
                        try:
                            os.remove(voice_file)
                        except:
                            pass
                            
        if not sent_voice:
            await update.message.reply_text(bot_response)
            
        await database.store_message(db_path=config.DB_FILE, chat_id=chat_id, role="model", sender_name=bot_username or "Bot", text=bot_response)

    except asyncio.TimeoutError:
        logger.error("GenAI pipeline processing exceeded standard time boundaries.")
        await update.message.reply_text("سرعت اینترنت خودت داغونه یا گوگل ریده؟ طول کشید، دوباره بگو 🥱")
    except Exception as e:
        error_msg = str(e)
        stack = traceback.format_exc()
        logger.error(f"Execution handling failure: {e}")
        await database.log_error(config.DB_FILE, "GENAI_ERROR", error_msg, stack)
        await update.message.reply_text(f"سیستم ریپ زد {sender_name}، یه بار دیگه بگو 🚶‍♂️")
