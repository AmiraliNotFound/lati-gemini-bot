import logging
import asyncio
import random
import re
import os
import uuid
import traceback
import subprocess
import html

try:
    import yt_dlp
    from yt_dlp.networking.impersonate import ImpersonateTarget
except ImportError:
    yt_dlp = None
    ImpersonateTarget = None

def format_media_caption(caption_text: str, webpage_url: str, platform: str, media_type: str = "video") -> str:
    """
    Formats the media caption in HTML format.
    The description is wrapped in a <blockquote> tag.
    The link is implicit inside a text string.
    """
    escaped_caption = html.escape(caption_text.strip()) if caption_text else ""
    
    # Determine the link text based on platform and type
    if "youtube" in platform.lower():
        link_text = "link to the YouTube post"
    elif "instagram" in platform.lower():
        if media_type == "photo":
            link_text = "link to the image"
        else:
            link_text = "link to the Instagram post"
    else:
        if media_type == "photo":
            link_text = "link to the image"
        else:
            link_text = "link to the video"
            
    link_html = f'<a href="{webpage_url}">{link_text}</a>'
    
    if escaped_caption:
        return f"<blockquote>{escaped_caption}</blockquote>\n\n{link_html}"
    else:
        return link_html

try:
    import edge_tts
except ImportError:
    edge_tts = None

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo, InputMediaPhoto, InputMediaVideo
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

def convert_audio_to_ogg(input_path: str, ogg_path: str, is_pcm: bool = False, sample_rate: int = 24000, channels: int = 1):
    # Determine if custom pitch shifting is configured
    try:
        pitch_factor = float(config.runtime_config.get("TTS_VOICE_PITCH", "1.0"))
    except Exception:
        pitch_factor = 1.0

    if pitch_factor != 1.0:
        # Try running with rubberband filter first
        cmd = ["ffmpeg", "-y"]
        if is_pcm:
            cmd.extend(["-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels)])
        cmd.extend(["-i", input_path, "-af", f"rubberband=pitch={pitch_factor}", "-acodec", "libopus", ogg_path])
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            return
        except subprocess.CalledProcessError as e:
            logger.warning(f"ffmpeg rubberband filter failed (likely not supported on this platform): {e}. Falling back to default pitch.")

    # Default / fallback path without pitch shifting
    cmd = ["ffmpeg", "-y"]
    if is_pcm:
        cmd.extend(["-f", "s16le", "-ar", str(sample_rate), "-ac", str(channels)])
    cmd.extend(["-i", input_path, "-acodec", "libopus", ogg_path])
    
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

async def generate_voice_reply(text: str, voice_name: str = None) -> str:
    """
    Generates a Persian TTS voice reply using edge-tts and converts it to OGG format.
    Returns the file path of the OGG file, or None if it fails.
    """
    if not edge_tts:
        logger.warning("edge-tts not installed, skipping voice generation.")
        return None
    if not voice_name:
        voice_name = config.runtime_config.get("TTS_EDGE_VOICE", "fa-IR-FaridNeural")
        
    mp3_filename = f"tts_{uuid.uuid4().hex}.mp3"
    ogg_filename = f"tts_{uuid.uuid4().hex}.ogg"
    try:
        # Generate MP3 using edge-tts
        communicate = edge_tts.Communicate(text, voice_name)
        await communicate.save(mp3_filename)
        # Convert to OGG using ffmpeg
        await asyncio.to_thread(convert_audio_to_ogg, mp3_filename, ogg_filename)
        return ogg_filename
    except Exception as e:
        logger.error(f"Failed to generate Edge TTS voice reply: {e}")
        await database.log_error(config.DB_FILE, "TTS_ERROR", f"Edge TTS failed: {e}", traceback.format_exc())
        return None
    finally:
        if os.path.exists(mp3_filename):
            try:
                os.remove(mp3_filename)
            except Exception as cleanup_err:
                logger.error(f"Failed to delete temp tts mp3: {cleanup_err}")

async def generate_gemini_voice_reply(text: str, voice_name: str = None) -> str:
    """
    Generates a voice reply using Gemini native TTS capabilities (supporting multiple fallback models).
    Returns the file path of the OGG audio file, or None if it fails.
    """
    if not voice_name:
        voice_name = config.runtime_config.get("TTS_GEMINI_VOICE", "Kore")
        
    model_str = config.runtime_config.get("TTS_GEMINI_MODEL", "gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview")
    candidate_models = [m.strip() for m in model_str.split(",") if m.strip()]
    if not candidate_models:
        candidate_models = ["gemini-2.5-flash-preview-tts"]
        
    client = get_ai_client()
    config_params = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name
                )
            )
        )
    )
    
    last_error = None
    for model_id in candidate_models:
        try:
            logger.info(f"Generating Gemini TTS using model {model_id} and voice {voice_name}...")
            response = await asyncio.wait_for(
                client.models.generate_content(
                    model=model_id,
                    contents=text,
                    config=config_params
                ),
                timeout=10.0
            )
            await database.log_api_request(config.DB_FILE, model_id, "tts", "success")
            
            # Extract audio bytes
            audio_part = None
            if response.candidates and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        audio_part = part
                        break
                        
            if not audio_part:
                logger.warning(f"No audio data returned in Gemini response parts for model {model_id}.")
                continue
                
            audio_bytes = audio_part.inline_data.data
            mime_type = audio_part.inline_data.mime_type or "audio/wav"
            
            is_pcm = False
            sample_rate = 24000
            channels = 1
            
            if "pcm" in mime_type.lower() or "l16" in mime_type.lower():
                is_pcm = True
                match = re.search(r"rate=(\d+)", mime_type.lower())
                if match:
                    sample_rate = int(match.group(1))
                    
            ext = "wav"
            if is_pcm:
                ext = "raw"
            elif "ogg" in mime_type:
                ext = "ogg"
            elif "mp3" in mime_type:
                ext = "mp3"
                
            temp_filename = f"gemini_tts_{uuid.uuid4().hex}.{ext}"
            ogg_filename = f"gemini_tts_{uuid.uuid4().hex}.ogg"
            
            with open(temp_filename, "wb") as f:
                f.write(audio_bytes)
                
            try:
                await asyncio.to_thread(
                    convert_audio_to_ogg, 
                    temp_filename, 
                    ogg_filename, 
                    is_pcm=is_pcm, 
                    sample_rate=sample_rate, 
                    channels=channels
                )
                logger.info(f"Successfully converted Gemini TTS output from model {model_id} to OGG.")
                return ogg_filename
            except Exception as conv_err:
                logger.error(f"Failed to convert Gemini TTS from {ext} to OGG: {conv_err}")
                await database.log_error(config.DB_FILE, "TTS_ERROR", f"Failed to convert Gemini TTS from {ext} to OGG: {conv_err}", traceback.format_exc())
                continue
            finally:
                if os.path.exists(temp_filename):
                    try:
                        os.remove(temp_filename)
                    except:
                        pass
        except Exception as e:
            await database.log_api_request(config.DB_FILE, model_id, "tts", "error")
            logger.warning(f"Gemini TTS generation failed for model {model_id}: {e}")
            last_error = e
            continue
            
    if last_error:
        logger.error(f"All configured Gemini TTS models failed. Last error: {last_error}")
        await database.log_error(config.DB_FILE, "TTS_ERROR", f"All configured Gemini TTS models failed. Last error: {last_error}", traceback.format_exc())
    return None


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
            "🔹 /tldr : خلاصه‌سازی پیام‌های گروه (فقط تو گروه‌ها کار میکنه)\n"
            "🔹 /ask <سوال> : پرسیدن سوال مستقیم بدون قاتی کردن با تاریخچه چت قبلی\n\n"
            "🎥 **دانلودر هوشمند:**\n"
            "اگه لینک **اینستاگرام** یا **یوتوب** بفرستی، ویدیو رو مستقیم برات همینجا دانلود می‌کنم و می‌فرستم!"
        )
        await update.message.reply_text(help_text, parse_mode="Markdown")

