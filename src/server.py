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

async def get_specials(request):
    check_auth(request)
    specials = await database.get_special_users(config.DB_FILE)
    return web.json_response([{"username": r[0], "instruction": r[1]} for r in specials])

async def add_special(request):
    check_auth(request)
    data = await request.json()
    await database.add_special_user(config.DB_FILE, data.get("username"), data.get("instruction"))
    return web.json_response({"status": "success"})

async def remove_special(request):
    check_auth(request)
    data = await request.json()
    await database.remove_special_user(config.DB_FILE, data.get("username"))
    return web.json_response({"status": "success"})

async def broadcast_msg(request):
    check_auth(request)
    data = await request.json()
    message_text = data.get("message")
    
    app = request.app.get("bot_app")
    if not app or not message_text:
        return web.json_response({"status": "error", "reason": "No bot app or message text"})
        
    chats = await database.get_all_chat_ids(config.DB_FILE)
    success = 0
    import asyncio
    
    async def send(chat_id):
        try:
            await app.bot.send_message(chat_id=chat_id, text=message_text, parse_mode="Markdown")
            return 1
        except Exception:
            return 0
            
    tasks = [send(c) for c in chats]
    results = await asyncio.gather(*tasks)
    success = sum(results)
    
    return web.json_response({"status": "success", "sent": success, "total": len(chats)})

async def index_handler(request):
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "webapp", "dist")
    index_file = os.path.join(frontend_dir, 'index.html')
    if os.path.exists(index_file):
        return web.FileResponse(index_file)
    return web.Response(text="Webapp not built yet.", status=404)

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
    
    cors.add(app.router.add_get('/api/specials', get_specials))
    cors.add(app.router.add_post('/api/specials', add_special))
    cors.add(app.router.add_post('/api/specials/delete', remove_special))
    cors.add(app.router.add_post('/api/broadcast', broadcast_msg))

    # Serve static frontend files and SPA root
    app.router.add_get('/', index_handler)
    
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "webapp", "dist")
    if os.path.exists(frontend_dir):
        app.router.add_static('/', frontend_dir, name='static', show_index=False)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("Web API Server started on port 8080")
    return runner
