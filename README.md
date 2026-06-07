# 🤖 Lati Gemini Telegram Bot

A highly customizable, production-grade, and containerized Telegram Bot powered by the modern **Google GenAI SDK** (Gemini) and featuring an interactive SQLite message-history pipeline. 

The bot is calibrated out-of-the-box with a witty, teasing, and sarcastic Persian persona (Tehrani slang). It also features advanced admin analytics, chat broadcasting, and **multimodal support** (handles images and voice messages natively).

---

## ✨ Features

* **🧠 Advanced Context Management**: Uses `aiosqlite` to store message histories per chat dynamically. Prunes context buffers efficiently to keep Gemini response latencies low.
  * **TL;DR Group Summarization**: Type `/tldr` in any group to get a snappy, sarcastic Persian summary of the last 150 messages of drama.
* **🎭 Random Unprovoked Roasts**: The bot will randomly jump into group chats (with a configurable probability) to roast users without being tagged, making it feel truly alive.
* **📸 Multimodal Capabilities**: 
  * **Images**: Send photos to the bot, and it will analyze and roast/respond to them in character.
  * **Voice Messages**: Send voice notes; the bot downloads and processes the audio natively through Gemini.
* **📥 Smart Media Downloader**: Automatically detects Instagram, YouTube, and Twitter links in chat. Downloads the actual video via `yt-dlp` and re-uploads it natively so nobody has to click the link!
  * **Instagram Bypass**: Due to strict data-center IP blocking by Meta, Instagram downloads may require a session cookie. Simply export a fake account's cookies using the "Get cookies.txt LOCALLY" browser extension and upload it to the `data/` folder as `cookies.txt`. The bot will automatically detect and use it to securely bypass the login wall!
* **🛡️ Secure Configuration**: Zero hardcoded secrets. Fully configured via environment variables and loaded asynchronously.
* **📊 Robust Admin Control Panel**:
  * **✨ Interactive Web App Dashboard**: A full React-based GUI panel right inside Telegram! Features butter-smooth sliding animations, glassmorphism toast notifications, and dark-mode styling.
    * **Stats Tab**: Live auto-updating system metrics (total chats, processed messages, DB size) and a detailed log of the 10 most recent system and scraper errors.
    * **Mod Tab**: View all active chats with their real names and block/leave them with one click.
    * **VIPs Tab**: Visual interface to add, edit, and remove Special Users and their custom personas.
    * **Cast Tab**: Type a broadcast message and fire it to every single user/group instantly.
    * **Conf Tab**: Adjust context limits, timeouts, model IDs, and system prompts on the fly.
  * **Fallback Inline Commands**: Classic `/admin` inline commands are still fully supported.
  * **Lazy Event Loop Binding**: Implements dynamic async client initialization to prevent runtime crashes during container redeployments.
* **📜 Production Logging**: Captures logs to both the terminal and rotating `bot.log` files.

---

## 📂 Project Structure

```text
lati_gemini_bot/
├── src/
│   ├── __init__.py      # Package initializer
│   ├── config.py        # Settings loader, environment validations, and logging setup
│   ├── database.py      # Asynchronous database connection, schema setup, stats, and operations
│   └── handlers.py      # Core Telegram handlers (Admin commands, Text/Multimodal messaging)
├── main.py              # Main entry point to initialize and start bot polling
├── requirements.txt     # Python dependency definitions
├── Dockerfile           # Multi-stage optimized Docker build definition
├── docker-compose.yml   # Compose setup for Docker volume and env mounts
├── .env.example         # Template configuration file
└── README.md            # Comprehensive system documentation
```

---

## 🚀 Deployment Guide

### Option A: The Recommended Docker Method (1-Click Deployment)

The most robust way to deploy the bot on your VPS or server is utilizing Docker. This isolates Python runtime dependencies and prevents OS configuration mismatches.

#### 1. Pre-requisites
Ensure Docker and Docker Compose are installed on your VPS:
```bash
sudo apt update && sudo apt install docker.io docker-compose -y
```

#### 2. Deploy Code
Clone or move the project folder onto the VPS at `/opt/lati_gemini_bot`:
```bash
mkdir -p /opt/lati_gemini_bot
cd /opt/lati_gemini_bot
# (Copy project files here via SFTP, git, or nano)
```

