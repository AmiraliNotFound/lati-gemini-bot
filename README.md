# 🤖 Lati Gemini Telegram Bot

A production-grade, highly customizable, and containerized Telegram Bot powered by the modern **Google GenAI SDK** (Gemini) featuring an asynchronous SQLite database history pipeline, dynamic failover model queues, premium Text-to-Speech (TTS) engines, and a rich moderation web app dashboard.

The bot is calibrated out-of-the-box with a witty, teasing, and sarcastic Persian persona (Tehrani slang). It supports multimodal processing (images/audio notes) and features automated high-speed media downloader links.

---

## ✨ Key Features

### 1. 🧠 Resilient AI Generation & Failover Stack
- **Context & Pruning**: Utilizes `aiosqlite` to log message histories and prunes them dynamically based on the configured context limits to keep response latency low.
- **Group Summary (TL;DR)**: Type `/tldr` in any group to get a snappy, sarcastic Persian summary of the last 150 messages of drama.
- **Configurable Fallback Models**: Specify your primary `MODEL_ID` (e.g. `gemini-2.5-flash`) and a comma-separated queue of `FALLBACK_MODELS` (e.g. `gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it`). If the primary model fails or gets rate-limited, the bot automatically steps down the queue to ensure uninterrupted service.
- **Adaptive VIP Personas**: Configures custom system instructions for specific users. The bot adapts its roasting persona when interacting with registered VIPs.

### 2. 🎙️ Dual-Engine Text-to-Speech (TTS) & Failover
- **Microsoft Edge TTS (Free, Natural Persian)**: Generates highly optimized, natural-sounding Persian speech (`fa-IR-FaridNeural` or `fa-IR-DilaraNeural`) offline with zero token costs.
- **Google Gemini TTS (Premium Native Audio)**: Utilizes Google's generative audio models (e.g., `gemini-2.5-flash-preview-tts`, `gemini-3.1-flash-tts-preview`) to generate expressive, prompt-steerable spoken voice replies.
- **Raw PCM Audio Decoder**: Automatically detects Gemini's raw `audio/L16` (PCM 24kHz mono) format and configures `ffmpeg` dynamically to convert the raw headerless bytes into high-quality, Telegram-compatible OGG files.
- **Multi-Model Gemini TTS Loop**: Specify a comma-separated list of Gemini TTS models (e.g. `gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview`). If the primary TTS model is rate-limited or fails, the bot fails over to the next TTS model.
- **Configurable Edge Fallback**: If Gemini TTS fails entirely, the bot checks the `TTS_FALLBACK_TO_EDGE` setting. If enabled (`True`), it automatically falls back to Edge TTS to generate the audio, ensuring the user always receives a voice response.

### 3. 📥 High-Speed Video & Album Downloader with Stream Fallback
- **Inline Previews & Streaming**: Automatically detects Instagram, YouTube, and Twitter/X links in messages. Utilizes `yt-dlp` to download media, queries metadata via `ffprobe` (to extract width, height, and duration), and generates a thumbnail using `ffmpeg`. Re-uploads the file to Telegram enabling instant in-app streaming.
- **Instagram Carousel & Album Downloader**: Handles multiple-media Instagram posts (photos and videos) and sends them to Telegram in a single Media Group (album format) with rich metadata, including the uploader's username, post caption, and direct link.
- **Telegram 50MB Bypass**: Telegram Bots are restricted to a **50 MB** maximum file upload limit. If a downloaded video exceeds 50 MB (or if transmission fails), the bot retrieves Cobalt's high-speed CDN direct stream download link and replies to the user with the direct link.
- **Instagram Scraper Bypass & Impersonation**: If cookies are not configured, the bot automatically utilizes dynamic browser impersonation (e.g. Chrome, Safari on iOS, Firefox, Edge) to mimic real user TLS fingerprints and bypass Instagram's login blocks/CAPTCHAs. If needed, you can still place cookies inside `data/cookies.txt` as a manual override.

### 4. 📊 Admin WebApp Dashboard
A React-based GUI console built directly inside the Telegram interface featuring Dark Mode, glassmorphism design, and smooth slide drawers:
- **Stats Tab**: View real-time database sizing, active chat metrics, and the 10 most recent scraper/system errors.
- **Mod Tab**: View all active chats, toggle mute status, set custom roast chances, configure message cooldown windows, and override the Gemini model for specific chats (marked on the dashboard with a custom purple model badge). Includes consolidated VIP/Special User overrides.
- **Cast Tab**: Instantly send broadcast messages to all group chats and users stored in the database.
- **Conf Tab**: Dynamically configure context windows, API timeouts, system persona prompts, model fallback queues, and TTS engine settings.

---

## 📂 Project Structure

```text
lati_gemini_bot/
├── src/
│   ├── __init__.py      # Package initializer
│   ├── config.py        # Settings loader, environment validations, and logging setup
│   ├── database.py      # Asynchronous database connection, schema setup, stats, and operations
│   └── handlers.py      # Core Telegram handlers (Admin commands, Text/Multimodal messaging)
├── webapp/
│   ├── src/
│   │   ├── App.jsx      # React WebApp frontend layout and dynamic configuration bindings
│   │   └── main.jsx     # Frontend entry point
│   ├── index.html       # WebApp template
│   └── vite.config.js   # Vite builder configurations
├── main.py              # Main entry point to initialize and start bot polling
├── requirements.txt     # Python dependency definitions
├── Dockerfile           # Multi-stage optimized Docker build definition (React + Python)
├── docker-compose.yml   # Compose setup for Docker volume and env mounts
├── .env.example         # Template configuration file
└── README.md            # Comprehensive system documentation
```

---

## 🚀 Deployment Guide

