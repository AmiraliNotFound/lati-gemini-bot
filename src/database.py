import os
import aiosqlite
import logging
from cryptography.fernet import Fernet
from src.config import runtime_config, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

_cipher = None
def get_cipher():
    global _cipher
    if _cipher is None:
        from src import config
        key = config.ENCRYPTION_KEY
        if key:
            try:
                _cipher = Fernet(key.encode())
            except Exception as e:
                logger.error(f"Failed to initialize Fernet cipher: {e}")
    return _cipher

def encrypt_text(text: str) -> str:
    if not text:
        return text
    cipher = get_cipher()
    if not cipher:
        return text
    try:
        encrypted = cipher.encrypt(text.encode("utf-8")).decode("utf-8")
        return f"enc:{encrypted}"
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return text

def decrypt_text(cipher_text: str) -> str:
    if not cipher_text or not cipher_text.startswith("enc:"):
        return cipher_text
    cipher = get_cipher()
    if not cipher:
        return cipher_text
    try:
        encrypted_part = cipher_text[4:]
        decrypted = cipher.decrypt(encrypted_part.encode("utf-8")).decode("utf-8")
        return decrypted
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return cipher_text


async def init_db(db_path: str):
    """Initializes schema and synchronizes live dynamic configuration values."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                role TEXT,
                sender_name TEXT,
                text TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON messages(chat_id);")
        
        await db.execute('''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS special_users (
                username TEXT PRIMARY KEY,
                instruction TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS blocked (
                target_id INTEGER PRIMARY KEY,
                type TEXT,
                name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS chat_metadata (
                chat_id INTEGER PRIMARY KEY,
                chat_name TEXT,
                chat_type TEXT,
                msg_count INTEGER DEFAULT 0,
                last_active DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_muted INTEGER DEFAULT 0,
                custom_roast_chance REAL DEFAULT NULL,
                custom_cooldown INTEGER DEFAULT NULL
            )
        ''')
        
        # Safe schema migrations for existing databases
        migration_columns = [
            ("chat_type", "TEXT"),
            ("msg_count", "INTEGER DEFAULT 0"),
            ("last_active", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
            ("is_muted", "INTEGER DEFAULT 0"),
            ("custom_roast_chance", "REAL DEFAULT NULL"),
            ("custom_cooldown", "INTEGER DEFAULT NULL")
        ]
        for col_name, col_type in migration_columns:
            try:
                await db.execute(f"ALTER TABLE chat_metadata ADD COLUMN {col_name} {col_type}")
                logger.info(f"Database migration: Added column {col_name} to chat_metadata.")
            except aiosqlite.OperationalError:
                # Column already exists
                pass
        await db.execute('''
            CREATE TABLE IF NOT EXISTS error_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                error_type TEXT,
                error_message TEXT,
                stack_trace TEXT
            )
        ''')
        await db.commit()
        
        # Populate config table if empty
        async with db.execute("SELECT COUNT(*) FROM config") as cursor:
            count = (await cursor.fetchone())[0]
            if count == 0:
                for k, v in DEFAULT_CONFIG.items():
                    await db.execute("INSERT INTO config (key, value) VALUES (?, ?)", (k, v))
                await db.commit()
                logger.info("Database config initialized with default parameters.")
            else:
                # Load configuration from database into dynamic config cache
                async with db.execute("SELECT key, value FROM config") as read_cursor:
                    rows = await read_cursor.fetchall()
                    for key, value in rows:
                        runtime_config[key] = value
                logger.info("Dynamic config loaded from database.")

async def store_message(db_path: str, chat_id: int, role: str, sender_name: str, text: str):
    """Logs an incoming or outgoing message and prunes history past the threshold."""
    encrypted_text = encrypt_text(text)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO messages (chat_id, role, sender_name, text) VALUES (?, ?, ?, ?)",
            (chat_id, role, sender_name, encrypted_text)
        )
        # Keep up to 200 messages in db for /tldr summarization
        await db.execute('''
            DELETE FROM messages WHERE id NOT IN (
                SELECT id FROM messages WHERE chat_id = ? 
                ORDER BY timestamp DESC LIMIT ?
            ) AND chat_id = ?
        ''', (chat_id, 200, chat_id))
        await db.commit()

async def get_chat_history(db_path: str, chat_id: int, limit: int) -> list:
    """Retrieves chat history in chronological order."""
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT role, sender_name, text FROM messages WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?",
            (chat_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()
            rows.reverse()
            decrypted_rows = []
            for role, name, val in rows:
                decrypted_rows.append((role, name, decrypt_text(val)))
            return decrypted_rows


async def save_config_key(db_path: str, key: str, value: str):
    """Updates runtime configuration cache and persists it to database."""
    runtime_config[key] = value
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, value))
        await db.commit()

async def get_db_stats(db_path: str) -> dict:
    """Computes stats from the local SQLite database for the admin panel."""
    stats = {}
    if not os.path.exists(db_path):
        return {"total_messages": 0, "total_chats": 0, "db_size_kb": 0}
        
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM messages") as cursor:
            stats["total_messages"] = (await cursor.fetchone())[0]
        async with db.execute("SELECT COUNT(DISTINCT chat_id) FROM messages") as cursor:
            stats["total_chats"] = (await cursor.fetchone())[0]
            
    size_bytes = os.path.getsize(db_path)
    stats["db_size_kb"] = round(size_bytes / 1024, 2)
    return stats

async def get_all_chat_ids(db_path: str) -> list:
    """Retrieves list of distinct chat IDs recorded in the database."""
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT DISTINCT chat_id FROM messages") as cursor:
            rows = await cursor.fetchall()
            return [r[0] for r in rows]

async def add_special_user(db_path: str, username: str, instruction: str):
    """Saves or updates a user-specific system instruction in the database."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO special_users (username, instruction) VALUES (?, ?)",
            (username.strip().lstrip("@"), instruction)
        )
        await db.commit()

