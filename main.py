import sys
import logging
from telegram.ext import Application, MessageHandler, CommandHandler, filters

from src import config
from src import database
from src import handlers

logger = logging.getLogger(__name__)

async def post_init(application: Application) -> None:
    """Safe post-initialization hook to run db setup inside the bot's native active loop."""
    await database.init_db(config.DB_FILE)
    logger.info("Database and dynamic config successfully loaded inside active runtime loop.")
    from src import server
    await server.setup_server(application)
    logger.info("Started Aiohttp Server inside Telegram post_init.")

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
    
    # Register command handlers
    application.add_handler(CommandHandler("start", handlers.start_handler))
    application.add_handler(CommandHandler("admin", handlers.admin_handler))
    application.add_handler(CommandHandler("tldr", handlers.tldr_handler))
    
    # Message Pipeline Routing: support text, photo, and voice messages
    message_filter = filters.TEXT | filters.PHOTO | filters.VOICE
    application.add_handler(MessageHandler(message_filter & ~filters.COMMAND, handlers.handle_message))
    
    logger.info("Telegram event routing configured. Starting polling...")
    application.run_polling()

if __name__ == '__main__':
    main()
