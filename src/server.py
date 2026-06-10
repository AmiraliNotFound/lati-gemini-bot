import logging
import json
import os
import hmac
import hashlib
import time
import asyncio
import traceback
from urllib.parse import parse_qsl
from aiohttp import web
import aiohttp_cors
from src import database, config

logger = logging.getLogger(__name__)

def validate_telegram_webapp_data(token: str, init_data: str) -> bool:
    if not init_data:
        return False
    try:
        parsed_data = dict(parse_qsl(init_data))
        if "hash" not in parsed_data:
            return False
        received_hash = parsed_data.pop("hash")
        
        # Sort and construct data-check-string
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed_data.items()))
        
        # Calculate secret key: HMAC-SHA256 of token with key "WebAppData"
        secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        
        # Calculate validation hash: HMAC-SHA256 of data-check-string with secret_key
        validation_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        
        # Verify hash matches
        if not hmac.compare_digest(validation_hash, received_hash):
            return False
            
        # Check authentication age (max 24 hours to prevent replay attacks)
        auth_date = int(parsed_data.get("auth_date", 0))
        if time.time() - auth_date > 86400: # 24 hours
            logger.warning("Telegram WebApp authentication expired")
            return False
            
        return True
    except Exception as e:
        logger.error(f"Error validating WebApp data: {e}")
        try:
            asyncio.run(database.log_error(config.DB_FILE, "WEBAPP_AUTH_ERROR", f"Error validating WebApp data: {e}", traceback.format_exc()))
        except Exception:
            pass
        return False

def check_auth(request):
    """
    Validates the Telegram WebApp initData HMAC signature and verifies
    that the requesting user is in the ALLOWED_ADMINS list.
    """
    if os.getenv("DEV_BYPASS") == "true":
        return

    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise web.HTTPUnauthorized(
            text=json.dumps({"status": "error", "reason": "Missing or invalid Authorization header"}),
            content_type="application/json"
        )
        
    init_data = auth_header.split(" ", 1)[1]
    if not init_data:
        raise web.HTTPUnauthorized(
            text=json.dumps({"status": "error", "reason": "Authentication token is empty"}),
            content_type="application/json"
        )

    if not validate_telegram_webapp_data(config.TELEGRAM_TOKEN, init_data):
        raise web.HTTPUnauthorized(
            text=json.dumps({"status": "error", "reason": "Invalid authentication signature"}),
            content_type="application/json"
        )
        
    # Verify user is an authorized admin
    try:
        parsed_data = dict(parse_qsl(init_data))
        user_str = parsed_data.get("user")
        if not user_str:
            raise web.HTTPForbidden(
                text=json.dumps({"status": "error", "reason": "User data missing from authentication details"}),
                content_type="application/json"
            )
            
        user_data = json.loads(user_str)
        username = user_data.get("username")
        
        if not username or username.lower() not in config.ALLOWED_ADMINS:
            raise web.HTTPForbidden(
                text=json.dumps({"status": "error", "reason": f"Access denied: User @{username} is not an authorized administrator"}),
                content_type="application/json"
            )
    except Exception as e:
        logger.error(f"Failed to check admin permissions: {e}")
        try:
            asyncio.run(database.log_error(config.DB_FILE, "WEBAPP_AUTH_ERROR", f"Failed to check admin permissions: {e}", traceback.format_exc()))
        except Exception:
            pass
        raise web.HTTPForbidden(
            text=json.dumps({"status": "error", "reason": f"Permission verification failed: {e}"}),
            content_type="application/json"
        )

async def get_stats(request):
    check_auth(request)
    stats = await database.get_db_stats(config.DB_FILE)
    errors = await database.get_recent_errors(config.DB_FILE, limit=10)
    stats["recent_errors"] = [{"timestamp": e[0], "type": e[1], "message": e[2], "stack_trace": e[3]} for e in errors]
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
    chats = await database.get_detailed_chats(config.DB_FILE)
    return web.json_response(chats)

async def update_chat_settings_handler(request):
    check_auth(request)
    data = await request.json()
    chat_id = int(data.get("chat_id"))
    is_muted = data.get("is_muted")
    custom_roast_chance = data.get("custom_roast_chance")
    custom_cooldown = data.get("custom_cooldown")
    custom_tts_engine = data.get("custom_tts_engine")

    await database.update_chat_settings(
        config.DB_FILE,
        chat_id,
        is_muted=is_muted,
        custom_roast_chance=custom_roast_chance,
        custom_cooldown=custom_cooldown,
        custom_tts_engine=custom_tts_engine
    )
    return web.json_response({"status": "success"})