async def ask_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for /ask command.
    Answers a question out of context of the chat history.
    """
    if not update.message:
        return
        
    chat_id = update.message.chat_id
    
    # 1. Check if a query was provided
    text_content = update.message.text or ""
    parts = text_content.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.message.reply_text(
            "⚠️ لطفاً سوال خود را بعد از دستور بنویسید.\nمثال: `/ask پایتخت فرانسه کجاست؟`",
            parse_mode="Markdown",
            reply_to_message_id=update.message.message_id
        )
        return
        
    question = parts[1].strip()
    
    # 2. Inform user we are generating reply
    status_msg = await update.message.reply_text("⏳ دارم فکر می‌کنم...")
    
    # 3. Retrieve configurations (Persona prompt, custom model, etc.)
    db_conn = await database.get_db_connection(config.DB_FILE)
    custom_model = None
    try:
        async with db_conn.execute(
            "SELECT custom_model FROM chat_metadata WHERE chat_id = ?", (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                custom_model = row[0]
    except Exception as db_err:
        logger.warning(f"Failed to fetch custom model in ask_handler: {db_err}")
    finally:
        await db_conn.close()

    # Determine Model ID
    model_id = custom_model if custom_model else config.runtime_config.get("MODEL_ID", "gemini-2.5-flash")
    
    # Determine System Instruction
    system_instruction = config.runtime_config.get("SYSTEM_INSTRUCTION", "")
    timeout_threshold = float(config.runtime_config.get("TIMEOUT", 10.0))
    
    # Build content prompt (No history!)
    contents = [
        f"You are inside a direct execution environment. A user has asked you a single question outside of the regular conversation context.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"TASK:\nFormulate a reply, strictly adhering to your system persona instruction.\n"
        f"CRITICAL RULE: DO NOT prefix your response with 'Bot:' or your name. Just output the raw message content."
    ]
    
    try:
        client = get_ai_client()
        fallback_str = config.runtime_config.get("FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it")
        fallback_list = [m.strip() for m in fallback_str.split(",") if m.strip()]
        candidate_models = [model_id]
        for fb in fallback_list:
            if fb not in candidate_models:
                candidate_models.append(fb)
                
        response = None
        last_error = None
        for current_model in candidate_models:
            try:
                logger.info(f"Attempting content generation in ask_handler using model: {current_model}")
                response = await asyncio.wait_for(
                    client.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=types.GenerateContentConfig(system_instruction=system_instruction)
                    ),
                    timeout=timeout_threshold
                )
                logger.info(f"Successfully generated ask reply with model: {current_model}")
                await database.log_api_request(config.DB_FILE, current_model, "text", "success")
                break
            except (Exception, asyncio.TimeoutError) as e:
                await database.log_api_request(config.DB_FILE, current_model, "text", "error")
                logger.warning(f"Model {current_model} failed in ask_handler: {e}. Trying fallback models...")
                last_error = e
                
        if response is None:
            if last_error:
                raise last_error
            else:
                raise ValueError("No models succeeded in ask_handler.")
        
        bot_response = response.text if response.text else "🗿 بنال ببینم چی میگی..."
        bot_response = re.sub(r"^Bot\s*\([^)]+\):\s*", "", bot_response).strip()
        
        await status_msg.edit_text(bot_response)
        
    except Exception as e:
        logger.error(f"Error in ask_handler: {e}")
        await database.log_error(config.DB_FILE, "ASK_HANDLER_ERROR", f"Error in ask_handler: {e}", traceback.format_exc())
        await status_msg.edit_text("مغزم ارور داد... یه بار دیگه بپرس 🚶‍♂️")


async def transcribe_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Transcribes the voice/audio message being replied to using Gemini.
    """
    if not update.message:
        return
        
    chat_id = update.message.chat_id
    
    # Verify it is a reply
    replied_msg = update.message.reply_to_message
    if not replied_msg:
        await update.message.reply_text(
            "⚠️ این دستور را باید روی یک پیام صوتی (ویس) ریپلای کنی! 🗿",
            reply_to_message_id=update.message.message_id
        )
        return
        
    # Check if the replied message has a voice or audio file
    voice = replied_msg.voice or replied_msg.audio
    if not voice:
        await update.message.reply_text(
            "⚠️ این پیام ویس نیست که بتونم متنش کنم! 🥱",
            reply_to_message_id=update.message.message_id
        )
        return
        
    status_msg = await update.message.reply_text(
        "⏳ در حال دانلود و ترجمه صوت به متن... منتظر بمون...",
        reply_to_message_id=update.message.message_id
    )
    
    try:
        # Download voice file
        voice_file = await context.bot.get_file(voice.file_id)
        voice_bytes = await voice_file.download_as_bytearray()
        
        # Call Gemini to transcribe
        client = get_ai_client()
        model_id = config.runtime_config.get("MODEL_ID", "gemini-2.5-flash")
        
        contents = [
            types.Part.from_bytes(data=bytes(voice_bytes), mime_type="audio/ogg"),
            (
                "Transcribe this audio precisely as spoken in Persian. Output ONLY the transcription text, "
                "completely verbatim without any translation, introduction, explanations or wrappers."
            )
        ]
        
        timeout_threshold = float(config.runtime_config.get("TIMEOUT", 12.0))
        
        # Run model chain with failover support
        fallback_str = config.runtime_config.get("FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it")
        fallback_list = [m.strip() for m in fallback_str.split(",") if m.strip()]
        candidate_models = [model_id]
        for fb in fallback_list:
            if fb not in candidate_models:
                candidate_models.append(fb)
                
        response = None
        for current_model in candidate_models:
            try:
                logger.info(f"Attempting voice transcription using model: {current_model}")
                response = await asyncio.wait_for(
                    client.models.generate_content(
                        model=current_model,
                        contents=contents
                    ),
                    timeout=timeout_threshold
                )
                await database.log_api_request(config.DB_FILE, current_model, "text", "success")
                break
            except Exception as e:
                await database.log_api_request(config.DB_FILE, current_model, "text", "error")
                logger.warning(f"Transcription model {current_model} failed: {e}")
                
        if not response or not response.text:
            raise ValueError("All models failed or returned empty text for voice transcription.")
            
        transcription = response.text.strip()
        
        # Send transcription text
        reply_text = f"🗣 *متن ویس:* \n\n<blockquote>{transcription}</blockquote>"
        await status_msg.edit_text(reply_text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Error transcribing voice note: {e}")
        await database.log_error(config.DB_FILE, "TRANSCRIBE_ERROR", f"Failed to transcribe voice note: {e}", traceback.format_exc())
        await status_msg.edit_text("❌ متأسفانه نتونستم متن این ویس رو بردارم. سرورها یاری نمی‌کنن 🚶‍♂️")


async def support_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Forwards user support queries to admins. Only active in DMs.
    """
    if not update.message:
        return
        
    if update.effective_chat.type != "private":
        # Ignore support requests sent in group chats
        return
        
    args = context.args
    if not args:
        await update.message.reply_text(
            "📝 برای ارسال پیام به ادمین، متنت رو بعد از دستور بنویس.\n\n"
            "مثال: `/support سلام، ربات کار نمیکنه`",
            parse_mode="Markdown"
        )
        return
        
    support_text = " ".join(args).strip()
    user = update.effective_user
    
    sender_info = f"👤 فرستنده: {user.first_name} {user.last_name or ''}\n"
    if user.username:
        sender_info += f"🆔 یوزرنیم: @{user.username}\n"
    sender_info += f"🔑 شناسه کاربر: `{user.id}`"
    
    # Query all registered admin chat IDs
    admin_chat_ids = []
    try:
        import aiosqlite
        async with aiosqlite.connect(config.DB_FILE) as db:
            # Check if table exists, and query
            async with db.execute("SELECT chat_id FROM admin_chats") as cursor:
                rows = await cursor.fetchall()
                admin_chat_ids = [r[0] for r in rows]
    except Exception as db_err:
        logger.error(f"Failed to query admin chats: {db_err}")
        
    if not admin_chat_ids:
        await update.message.reply_text(
            "❌ متأسفانه در حال حاضر ارتباط برقرار نشد چون ادمین فعال ثبت‌نشده. "
            "صبر کن تا یکی از ادمین‌ها دستور `/admin` رو بنویسه تا ثبت بشه. 🗿"
        )
        return
        
    sent_count = 0
    for admin_cid in admin_chat_ids:
        try:
            await context.bot.send_message(
                chat_id=admin_cid,
                text=f"📬 *پیام پشتیبانی جدید:*\n\n{sender_info}\n\n📝 *متن پیام:*\n{support_text}",
                parse_mode="Markdown"
            )
            sent_count += 1
        except Exception as forward_err:
            logger.error(f"Failed to send support forward to admin chat {admin_cid}: {forward_err}")
            
    if sent_count > 0:
        await update.message.reply_text("✅ پیام شما با موفقیت برای مدیریت ارسال شد. به زودی همینجا پاسخ داده خواهد شد.")
    else:
        await update.message.reply_text("❌ مشکلی در ارسال پیام پیش آمد. لطفا بعدا امتحان کنید.")


async def reply_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Allows system admins to reply back to support requests.
    Usage: /reply <user_id> <message>
    """
    if not update.message:
        return
        
    username = update.effective_user.username
    if not username or username.lower() not in config.ALLOWED_ADMINS:
        logger.warning(f"Unauthorized use of /reply command by user: {username}")
        return
        
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "⚠️ استفاده نادرست. فرمت صحیح:\n"
            "`/reply <user_id> <text>`",
            parse_mode="Markdown"
        )
        return
        
    target_user_id_str = args[0]
    reply_text = " ".join(args[1:]).strip()
    
    try:
        target_user_id = int(target_user_id_str)
        # Send reply message to user
        await context.bot.send_message(
            chat_id=target_user_id,
            text=f"✉️ *پاسخ مدیریت به پیام پشتیبانی شما:*\n\n{reply_text}",
            parse_mode="Markdown"
        )
        await update.message.reply_text(f"✅ پاسخ شما برای کاربر `{target_user_id}` ارسال شد.")
    except ValueError:
        await update.message.reply_text("❌ خطا: شناسه کاربر باید عدد باشد.")
    except Exception as send_err:
        logger.error(f"Failed to send admin reply to user {target_user_id_str}: {send_err}")
        await update.message.reply_text(f"❌ خطا در ارسال پاسخ به کاربر: {send_err}")


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

    # Dynamically register admin chat ID
    try:
        await database.register_admin_chat(config.DB_FILE, username, update.effective_user.id)
    except Exception as reg_err:
        logger.error(f"Failed to register admin chat in admin_handler: {reg_err}")

    args = context.args
    if not args:
        help_text = (
            "⚙️ *Admin Configuration Dashboard*\n\n"
            f"• *Model ID:* `{config.runtime_config['MODEL_ID']}`\n"
            f"• *Fallback Models:* `{config.runtime_config.get('FALLBACK_MODELS', '')}`\n"
            f"• *TTS Engine:* `{config.runtime_config.get('TTS_ENGINE', 'edge')}`\n"
            f"• *TTS Gemini Model:* `{config.runtime_config.get('TTS_GEMINI_MODEL', 'gemini-2.5-flash')}`\n"
            f"• *TTS Gemini Voice:* `{config.runtime_config.get('TTS_GEMINI_VOICE', 'Kore')}`\n"
            f"• *TTS Edge Voice:* `{config.runtime_config.get('TTS_EDGE_VOICE', 'fa-IR-FaridNeural')}`\n"
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
                await database.log_error(config.DB_FILE, "BROADCAST_ERROR", f"Failed to send broadcast to chat {cid}: {e}", traceback.format_exc())
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
        
        fallback_str = config.runtime_config.get("FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it")
        fallback_list = [m.strip() for m in fallback_str.split(",") if m.strip()]
        candidate_models = [model_id]
        for fb in fallback_list:
            if fb not in candidate_models:
                candidate_models.append(fb)
                
        response = None
        last_error = None
        for current_model in candidate_models:
            try:
                logger.info(f"Attempting content generation in tldr_handler using model: {current_model}")
                response = await asyncio.wait_for(
                    client.models.generate_content(
                        model=current_model,
                        contents=[prompt]
                    ),
                    timeout=timeout_threshold
                )
                logger.info(f"Successfully generated TL;DR with model: {current_model}")
                await database.log_api_request(config.DB_FILE, current_model, "text", "success")
                break
            except (Exception, asyncio.TimeoutError) as e:
                await database.log_api_request(config.DB_FILE, current_model, "text", "error")
                logger.warning(f"Model {current_model} failed in tldr_handler: {e}. Trying fallback models...")
                last_error = e
                
        if response is None:
            if last_error:
                raise last_error
            else:
                raise ValueError("No models succeeded in tldr_handler.")
                
        await update.message.reply_text(response.text if response.text else "نتونستم بخونمش، یه جای کار میلنگه.")
    except Exception as e:
        error_msg = str(e)
        stack = traceback.format_exc()
        logger.error(f"GenAI error: {e}")
        await database.log_error(config.DB_FILE, "GENAI_ERROR", error_msg, stack)
        await update.message.reply_text("مغزم ارور داد از بس حرف مفت زدین... دفعه بعد 🚶‍♂️")

async def cobalt_fallback_download(url: str, output_path: str) -> tuple[bool, str]:
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
                                return True, video_url
                        return False, video_url
    except Exception as e:
        logger.error(f"Cobalt fallback failed: {e}")
        await database.log_error(config.DB_FILE, "DOWNLOAD_COBALT_ERROR", f"Cobalt fallback failed: {e}", traceback.format_exc())
    return False, None

def sync_download_video(url: str, output_path: str) -> dict:
    if not yt_dlp:
        raise ImportError("yt_dlp is not installed")
        
    # Strip tracking parameters from Instagram/X URLs to bypass basic blocks
    if "instagram.com" in url or "x.com" in url or "twitter.com" in url:
        url = url.split("?")[0]
        
    ydl_opts_base = {
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
    
    cookies_exist = False
    if os.path.exists(cookies_data_path):
        ydl_opts_base['cookiefile'] = cookies_data_path
        cookies_exist = True
    elif os.path.exists(cookies_root_path):
        ydl_opts_base['cookiefile'] = cookies_root_path
        cookies_exist = True
        
    if cookies_exist:
        logger.info("Cookies defined. Attempting standard download.")
        with yt_dlp.YoutubeDL(ydl_opts_base) as ydl:
            info = ydl.extract_info(url, download=True)
            return {'title': info.get('title') or '', 'url': info.get('webpage_url') or url}

    # Use impersonation client for Instagram downloading to not be contingent on cookies
    if ImpersonateTarget:
        targets = [
            'chrome-131:android-14',
            'safari-18.0:ios-18.0',
            'chrome-110:windows-10',
            'chrome',
            'safari',
            'firefox',
            'edge'
        ]
        last_err = None
        for target in targets:
            try:
                ydl_opts = ydl_opts_base.copy()
                ydl_opts['impersonate'] = ImpersonateTarget.from_str(target)
                logger.info(f"Attempting download with impersonate target: {target}")
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                logger.info(f"Download succeeded using impersonate target: {target}")
                return {'title': info.get('title') or '', 'url': info.get('webpage_url') or url}
            except Exception as e:
                logger.warning(f"Download with impersonation target {target} failed: {e}")
                last_err = e
        if last_err:
            raise last_err
    else:
        # Fallback to standard download if ImpersonateTarget is not imported
        with yt_dlp.YoutubeDL(ydl_opts_base) as ydl:
            info = ydl.extract_info(url, download=True)
            return {'title': info.get('title') or '', 'url': info.get('webpage_url') or url}

import json

def get_video_metadata(video_path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", video_path
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            metadata = {}
            # Format info
            format_info = data.get("format", {})
            if "duration" in format_info:
                metadata["duration"] = int(float(format_info["duration"]))
            
            # Stream info
            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    metadata["width"] = int(stream.get("width", 0))
                    metadata["height"] = int(stream.get("height", 0))
                    break
            return metadata
    except Exception as e:
        logger.error(f"Failed to query ffprobe metadata: {e}")
        try:
            asyncio.run(database.log_error(config.DB_FILE, "FFPROBE_ERROR", f"Failed to query ffprobe metadata: {e}", traceback.format_exc()))
        except Exception:
            pass
    return {}

def generate_video_thumbnail(video_path: str, thumbnail_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01", "-vframes", "1",
        "-vf", "scale=320:-1", thumbnail_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Failed to generate video thumbnail: {e}")
        try:
            asyncio.run(database.log_error(config.DB_FILE, "FFMPEG_ERROR", f"Failed to generate video thumbnail: {e}", traceback.format_exc()))
        except Exception:
            pass
    return False

async def download_instagram_post(url: str, cookies_path: str = None) -> tuple[list[dict], dict]:
    """
    Downloads all media from an Instagram post/carousel.
    Strategy:
      1. Try instaloader (native Instagram library — handles images natively)
      2. Fall back to yt-dlp (mainly works for video-only posts / reels)
    Returns: (list_of_items, metadata_dict)
      list_of_items: [{'path': '...', 'type': 'photo'|'video'}]
      metadata_dict: {'uploader': '...', 'caption': '...', 'webpage_url': '...'}
    """
    import aiohttp
    import re
    import http.cookiejar

    # Extract clean shortcode from any Instagram URL variant
    shortcode_match = re.search(r'instagram\.com/(?:p|reel|tv|reels)/([A-Za-z0-9_-]+)', url)
    if not shortcode_match:
        raise ValueError(f"Could not extract Instagram shortcode from URL: {url}")
    shortcode = shortcode_match.group(1)
    clean_url = f"https://www.instagram.com/p/{shortcode}/"

    # ---------------------------------------------------------------
    # Method 1: instaloader — the right tool for Instagram images
    # ---------------------------------------------------------------
    try:
        import instaloader

        L = instaloader.Instaloader(
            download_videos=True,
            download_pictures=True,
            save_metadata=False,
            download_comments=False,
            download_geotags=False,
            quiet=True,
            dirname_pattern='/tmp',  # We manage filenames ourselves
        )

        # Inject Netscape cookies into instaloader's requests session
        if cookies_path and os.path.exists(cookies_path):
            try:
                cj = http.cookiejar.MozillaCookieJar()
                cj.load(cookies_path, ignore_discard=True, ignore_expires=True)
                for cookie in cj:
                    if 'instagram.com' in cookie.domain:
                        L.context._session.cookies.set(
                            cookie.name, cookie.value, domain=cookie.domain
                        )
                csrf = None
                for cookie in L.context._session.cookies:
                    if cookie.name == 'csrftoken':
                        csrf = cookie.value
                        break
                if csrf:
                    L.context._session.headers.update({'X-CSRFToken': csrf})
                logger.info("Loaded Instagram cookies into instaloader session.")
            except Exception as ce:
                logger.warning(f"Could not inject cookies into instaloader: {ce}")

        # Fetch post metadata (synchronous, run in thread)
        post = await asyncio.to_thread(
            instaloader.Post.from_shortcode, L.context, shortcode
        )

        metadata = {
            'uploader': post.owner_username,
            'caption': post.caption or '',
            'webpage_url': clean_url
        }

        # Build list of (url, type) pairs for each media item
        media_list = []
        if post.typename == 'GraphSidecar':
            # Carousel — multiple images/videos
            nodes = await asyncio.to_thread(lambda: list(post.get_sidecar_nodes()))
            for node in nodes:
                if node.is_video:
                    media_list.append({'url': node.video_url, 'type': 'video'})
                else:
                    media_list.append({'url': node.display_url, 'type': 'photo'})
        elif post.is_video:
            media_list.append({'url': post.video_url, 'type': 'video'})
        else:
            # Single image post
            media_list.append({'url': post.url, 'type': 'photo'})

        # Download all media files via aiohttp
        items = []
        dl_headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
            'Referer': 'https://www.instagram.com/',
        }
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession() as session:
            for idx, media in enumerate(media_list):
                ext = '.mp4' if media['type'] == 'video' else '.jpg'
                filename = f"ig_item_{idx}_{uuid.uuid4().hex}{ext}"
                try:
                    async with session.get(media['url'], headers=dl_headers, timeout=timeout) as resp:
                        if resp.status == 200:
                            with open(filename, 'wb') as f:
                                f.write(await resp.read())
                            items.append({'path': filename, 'type': media['type']})
                        else:
                            logger.warning(f"Instagram media {idx} HTTP {resp.status}")
                except Exception as dl_err:
                    logger.error(f"Failed to download Instagram media {idx}: {dl_err}")

        if items:
            logger.info(f"instaloader successfully downloaded {len(items)} item(s) from {shortcode}")
            return items, metadata

        raise ValueError("instaloader extracted post but no media items were downloadable.")

    except ImportError:
        logger.info("instaloader not installed — falling back to yt-dlp")
    except Exception as il_err:
        logger.warning(f"instaloader failed for {shortcode}: {il_err} — falling back to yt-dlp")

    # ---------------------------------------------------------------
    # Method 2: yt-dlp fallback (works for video reels, may fail for images)
    # ---------------------------------------------------------------
    if not yt_dlp:
        raise ImportError("Neither instaloader nor yt_dlp is available")

    ydl_opts_base = {
        'extract_flat': False,
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
        'ignore_no_formats_error': True,
    }
    if cookies_path and os.path.exists(cookies_path):
        ydl_opts_base['cookiefile'] = cookies_path

    if cookies_path and os.path.exists(cookies_path):
        targets = [None, 'chrome-131:android-14', 'safari-18.0:ios-18.0']
    else:
        targets = ['chrome-131:android-14', 'safari-18.0:ios-18.0', 'chrome-110:windows-10', 'chrome']

    info = None
    last_err = None
    for target in targets:
        try:
            ydl_opts = ydl_opts_base.copy()
            if target and ImpersonateTarget:
                ydl_opts['impersonate'] = ImpersonateTarget.from_str(target)
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, clean_url, download=False)
            if info:
                break
        except Exception as e:
            last_err = e
            logger.warning(f"yt-dlp Instagram extraction (target={target}) failed: {e}")

    if not info:
        raise last_err or ValueError("yt-dlp could not extract Instagram post metadata.")

    metadata = {
        'uploader': info.get('uploader') or info.get('uploader_id') or 'unknown',
        'caption': info.get('description') or info.get('title') or '',
        'webpage_url': info.get('webpage_url') or clean_url
    }

    entries = info.get('entries') or ([info] if info.get('_type') != 'playlist' else [])

    items = []
    async with aiohttp.ClientSession() as session:
        for idx, entry in enumerate(entries):
            media_url = entry.get('url')
            if not media_url and entry.get('formats'):
                media_url = (entry['formats'] or [{}])[-1].get('url')
            if not media_url:
                thumbnails = entry.get('thumbnails') or []
                if thumbnails:
                    media_url = thumbnails[-1].get('url')
            if not media_url:
                logger.debug(f"No media URL for entry {idx}, skipping.")
                continue

            vcodec = entry.get('vcodec') or 'none'
            ext = entry.get('ext') or ''
            media_type = 'video' if (ext in ['mp4', 'mkv', 'webm'] or (vcodec and vcodec != 'none') or '.mp4' in media_url.lower()) else 'photo'
            filename = f"ig_item_{idx}_{uuid.uuid4().hex}.{'mp4' if media_type == 'video' else 'jpg'}"

            try:
                dl_headers = {
                    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
                }
                timeout = aiohttp.ClientTimeout(total=45)
                async with session.get(media_url, headers=dl_headers, timeout=timeout) as resp:
                    if resp.status == 200:
                        with open(filename, 'wb') as f:
                            f.write(await resp.read())
                        items.append({'path': filename, 'type': media_type})
                    else:
                        logger.warning(f"yt-dlp entry {idx} HTTP {resp.status}")
            except Exception as dl_err:
                logger.error(f"Failed to download yt-dlp entry {idx}: {dl_err}")

    return items, metadata

async def download_and_send_video(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    chat_id = update.message.chat_id
    status_msg = await update.message.reply_text("⏳ دارم مدیا رو میکشم بیرون... وایسا...")
    
    filename = f"video_{uuid.uuid4().hex}.mp4"
    thumbnail_filename = f"thumb_{uuid.uuid4().hex}.jpg"
    
    # Cookies check
    cookies_data_path = os.path.join(os.path.dirname(config.DB_FILE), "cookies.txt")
    cookies_root_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
    cookies_path = cookies_data_path if os.path.exists(cookies_data_path) else (cookies_root_path if os.path.exists(cookies_root_path) else None)

    # If it's an Instagram link, try downloading it as a post/carousel first
    if "instagram.com" in url:
        try:
            items, metadata = await download_instagram_post(url, cookies_path)
            if items:
                # Format caption
                caption = metadata.get('caption', '').strip()
                webpage_url = metadata.get('webpage_url', url)
                
                # Limit caption length for Telegram (max 1024 chars for captions)
                max_caption_len = 800
                if len(caption) > max_caption_len:
                    caption = caption[:max_caption_len] + "..."

                single_media_type = items[0]['type'] if len(items) == 1 else "video"
                formatted_caption = format_media_caption(caption, webpage_url, "instagram", single_media_type)

                if len(items) == 1:
                    # Single item send
                    item = items[0]
                    if item['type'] == 'photo':
                        with open(item['path'], 'rb') as photo:
                            await context.bot.send_photo(
                                chat_id=chat_id,
                                photo=photo,
                                caption=formatted_caption,
                                parse_mode="HTML",
                                reply_to_message_id=update.message.message_id
                            )
                    else:
                        # Video: extract metadata & thumbnail
                        path = item['path']
                        metadata_vid = await asyncio.to_thread(get_video_metadata, path)
                        duration = metadata_vid.get("duration")
                        width = metadata_vid.get("width")
                        height = metadata_vid.get("height")
                        
                        t_filename = f"thumb_{uuid.uuid4().hex}.jpg"
                        has_thumb = await asyncio.to_thread(generate_video_thumbnail, path, t_filename)
                        
                        try:
                            with open(path, 'rb') as video:
                                thumb_file = open(t_filename, 'rb') if (has_thumb and os.path.exists(t_filename)) else None
                                await context.bot.send_video(
                                    chat_id=chat_id,
                                    video=video,
                                    duration=duration,
                                    width=width,
                                    height=height,
                                    thumbnail=thumb_file,
                                    supports_streaming=True,
                                    caption=formatted_caption,
                                    parse_mode="HTML",
                                    reply_to_message_id=update.message.message_id
                                )
                                if thumb_file:
                                    thumb_file.close()
                        finally:
                            if os.path.exists(t_filename):
                                os.remove(t_filename)
                else:
                    # Multi-item album
                    media_group = []
                    open_files = [] # Keep track to close them later
                    
                    for idx, item in enumerate(items):
                        path = item['path']
                        # Caption goes on the first item only
                        item_caption = formatted_caption if idx == 0 else None
                        item_parse_mode = "HTML" if idx == 0 else None
                        
                        if item['type'] == 'photo':
                            f = open(path, 'rb')
                            open_files.append(f)
                            media_group.append(InputMediaPhoto(
                                media=f,
                                caption=item_caption,
                                parse_mode=item_parse_mode
                            ))
                        else:
                            # Video
                            metadata_vid = await asyncio.to_thread(get_video_metadata, path)
                            duration = metadata_vid.get("duration")
                            width = metadata_vid.get("width")
                            height = metadata_vid.get("height")
                            
                            t_filename = f"thumb_{uuid.uuid4().hex}.jpg"
                            has_thumb = await asyncio.to_thread(generate_video_thumbnail, path, t_filename)
                            
                            f = open(path, 'rb')
                            open_files.append(f)
                            
                            thumb_f = None
                            if has_thumb and os.path.exists(t_filename):
                                thumb_f = open(t_filename, 'rb')
                                open_files.append(thumb_f)
                                
                            media_group.append(InputMediaVideo(
                                media=f,
                                thumbnail=thumb_f,
                                width=width,
                                height=height,
                                duration=duration,
                                supports_streaming=True,
                                caption=item_caption,
                                parse_mode=item_parse_mode
                            ))
                            
                    try:
                        await context.bot.send_media_group(
                            chat_id=chat_id,
                            media=media_group,
                            reply_to_message_id=update.message.message_id
                        )
                    finally:
                        for f in open_files:
                            try:
                                f.close()
                            except:
                                pass
                
                # Cleanup downloaded files
                for item in items:
                    if os.path.exists(item['path']):
                        os.remove(item['path'])
                await status_msg.delete()
                return
        except Exception as ig_err:
            logger.warning(f"Instagram dedicated downloader failed: {ig_err}")
            # For Instagram links, NEVER fall through to the generic video downloader —
            # it uses yt-dlp which cannot handle image posts and would produce the same error.
            # Instead, inform the user directly.
            await status_msg.edit_text(
                "❌ نتونستم این پست اینستاگرامو بگیرم.\n\n"
                "📸 اگه پست عکسه و خصوصی نیست، ممکنه مشکل از کوکی‌ها باشه — "
                "از بخش *Conf* پنل ادمین کوکی‌های جدید آپلود کن.",
                parse_mode="Markdown"
            )
            return
    
    async def try_send_video_file(path: str, caption: str = None) -> bool:
        if not os.path.exists(path):
            return False
            
        metadata = await asyncio.to_thread(get_video_metadata, path)
        duration = metadata.get("duration")
        width = metadata.get("width")
        height = metadata.get("height")
        
        has_thumb = await asyncio.to_thread(generate_video_thumbnail, path, thumbnail_filename)
        
        try:
            with open(path, 'rb') as video:
                thumb_file = None
                if has_thumb and os.path.exists(thumbnail_filename):
                    thumb_file = open(thumbnail_filename, 'rb')
                
                await context.bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    duration=duration,
                    width=width,
                    height=height,
                    thumbnail=thumb_file,
                    supports_streaming=True,
                    caption=caption,
                    parse_mode="HTML",
                    reply_to_message_id=update.message.message_id
                )
                if thumb_file:
                    thumb_file.close()
            return True
        except Exception as send_err:
            logger.error(f"Failed to send video file: {send_err}")
            await database.log_error(config.DB_FILE, "TELEGRAM_SEND_ERROR", f"Failed to send video file: {send_err}", traceback.format_exc())
            raise send_err

    try:
        # Step 1: Try yt-dlp first
        video_metadata = None
        try:
            video_metadata = await asyncio.to_thread(sync_download_video, url, filename)
            if os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size <= 50 * 1024 * 1024:
                    title = video_metadata.get('title', '') if video_metadata else ''
                    webpage_url = video_metadata.get('url', url) if video_metadata else url
                    platform = "youtube" if "youtube.com" in webpage_url or "youtu.be" in webpage_url else "other"
                    yt_caption = format_media_caption(title, webpage_url, platform, "video")
                    await try_send_video_file(filename, caption=yt_caption)
                    await status_msg.delete()
                    return
                else:
                    logger.warning(f"yt-dlp downloaded video exceeds 50MB ({file_size} bytes). Redirecting to link fallback.")
            else:
                logger.warning("yt-dlp sync download did not produce a file.")
        except Exception as ytdl_err:
            logger.error(f"yt-dlp failed: {ytdl_err}")
            await database.log_error(config.DB_FILE, "DOWNLOAD_YTDL_ERROR", f"yt-dlp failed for URL {url}: {ytdl_err}", traceback.format_exc())

        # Step 2: Try Cobalt Fallback
        cobalt_stream_url = None
        try:
            if os.path.exists(filename):
                os.remove(filename)
                
            cobalt_success, cobalt_stream_url = await cobalt_fallback_download(url, filename)
            
            if cobalt_success and os.path.exists(filename):
                file_size = os.path.getsize(filename)
                if file_size <= 50 * 1024 * 1024:
                    try:
                        cobalt_caption = format_media_caption("", url, "other", "video")
                        await try_send_video_file(filename, caption=cobalt_caption)
                        await status_msg.delete()
                        return
                    except Exception:
                        pass
            
            if cobalt_stream_url:
                await status_msg.edit_text(
                    f"حجم ویدیو خیلی زیاده یا تلگرام یاری نمی‌کنه! 🚶‍♂️\n"
                    f"می‌تونی مستقیم از این لینک دانلودش کنی:\n"
                    f"{cobalt_stream_url}"
                )
                return
        except Exception as cobalt_err:
            logger.error(f"Cobalt process failed: {cobalt_err}")
            await database.log_error(config.DB_FILE, "DOWNLOAD_COBALT_ERROR", f"Cobalt fallback process failed for URL {url}: {cobalt_err}", traceback.format_exc())

        # Step 3: Failure message
        if "instagram.com" in url:
            await status_msg.edit_text("❌ اینستاگرام جلوشو گرفت! ادمین باید کوکی ست کنه.")
        else:
            await status_msg.edit_text("❌ نتونستم دانلودش کنم، یوتوب/اینستا گیر داده.")
            
    finally:
        for fpath in [filename, thumbnail_filename]:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception as cleanup_err:
                    logger.error(f"Cleanup error for file {fpath}: {cleanup_err}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes message history pipelines, handles text/media targets, and fires requests to GenAI."""
    if not update.message:
        return

    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    sender_username = update.message.from_user.username
    is_admin = sender_username and sender_username.lower() in config.ALLOWED_ADMINS

    if is_admin:
        try:
            await database.register_admin_chat(config.DB_FILE, sender_username, user_id)
        except Exception as reg_err:
            logger.error(f"Failed to register admin chat in handle_message: {reg_err}")

    # 1. Enforce Blocks
    if await database.is_blocked(config.DB_FILE, user_id):
        return # Ignore blocked user

    if await database.is_blocked(config.DB_FILE, chat_id):
        if chat_id < 0: # It's a group
            try:
                await context.bot.leave_chat(chat_id)
            except Exception as e:
                logger.error(f"Failed to leave blocked chat {chat_id}: {e}")
                await database.log_error(config.DB_FILE, "TELEGRAM_API_ERROR", f"Failed to leave blocked chat {chat_id}: {e}", traceback.format_exc())
        return

    # 1.5 Mute Check
    if not is_admin and await database.is_chat_muted(config.DB_FILE, chat_id):
        return

    chat_name = update.message.chat.title or update.message.chat.first_name or str(chat_id)
    chat_type = update.message.chat.type
    await database.save_chat_metadata(config.DB_FILE, chat_id, chat_name, chat_type=chat_type)

    # Extract user inputs: caption for photos, text for text messages
    user_text = update.message.text or update.message.caption or ""
    sender_name = update.message.from_user.first_name or "User"
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

    # Fetch custom chat settings
    chat_settings = await database.get_chat_settings(config.DB_FILE, chat_id)

    # 2. Trigger verification
    is_tagged = bot_username and f"@{bot_username}" in user_text
    is_reply_to_bot = (
        update.message.reply_to_message and 
        update.message.reply_to_message.from_user.id == context.bot.id
    )
    is_private = update.message.chat.type == "private"
    
    custom_chance = chat_settings.get("custom_roast_chance")
    if custom_chance is not None:
        random_chance = float(custom_chance)
    else:
        random_chance = float(config.runtime_config.get("RANDOM_ROAST_CHANCE", 0.02))
        
    triggered_randomly = False
    
    if not (is_private or is_tagged or is_reply_to_bot):
        # Roll the dice for an unprovoked roast if it's a group chat message
        if random.random() < random_chance and user_text:
            triggered_randomly = True
        else:
            return

    # 2.5 Rate Limiting Check (Admins are exempt)
    if not is_admin:
        now = asyncio.get_event_loop().time()
        cooldown_key = (chat_id, user_id)
        if cooldown_key not in _user_cooldowns:
            _user_cooldowns[cooldown_key] = []
        
        custom_cooldown = chat_settings.get("custom_cooldown")
        cooldown_window = int(custom_cooldown) if custom_cooldown is not None else COOLDOWN_WINDOW
        
        # Prune old timestamps
        _user_cooldowns[cooldown_key] = [t for t in _user_cooldowns[cooldown_key] if now - t < cooldown_window]
        
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
    
    custom_model = chat_settings.get("custom_model")
    model_id = custom_model if custom_model else config.runtime_config["MODEL_ID"]

    # Check if this chat has a custom TTS engine override (edge / gemini)
    custom_tts_engine = chat_settings.get("custom_tts_engine")
    
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
        
    if special_instruction:
        system_instruction = special_instruction
    else:
        chat_custom_instruction = chat_settings.get("custom_system_instruction")
        system_instruction = chat_custom_instruction if chat_custom_instruction else config.runtime_config["SYSTEM_INSTRUCTION"]

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
        await database.log_error(config.DB_FILE, "MEDIA_DOWNLOAD_ERROR", f"Error handling media download: {media_err}", traceback.format_exc())
        # Proceed with text transcript prompt even if downloading media fails

    try:
        # 5. Fire Async Client generate_content requests (instantiated lazily)
        client = get_ai_client()
        
        fallback_str = config.runtime_config.get("FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it")
        fallback_list = [m.strip() for m in fallback_str.split(",") if m.strip()]
        candidate_models = [model_id]
        for fb in fallback_list:
            if fb not in candidate_models:
                candidate_models.append(fb)
                
        response = None
        last_error = None
        for current_model in candidate_models:
            try:
                logger.info(f"Attempting content generation in handle_message using model: {current_model}")
                response = await asyncio.wait_for(
                    client.models.generate_content(
                        model=current_model,
                        contents=contents,
                        config=types.GenerateContentConfig(system_instruction=system_instruction)
                    ),
                    timeout=timeout_threshold
                )
                logger.info(f"Successfully generated reply with model: {current_model}")
                await database.log_api_request(config.DB_FILE, current_model, "text", "success")
                break
            except (Exception, asyncio.TimeoutError) as e:
                await database.log_api_request(config.DB_FILE, current_model, "text", "error")
                logger.warning(f"Model {current_model} failed in handle_message: {e}. Trying fallback models...")
                last_error = e
                
        if response is None:
            if last_error:
                raise last_error
            else:
                raise ValueError("No models succeeded in handle_message.")
        
        bot_response = response.text if response.text else "🗿 بنال ببینم چی میگی..."
        
        # Fallback strip prefix if Gemini disobeys
        bot_response = re.sub(r"^Bot\s*\([^)]+\):\s*", "", bot_response).strip()
        
        # 6. Reply and log to DB
        is_voice_request = bool(update.message.voice)
        sent_voice = False
        
        if is_voice_request:
            await context.bot.send_chat_action(chat_id=chat_id, action="record_voice")
            
            tts_engine = custom_tts_engine if custom_tts_engine else config.runtime_config.get("TTS_ENGINE", "edge").lower()
            voice_file = None
            
            if tts_engine == "gemini":
                logger.info("Using Gemini as primary TTS engine...")
                voice_file = await generate_gemini_voice_reply(bot_response)
                if not voice_file:
                    fallback_to_edge = config.runtime_config.get("TTS_FALLBACK_TO_EDGE", "True").strip().lower() == "true"
                    if fallback_to_edge:
                        logger.warning("Gemini TTS failed. Falling back to Edge TTS...")
                        voice_file = await generate_voice_reply(bot_response)
                    else:
                        logger.warning("Gemini TTS failed and Edge fallback is disabled. Skipping voice reply.")
            else:
                logger.info("Using Edge TTS as primary TTS engine...")
                voice_file = await generate_voice_reply(bot_response)
                
            if voice_file and os.path.exists(voice_file):
                try:
                    with open(voice_file, 'rb') as vf:
                        await update.message.reply_voice(voice=vf)
                    sent_voice = True
                except Exception as voice_send_err:
                    logger.error(f"Failed to send voice reply: {voice_send_err}")
                    await database.log_error(config.DB_FILE, "TELEGRAM_SEND_ERROR", f"Failed to send voice reply: {voice_send_err}", traceback.format_exc())
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
        await database.log_error(config.DB_FILE, "GENAI_TIMEOUT_ERROR", "GenAI pipeline processing exceeded standard time boundaries.", traceback.format_exc())
        await update.message.reply_text("سرعت اینترنت خودت داغونه یا گوگل ریده؟ طول کشید، دوباره بگو 🥱")
    except Exception as e:
        error_msg = str(e)
        stack = traceback.format_exc()
        logger.error(f"Execution handling failure: {e}")
        await database.log_error(config.DB_FILE, "GENAI_ERROR", error_msg, stack)
        await update.message.reply_text(f"سیستم ریپ زد {sender_name}، یه بار دیگه بگو 🚶‍♂️")
