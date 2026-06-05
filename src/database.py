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
                chat_name TEXT
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

async def save_chat_metadata(db_path: str, chat_id: int, chat_name: str):
    """Saves the latest chat title."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT OR REPLACE INTO chat_metadata (chat_id, chat_name) VALUES (?, ?)", (chat_id, chat_name))
        await db.commit()

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