async def leave_chat_handler(request):
    check_auth(request)
    data = await request.json()
    chat_id = int(data.get("chat_id"))
    app = request.app.get("bot_app")
    if not app:
        return web.json_response({"status": "error", "reason": "Bot application not running"}, status=500)
    try:
        await app.bot.leave_chat(chat_id)
        return web.json_response({"status": "success"})
    except Exception as e:
        logger.error(f"Failed to leave chat {chat_id}: {e}")
        await database.log_error(config.DB_FILE, "TELEGRAM_API_ERROR", f"Failed to leave chat {chat_id}: {e}", traceback.format_exc())
        return web.json_response({"status": "error", "reason": str(e)}, status=500)

async def alert_chat_handler(request):
    check_auth(request)
    data = await request.json()
    chat_id = int(data.get("chat_id"))
    message = data.get("message")
    if not message:
        return web.json_response({"status": "error", "reason": "Message text is empty"}, status=400)
    app = request.app.get("bot_app")
    if not app:
        return web.json_response({"status": "error", "reason": "Bot application not running"}, status=500)
    try:
        await app.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")
        return web.json_response({"status": "success"})
    except Exception as e:
        logger.error(f"Failed to send alert to chat {chat_id}: {e}")
        await database.log_error(config.DB_FILE, "TELEGRAM_SEND_ERROR", f"Failed to send alert to chat {chat_id}: {e}", traceback.format_exc())
        return web.json_response({"status": "error", "reason": str(e)}, status=500)

async def get_top_users_handler(request):
    check_auth(request)
    chat_id = int(request.query.get("chat_id"))
    limit = int(request.query.get("limit", 5))
    users = await database.get_top_chat_users(config.DB_FILE, chat_id, limit=limit)
    return web.json_response(users)


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
            await database.log_error(config.DB_FILE, "TELEGRAM_API_ERROR", f"Could not leave chat {target_id}: {e}", traceback.format_exc())
            
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

async def upload_cookies(request):
    check_auth(request)
    try:
        data = await request.json()
        cookies_text = data.get("cookies", "")
        if not cookies_text:
            return web.json_response({"status": "error", "reason": "No cookies text provided"}, status=400)
            
        cookies_path = os.path.join(os.path.dirname(config.DB_FILE), "cookies.txt")
        root_cookies_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cookies.txt")
        
        with open(cookies_path, "w", encoding="utf-8") as f:
            f.write(cookies_text)
        try:
            with open(root_cookies_path, "w", encoding="utf-8") as f:
                f.write(cookies_text)
        except:
            pass
            
        logger.info("New cookies.txt file successfully uploaded and synced.")
        return web.json_response({"status": "success"})
    except Exception as e:
        logger.error(f"Failed to save uploaded cookies: {e}")
        await database.log_error(config.DB_FILE, "COOKIES_UPLOAD_ERROR", f"Failed to save uploaded cookies: {e}", traceback.format_exc())
        return web.json_response({"status": "error", "reason": str(e)}, status=500)