### Option A: Optimized Docker Deployment (Recommended)

Docker isolates the runtime environment, handles the multi-stage React production builds, and installs system audio dependencies like `ffmpeg` and `ffprobe` out of the box.

#### 1. Setup Prerequisites
Ensure Docker and Docker Compose are installed:
```bash
sudo apt update && sudo apt install docker.io docker-compose -y
```

#### 2. Copy Code and Configure Settings
Clone the repository to `/opt/lati_gemini_bot` and configure the environment variables:
```bash
cd /opt/lati_gemini_bot
cp .env.example .env
nano .env
```
Fill in the following fields:
* `TELEGRAM_TOKEN`: Your bot API token (from [@BotFather](https://t.me/BotFather))
* `GEMINI_API_KEY`: Google Gemini API key (from [Google AI Studio](https://aistudio.google.com))
* `ALLOWED_ADMINS`: Comma-separated list of admin usernames allowed to access settings (e.g. `AmiraliNotFound,MyUser`)
* `WEBAPP_URL`: Optional (e.g., `https://admin.yourdomain.com`). Set this to enable the WebApp Mini App button.

#### 3. Start the Container
Start the containerized bot in the background:
```bash
docker-compose up -d --build
```
Your database and custom configurations will persist inside the local `./data` folder automatically. The Admin WebApp API will be exposed on port `8080`.

#### 4. Setup Secure HTTPS for WebApp (Cloudflare Tunnel)
Telegram requires all WebApp Mini Apps to be served over HTTPS. You can easily set up a free Cloudflare Tunnel:
1. Direct your domain (e.g. `admin.yourdomain.com`) to `http://localhost:8080` in your Cloudflare dashboard.
2. Add `WEBAPP_URL=https://admin.yourdomain.com` inside your `.env` file.
3. Restart the containers (`docker-compose down && docker-compose up -d`).
4. Type `/admin` in chat to reveal the "🚀 Open Admin Dashboard" button.

---

### Option B: Manual Host Deployment (Systemd)

#### 1. Install System Dependencies
Install system runtimes, Python, SQLite, and audio tools:
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv sqlite3 ffmpeg git -y
```

#### 2. Configure Virtual Environment & Install Requirements
```bash
cd /opt/lati_gemini_bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### 3. Build WebApp Client Assets
```bash
cd webapp
npm install
npm run build
cd ..
```

#### 4. Create Service Daemon
```bash
sudo nano /etc/systemd/system/gemini-bot.service
```
Paste the configuration:
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
Enable and start the service:
```bash
sudo systemctl daemon-reload
sudo systemctl enable gemini-bot.service
sudo systemctl start gemini-bot.service
```

---

## ⚙️ Configuration Properties

| Key | Default Value | Description |
| :--- | :--- | :--- |
| `MODEL_ID` | `gemini-2.5-flash` | The primary Google Gemini model ID for chat replies. |
| `FALLBACK_MODELS` | `gemini-2.5-flash-lite,gemini-2.5-flash,gemma-4-31b-it` | Comma-separated queue of models used if primary generation fails. |
| `CONTEXT_LIMIT` | `12` | Historical message window limit sent to the model. |
| `TIMEOUT` | `12.0` | API generation timeout threshold in seconds. |
| `TTS_ENGINE` | `edge` | Active TTS Engine (`edge` or `gemini`). |
| `TTS_GEMINI_MODEL` | `gemini-2.5-flash-preview-tts,gemini-3.1-flash-tts-preview` | Comma-separated list of Gemini models used for TTS. |
| `TTS_GEMINI_VOICE` | `Kore` | Gemini voice name (`Kore`, `Puck`, `Fenrir`, `Aoede`, `Charon`). |
| `TTS_EDGE_VOICE` | `fa-IR-FaridNeural` | Edge voice name (`fa-IR-FaridNeural`, `fa-IR-DilaraNeural`). |
| `TTS_FALLBACK_TO_EDGE`| `True` | Fallback to Edge TTS if all Gemini TTS models fail (`True` / `False`). |
| `RANDOM_ROAST_CHANCE` | `0.02` | Probability (0.0 to 1.0) that the bot roasts an unprovoked message. |
| `SYSTEM_INSTRUCTION` | *(Persian Persona)* | Custom persona settings / default prompt. |

---

## 💬 Command Reference

### User Commands
- `/start`: Cheeky greeting message.
- `/tldr`: Summarizes group chat topics and drama sarcasticly (Persian slang, up to 150 messages).

### Admin Inline Commands
- `/admin`: Displays configuration dashboard metrics.
- `/admin set_model <model_id>`: Changes primary Gemini model ID.
- `/admin set_limit <number>`: Sets conversation history context limit.
- `/admin set_timeout <float>`: Sets AI response generation timeouts.
- `/admin set_chance <float>`: Sets random unprovoked roast probability.
- `/admin set_instruction <prompt>`: Overwrites system persona prompts.
- `/admin add_special <name> <prompt>`: Assigns unique VIP roasting instructions.
- `/admin remove_special <name>`: Removes VIP instructions.
- `/admin list_special`: Lists all registered VIP accounts.
- `/admin stats`: Outputs database size and message counts.
- `/admin broadcast <text>`: Sends broadcast alerts to all active chats.

---

## ⚠️ Disclaimer

This bot is designed purely for entertainment and humorous banter among friends in private or controlled group settings. The AI is explicitly instructed to generate sarcastic, provocative, and insulting responses ("roasts"). 

**The creator of this project is NOT responsible for any offense, emotional distress, or conflicts caused by the bot's outputs.** Please ensure all members of your group chat are comfortable with this type of humor before adding the bot. Do not use this bot inappropriately, maliciously, or for targeted harassment.
