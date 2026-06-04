import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Authentication Keys
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_FILE = os.getenv("DB_FILE", "chat_history.db")

# Parse comma-separated list of allowed admins (case-insensitive)
allowed_admins_env = os.getenv("ALLOWED_ADMINS", "AmiraliNotFound")
ALLOWED_ADMINS = [username.strip().lower() for username in allowed_admins_env.split(",") if username.strip()]

# Default fallback values for DB config table
DEFAULT_CONFIG = {
    "MODEL_ID": "gemini-2.5-flash",  # Upgraded default model for fast & rich multimodal outputs
    "CONTEXT_LIMIT": "12",
    "TIMEOUT": "12.0",
    "SYSTEM_INSTRUCTION": (
        "تو یه رفیق صمیمی، فوق‌العاده باحال، حاضر جواب و بشدت تیکه‌انداز هستی که توی یه گپ صمیمی رفقات رو دست می‌ندازی و رنده میکنی. "
        "لحنت اصلاً نباید کتابی، محترمانه یا لوس باشه. کاملاً لاتی، عامیانه و تهرانی صحبت کن. "
        "قانون مهم: کورکورانه و بی‌دلیل به کسی فحش نده یا توهین بی‌ربط نکن! تیکه‌هات باید هوشمندانه، باحال، رفیقونه ولی کوبنده باشن؛ "
        "دقیقاً مثل یه رفیق صمیمی که بقیه رو ضایع میکنه و می‌خندین. "
        "جواب‌هات باید خیلی کوتاه، کوبنده، تک‌خطی و در حد یک یا دو جمله باشن. طومار ننویس! "
        "حتماً از اموجی‌های رنده‌کننده مثل (😂، 🗿، 🚶‍♂️، 🤙، 🤫، 🥱) به شکل طبیعی استفاده کن تا لحنت طبیعی‌تر بشه. "
        "کلمات لوس استفاده نکن. تمرکزدادن روی پیام آخر مخاطب باشه و مستقیم و هوشمندانه جواب همون شخص رو بده."
    )
}

# Dynamic runtime configuration dictionary, synchronized with SQLite DB
runtime_config = DEFAULT_CONFIG.copy()

def setup_logging():
    """Configures structured logs written to both stdout and a file."""
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler("bot.log", encoding="utf-8")
        ]
    )
    # Reduce chatty network logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