async def update_ytdlp(request):
    check_auth(request)
    try:
        process = await asyncio.create_subprocess_exec(
            "pip", "install", "--upgrade", "yt-dlp",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        if process.returncode == 0:
            logger.info("yt-dlp updated successfully.")
            return web.json_response({"status": "success", "output": stdout.decode()})
        else:
            reason = stderr.decode() or "Unknown process error"
            logger.error(f"Failed to update yt-dlp: {reason}")
            await database.log_error(config.DB_FILE, "SYSTEM_UPDATE_ERROR", f"Failed to update yt-dlp: {reason}")
            return web.json_response({"status": "error", "reason": reason}, status=500)
    except Exception as e:
        logger.error(f"Failed to update yt-dlp via subprocess: {e}")
        await database.log_error(config.DB_FILE, "SYSTEM_UPDATE_ERROR", f"Failed to update yt-dlp via subprocess: {e}", traceback.format_exc())
        return web.json_response({"status": "error", "reason": str(e)}, status=500)

async def get_model_limits(request):
    check_auth(request)
    
    # Gather in-use models
    models_in_use = set()
    
    primary_model = config.runtime_config.get("MODEL_ID")
    if primary_model:
        models_in_use.add(primary_model.strip())
        
    fallback_str = config.runtime_config.get("FALLBACK_MODELS", "")
    for m in fallback_str.split(","):
        if m.strip():
            models_in_use.add(m.strip())
            
    tts_models = set()
    tts_str = config.runtime_config.get("TTS_GEMINI_MODEL", "")
    for m in tts_str.split(","):
        if m.strip():
            name = m.strip()
            models_in_use.add(name)
            tts_models.add(name)
            if not name.startswith("models/"):
                tts_models.add(f"models/{name}")
                
    # Fetch usage stats from DB
    usage_stats = await database.get_model_usage_stats(config.DB_FILE)
    
    # Fetch details for each model from GenAI client
    from src.handlers import get_ai_client
    
    try:
        limit_rpm = int(config.runtime_config.get("MONITOR_LIMIT_RPM", "15"))
    except ValueError:
        limit_rpm = 15

    try:
        limit_rpd = int(config.runtime_config.get("MONITOR_LIMIT_RPD", "1500"))
    except ValueError:
        limit_rpd = 1500

    try:
        limit_tts_rpm = int(config.runtime_config.get("MONITOR_LIMIT_TTS_RPM", "15"))
    except ValueError:
        limit_tts_rpm = 15

    try:
        limit_tts_rpd = int(config.runtime_config.get("MONITOR_LIMIT_TTS_RPD", "1500"))
    except ValueError:
        limit_tts_rpd = 1500
    
    models_data = []
    
    try:
        client = get_ai_client()
    except Exception as e:
        logger.error(f"Failed to initialize AI Client for limits check: {e}")
        await database.log_error(config.DB_FILE, "GENAI_ERROR", f"Failed to initialize AI Client for limits check: {e}", traceback.format_exc())
        client = None

    for model_name in sorted(list(models_in_use)):
        is_tts = model_name in tts_models
        curr_rpm = limit_tts_rpm if is_tts else limit_rpm
        curr_rpd = limit_tts_rpd if is_tts else limit_rpd
        limits = {"rpm": curr_rpm, "tpm": 1000000, "rpd": curr_rpd}
        
        # Get specific model usage stats (total attempts & errors)
        model_usage = await database.get_specific_model_usage(config.DB_FILE, model_name)
        
        if not client:
            models_data.append({
                "model_id": model_name,
                "display_name": model_name,
                "description": "API client configuration error.",
                "input_token_limit": None,
                "output_token_limit": None,
                "status": "error",
                "is_tts": is_tts,
                "error": "API Key or client initialization failed.",
                "limits": limits,
                "usage": model_usage
            })
            continue

        try:
            # Query standard API model details
            info = await client.models.get(model=model_name)
            models_data.append({
                "model_id": model_name,
                "display_name": getattr(info, "display_name", model_name),
                "description": getattr(info, "description", ""),
                "input_token_limit": getattr(info, "input_token_limit", None),
                "output_token_limit": getattr(info, "output_token_limit", None),
                "status": "active",
                "is_tts": is_tts,
                "error": None,
                "limits": limits,
                "usage": model_usage
            })
        except Exception as e:
            # Try with models/ prefix if it doesn't already have it
            if not model_name.startswith("models/"):
                try:
                    info = await client.models.get(model=f"models/{model_name}")
                    models_data.append({
                        "model_id": model_name,
                        "display_name": getattr(info, "display_name", model_name),
                        "description": getattr(info, "description", ""),
                        "input_token_limit": getattr(info, "input_token_limit", None),
                        "output_token_limit": getattr(info, "output_token_limit", None),
                        "status": "active",
                        "is_tts": is_tts,
                        "error": None,
                        "limits": limits,
                        "usage": model_usage
                    })
                    continue
                except Exception:
                    pass
            
            models_data.append({
                "model_id": model_name,
                "display_name": model_name,
                "description": "Could not query model metadata.",
                "input_token_limit": None,
                "output_token_limit": None,
                "status": "error",
                "is_tts": is_tts,
                "error": str(e),
                "limits": limits,
                "usage": model_usage
            })

    return web.json_response({
        "models": models_data,
        "usage": usage_stats
    })

async def index_handler(request):
    frontend_dir = os.path.join(os.path.dirname(__file__), "..", "webapp", "dist")
    index_file = os.path.join(frontend_dir, 'index.html')
    if os.path.exists(index_file):
        return web.FileResponse(index_file)
    return web.Response(text="Webapp not built yet.", status=404)

async def setup_server(bot_app):
    app = web.Application()
    app["bot_app"] = bot_app
    
    # CORS origin lock: lock to WebApp URL domain if configured
    cors_origin = config.WEBAPP_URL if config.WEBAPP_URL else "*"
    cors = aiohttp_cors.setup(app, defaults={
        cors_origin: aiohttp_cors.ResourceOptions(
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
    
    # New management routes
    cors.add(app.router.add_post('/api/upload_cookies', upload_cookies))
    cors.add(app.router.add_post('/api/update_ytdlp', update_ytdlp))
    cors.add(app.router.add_post('/api/chat/settings', update_chat_settings_handler))
    cors.add(app.router.add_post('/api/chat/leave', leave_chat_handler))
    cors.add(app.router.add_post('/api/chat/alert', alert_chat_handler))
    cors.add(app.router.add_get('/api/chat/top_users', get_top_users_handler))
    cors.add(app.router.add_get('/api/model_limits', get_model_limits))

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
