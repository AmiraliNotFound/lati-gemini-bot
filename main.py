import sys
import logging
import asyncio
from datetime import datetime
import traceback
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, TypeHandler, filters
from google.genai import types

from src import config
from src import database
from src import handlers

logger = logging.getLogger(__name__)

async def daily_summary_scheduler(application) -> None:
    """
    Runs in the background and sends a sarcastic daily summary to all groups.
    """
    scheduler_logger = logging.getLogger("summary_scheduler")
    scheduler_logger.info("Daily summary scheduler background task started.")
    
    last_sent_date = ""
    
    while True:
        try:
            # Check if scheduler is enabled
            is_enabled = config.runtime_config.get("DAILY_SUMMARY_ENABLED", "False").strip().lower() == "true"
            if not is_enabled:
                await asyncio.sleep(60)
                continue
                
            now = datetime.now()
            current_time_str = now.strftime("%H:%M")
            current_date_str = now.strftime("%Y-%m-%d")
            
            scheduled_time = config.runtime_config.get("DAILY_SUMMARY_TIME", "00:00").strip()
            
            if current_time_str == scheduled_time and current_date_str != last_sent_date:
                scheduler_logger.info(f"Triggering scheduled daily summaries for all active group chats at {current_time_str}...")
                
                db_path = config.DB_FILE
                chats = await database.get_detailed_chats(db_path)
                
                summary_prompt = config.runtime_config.get(
                    "DAILY_SUMMARY_PROMPT",
                    "خلاصه بحث‌های امروز را بنویس."
                )
                
                model_id = config.runtime_config.get("MODEL_ID", "gemini-2.5-flash")
                timeout_threshold = float(config.runtime_config.get("TIMEOUT", 12.0))
                
                fallback_str = config.runtime_config.get("FALLBACK_MODELS", "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it")
                fallback_list = [m.strip() for m in fallback_str.split(",") if m.strip()]
                candidate_models = [model_id]
                for fb in fallback_list:
                    if fb not in candidate_models:
                        candidate_models.append(fb)
                
                from src.handlers import get_ai_client
                client = get_ai_client()
                
                for chat in chats:
                    chat_id = chat["chat_id"]
                    chat_type = chat["type"]
                    is_muted = chat["is_muted"] == 1
                    
                    if is_muted or chat_type not in ["group", "supergroup"]:
                        continue
                        
                    history = await database.get_chat_history(db_path, chat_id, limit=150)
                    if not history:
                        continue
                        
                    transcript_lines = []
                    for role, name, text in history:
                        if role == "user":
                            transcript_lines.append(f"{name}: {text}")
                            
                    if not transcript_lines:
                        continue
                        
                    chat_transcript = "\n".join(transcript_lines)
                    
                    prompt = (
                        f"CHAT HISTORICAL TRANSCRIPT:\n"
                        f"{chat_transcript}\n\n"
                        f"TASK:\n"
                        f"{summary_prompt}\n\n"
                        f"CRITICAL PERSONA: Act strictly as a teasing Persian friend. "
                        f"Output ONLY the summary directly, without 'Here is the summary' or prefixes."
                    )
                    
                    summary_text = None
                    for current_model in candidate_models:
                        try:
                            response = await asyncio.wait_for(
                                client.models.generate_content(
                                    model=current_model,
                                    contents=[prompt]
                                ),
                                timeout=timeout_threshold
                            )
                            await database.log_api_request(db_path, current_model, "text", "success")
                            summary_text = response.text
                            break
                        except Exception as gen_err:
                            await database.log_api_request(db_path, current_model, "text", "error")
                            scheduler_logger.error(f"Failed to generate summary for chat {chat_id} using model {current_model}: {gen_err}")
                            
                    if summary_text:
                        try:
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=f"📢 *خلاصه صمیمی وقایع امروز گروه:*\n\n{summary_text.strip()}",
                                parse_mode="Markdown"
                            )
                            scheduler_logger.info(f"Successfully sent daily summary to group {chat_id}")
                            await asyncio.sleep(0.5)
                        except Exception as send_err:
                            scheduler_logger.error(f"Failed to send daily summary to group {chat_id}: {send_err}")
                            await database.log_error(db_path, "DAILY_SUMMARY_SEND_ERROR", f"Chat {chat_id}: {send_err}", traceback.format_exc())
                
                last_sent_date = current_date_str
                scheduler_logger.info(f"Daily summaries completed for date {current_date_str}.")
                
        except Exception as loop_err:
            scheduler_logger.error(f"Error in daily_summary_scheduler loop: {loop_err}")
            
        await asyncio.sleep(30)


