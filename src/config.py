import os
import logging
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Authentication Keys
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
DB_FILE = os.getenv("DB_FILE", "chat_history.db")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")

# Database Encryption Key (AES-256 Fernet)
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
key_file_path = os.path.join(os.path.dirname(DB_FILE) or ".", "encryption_key.key")

if not ENCRYPTION_KEY:
    if os.path.exists(key_file_path):
        try:
            with open(key_file_path, "r", encoding="utf-8") as f:
                ENCRYPTION_KEY = f.read().strip()
            logging.getLogger(__name__).info("Loaded existing database ENCRYPTION_KEY from persistent key file.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to load key from persistent file: {e}")

if not ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet
        ENCRYPTION_KEY = Fernet.generate_key().decode()
        env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
        if os.path.exists(env_path):
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\n# Database Encryption Key\nENCRYPTION_KEY={ENCRYPTION_KEY}\n")
            logging.getLogger(__name__).info("Generated new database ENCRYPTION_KEY and saved to .env")
        
        # Save to persistent file to ensure it survives restarts
        with open(key_file_path, "w", encoding="utf-8") as f:
            f.write(ENCRYPTION_KEY)
        logging.getLogger(__name__).info("Saved database ENCRYPTION_KEY to persistent key file.")
    except Exception as e:
        logging.getLogger(__name__).error(f"Could not generate/save ENCRYPTION_KEY: {e}")
        # Use a stable dummy key as last-resort fallback to prevent boot crashes
        import base64
        import hashlib
        ENCRYPTION_KEY = base64.urlsafe_b64encode(hashlib.sha256(b"fallback_key").digest()).decode()


# Parse comma-separated list of allowed admins (case-insensitive)
allowed_admins_env = os.getenv("ALLOWED_ADMINS", "AmiraliNotFound")
ALLOWED_ADMINS = [username.strip().lower() for username in allowed_admins_env.split(",") if username.strip()]

# Default fallback values for DB config table
DEFAULT_CONFIG = {
    "MODEL_ID": "gemini-2.5-flash",  # Upgraded default model for fast & rich multimodal outputs
    "FALLBACK_MODELS": "gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it",
    "CONTEXT_LIMIT": "12",
    "TIMEOUT": "12.0",
    "MONITOR_LIMIT_RPM": "15",
    "MONITOR_LIMIT_RPD": "1500",
    "MONITOR_LIMIT_TTS_RPM": "15",
    "MONITOR_LIMIT_TTS_RPD": "1500",
    "TTS_ENGINE": "edge",  # "edge" or "gemini"
    "TTS_GEMINI_MODEL": "gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview",
    "TTS_GEMINI_VOICE": "Kore",
    "TTS_EDGE_VOICE": "fa-IR-FaridNeural",
    "TTS_FALLBACK_TO_EDGE": "True",
    "TTS_VOICE_PITCH": "1.0",
    "PERSONA_PRESETS": (
        '[{"name": "رفیق لاتی تیکه‌انداز (پیش‌فرض)", "prompt": "تو یه رفیق صمیمی، فوق‌العاده باحال، حاضر جواب و بشدت تیکه‌انداز هستی که توی یه گپ صمیمی رفقات رو دست می‌ندازی و رنده میکنی. لحنت اصلاً نباید کتابی، محترمانه یا لوس باشه. کاملاً لاتی، عامیانه و تهرانی صحبت کن. قانون مهم: کورکورانه و بی‌دلیل به کسی فحش نده یا توهین بی‌ربط نکن! تیکه‌هات باید هوشمندانه، باحال، رفیقونه ولی کوبنده باشن؛ دقیقاً مثل یه رفیق صمیمی که بقیه رو ضایع میکنه و می‌خندین. جواب‌هات باید خیلی کوتاه، کوبنده، تک‌خطی و در حد یک یا دو جمله باشن. طومار ننویس! حتماً از اموجی‌های رنده‌کننده مثل (😂، 🗿، 🚶‍♂️، 🤙، 🤫، 🥱) به شکل طبیعی استفاده کن تا لحنت طبیعی‌تر بشه. کلمات لوس استفاده نکن. تمرکزدادن روی پیام آخر مخاطب باشه و مستقیم و هوشمندانه جواب همون شخص رو بده."}, '
        '{"name": "دستیار مؤدب و مهربان", "prompt": "تو یک دستیار بسیار مؤدب، مهربان و راهنما هستی که با حوصله به سوالات پاسخ می‌دهد و لحن رسمی و مودبانه‌ای دارد."}, '
        '{"name": "دانشمند فیلسوف سنگین", "prompt": "تو یک دانشمند فیلسوف متفکر و جدی هستی که با لحنی سنگین، علمی و ادبی صحبت می‌کنی و مسائل عمیق را تحلیل می‌کنی."}]'
    ),
    "DAILY_SUMMARY_ENABLED": "False",
    "DAILY_SUMMARY_TIME": "00:00",
    "DAILY_SUMMARY_PROMPT": (
        "خلاصه غیبت‌ها، دعواها و بحث‌های امروز این گروه را در ۳ الی ۴ مورد کوتاه، با لحن لاتی، صمیمی و تمسخرآمیز بنویس. "
        "مشارکت‌کنندگان را بر اساس پیام‌هایشان دست بنداز و بدون سلام و تعارف فقط خلاصه را بفرست."
    ),
    "SYSTEM_INSTRUCTION": (
        "تو یه رفیق صمیمی، فوق‌العاده باحال، حاضر جواب و بشدت تیکه‌انداز هستی که توی یه گپ صمیمی رفقات رو دست می‌ندازی و رنده میکنی. "
        "لحنت اصلاً نباید کتابی، محترمانه یا لوس باشه. کاملاً لاتی، عامیانه و تهرانی صحبت کن. "
        "قانون مهم: کورکورانه و بی‌دلیل به کسی فحش نده یا توهین بی‌ربط نکن! تیکه‌هات باید هوشمندانه، باحال، رفیقونه ولی کوبنده باشن؛ "
        "دقیقاً مثل یه رفیق صمیمی که بقیه رو ضایع میکنه و می‌خندین. "
        "جواب‌هات باید خیلی کوتاه، کوبنده، تک‌خطی و در حد یک یا دو جمله باشن. طومار ننویس! "
        "حتماً از اموجی‌های رنده‌کننده مثل (😂، 🗿، 🚶‍♂️، 🤙، 🤫، 🥱) به شکل طبیعی استفاده کن تا لحنت طبیعی‌تر بشه. "
        "کلمات لوس استفاده نکن. تمرکزدادن روی پیام آخر مخاطب باشه و مستقیم و هوشمندانه جواب همون شخص رو بده."
    ),
    "RANDOM_ROAST_CHANCE": "0.02"
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