#### 3. Configuration
Copy the template configuration file and fill in your secrets:
```bash
cp .env.example .env
nano .env
```
Fill in:
* `TELEGRAM_TOKEN` (from [@BotFather](https://t.me/BotFather))
* `GEMINI_API_KEY` (from [Google AI Studio](https://aistudio.google.com))
* `ALLOWED_ADMINS` (your username, e.g., `AmiraliNotFound`)
* `WEBAPP_URL` (optional, e.g., `https://admin.yourdomain.com` — enables the Telegram Mini App dashboard button inside `/admin`)

#### 4. Launch Bot Container
Run the bot daemon in the background:
```bash
docker-compose up -d --build
```
Your database will persist inside the `./data` directory on the VPS automatically. The backend API for the WebApp will be exposed on port `8080`.

#### 5. Cloudflare Tunnel / WebApp Setup
If you want to use the Mini App Dashboard, you must serve the app over HTTPS. The easiest way is using a free Cloudflare Tunnel:
1. Point your free domain (e.g., `admin.yourdomain.com`) to `http://localhost:8080` using `cloudflared`.
2. Add `WEBAPP_URL=https://admin.yourdomain.com` to your `.env` file.
3. Restart the bot (`docker-compose down && docker-compose up -d`).
4. Now, typing `/admin` will show a shiny "🚀 Open Admin Dashboard" button!

#### 5. Verify Logs
```bash
docker-compose logs -f --tail=50
```

#### Troubleshooting Updates (`KeyError: ContainerConfig`)
If you are using the older `docker-compose` (hyphenated) and encounter a `KeyError: ContainerConfig` crash when deploying new code, it is due to an incompatibility with modern Docker engines. To bypass it:
```bash
# Clean the old container state first
docker-compose down
# Rebuild and run fresh
docker-compose up -d --build
```

---

### Option B: The Manual Systemd Method

If you prefer deploying directly inside the host system using Python and Systemd:

#### 1. Setup Environment on Server
Install system runtime dependencies:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv sqlite3 git -y
```

#### 2. Create Isolated Path & Copy Code
```bash
mkdir -p /opt/lati_gemini_bot
cd /opt/lati_gemini_bot
# (Copy project files here)
```

#### 3. Configure the Virtual Environment
Create and activate the virtual environment, then install Python requirements:
```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

#### 4. Configuration Secrets Setup
Create the production environment file:
```bash
cp .env.example .env
nano .env
```

#### 5. Create Systemd Service File
```bash
sudo nano /etc/systemd/system/gemini-bot.service
```
Paste the following service configuration:
```ini
[Unit]
Description=Lati Gemini Telegram Bot Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/lati_gemini_bot
ExecStart=/opt/lati_gemini_bot/venv/bin/python /opt/lati_gemini_bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
Save and exit (`Ctrl + O`, `Enter`, `Ctrl + X`).

#### 6. Fire Up the Daemon
```bash
sudo systemctl daemon-reload
sudo systemctl enable gemini-bot.service
sudo systemctl start gemini-bot.service
```

#### 7. Verify Logs
```bash
sudo journalctl -u gemini-bot.service -f -n 50
```

---

## 💬 General Commands

These commands can be used by anyone in a group chat where the bot is active:

| Command | Action / Parameter Description |
| :--- | :--- |
| `/start` | Cheeky entry point command greeting |
| `/tldr` | Summarizes the drama and main topics of the recent chat history (up to 150 messages) in Persian slang |

---

## 🛠️ Admin Commands

Administrators defined in the `.env` configuration can execute parameters inside the bot's private message screen:

| Command | Action / Parameter Description |
| :--- | :--- |
| `/admin` | Displays configuration dashboard with available dynamic options |
| `/admin set_model <model_id>` | Changes Gemini model ID (e.g. `gemini-2.5-flash`) |
| `/admin set_limit <number>` | Configures historical context window limit |
| `/admin set_timeout <float>` | Timeout threshold for AI responses in seconds |
| `/admin set_chance <float>` | Random unprovoked roast probability (0.0 to 1.0) |
| `/admin set_instruction <text>` | Overwrites the system persona/prompt |
| `/admin add_special <username/name> <instruction>` | Creates or updates a special user/VIP with a custom system prompt override. For account names containing spaces, wrap them in quotes (e.g. `add_special "John Doe" instruction`). |
| `/admin remove_special <username/name>` | Removes a special user's custom override (supports quotes for names with spaces). |
| `/admin list_special` | Displays all registered special users |
| `/admin stats` | Outputs total messages processed, unique chat IDs, and SQLite database file size |
| `/admin broadcast <text>` | Instantly broadcasts a message to every active chat saved in the database |

---

## ⚠️ Disclaimer

This bot is designed purely for entertainment and humorous banter among friends in private or controlled group settings. The AI is explicitly instructed to generate sarcastic, provocative, and insulting responses ("roasts"). 

**The creator of this project is NOT responsible for any offense, emotional distress, or conflicts caused by the bot's outputs.** Please ensure all members of your group chat are comfortable with this type of humor before adding the bot. Do not use this bot inappropriately, maliciously, or for targeted harassment.