async def remove_special_user(db_path: str, username: str):
    """Removes a user from the special_users database table."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "DELETE FROM special_users WHERE LOWER(username) = ?",
            (username.strip().lower().lstrip("@"),)
        )
        await db.commit()

async def get_special_users(db_path: str) -> list:
    """Retrieves all special users and their custom instructions."""
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT username, instruction FROM special_users") as cursor:
            return await cursor.fetchall()

async def get_special_user_instruction(db_path: str, username: str) -> str:
    """Fetches custom instruction for a user if one is defined."""
    if not username or not os.path.exists(db_path):
        return None
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT instruction FROM special_users WHERE LOWER(username) = ?",
            (username.strip().lower(),)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

async def block_target(db_path: str, target_id: int, target_type: str, name: str):
    """Blocks a user or group."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO blocked (target_id, type, name) VALUES (?, ?, ?)",
            (target_id, target_type, name)
        )
        await db.commit()

async def unblock_target(db_path: str, target_id: int):
    """Unblocks a user or group."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("DELETE FROM blocked WHERE target_id = ?", (target_id,))
        await db.commit()

async def get_blocked_targets(db_path: str) -> list:
    """Retrieves all blocked entities."""
    if not os.path.exists(db_path): return []
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT target_id, type, name FROM blocked ORDER BY timestamp DESC") as cursor:
            return [{"id": r[0], "type": r[1], "name": r[2]} for r in await cursor.fetchall()]

async def is_blocked(db_path: str, target_id: int) -> bool:
    """Checks if an ID is blocked."""
    if not os.path.exists(db_path): return False
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT 1 FROM blocked WHERE target_id = ?", (target_id,)) as cursor:
            return await cursor.fetchone() is not None

async def save_chat_metadata(db_path: str, chat_id: int, chat_name: str, chat_type: str = "unknown"):
    async with aiosqlite.connect(db_path) as db:
        # Check if chat metadata row exists
        async with db.execute("SELECT msg_count FROM chat_metadata WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            
        if row is None:
            await db.execute(
                "INSERT INTO chat_metadata (chat_id, chat_name, chat_type, msg_count, last_active) VALUES (?, ?, ?, 1, CURRENT_TIMESTAMP)",
                (chat_id, chat_name, chat_type)
            )
        else:
            new_msg_count = row[0] + 1
            await db.execute(
                "UPDATE chat_metadata SET chat_name = ?, chat_type = ?, msg_count = ?, last_active = CURRENT_TIMESTAMP WHERE chat_id = ?",
                (chat_name, chat_type, new_msg_count, chat_id)
            )
        await db.commit()

async def log_error(db_path: str, error_type: str, error_message: str, stack_trace: str = ""):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO error_logs (error_type, error_message, stack_trace) VALUES (?, ?, ?)",
            (error_type, error_message, stack_trace)
        )
        # Keep only the last 50 errors
        await db.execute(
            "DELETE FROM error_logs WHERE id NOT IN (SELECT id FROM error_logs ORDER BY id DESC LIMIT 50)"
        )
        await db.commit()

async def get_recent_errors(db_path: str, limit: int = 10):
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT timestamp, error_type, error_message, stack_trace FROM error_logs ORDER BY id DESC LIMIT ?", (limit,)) as cursor:
            return await cursor.fetchall()

async def get_recent_chats(db_path: str, limit: int = 50) -> list:
    """Gets recently active chats for the admin panel."""
    if not os.path.exists(db_path): return []
    async with aiosqlite.connect(db_path) as db:
        # Join with chat_metadata to get the real group/user name
        query = '''
            SELECT m.chat_id, MAX(m.timestamp) as last_active, cm.chat_name
            FROM messages m
            LEFT JOIN chat_metadata cm ON m.chat_id = cm.chat_id
            GROUP BY m.chat_id
            ORDER BY last_active DESC LIMIT ?
        '''
        async with db.execute(query, (limit,)) as cursor:
            return [{"chat_id": r[0], "last_active": r[1], "name": r[2] or f"ID: {r[0]}"} for r in await cursor.fetchall()]

async def is_chat_muted(db_path: str, chat_id: int) -> bool:
    """Checks if a specific chat has been muted by the admin."""
    if not os.path.exists(db_path):
        return False
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT is_muted FROM chat_metadata WHERE chat_id = ?", (chat_id,)) as cursor:
            row = await cursor.fetchone()
            return row is not None and row[0] == 1

async def get_chat_settings(db_path: str, chat_id: int) -> dict:
    """Retrieves dynamic settings (roast chance, cooldown, mute status) for a chat."""
    if not os.path.exists(db_path):
        return {}
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(
            "SELECT custom_roast_chance, custom_cooldown, is_muted FROM chat_metadata WHERE chat_id = ?",
            (chat_id,)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "custom_roast_chance": row[0],
                    "custom_cooldown": row[1],
                    "is_muted": row[2]
                }
            return {}

async def update_chat_settings(db_path: str, chat_id: int, is_muted: int = None, custom_roast_chance: float = None, custom_cooldown: int = None):
    """Updates settings for a specific chat."""
    async with aiosqlite.connect(db_path) as db:
        updates = []
        params = []
        if is_muted is not None:
            updates.append("is_muted = ?")
            params.append(is_muted)
            
        if custom_roast_chance is not None:
            if custom_roast_chance == "" or custom_roast_chance is None:
                updates.append("custom_roast_chance = NULL")
            else:
                updates.append("custom_roast_chance = ?")
                params.append(float(custom_roast_chance))
                
        if custom_cooldown is not None:
            if custom_cooldown == "" or custom_cooldown is None:
                updates.append("custom_cooldown = NULL")
            else:
                updates.append("custom_cooldown = ?")
                params.append(int(custom_cooldown))
                
        if updates:
            query = f"UPDATE chat_metadata SET {', '.join(updates)} WHERE chat_id = ?"
            params.append(chat_id)
            await db.execute(query, params)
            await db.commit()

async def get_detailed_chats(db_path: str) -> list:
    """Gets detailed chat info for the moderation panel."""
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as db:
        query = '''
            SELECT chat_id, chat_name, chat_type, msg_count, last_active, is_muted, custom_roast_chance, custom_cooldown
            FROM chat_metadata
            ORDER BY last_active DESC
        '''
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [{
                "chat_id": r[0],
                "name": r[1] or f"ID: {r[0]}",
                "type": r[2] or "unknown",
                "msg_count": r[3],
                "last_active": r[4],
                "is_muted": r[5],
                "custom_roast_chance": r[6],
                "custom_cooldown": r[7]
            } for r in rows]

async def get_top_chat_users(db_path: str, chat_id: int, limit: int = 5) -> list:
    """Gets the top active group participants based on logged history."""
    if not os.path.exists(db_path):
        return []
    async with aiosqlite.connect(db_path) as db:
        query = '''
            SELECT sender_name, COUNT(*) as cnt
            FROM messages
            WHERE chat_id = ? AND role = 'user'
            GROUP BY sender_name
            ORDER BY cnt DESC
            LIMIT ?
        '''
        async with db.execute(query, (chat_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [{"name": r[0], "count": r[1]} for r in rows]