async def post_init(application: Application) -> None:
    """Safe post-initialization hook to run db setup inside the bot's native active loop."""
    await database.init_db(config.DB_FILE)
    logger.info("Database and dynamic config successfully loaded inside active runtime loop.")
    
    # Clean up any residual temp files on startup (no leftover files)
    try:
        import os
        import re
        temp_dir = os.path.join(os.path.dirname(__file__), "temp_downloads")
        if os.path.exists(temp_dir):
            cleaned_count = 0
            for filename in os.listdir(temp_dir):
                file_path = os.path.join(temp_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                    cleaned_count += 1
            if cleaned_count > 0:
                logger.info(f"Cleaned up {cleaned_count} residual guest temp files from temp_downloads on startup.")
        
        # Clean up residual files in root directory
        root_dir = os.path.dirname(__file__) or "."
        cleaned_root_count = 0
        patterns = [
            r"^video_[a-f0-9]{32}\.mp4$",
            r"^thumb_[a-f0-9]{32}\.jpg$",
            r"^tts_[a-f0-9]{32}\.(mp3|ogg)$",
            r"^gemini_tts_[a-f0-9]{32}\..*$",
            r"^ig_item_\d+_[a-f0-9]{32}\..*$",
            r"^temp_media_[a-f0-9]{32}\..*$"
        ]
        for filename in os.listdir(root_dir):
            if any(re.match(p, filename) for p in patterns):
                file_path = os.path.join(root_dir, filename)
                if os.path.isfile(file_path):
                    os.unlink(file_path)
                    cleaned_root_count += 1
        if cleaned_root_count > 0:
            logger.info(f"Cleaned up {cleaned_root_count} residual media/tts files from root directory on startup.")
    except Exception as cleanup_err:
        logger.error(f"Failed to perform startup temp files cleanup: {cleanup_err}")

    from src import server
    await server.setup_server(application)
    
    # Start the Daily Summary background task scheduler
    asyncio.create_task(daily_summary_scheduler(application))
    
    from telegram import BotCommand
    commands = [
        BotCommand("start", "بیدار کردن ربات"),
        BotCommand("help", "راهنمای استفاده و قابلیت‌ها"),
        BotCommand("tldr", "خلاصه کردن پیام‌های گروه (۱۵۰ پیام آخر)"),
        BotCommand("ask", "پرسیدن سوال مستقیم بدون در نظر گرفتن تاریخچه"),
        BotCommand("transcribe", "تبدیل ویس ریپلای شده به متن"),
        BotCommand("support", "ارسال پیام به ادمین (فقط در پی‌وی)")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Database and Server initialized successfully. Commands registered.")
    except Exception as e:
        logger.error(f"Failed to register commands: {e}")
        import traceback
        await database.log_error(config.DB_FILE, "TELEGRAM_API_ERROR", str(e), traceback.format_exc())

def main():
    # Initialize logger configuration
    config.setup_logging()

    # Integrity verification of environment configuration
    if not config.TELEGRAM_TOKEN or not config.GEMINI_API_KEY:
        logger.critical("Missing critical environment variables: TELEGRAM_TOKEN or GEMINI_API_KEY.")
        logger.critical("Configure a .env file or export them before running the bot.")
        sys.exit(1)

    logger.info("Initializing Lati Gemini Telegram Bot...")

    # Build Telegram Bot application
    application = Application.builder().token(config.TELEGRAM_TOKEN).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", handlers.start_handler))
    application.add_handler(CommandHandler("help", handlers.help_handler))
    application.add_handler(CommandHandler("admin", handlers.admin_handler))
    application.add_handler(CommandHandler("tldr", handlers.tldr_handler))
    application.add_handler(CommandHandler("ask", handlers.ask_handler))
    application.add_handler(CommandHandler("transcribe", handlers.transcribe_handler))
    application.add_handler(CommandHandler("support", handlers.support_handler))
    application.add_handler(CommandHandler("reply", handlers.reply_handler))
    
    # Message Pipeline Routing: support text, photo, and voice messages
    message_filter = filters.TEXT | filters.PHOTO | filters.VOICE
    application.add_handler(MessageHandler(message_filter & ~filters.COMMAND, handlers.handle_message))
    application.add_handler(TypeHandler(Update, handlers.handle_any_update))
    
    logger.info("Telegram event routing configured. Starting polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
