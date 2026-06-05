import logging
import json
import os
from aiohttp import web
from src import database, config

logger = logging.getLogger(__name__)

def check_auth(request):
    """
    Security is expected to be handled by Cloudflare Zero Trust/Tunnels,
    since the API and frontend will be served behind a secure proxy.
    """
    pass

async def get_stats(request):
    check_auth(request)
    stats = await database.get_db_stats(config.DB_FILE)
    return web.json_response(stats)

async def get_config(request):
    check_auth(request)
    return web.json_response(config.runtime_config)

async def update_config(request):
    check_auth(request)
    data = await request.json()
    for key, value in data.items():
        await database.save_config_key(config.DB_FILE, key, str(value))
    return web.json_response({"status": "success"})

async def get_recent_chats(request):
    check_auth(request)
    chats = await database.get_recent_chats(config.DB_FILE, limit=100)
    return web.json_response(chats)

async def get_blocked(request):
    check_auth(request)
    blocked = await database.get_blocked_targets(config.DB_FILE)
    return web.json_response(blocked)

async def block_target(request):
    check_auth(request)
    data = await request.json()
    target_id = int(data.get("target_id"))
    target_type = data.get("type", "unknown")
    name = data.get("name", "Unknown")
    await database.block_target(config.DB_FILE, target_id, target_type, name)
    
    # Try leaving if it's a chat
    app = request.app.get("bot_app")
    if app and target_id < 0:
        try:
            await app.bot.leave_chat(target_id)
        except Exception as e:
            logger.error(f"Could not leave chat {target_id}: {e}")
            
    return web.json_response({"status": "success"})

async def unblock_target(request):
    check_auth(request)
    data = await request.json()
    target_id = int(data.get("target_id"))
    await database.unblock_target(config.DB_FILE, target_id)
    return web.json_response({"status": "success"})

async def setup_server(bot_app):
    app = web.Application()
    app["bot_app"] = bot_app
    
    # CORS handling for local dev
    import aiohttp_cors
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    cors.add(app.router.add_get('/api/stats', get_stats))
    cors.add(app.router.add_get('/api/config', get_config))
    cors.add(app.router.add_post('/api/config', update_config))
    cors.add(app.router.add_get('/api/chats', get_recent_chats))
    cors.add(app.router.add_get('/api/blocked', get_blocked))
    cors.add(app.router.add_post('/api/block', block_target))
    cors.add(app.router.add_post('/api/unblock', unblock_target))

    # Serve static frontend files
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "webapp", "dist")
    if os.path.exists(frontend_dir):
        app.router.add_static('/', frontend_dir, name='static', show_index=True)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Web API Server started on port 8080")
    return runner
