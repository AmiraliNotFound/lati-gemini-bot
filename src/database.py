import os
import aiosqlite
import logging
from src.config import runtime_config, DEFAULT_CONFIG

logger = logging.getLogger(__name__)

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
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO messages (chat_id, role, sender_name, text) VALUES (?, ?, ?, ?)",
            (chat_id, role, sender_name, text)
        )
        limit = int(runtime_config.get("CONTEXT_LIMIT", 12))
        # Keep limit * 2 messages in db to have buffer history
        await db.execute('''
            DELETE FROM messages WHERE id NOT IN (
                SELECT id FROM messages WHERE chat_id = ? 
                ORDER BY timestamp DESC LIMIT ?
            ) AND chat_id = ?
        ''', (chat_id, limit * 2, chat_id))
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
            return rows

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

