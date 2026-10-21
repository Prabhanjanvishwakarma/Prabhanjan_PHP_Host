import asyncio
import os
import sys
import logging
import subprocess
import re
import importlib.util
import psutil
import sqlite3
import hashlib
import json
import zipfile
import shutil
import signal
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import aiohttp
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('BOT_TOKEN')
OWNER_ID_STR = os.getenv('OWNER_ID')
ADMIN_ID_STR = os.getenv('ADMIN_ID')
YOUR_USERNAME = os.getenv('YOUR_USERNAME')
UPDATE_CHANNEL = os.getenv('UPDATE_CHANNEL')

if not TOKEN:
    logger.error("BOT_TOKEN not found in environment variables!")
    raise ValueError("BOT_TOKEN is required. Please set it in .env file or environment variables.")

if not OWNER_ID_STR or not ADMIN_ID_STR:
    logger.error("OWNER_ID or ADMIN_ID not found in environment variables!")
    raise ValueError("OWNER_ID and ADMIN_ID are required. Please set them in .env file.")

try:
    OWNER_ID = int(OWNER_ID_STR)
    ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    logger.error("OWNER_ID or ADMIN_ID must be valid integers!")
    raise

YOUR_USERNAME = '@prabhanjanexploits'
UPDATE_CHANNEL = 'https://t.me/prabhanjan12345'
YOUTUBE_CHANNEL = 'https://youtube.com/@prabhanjanexploits'
CHANNEL_USERNAME = '@' + UPDATE_CHANNEL.rstrip('/').split('/')[-1]

BASE_DIR = Path(__file__).parent.absolute()
UPLOAD_BOTS_DIR = BASE_DIR / 'upload_bots'
IROTECH_DIR = BASE_DIR / 'inf'
DATABASE_PATH = IROTECH_DIR / 'bot_data.db'

FREE_USER_LIMIT = float('inf')
SUBSCRIBED_USER_LIMIT = float('inf')
ADMIN_LIMIT = 999
OWNER_LIMIT = float('inf')

UPLOAD_BOTS_DIR.mkdir(parents=True, exist_ok=True)
IROTECH_DIR.mkdir(exist_ok=True)

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())
BOT_START_TIME = datetime.now()

bot_scripts = {}
user_subscriptions = {}
user_files = {}
user_favorites = {}
banned_users = set()
active_users = set()
admin_ids = {ADMIN_ID, OWNER_ID}
bot_locked = False
bot_stats = {'total_uploads': 0, 'total_downloads': 0, 'total_runs': 0}

# PHP + ngrok project runtime (tokens are kept in memory only, never written to DB/logs).
php_setup_state = {}
php_projects = {}
PHP_ASSET_EXTENSIONS = {'.php', '.css', '.html', '.htm', '.json', '.txt', '.js', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.webp', '.xml'}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

def migrate_db():
    logger.info("Running database migrations...")
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        
        c.execute("PRAGMA table_info(user_files)")
        columns = [row[1] for row in c.fetchall()]
        if 'upload_date' not in columns:
            logger.info("Adding upload_date column to user_files table...")
            c.execute('ALTER TABLE user_files ADD COLUMN upload_date TEXT')
            logger.info("upload_date column added successfully.")
        
        c.execute("PRAGMA table_info(active_users)")
        columns = [row[1] for row in c.fetchall()]
        if 'join_date' not in columns:
            logger.info("Adding join_date column to active_users table...")
            c.execute('ALTER TABLE active_users ADD COLUMN join_date TEXT')
            logger.info("join_date column added successfully.")
        if 'last_active' not in columns:
            logger.info("Adding last_active column to active_users table...")
            c.execute('ALTER TABLE active_users ADD COLUMN last_active TEXT')
            logger.info("last_active column added successfully.")
        
        conn.commit()
        conn.close()
        logger.info("Database migrations completed successfully.")
    except Exception as e:
        logger.error(f"Database migration error: {e}", exc_info=True)

def init_db():
    logger.info(f"Initializing database at: {DATABASE_PATH}")
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions
                     (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files
                     (user_id INTEGER, file_name TEXT, file_type TEXT, upload_date TEXT,
                      PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users
                     (user_id INTEGER PRIMARY KEY, join_date TEXT, last_active TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins
                     (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS banned_users
                     (user_id INTEGER PRIMARY KEY, banned_date TEXT, reason TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS favorites
                     (user_id INTEGER, file_name TEXT, PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS bot_stats
                     (stat_name TEXT PRIMARY KEY, stat_value INTEGER)''')
        
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        
        for stat in ['total_uploads', 'total_downloads', 'total_runs']:
            c.execute('INSERT OR IGNORE INTO bot_stats (stat_name, stat_value) VALUES (?, 0)', (stat,))
        
        conn.commit()
        conn.close()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Database initialization error: {e}", exc_info=True)

def load_data():
    logger.info("Loading data from database...")
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except ValueError:
                logger.warning(f"Invalid expiry date for user {user_id}")
        
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))
        
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())
        
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())
        
        c.execute('SELECT user_id FROM banned_users')
        banned_users.update(user_id for (user_id,) in c.fetchall())
        
        c.execute('SELECT user_id, file_name FROM favorites')
        for user_id, file_name in c.fetchall():
            if user_id not in user_favorites:
                user_favorites[user_id] = []
            user_favorites[user_id].append(file_name)
        
        c.execute('SELECT stat_name, stat_value FROM bot_stats')
        for stat_name, stat_value in c.fetchall():
            bot_stats[stat_name] = stat_value
        
        conn.close()
        logger.info(f"Data loaded: {len(active_users)} users, {len(banned_users)} banned, {len(admin_ids)} admins.")
    except Exception as e:
        logger.error(f"Error loading data: {e}", exc_info=True)

init_db()
migrate_db()
load_data()

# ── AUTO DEPENDENCY INSTALLER ────────────────────────
# Import name → actual pip package name (jab dono alag hote hain)
IMPORT_TO_PIP = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'pyrogram': 'pyrogram',
    'tgcrypto': 'TgCrypto',
    'PIL': 'Pillow',
    'cv2': 'opencv-python-headless',
    'bs4': 'beautifulsoup4',
    'yaml': 'PyYAML',
    'dotenv': 'python-dotenv',
    'Crypto': 'pycryptodome',
    'Cryptodome': 'pycryptodomex',
    'jwt': 'PyJWT',
    'dateutil': 'python-dateutil',
    'discord': 'discord.py',
    'yt_dlp': 'yt-dlp',
    'youtube_dl': 'youtube-dl',
    'sklearn': 'scikit-learn',
    'websocket': 'websocket-client',
    'serial': 'pyserial',
}

# Python built-in modules — inhe kabhi install karne ki koshish nahi karni
STDLIB_MODULES = set(sys.stdlib_module_names) if hasattr(sys, 'stdlib_module_names') else {
    'os', 'sys', 'time', 'json', 're', 'math', 'random', 'datetime', 'collections',
    'itertools', 'functools', 'subprocess', 'threading', 'asyncio', 'socket', 'io',
    'sqlite3', 'hashlib', 'base64', 'logging', 'pathlib', 'shutil', 'zipfile',
    'urllib', 'http', 'email', 'csv', 'string', 'copy', 'typing', 'traceback',
    'signal', 'struct', 'queue', 'multiprocessing', 'abc', 'enum', 'uuid', 'glob'
}

def extract_imports(file_path):
    """File ke andar se saare top-level 'import X' / 'from X import Y' nikalta hai"""
    modules = set()
    try:
        content = Path(file_path).read_text(errors='ignore')
        for match in re.finditer(r'^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)', content, re.MULTILINE):
            top_module = match.group(1).split('.')[0]
            modules.add(top_module)
    except Exception as e:
        logger.error(f"Import extraction error: {e}")
    return modules

def install_missing_dependencies(file_path, timeout=90):
    """Script mein use ho rahi libraries check karke jo missing hain unhe pip se install karta hai.
    Returns: (installed_list, failed_list)"""
    modules = extract_imports(file_path)
    installed, failed = [], []

    for mod in modules:
        if mod in STDLIB_MODULES or mod in ('__future__',):
            continue
        if importlib.util.find_spec(mod) is not None:
            continue  # already installed

        pip_name = IMPORT_TO_PIP.get(mod, mod)
        try:
            result = subprocess.run(
                [sys.executable, '-m', 'pip', 'install', pip_name, '--break-system-packages', '--quiet'],
                capture_output=True, text=True, timeout=timeout
            )
            if result.returncode == 0:
                installed.append(pip_name)
            else:
                failed.append(pip_name)
        except Exception as e:
            logger.error(f"pip install error for {pip_name}: {e}")
            failed.append(pip_name)

    return installed, failed

def get_user_file_limit(user_id):
    return OWNER_LIMIT  # Sabke liye unlimited — koi upload limit nahi

def format_limit(limit):
    return "Unlimited ♾️" if limit == float('inf') else f"{limit} files"

def get_main_keyboard(user_id):
    if user_id in admin_ids:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Updates", url=UPDATE_CHANNEL)],
            [InlineKeyboardButton(text="📤 Upload File", callback_data="upload_file"),
             InlineKeyboardButton(text="📁 My Files", callback_data="check_files")],
            [InlineKeyboardButton(text="🔍 Search Files", callback_data="search_files"),
             InlineKeyboardButton(text="⚡ Bot Speed", callback_data="bot_speed")],
            [InlineKeyboardButton(text="📊 My Stats", callback_data="statistics"),
             InlineKeyboardButton(text="👑 Admin Panel", callback_data="admin_panel")],
            [InlineKeyboardButton(text="🐘 PHP + Ngrok Host", callback_data="php_host")],
            [InlineKeyboardButton(text="💬 Contact", url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}")]
        ])
    else:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📢 Updates Channel", url=UPDATE_CHANNEL)],
            [InlineKeyboardButton(text="📤 Upload File", callback_data="upload_file"),
             InlineKeyboardButton(text="📁 My Files", callback_data="check_files")],
            [InlineKeyboardButton(text="🔍 Search Files", callback_data="search_files"),
             InlineKeyboardButton(text="⚡ Bot Speed", callback_data="bot_speed")],
            [InlineKeyboardButton(text="📊 My Stats", callback_data="statistics")],
            [InlineKeyboardButton(text="💬 Contact Owner", url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}")]
        ])
    return keyboard

def get_admin_panel_keyboard():
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏱️ Bot Uptime", callback_data="admin_bot_uptime")],
        [InlineKeyboardButton(text="👥 Total Users", callback_data="admin_total_users_simple")],
        [InlineKeyboardButton(text="📝 Logs", callback_data="admin_view_logs")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    return keyboard

def build_welcome_text(user_id: int, full_name: str) -> str:
    return f"""╔═══════════════════════╗
    🚀 <b>PRABHANJAN HOST BOT</b> 🚀
    ⚡ <i>Script Runner Bot</i>
╚═══════════════════════╝

👋 <b>Welcome, {full_name}!</b>

🆔 <b>Your ID:</b> <code>{user_id}</code>
📦 <b>Upload Limit:</b> Unlimited ♾️

━━━━━━━━━━━━━━━━━━━━
<b>🔥 AVAILABLE FEATURES</b>

📤 <b>Upload Files</b> — Python, JS, PHP, ZIP
🌐 <b>PHP + Ngrok Host</b> — Multi-file PHP projects
📁 <b>Manage Files</b> — View, delete, organize
🔍 <b>Search Files</b> — Find by name/type
▶️ <b>Run Scripts</b> — Execute Python/Node.js
🛑 <b>Stop Scripts</b> — Force stop running
📊 <b>View Stats</b> — Usage & storage
⚡ <b>Speed Test</b> — Bot response time
💾 <b>File Info</b> — Size, type, date
ℹ️ <b>Help &amp; Support</b> — Get assistance

━━━━━━━━━━━━━━━━━━━━
📢 <b>Updates:</b> {UPDATE_CHANNEL}
🎥 <b>YouTube:</b> {YOUTUBE_CHANNEL}

⚡ <b>Developed by</b> Prabhanan Exploits
🔒 <b>Version:</b> 2.0"""

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in banned_users:
        await message.answer("🚫 <b>You are banned from using this bot!</b>\n\nContact admin for more info.", parse_mode="HTML")
        return
    
    active_users.add(user_id)
    
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('INSERT OR IGNORE INTO active_users (user_id, join_date, last_active) VALUES (?, ?, ?)', 
                  (user_id, now, now))
        c.execute('UPDATE active_users SET last_active = ? WHERE user_id = ?', (now, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error saving active user: {e}")
    
    welcome_text = build_welcome_text(user_id, message.from_user.full_name)
    await message.answer(welcome_text, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")

@dp.callback_query(F.data == "back_to_main")
async def callback_back_to_main(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    welcome_text = f"""
╔═══════════════════════╗
    🏠 <b>MAIN MENU</b> 🏠
╚═══════════════════════╝

👤 <b>User:</b> {callback.from_user.full_name}
🆔 <b>ID:</b> <code>{user_id}</code>
📦 <b>Files:</b> {len(user_files.get(user_id, []))} (Unlimited ♾️)

Use buttons below to navigate 👇
"""
    await callback.message.edit_text(welcome_text, reply_markup=get_main_keyboard(user_id), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "upload_file")
async def callback_upload_file(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if bot_locked and user_id not in admin_ids:
        await callback.answer("🔒 Bot is locked for maintenance!", show_alert=True)
        return
    
    current_files = len(user_files.get(user_id, []))
    limit = get_user_file_limit(user_id)
    
    upload_text = f"""
╔═══════════════════════╗
    📤 <b>UPLOAD FILES</b> 📤
╚═══════════════════════╝

📊 <b>Current Usage:</b> {current_files} files (Unlimited ♾️)

📝 <b>Supported Formats:</b>
🐍 Python (.py)
🟨 JavaScript (.js)
🐘 PHP (.php) + web assets
📦 ZIP Archives (.zip)

━━━━━━━━━━━━━━━━━━━━
<b>💡 How to Upload:</b>

1️⃣ Send your file to the bot
2️⃣ Wait for upload confirmation
3️⃣ File will be saved automatically

⚡ <b>Upload limit:</b> {limit} files
🔥 <b>Quick & Easy!</b>
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(upload_text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "check_files")
async def callback_check_files(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    files = user_files.get(user_id, [])
    
    if not files:
        text = """
╔═══════════════════════╗
    📁 <b>MY FILES</b> 📁
╚═══════════════════════╝

📭 <b>No files found!</b>

Upload your first file to get started! 🚀
"""
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📤 Upload File", callback_data="upload_file")],
            [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
        ])
    else:
        text = f"""
╔═══════════════════════╗
    📁 <b>MY FILES ({len(files)})</b> 📁
╚═══════════════════════╝

"""
        buttons = []
        for i, (file_name, file_type) in enumerate(files, 1):
            icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📦"
            text += f"{i}. {icon} <code>{file_name}</code>\n"
            
            is_favorite = file_name in user_favorites.get(user_id, [])
            star = "⭐" if is_favorite else "☆"
            
            buttons.append([
                InlineKeyboardButton(text=f"▶️ Run {file_name[:15]}", callback_data=f"run_script:{file_name}"),
                InlineKeyboardButton(text=f"{star}", callback_data=f"toggle_fav:{file_name}")
            ])
            buttons.append([
                InlineKeyboardButton(text=f"ℹ️ Info {file_name[:15]}", callback_data=f"file_info:{file_name}"),
                InlineKeyboardButton(text=f"🗑️ Delete", callback_data=f"delete_file:{file_name}")
            ])
        
        buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")])
        back_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "my_favorites")
async def callback_my_favorites(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    favorites = user_favorites.get(user_id, [])
    
    if not favorites:
        text = """
╔═══════════════════════╗
    ⭐ <b>FAVORITES</b> ⭐
╚═══════════════════════╝

💭 No favorite files yet!

Add files to favorites for quick access! 🚀
"""
        buttons = [[InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]]
    else:
        text = f"""
╔═══════════════════════╗
    ⭐ <b>FAVORITES ({len(favorites)})</b> ⭐
╚═══════════════════════╝

"""
        buttons = []
        for i, file_name in enumerate(favorites, 1):
            text += f"{i}. ⭐ <code>{file_name}</code>\n"
            buttons.append([
                InlineKeyboardButton(text=f"▶️ {file_name[:20]}", callback_data=f"run_script:{file_name}"),
                InlineKeyboardButton(text=f"❌", callback_data=f"toggle_fav:{file_name}")
            ])
        
        buttons.append([InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")])
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "search_files")
async def callback_search_files(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    files = user_files.get(user_id, [])
    
    text = f"""
╔═══════════════════════╗
    🔍 <b>SEARCH FILES</b> 🔍
╚═══════════════════════╝

📊 <b>Total Files:</b> {len(files)}

<b>File Types:</b>
🐍 Python: {sum(1 for f in files if f[1] == 'py')}
🟨 JavaScript: {sum(1 for f in files if f[1] == 'js')}
📦 ZIP: {sum(1 for f in files if f[1] == 'zip')}

━━━━━━━━━━━━━━━━━━━━
To search, use:
<code>/search filename</code>
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📁 View All Files", callback_data="check_files")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "bot_speed")
async def callback_bot_speed(callback: types.CallbackQuery):
    start_time = datetime.now()
    await callback.answer("⚡ Testing...")
    end_time = datetime.now()
    speed = (end_time - start_time).total_seconds() * 1000
    
    if speed < 100:
        status = "🟢 Excellent"
        emoji = "🚀"
    elif speed < 300:
        status = "🟡 Good"
        emoji = "⚡"
    else:
        status = "🔴 Slow"
        emoji = "🐌"
    
    text = f"""
╔═══════════════════════╗
    ⚡ <b>SPEED TEST</b> ⚡
╚═══════════════════════╝

{emoji} <b>Response Time:</b> {speed:.2f}ms
📊 <b>Status:</b> {status}

🖥️ <b>Server Info:</b>
• CPU: {psutil.cpu_percent()}%
• Memory: {psutil.virtual_memory().percent}%
• Uptime: Online ✅

✨ Bot is running smoothly!
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Test Again", callback_data="bot_speed"),
         InlineKeyboardButton(text="🏠 Home", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")

@dp.callback_query(F.data == "statistics")
async def callback_statistics(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    user_file_count = len(user_files.get(user_id, []))
    user_fav_count = len(user_favorites.get(user_id, []))
    limit = get_user_file_limit(user_id)
    is_premium = user_id in user_subscriptions
    
    text = f"""
╔═══════════════════════╗
    📊 <b>YOUR STATISTICS</b> 📊
╚═══════════════════════╝

👤 <b>User:</b> {callback.from_user.full_name}
🆔 <b>ID:</b> <code>{user_id}</code>

━━━━━━━━━━━━━━━━━━━━
📦 <b>FILE STATISTICS:</b>

📁 Total Files: {user_file_count} (Unlimited ♾️)
⭐ Favorites: {user_fav_count}
💎 Account: Free 🆓 (Unlimited ♾️)
🚀 Running: {sum(1 for k in bot_scripts if k.startswith(f"{user_id}_"))}

━━━━━━━━━━━━━━━━━━━━
📈 <b>USAGE:</b>

📤 Uploads: {bot_stats.get('total_uploads', 0)}
📥 Downloads: {bot_stats.get('total_downloads', 0)}
▶️ Script Runs: {bot_stats.get('total_runs', 0)}

{'✅ Bot Status: Active' if not bot_locked else '🔒 Bot: Maintenance'}
"""
    
    if user_id in admin_ids:
        text += f"\n━━━━━━━━━━━━━━━━━━━━\n👑 <b>ADMIN STATS:</b>\n"
        text += f"👥 Total Users: {len(active_users)}\n"
        text += f"📁 Total Files: {sum(len(files) for files in user_files.values())}\n"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "help_info")
async def callback_help_info(callback: types.CallbackQuery):
    text = """
╔═══════════════════════╗
    ℹ️ <b>HELP & INFO</b> ℹ️
╚═══════════════════════╝

<b>🎯 HOW TO USE:</b>

1️⃣ <b>Upload Files:</b>
   • Click 'Upload File'
   • Send your .py, .js, or .zip file
   • File will be saved automatically

2️⃣ <b>Run Scripts:</b>
   • Go to 'My Files'
   • Click 'Run' on any file
   • Monitor script execution

3️⃣ <b>Manage Files:</b>
   • View all files in 'My Files'
   • Add to favorites with ⭐
   • Delete unwanted files

4️⃣ <b>Search:</b>
   • Use /search [filename]
   • Quick file lookup

━━━━━━━━━━━━━━━━━━━━
<b>💡 COMMANDS:</b>

/start - Start the bot
/help - Show this help
/search - Search files
/stats - Your statistics
/premium - Free info

<b>Need help? Contact owner! 💬</b>
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Features", callback_data="all_features")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "all_features")
async def callback_all_features(callback: types.CallbackQuery):
    text = """
╔═══════════════════════╗
    🎯 <b>ALL FEATURES</b> 🎯
╚═══════════════════════╝

<b>✨ FREE USER FEATURES (12+):</b>

1. 📤 Upload Files (Python, JS, ZIP)
2. 📁 View & Manage Files
3. ⭐ Add to Favorites
4. 🔍 Search Files by Name
5. ▶️ Run Python Scripts
6. ▶️ Run JavaScript Scripts
7. 🛑 Stop Running Scripts
8. 📊 View Your Statistics
9. ⚡ Bot Speed Test
10. 📥 Download Your Files
11. 💾 View File Information
12. ℹ️ Help & Support
13. 🎯 Feature Discovery

━━━━━━━━━━━━━━━━━━━━
<b>🆓 SAB KUCH FREE HAI!</b>

• Unlimited file uploads
• Koi premium plan nahi
• Sabke liye same features
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "get_premium")
async def callback_get_premium(callback: types.CallbackQuery):
    text = """
╔═══════════════════════╗
    🆓 <b>100% FREE</b> 🆓
╚═══════════════════════╝

Is bot mein <b>koi premium plan nahi</b> hai.
✅ Unlimited uploads
✅ Saare features sabke liye free

Help chahiye? Owner se contact karo 💬
"""
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💬 Contact Owner", url=f"https://t.me/{YOUR_USERNAME.replace('@', '')}")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_panel")
async def callback_admin_panel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in admin_ids:
        await callback.answer("❌ Admin access required!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    👑 <b>ADMIN PANEL</b> 👑
╚═══════════════════════╝

Select an option below 👇
"""
    
    await callback.message.edit_text(text, reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_bot_uptime")
async def callback_admin_bot_uptime(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer("❌ Admin access required!", show_alert=True)
        return

    delta = datetime.now() - BOT_START_TIME
    days = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(rem, 60)

    text = f"""╔═══════════════════════╗
    ⏱️ <b>BOT UPTIME</b> ⏱️
╚═══════════════════════╝

🟢 <b>Status:</b> Running
📅 <b>Started:</b> {BOT_START_TIME.strftime('%d %b %Y, %I:%M %p')}
⏳ <b>Uptime:</b> {days}d {hours}h {minutes}m {seconds}s"""

    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]
    ])
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_total_users_simple")
async def callback_admin_total_users_simple(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer("❌ Admin access required!", show_alert=True)
        return

    total_users = len(active_users)
    total_active_scripts = len(bot_scripts)

    text = f"""╔═══════════════════════╗
    👥 <b>TOTAL USERS</b> 👥
╚═══════════════════════╝

👤 <b>Total Users:</b> {total_users}
▶️ <b>Currently Running Scripts:</b> {total_active_scripts}"""

    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]
    ])
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data.startswith("toggle_fav:"))
async def callback_toggle_favorite(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    
    if user_id not in user_favorites:
        user_favorites[user_id] = []
    
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        
        if file_name in user_favorites[user_id]:
            user_favorites[user_id].remove(file_name)
            c.execute('DELETE FROM favorites WHERE user_id = ? AND file_name = ?', (user_id, file_name))
            await callback.answer("❌ Removed from favorites!", show_alert=True)
        else:
            user_favorites[user_id].append(file_name)
            c.execute('INSERT OR IGNORE INTO favorites (user_id, file_name) VALUES (?, ?)', (user_id, file_name))
            await callback.answer("⭐ Added to favorites!", show_alert=True)
        
        conn.commit()
        conn.close()
        
        await callback_check_files(callback)
        
    except Exception as e:
        logger.error(f"Error toggling favorite: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("file_info:"))
async def callback_file_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    file_path = user_folder / file_name
    
    if not file_path.exists():
        await callback.answer("❌ File not found!", show_alert=True)
        return
    
    file_size = file_path.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    file_ext = file_path.suffix
    modified_time = datetime.fromtimestamp(file_path.stat().st_mtime)
    
    is_favorite = file_name in user_favorites.get(user_id, [])
    
    text = f"""
╔═══════════════════════╗
    ℹ️ <b>FILE INFO</b> ℹ️
╚═══════════════════════╝

📄 <b>Name:</b> <code>{file_name}</code>

📦 <b>Type:</b> {file_ext.upper()} File
💾 <b>Size:</b> {file_size_mb:.2f} MB ({file_size} bytes)
📅 <b>Modified:</b> {modified_time.strftime('%Y-%m-%d %H:%M')}
⭐ <b>Favorite:</b> {'Yes ✨' if is_favorite else 'No'}

🔐 <b>MD5:</b> <code>{hashlib.md5(file_path.read_bytes()).hexdigest()[:16]}...</code>
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Run", callback_data=f"run_script:{file_name}"),
         InlineKeyboardButton(text="🗑️ Delete", callback_data=f"delete_file:{file_name}")],
        [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
         InlineKeyboardButton(text="🏠 Home", callback_data="back_to_main")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.message(F.document)
async def handle_document(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in banned_users:
        await message.answer("🚫 You are banned from using this bot!")
        return
    
    if bot_locked and user_id not in admin_ids:
        await message.answer("🔒 Bot is currently locked!")
        return
    
    document = message.document
    file_name = Path(document.file_name or 'upload.bin').name
    file_ext = Path(file_name).suffix.lower()
    if not file_name or file_name in {'.', '..'} or len(file_name) > 180:
        await message.answer('❌ Invalid file name.')
        return
    if file_ext not in ({'.py', '.js', '.php', '.zip'} | PHP_ASSET_EXTENSIONS):
        await message.answer('❌ Supported: .py, .js, .php, ZIP, and common PHP web assets (CSS/HTML/images/JSON/XML).')
        return
    if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
        await message.answer('❌ Maximum upload size is 20 MB per file.')
        return
    
    current_files = len(user_files.get(user_id, []))
    limit = get_user_file_limit(user_id)
    
    if current_files >= limit:
        await message.answer(f"❌ Upload limit reached! ({current_files}/{limit})")
        return
    
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    user_folder.mkdir(exist_ok=True)
    
    file_path = user_folder / file_name
    
    try:
        file_size_kb = document.file_size / 1024
        
        status_msg = await message.answer(
            f"📤 <b>Preparing upload...</b>\n\n"
            f"📄 File: <code>{file_name}</code>\n"
            f"💾 Size: {file_size_kb:.2f} KB\n\n"
            f"▓░░░░░░░░░ 0%",
            parse_mode="HTML"
        )
        
        await asyncio.sleep(0.3)
        await status_msg.edit_text(
            f"📥 <b>Downloading...</b>\n\n"
            f"📄 File: <code>{file_name}</code>\n"
            f"💾 Size: {file_size_kb:.2f} KB\n\n"
            f"▓▓▓░░░░░░░ 30%",
            parse_mode="HTML"
        )
        
        await bot.download(document, destination=file_path)
        if file_ext in PHP_ASSET_EXTENSIONS:
            php_dir = user_folder / 'php_project'
            php_dir.mkdir(parents=True, exist_ok=True)
            # Keep web-served files separate from bot logs, database and Python scripts.
            shutil.copy2(file_path, php_dir / file_name)
        
        await status_msg.edit_text(
            f"💾 <b>Saving to database...</b>\n\n"
            f"📄 File: <code>{file_name}</code>\n"
            f"💾 Size: {file_size_kb:.2f} KB\n\n"
            f"▓▓▓▓▓▓▓░░░ 70%",
            parse_mode="HTML"
        )
        
        if user_id not in user_files:
            user_files[user_id] = []
        
        user_files[user_id].append((file_name, file_ext[1:]))
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        now = datetime.now().isoformat()
        c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, upload_date) VALUES (?, ?, ?, ?)',
                  (user_id, file_name, file_ext[1:], now))
        c.execute('UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = ?', ('total_uploads',))
        conn.commit()
        conn.close()
        
        bot_stats['total_uploads'] = bot_stats.get('total_uploads', 0) + 1
        
        await status_msg.edit_text(
            f"✅ <b>Finalizing...</b>\n\n"
            f"📄 File: <code>{file_name}</code>\n"
            f"💾 Size: {file_size_kb:.2f} KB\n\n"
            f"▓▓▓▓▓▓▓▓▓▓ 100%",
            parse_mode="HTML"
        )
        
        await asyncio.sleep(0.5)
        
        if file_ext == '.zip':
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📦 Extract ZIP", callback_data=f"extract_zip:{file_name}"),
                 InlineKeyboardButton(text="⭐ Add Favorite", callback_data=f"toggle_fav:{file_name}")],
                [InlineKeyboardButton(text="ℹ️ File Info", callback_data=f"file_info:{file_name}"),
                 InlineKeyboardButton(text="🗑️ Delete", callback_data=f"delete_file:{file_name}")],
                [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
                 InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
            ])
        elif file_ext in PHP_ASSET_EXTENSIONS and file_ext != '.js':
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🌐 Host PHP Project", callback_data="php_host")],
                [InlineKeyboardButton(text="⭐ Add Favorite", callback_data=f"toggle_fav:{file_name}"),
                 InlineKeyboardButton(text="🗑️ Delete", callback_data=f"delete_file:{file_name}")],
                [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
                 InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
            ])
        else:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="▶️ Run Now", callback_data=f"run_script:{file_name}"),
                 InlineKeyboardButton(text="⭐ Add Favorite", callback_data=f"toggle_fav:{file_name}")],
                [InlineKeyboardButton(text="ℹ️ File Info", callback_data=f"file_info:{file_name}"),
                 InlineKeyboardButton(text="🗑️ Delete", callback_data=f"delete_file:{file_name}")],
                [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
                 InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
            ])
        
        await status_msg.edit_text(
            f"""
╔═══════════════════════╗
    ✅ <b>UPLOAD SUCCESS!</b> ✅
╚═══════════════════════╝

📄 <b>File:</b> <code>{file_name}</code>
📦 <b>Type:</b> {file_ext[1:].upper()}
💾 <b>Size:</b> {document.file_size / 1024:.2f} KB
📊 <b>Usage:</b> {current_files + 1} files (Unlimited ♾️)

🎉 File uploaded successfully!
""",
            reply_markup=keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error uploading file: {e}")
        await message.answer(f"❌ Upload failed: {str(e)}")

@dp.callback_query(F.data.startswith("run_script:"))
async def callback_run_script(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    file_path = user_folder / file_name
    
    if not file_path.exists():
        await callback.answer("❌ File not found!", show_alert=True)
        return
    
    script_key = f"{user_id}_{file_name}"
    
    if script_key in bot_scripts:
        await callback.answer("⚠️ Script is already running!", show_alert=True)
        return
    
    file_ext = file_path.suffix.lower()
    if file_ext in PHP_ASSET_EXTENSIONS and file_ext != '.js':
        await callback.answer('PHP files/assets ko run karne ke liye PHP + Ngrok Host button use karein.', show_alert=True)
        return
    
    try:
        log_file_path = user_folder / f"{file_path.stem}.log"
        log_file = open(log_file_path, 'w')
        
        if file_ext == '.py':
            status_msg = await callback.message.answer("📦 <i>Checking dependencies...</i>", parse_mode="HTML")
            installed, failed = install_missing_dependencies(file_path)
            if installed:
                log_file.write(f"[AUTO-INSTALLER] Installed: {', '.join(installed)}\n")
            if failed:
                log_file.write(f"[AUTO-INSTALLER] Failed to install: {', '.join(failed)}\n")
            log_file.flush()
            try:
                await status_msg.delete()
            except Exception:
                pass

            process = subprocess.Popen(
                [sys.executable, str(file_path)],
                cwd=str(user_folder),
                stdout=log_file,
                stderr=log_file
            )
        elif file_ext == '.js':
            process = subprocess.Popen(
                ['node', str(file_path)],
                cwd=str(user_folder),
                stdout=log_file,
                stderr=log_file
            )
        else:
            log_file.close()
            await callback.answer("❌ Cannot run this file type!", show_alert=True)
            return
        
        bot_scripts[script_key] = {
            'process': process,
            'file_name': file_name,
            'script_owner_id': user_id,
            'start_time': datetime.now(),
            'user_folder': str(user_folder),
            'type': file_ext[1:],
            'log_file': log_file
        }
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('UPDATE bot_stats SET stat_value = stat_value + 1 WHERE stat_name = ?', ('total_runs',))
        conn.commit()
        conn.close()
        bot_stats['total_runs'] = bot_stats.get('total_runs', 0) + 1
        
        try:
            await callback.answer(f"✅ Script started! (PID: {process.pid})", show_alert=True)
        except Exception:
            await callback.message.answer(f"✅ Script started! (PID: {process.pid})")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🛑 Stop Script", callback_data=f"stop_script:{script_key}")],
            [InlineKeyboardButton(text="📋 View Output", callback_data=f"view_log:{file_name}")],
            [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
             InlineKeyboardButton(text="🏠 Home", callback_data="back_to_main")]
        ])
        
        await callback.message.edit_reply_markup(reply_markup=keyboard)
        
    except Exception as e:
        logger.error(f"Error running script: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("view_log:"))
async def callback_view_log(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    file_stem = Path(file_name).stem
    log_path = user_folder / f"{file_stem}.log"

    if not log_path.exists():
        await callback.answer("❌ Koi log nahi mila. Pehle script run karo.", show_alert=True)
        return

    try:
        content = log_path.read_text(errors='replace').strip()
        script_key = f"{user_id}_{file_name}"
        running_note = "🟢 Status: Running" if script_key in bot_scripts else "🔴 Status: Stopped/Exited"

        if not content:
            content = "(Abhi tak koi output nahi — script chup-chaap chal rahi hai)"
        elif len(content) > 3500:
            content = "...(truncated)...\n" + content[-3500:]

        text = f"""╔═══════════════════════╗
    📋 <b>SCRIPT OUTPUT</b> 📋
╚═══════════════════════╝

📄 <b>File:</b> <code>{file_name}</code>
{running_note}

<pre>{content}</pre>"""

        back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"view_log:{file_name}")],
            [InlineKeyboardButton(text="📁 My Files", callback_data="check_files")]
        ])
        await callback.message.answer(text, reply_markup=back_keyboard, parse_mode="HTML")
        await callback.answer()
    except Exception as e:
        await callback.answer(f"❌ Log read error: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("stop_script:"))
async def callback_stop_script(callback: types.CallbackQuery):
    script_key = callback.data.split(":", 1)[1]
    
    if script_key not in bot_scripts:
        await callback.answer("❌ Script not found or already stopped!", show_alert=True)
        return
    
    try:
        script_info = bot_scripts[script_key]
        process = script_info['process']
        log_file = script_info.get('log_file')
        
        if log_file and not log_file.closed:
            log_file.close()
        
        parent = psutil.Process(process.pid)
        children = parent.children(recursive=True)
        
        for child in children:
            child.terminate()
        
        parent.terminate()
        
        del bot_scripts[script_key]
        
        await callback.answer("✅ Script stopped successfully!", show_alert=True)
        
        if callback.from_user.id in admin_ids:
            await callback.message.edit_text("🛑 Script stopped!", parse_mode="HTML")
        else:
            await callback_back_to_main(callback)
        
    except Exception as e:
        logger.error(f"Error stopping script: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("extract_zip:"))
async def callback_extract_zip(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    zip_path = user_folder / file_name
    
    if not zip_path.exists():
        await callback.answer("❌ ZIP file not found!", show_alert=True)
        return
    
    if not zipfile.is_zipfile(zip_path):
        await callback.answer("❌ Invalid ZIP file!", show_alert=True)
        return
    
    try:
        status_text = f"""
╔═══════════════════════╗
    📦 <b>EXTRACTING ZIP</b> 📦
╚═══════════════════════╝

📄 File: <code>{file_name}</code>
⏳ Status: <b>Extracting...</b>

Please wait...
"""
        await callback.message.edit_text(status_text, parse_mode="HTML")
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(user_folder)
            all_files = zip_ref.namelist()
        
        registered_files = []
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        now = datetime.now().isoformat()
        
        for extracted_file in all_files:
            if extracted_file.endswith('/'):
                continue
            
            file_path = Path(extracted_file)
            file_ext = file_path.suffix.lower()
            
            if file_ext in (['.py', '.js'] + list(PHP_ASSET_EXTENSIONS)):
                just_name = file_path.name
                
                # Agar file kisi subfolder mein extract hui hai, use root user_folder mein move karo
                # taaki baad mein run karte waqt file mil jaye (registration sirf naam track karta hai)
                extracted_full_path = user_folder / extracted_file
                target_path = user_folder / just_name
                if extracted_full_path.exists() and extracted_full_path != target_path:
                    if not target_path.exists():
                        extracted_full_path.rename(target_path)
                        just_name = target_path.name
                    else:
                        # Naam already exist karta hai to unique bana do (overwrite se bachne ke liye)
                        stem, suf = target_path.stem, target_path.suffix
                        counter = 1
                        new_target = user_folder / f"{stem}_{counter}{suf}"
                        while new_target.exists():
                            counter += 1
                            new_target = user_folder / f"{stem}_{counter}{suf}"
                        extracted_full_path.rename(new_target)
                        just_name = new_target.name
                
                if file_ext in PHP_ASSET_EXTENSIONS:
                    php_dir = user_folder / 'php_project'
                    php_dir.mkdir(parents=True, exist_ok=True)
                    staged_source = user_folder / just_name
                    if staged_source.exists():
                        shutil.copy2(staged_source, php_dir / just_name)

                if user_id not in user_files:
                    user_files[user_id] = []
                
                user_files[user_id].append((just_name, file_ext[1:]))
                
                c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type, upload_date) VALUES (?, ?, ?, ?)',
                          (user_id, just_name, file_ext[1:], now))
                
                registered_files.append(just_name)
        
        if user_id in user_files:
            user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
        
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        c.execute('DELETE FROM favorites WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()
        
        if zip_path.exists():
            zip_path.unlink()
        
        registered_text = "\n".join([f"  • <code>{f}</code>" for f in registered_files[:10]])
        if len(registered_files) > 10:
            registered_text += f"\n  ... and {len(registered_files) - 10} more files"
        elif len(registered_files) == 0:
            registered_text = "  <i>No .py or .js files found</i>"
        
        current_count = len(user_files.get(user_id, []))
        limit = get_user_file_limit(user_id)
        
        success_text = f"""
╔═══════════════════════╗
    ✅ <b>EXTRACTION SUCCESS!</b> ✅
╚═══════════════════════╝

📄 <b>ZIP File:</b> <code>{file_name}</code>
📊 <b>Total Extracted:</b> {len(all_files)} files
✅ <b>Registered:</b> {len(registered_files)} files (.py, .js)
🗑️ <b>ZIP Deleted:</b> Automatically

<b>📋 Registered Files:</b>
{registered_text}

📦 <b>Your Files:</b> {current_count} (Unlimited ♾️)

✨ Extraction completed successfully!
"""
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📁 My Files", callback_data="check_files"),
             InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
        ])
        
        await callback.message.edit_text(success_text, reply_markup=keyboard, parse_mode="HTML")
        await callback.answer("✅ ZIP extracted & registered!")
        
    except zipfile.BadZipFile:
        await callback.answer("❌ Corrupted ZIP file!", show_alert=True)
    except Exception as e:
        logger.error(f"Error extracting ZIP: {e}")
        await callback.answer(f"❌ Extraction failed: {str(e)}", show_alert=True)

@dp.callback_query(F.data.startswith("delete_file:"))
async def callback_delete_file(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    file_name = callback.data.split(":", 1)[1]
    
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    file_path = user_folder / file_name
    
    try:
        if file_path.exists():
            file_path.unlink()
        
        if user_id in user_files:
            user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
        
        if file_name in user_favorites.get(user_id, []):
            user_favorites[user_id].remove(file_name)
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        c.execute('DELETE FROM favorites WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()
        
        await callback.answer("✅ File deleted successfully!", show_alert=True)
        await callback_check_files(callback)
        
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        await callback.answer(f"❌ Error: {str(e)}", show_alert=True)

@dp.callback_query(F.data == "admin_total_users")
async def callback_admin_total_users(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    user_list = "\n".join([f"• <code>{uid}</code>" for uid in list(active_users)[:15]])
    text = f"""
╔═══════════════════════╗
    👥 <b>USER STATISTICS</b> 👥
╚═══════════════════════╝

📊 <b>Total Users:</b> {len(active_users)}
🚫 <b>Banned:</b> {len(banned_users)}
✅ <b>Active:</b> {len(active_users) - len(banned_users)}

<b>📝 Recent Users (15):</b>
{user_list}

{'...' if len(active_users) > 15 else ''}
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_total_files")
async def callback_admin_total_files(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    total_files = sum(len(files) for files in user_files.values())
    py_files = sum(1 for files in user_files.values() for f in files if f[1] == 'py')
    js_files = sum(1 for files in user_files.values() for f in files if f[1] == 'js')
    zip_files = sum(1 for files in user_files.values() for f in files if f[1] == 'zip')
    
    text = f"""
╔═══════════════════════╗
    📁 <b>FILE STATISTICS</b> 📁
╚═══════════════════════╝

📊 <b>Total Files:</b> {total_files}

<b>📦 By Type:</b>
🐍 Python: {py_files}
🟨 JavaScript: {js_files}
📦 ZIP: {zip_files}

<b>📈 Top Users:</b>
"""
    
    top_users = sorted(user_files.items(), key=lambda x: len(x[1]), reverse=True)[:5]
    for user_id, files in top_users:
        text += f"• User <code>{user_id}</code>: {len(files)} files\n"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_running_scripts")
async def callback_admin_running_scripts(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    if not bot_scripts:
        text = """
╔═══════════════════════╗
    🚀 <b>RUNNING SCRIPTS</b> 🚀
╚═══════════════════════╝

💤 No scripts running currently
"""
        buttons = []
    else:
        text = f"""
╔═══════════════════════╗
    🚀 <b>RUNNING ({len(bot_scripts)})</b> 🚀
╚═══════════════════════╝

"""
        buttons = []
        for script_key, info in bot_scripts.items():
            runtime = (datetime.now() - info['start_time']).total_seconds()
            text += f"🔸 <code>{info['file_name']}</code>\n"
            text += f"   PID: {info['process'].pid} | User: {info['script_owner_id']}\n"
            text += f"   Runtime: {int(runtime)}s\n\n"
            buttons.append([InlineKeyboardButton(
                text=f"🛑 Stop {info['file_name'][:15]}", 
                callback_data=f"stop_script:{script_key}"
            )])
    
    buttons.append([InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")])
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_premium_users")
async def callback_admin_premium_users(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    premium_users = [(u, data) for u, data in user_subscriptions.items() if data['expiry'] > datetime.now()]
    
    if not premium_users:
        text = """
╔═══════════════════════╗
    💎 <b>PREMIUM USERS</b> 💎
╚═══════════════════════╝

No active premium subscriptions.
"""
    else:
        text = f"""
╔═══════════════════════╗
    💎 <b>PREMIUM ({len(premium_users)})</b> 💎
╚═══════════════════════╝

"""
        for user_id, data in premium_users:
            expiry_date = data['expiry'].strftime('%Y-%m-%d')
            text += f"💎 User <code>{user_id}</code>\n   Expires: {expiry_date}\n\n"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Premium", callback_data="add_premium")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_analytics")
async def callback_admin_analytics(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = f"""
╔═══════════════════════╗
    📊 <b>BOT ANALYTICS</b> 📊
╚═══════════════════════╝

<b>📈 GLOBAL STATS:</b>

📤 Total Uploads: {bot_stats.get('total_uploads', 0)}
📥 Total Downloads: {bot_stats.get('total_downloads', 0)}
▶️ Script Runs: {bot_stats.get('total_runs', 0)}
👥 Total Users: {len(active_users)}
📁 Total Files: {sum(len(files) for files in user_files.values())}
🚀 Running Now: {len(bot_scripts)}
⭐ Total Favorites: {sum(len(favs) for favs in user_favorites.values())}

<b>💎 PREMIUM:</b>
Active: {len([u for u in user_subscriptions if user_subscriptions[u]['expiry'] > datetime.now()])}
Expired: {len([u for u in user_subscriptions if user_subscriptions[u]['expiry'] <= datetime.now()])}

<b>🛡️ SECURITY:</b>
Banned Users: {len(banned_users)}
Admins: {len(admin_ids)}
Bot Status: {'🔒 Locked' if bot_locked else '✅ Active'}
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_system_status")
async def callback_admin_system_status(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    cpu = psutil.cpu_percent(interval=1)
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    
    text = f"""
╔═══════════════════════╗
    ⚙️ <b>SYSTEM STATUS</b> ⚙️
╚═══════════════════════╝

<b>💻 CPU:</b>
Usage: {cpu}%
{'🟢 Normal' if cpu < 70 else '🟡 High' if cpu < 90 else '🔴 Critical'}

<b>🧠 MEMORY:</b>
Used: {memory.percent}%
Free: {memory.available / (1024**3):.1f} GB
Total: {memory.total / (1024**3):.1f} GB

<b>💾 DISK:</b>
Used: {disk.percent}%
Free: {disk.free / (1024**3):.1f} GB
Total: {disk.total / (1024**3):.1f} GB

<b>🤖 BOT STATUS:</b>
Status: {'🔒 Locked' if bot_locked else '✅ Running'}
Scripts: {len(bot_scripts)} active
Uptime: ✅ Online
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Refresh", callback_data="admin_system_status")],
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_add_admin")
async def callback_admin_add_admin(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    ➕ <b>ADD ADMIN</b> ➕
╚═══════════════════════╝

To add a new admin, use:
<code>/addadmin USER_ID</code>

<b>Example:</b>
<code>/addadmin 123456789</code>

The user will get full admin privileges!
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_remove_admin")
async def callback_admin_remove_admin(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = f"""
╔═══════════════════════╗
    ➖ <b>REMOVE ADMIN</b> ➖
╚═══════════════════════╝

<b>Current Admins ({len(admin_ids)}):</b>

"""
    
    for admin_id in admin_ids:
        text += f"👑 <code>{admin_id}</code>\n"
    
    text += "\n<b>To remove:</b>\n<code>/removeadmin USER_ID</code>"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_ban_user")
async def callback_admin_ban_user(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = f"""
╔═══════════════════════╗
    🚫 <b>BAN USER</b> 🚫
╚═══════════════════════╝

<b>Currently Banned:</b> {len(banned_users)} users

To ban a user, use:
<code>/ban USER_ID REASON</code>

<b>Example:</b>
<code>/ban 123456789 Spam</code>

Banned users cannot use the bot!
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_unban_user")
async def callback_admin_unban_user(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = f"""
╔═══════════════════════╗
    ✅ <b>UNBAN USER</b> ✅
╚═══════════════════════╝

<b>Banned Users:</b> {len(banned_users)}

"""
    
    if banned_users:
        text += "<b>List:</b>\n"
        for ban_id in list(banned_users)[:10]:
            text += f"🚫 <code>{ban_id}</code>\n"
    
    text += "\n<b>To unban:</b>\n<code>/unban USER_ID</code>"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "lock_bot")
async def callback_lock_bot(callback: types.CallbackQuery):
    global bot_locked
    
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    bot_locked = not bot_locked
    status = "🔒 LOCKED" if bot_locked else "🔓 UNLOCKED"
    
    await callback.answer(f"Bot is now {status}!", show_alert=True)
    await callback_admin_panel(callback)

@dp.callback_query(F.data == "broadcast")
async def callback_broadcast(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = f"""
╔═══════════════════════╗
    📢 <b>BROADCAST</b> 📢
╚═══════════════════════╝

Send a message to all users!

<b>Total Recipients:</b> {len(active_users)}

<b>Command:</b>
<code>/broadcast Your message here</code>

⚠️ Use this feature responsibly!
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "add_premium")
async def callback_add_premium(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    💎 <b>ADD PREMIUM</b> 💎
╚═══════════════════════╝

Give premium access to users!

<b>Command:</b>
<code>/addpremium USER_ID DAYS</code>

<b>Examples:</b>
<code>/addpremium 123456789 30</code> (30 days)
<code>/addpremium 987654321 7</code> (7 days)

Premium benefits:
• 50 file limit (vs 20)
• Priority support
• Premium badge
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_clean_files")
async def callback_admin_clean_files(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    🗑️ <b>CLEAN FILES</b> 🗑️
╚═══════════════════════╝

Clean old or unused files from the system.

<b>Options:</b>
• Delete files older than 30 days
• Remove files from banned users
• Clean temp/log files

<b>Command:</b>
<code>/clean OPTION</code>

⚠️ This action cannot be undone!
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_backup_db")
async def callback_admin_backup_db(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    try:
        backup_path = IROTECH_DIR / f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        
        conn = sqlite3.connect(DATABASE_PATH)
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()
        
        await callback.answer("✅ Database backed up!", show_alert=True)
        
        await callback.message.answer_document(
            FSInputFile(backup_path),
            caption="💾 <b>Database Backup</b>\n\nCreated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            parse_mode="HTML"
        )
        
        backup_path.unlink()
        
    except Exception as e:
        logger.error(f"Backup error: {e}")
        await callback.answer(f"❌ Backup failed: {str(e)}", show_alert=True)

@dp.callback_query(F.data == "admin_view_logs")
async def callback_admin_view_logs(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer("❌ Admin only!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    📝 <b>SYSTEM LOGS</b> 📝
╚═══════════════════════╝

View bot logs and activity.

<b>Available Logs:</b>
• Error logs
• User activity
• Script executions
• Admin actions

Logs are stored in the system directory.
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.callback_query(F.data == "admin_restart_bot")
async def callback_admin_restart_bot(callback: types.CallbackQuery):
    if callback.from_user.id != OWNER_ID:
        await callback.answer("❌ Owner only!", show_alert=True)
        return
    
    text = """
╔═══════════════════════╗
    🔄 <b>RESTART BOT</b> 🔄
╚═══════════════════════╝

⚠️ <b>WARNING:</b>
This will restart the entire bot!

All running scripts will be stopped.
Users may experience brief downtime.

<b>Only use if necessary!</b>

Use <code>/restart</code> to confirm.
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Admin Panel", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
    await callback.answer()

@dp.message(Command("addadmin"))
async def cmd_add_admin(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Permission denied!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /addadmin USER_ID")
            return
        
        new_admin_id = int(args[1])
        
        if new_admin_id in admin_ids:
            await message.answer(f"✅ User {new_admin_id} is already an admin!")
            return
        
        admin_ids.add(new_admin_id)
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (new_admin_id,))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ User <code>{new_admin_id}</code> added as admin!", parse_mode="HTML")
        
    except ValueError:
        await message.answer("❌ Invalid USER_ID!")
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("removeadmin"))
async def cmd_remove_admin(message: types.Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Only owner can remove admins!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /removeadmin USER_ID")
            return
        
        remove_admin_id = int(args[1])
        
        if remove_admin_id == OWNER_ID:
            await message.answer("❌ Cannot remove owner!")
            return
        
        if remove_admin_id not in admin_ids:
            await message.answer(f"❌ User {remove_admin_id} is not an admin!")
            return
        
        admin_ids.remove(remove_admin_id)
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM admins WHERE user_id = ?', (remove_admin_id,))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ User <code>{remove_admin_id}</code> removed from admins!", parse_mode="HTML")
        
    except ValueError:
        await message.answer("❌ Invalid USER_ID!")
    except Exception as e:
        logger.error(f"Error removing admin: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("addpremium"))
async def cmd_add_premium(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Permission denied!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Usage: /addpremium USER_ID DAYS")
            return
        
        user_id = int(args[1])
        days = int(args[2])
        
        if days <= 0:
            await message.answer("❌ Days must be greater than 0!")
            return
        
        expiry = datetime.now() + timedelta(days=days)
        user_subscriptions[user_id] = {'expiry': expiry}
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                  (user_id, expiry.isoformat()))
        conn.commit()
        conn.close()
        
        await message.answer(
            f"✅ <b>Premium Added!</b>\n\n"
            f"User: <code>{user_id}</code>\n"
            f"Duration: {days} days\n"
            f"Expires: {expiry.strftime('%Y-%m-%d %H:%M')}",
            parse_mode="HTML"
        )
        
    except ValueError:
        await message.answer("❌ Invalid input!")
    except Exception as e:
        logger.error(f"Error adding premium: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("ban"))
async def cmd_ban_user(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Permission denied!")
        return
    
    try:
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            await message.answer("Usage: /ban USER_ID [REASON]")
            return
        
        ban_user_id = int(args[1])
        reason = args[2] if len(args) > 2 else "No reason provided"
        
        if ban_user_id in admin_ids:
            await message.answer("❌ Cannot ban an admin!")
            return
        
        banned_users.add(ban_user_id)
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO banned_users (user_id, banned_date, reason) VALUES (?, ?, ?)',
                  (ban_user_id, datetime.now().isoformat(), reason))
        conn.commit()
        conn.close()
        
        await message.answer(f"🚫 User <code>{ban_user_id}</code> has been banned!\n\nReason: {reason}", parse_mode="HTML")
        
    except ValueError:
        await message.answer("❌ Invalid USER_ID!")
    except Exception as e:
        logger.error(f"Error banning user: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("unban"))
async def cmd_unban_user(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Permission denied!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 2:
            await message.answer("Usage: /unban USER_ID")
            return
        
        unban_user_id = int(args[1])
        
        if unban_user_id not in banned_users:
            await message.answer(f"❌ User {unban_user_id} is not banned!")
            return
        
        banned_users.remove(unban_user_id)
        
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM banned_users WHERE user_id = ?', (unban_user_id,))
        conn.commit()
        conn.close()
        
        await message.answer(f"✅ User <code>{unban_user_id}</code> has been unbanned!", parse_mode="HTML")
        
    except ValueError:
        await message.answer("❌ Invalid USER_ID!")
    except Exception as e:
        logger.error(f"Error unbanning user: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("broadcast"))
async def cmd_broadcast(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer("❌ Permission denied!")
        return
    
    try:
        broadcast_text = message.text.replace("/broadcast", "", 1).strip()
        
        if not broadcast_text:
            await message.answer("Usage: /broadcast Your message here")
            return
        
        sent_count = 0
        failed_count = 0
        
        status_msg = await message.answer(f"📢 Broadcasting to {len(active_users)} users...")
        
        for user_id in active_users:
            if user_id in banned_users:
                continue
            
            try:
                await bot.send_message(user_id, f"📢 <b>Announcement:</b>\n\n{broadcast_text}", parse_mode="HTML")
                sent_count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Failed to send to {user_id}: {e}")
                failed_count += 1
        
        await status_msg.edit_text(
            f"✅ <b>Broadcast Complete!</b>\n\n"
            f"✅ Sent: {sent_count}\n"
            f"❌ Failed: {failed_count}",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logger.error(f"Error broadcasting: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("search"))
async def cmd_search_files(message: types.Message):
    user_id = message.from_user.id
    
    try:
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Usage: /search filename")
            return
        
        search_term = args[1].lower()
        user_file_list = user_files.get(user_id, [])
        
        matches = [f for f in user_file_list if search_term in f[0].lower()]
        
        if not matches:
            await message.answer(f"🔍 No files found matching '<code>{search_term}</code>'", parse_mode="HTML")
            return
        
        text = f"🔍 <b>Search Results ({len(matches)}):</b>\n\n"
        
        for file_name, file_type in matches:
            icon = "🐍" if file_type == "py" else "🟨" if file_type == "js" else "📦"
            text += f"{icon} <code>{file_name}</code>\n"
        
        await message.answer(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Search error: {e}")
        await message.answer(f"❌ Error: {str(e)}")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    text = """
╔═══════════════════════╗
    ℹ️ <b>HELP & INFO</b> ℹ️
╚═══════════════════════╝

<b>🎯 HOW TO USE:</b>

1️⃣ <b>Upload Files:</b>
   • Click 'Upload File'
   • Send your .py, .js, or .zip file
   • File will be saved automatically

2️⃣ <b>Run Scripts:</b>
   • Go to 'My Files'
   • Click 'Run' on any file
   • Monitor script execution

3️⃣ <b>Manage Files:</b>
   • View all files in 'My Files'
   • Add to favorites with ⭐
   • Delete unwanted files

4️⃣ <b>Search:</b>
   • Use /search [filename]
   • Quick file lookup

━━━━━━━━━━━━━━━━━━━━
<b>💡 COMMANDS:</b>

/start - Start the bot
/help - Show this help
/search - Search files
/stats - Your statistics
/premium - Free info

<b>Need help? Contact owner! 💬</b>
"""
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 Features", callback_data="all_features")],
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await message.answer(text, reply_markup=back_keyboard, parse_mode="HTML")

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    user_id = message.from_user.id
    user_file_count = len(user_files.get(user_id, []))
    user_fav_count = len(user_favorites.get(user_id, []))
    is_premium = user_id in user_subscriptions and user_subscriptions[user_id]['expiry'] > datetime.now()
    
    text = f"""
╔═══════════════════════╗
    📊 <b>YOUR STATISTICS</b> 📊
╚═══════════════════════╝

<b>👤 USER INFO:</b>

🆔 User ID: <code>{user_id}</code>
👤 Name: {message.from_user.full_name}
📦 Files Uploaded: {user_file_count} (Unlimited ♾️)
⭐ Favorites: {user_fav_count}
💎 Account: Free 🆓 (Unlimited ♾️)
🚀 Running: {sum(1 for k in bot_scripts if k.startswith(f"{user_id}_"))}

━━━━━━━━━━━━━━━━━━━━
📈 <b>USAGE:</b>

📤 Uploads: {bot_stats.get('total_uploads', 0)}
📥 Downloads: {bot_stats.get('total_downloads', 0)}
▶️ Script Runs: {bot_stats.get('total_runs', 0)}

{'✅ Bot Status: Active' if not bot_locked else '🔒 Bot: Maintenance'}
"""
    
    if user_id in admin_ids:
        text += f"\n━━━━━━━━━━━━━━━━━━━━\n👑 <b>ADMIN STATS:</b>\n"
        text += f"👥 Total Users: {len(active_users)}\n"
        text += f"📁 Total Files: {sum(len(files) for files in user_files.values())}\n"
    
    back_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏠 Main Menu", callback_data="back_to_main")]
    ])
    
    await message.answer(text, reply_markup=back_keyboard, parse_mode="HTML")


# ── WEBSITE: LANDING PAGE + ADMIN PANEL ──────────────
import base64
import hmac
import html as html_lib

ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
_BOT_USERNAME = None

def _auth_ok(request):
    if not ADMIN_PASSWORD:
        return True  # password set nahi hai to panel open rahega
    h = request.headers.get('Authorization', '')
    if not h.startswith('Basic '):
        return False
    try:
        pwd = base64.b64decode(h[6:]).decode('utf-8').partition(':')[2]
    except Exception:
        return False
    return hmac.compare_digest(pwd.encode(), ADMIN_PASSWORD.encode())

def _deny():
    if not ADMIN_PASSWORD:
        return web.Response(text="Panel off hai. Render Environment mein ADMIN_PASSWORD set karo.", status=503)
    return web.Response(status=401, headers={'WWW-Authenticate': 'Basic realm="Admin"'}, text="Login required")

def _running_list():
    out = []
    for key, info in list(bot_scripts.items()):
        try:
            if info['process'].poll() is None:
                out.append({'key': key, 'owner': info['script_owner_id'], 'file': info['file_name'],
                            'type': info['type'], 'since': info['start_time'].strftime('%d %b %H:%M')})
        except Exception:
            pass
    return out

def _collect():
    now = datetime.now()
    running = _running_list()
    users = []
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('SELECT user_id, join_date, last_active FROM active_users ORDER BY COALESCE(last_active, join_date) DESC LIMIT 300')
        for uid, jd, la in c.fetchall():
            users.append({'id': uid, 'joined': (jd or '-')[:16].replace('T', ' '), 'last': (la or '-')[:16].replace('T', ' '),
                          'files': len(user_files.get(uid, [])), 'banned': uid in banned_users, 'admin': uid in admin_ids,
                          'sub': uid in user_subscriptions and user_subscriptions[uid]['expiry'] > now,
                          'running': sum(1 for r in running if r['owner'] == uid)})
        conn.close()
    except Exception as e:
        logger.error(f"Panel user fetch error: {e}")
    extra = {'joins7': [], 'new_today': 0, 'active24': 0}
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        d6 = (now - timedelta(days=6)).strftime('%Y-%m-%d')
        c.execute("SELECT substr(join_date,1,10), COUNT(*) FROM active_users WHERE join_date >= ? GROUP BY 1", (d6,))
        cnt = dict(c.fetchall())
        days = [now - timedelta(days=i) for i in range(6, -1, -1)]
        extra['joins7'] = [[x.strftime('%d %b'), cnt.get(x.strftime('%Y-%m-%d'), 0)] for x in days]
        extra['new_today'] = cnt.get(now.strftime('%Y-%m-%d'), 0)
        c.execute("SELECT COUNT(*) FROM active_users WHERE last_active >= ?", ((now - timedelta(hours=24)).isoformat(),))
        extra['active24'] = c.fetchone()[0]
        conn.close()
    except Exception as e:
        logger.error(f"Panel stats error: {e}")
    types = {}
    for fl in user_files.values():
        for f in fl:
            types[f[1]] = types.get(f[1], 0) + 1
    top = sorted(((u, len(fl)) for u, fl in user_files.items()), key=lambda x: -x[1])[:5]
    try:
        ram = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
    except Exception:
        ram = 0
    up = now - BOT_START_TIME
    return {'total_users': len(active_users), 'banned': len(banned_users), 'admins': len(admin_ids),
            'total_files': sum(len(f) for f in user_files.values()), 'running_now': len(running),
            'uploads': bot_stats.get('total_uploads', 0), 'downloads': bot_stats.get('total_downloads', 0),
            'runs': bot_stats.get('total_runs', 0), 'locked': bot_locked,
            'uptime': f"{up.days}d {up.seconds // 3600}h {(up.seconds % 3600) // 60}m",
            'files_list': [{'owner': u, 'name': f[0], 'type': f[1], 'on': f'{u}_{f[0]}' in bot_scripts and bot_scripts[f'{u}_{f[0]}']['process'].poll() is None} for u, fl in user_files.items() for f in fl][:500], 'ram_mb': ram, 'cpu': psutil.cpu_percent(interval=None), 'running': running, 'users': users, 'types': types, 'top': top, **extra}

def _stop_script(key):
    info = bot_scripts.get(key)
    if not info:
        return False
    try:
        lf = info.get('log_file')
        if lf and not lf.closed:
            lf.close()
        parent = psutil.Process(info['process'].pid)
        for ch in parent.children(recursive=True):
            ch.terminate()
        parent.terminate()
    except Exception:
        pass
    bot_scripts.pop(key, None)
    return True

def _db(sql, params=()):
    conn = sqlite3.connect(DATABASE_PATH)
    conn.execute(sql, params)
    conn.commit()
    conn.close()

async def _broadcast(text):
    sent = 0
    for uid in list(active_users):
        if uid in banned_users:
            continue
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.06)
    logger.info(f"Broadcast finished: {sent} users")

async def panel_action(request):
    global bot_locked
    if not _auth_ok(request):
        return _deny()
    if request.headers.get('X-Panel') != '1':
        return web.json_response({'ok': False, 'msg': 'Bad request'}, status=400)
    try:
        d = await request.json()
        act = d.get('action')
        uid = int(d['user_id']) if d.get('user_id') not in (None, '') else None
    except Exception:
        return web.json_response({'ok': False, 'msg': 'Invalid data'}, status=400)

    if act in ('ban', 'unban', 'stop_user') and uid is None:
        return web.json_response({'ok': False, 'msg': 'User ID chahiye'})
    if act == 'ban':
        if uid in admin_ids:
            return web.json_response({'ok': False, 'msg': 'Admin ko ban nahi kar sakte'})
        banned_users.add(uid)
        _db('INSERT OR REPLACE INTO banned_users (user_id, banned_date, reason) VALUES (?, ?, ?)',
            (uid, datetime.now().isoformat(), 'Banned from web panel'))
        for r in _running_list():
            if r['owner'] == uid:
                _stop_script(r['key'])
        return web.json_response({'ok': True, 'msg': f'{uid} banned'})
    if act == 'unban':
        banned_users.discard(uid)
        _db('DELETE FROM banned_users WHERE user_id = ?', (uid,))
        return web.json_response({'ok': True, 'msg': f'{uid} unbanned'})
    if act == 'stop_user':
        n = sum(1 for r in _running_list() if r['owner'] == uid and _stop_script(r['key']))
        return web.json_response({'ok': True, 'msg': f'{n} bot(s) stop kiye'})
    if act == 'stop_script':
        return web.json_response({'ok': _stop_script(d.get('key')), 'msg': 'Script stop kiya'})
    if act == 'stop_all':
        n = sum(1 for r in _running_list() if _stop_script(r['key']))
        return web.json_response({'ok': True, 'msg': f'{n} bot(s) stop kiye'})
    if act == 'lock':
        bot_locked = not bot_locked
        return web.json_response({'ok': True, 'msg': 'Bot LOCKED' if bot_locked else 'Bot UNLOCKED'})
    if act == 'broadcast':
        text = (d.get('text') or '').strip()
        if not text or len(text) > 3500:
            return web.json_response({'ok': False, 'msg': 'Message khali ya bahut lamba hai'})
        asyncio.create_task(_broadcast(text))
        return web.json_response({'ok': True, 'msg': f'Broadcast shuru: {len(active_users)} users ko'})
    return web.json_response({'ok': False, 'msg': 'Unknown action'})

async def panel_api(request):
    if not _auth_ok(request):
        return _deny()
    return web.json_response(_collect())

ADMIN_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prabhanjan Host Bot - Dashboard</title><style>
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:#0b0f17;color:#e6edf3;display:flex;min-height:100vh}
#side{width:220px;background:#111826;border-right:1px solid #1f2a3d;padding:16px 10px;position:fixed;top:0;bottom:0;left:0;z-index:20;transition:.2s}
.logo{font-weight:800;font-size:17px;padding:4px 10px 16px}.logo span{color:#58a6ff}
.nav{display:block;width:100%;text-align:left;background:none;border:0;color:#9aa7b5;padding:11px 12px;border-radius:8px;font-size:14px;cursor:pointer;margin-bottom:3px}
.nav:hover{background:#1a2438}.nav.on{background:#1f6feb;color:#fff}
.side-foot{position:absolute;bottom:14px;left:14px;font-size:11px;color:#6b7785}
#main{margin-left:220px;flex:1;padding:18px;min-width:0}
#top{display:none;align-items:center;gap:10px;margin-bottom:12px}
h2{margin:0 0 4px;font-size:20px}.sub{color:#8b949e;font-size:12px;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;margin-bottom:18px}
.card{background:#111826;border:1px solid #1f2a3d;border-radius:12px;padding:14px}.n{font-size:22px;font-weight:700}.l{font-size:12px;color:#8b949e;margin-top:3px}
.box{background:#111826;border:1px solid #1f2a3d;border-radius:12px;padding:14px;margin-bottom:14px}
.wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:8px;border-bottom:1px solid #1f2a3d;white-space:nowrap}th{color:#8b949e;font-weight:600}
button.a{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px;padding:6px 10px;font-size:12px;cursor:pointer}
.r{background:#da3633!important;border-color:#da3633!important}.g{background:#238636!important;border-color:#238636!important}.b{background:#1f6feb!important;border-color:#1f6feb!important}
input,textarea{background:#0b0f17;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:9px;font-size:13px;width:100%}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}.row input{flex:1;min-width:120px}
.tag{padding:2px 8px;border-radius:20px;font-size:11px}.on{}.t-on{background:#12361f;color:#3fb950}.t-off{background:#2a2f38;color:#8b949e}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:14px}.bars{display:flex;align-items:flex-end;gap:8px;height:130px;margin-top:12px}
.bc{flex:1;text-align:center;display:flex;flex-direction:column;justify-content:flex-end;align-items:center}.bc i{font-style:normal;font-size:11px;color:#9aa7b5}.bc small{font-size:10px;color:#6b7785}
.bar{width:100%;background:linear-gradient(#58a6ff,#1f6feb);border-radius:5px 5px 0 0}.hb{display:flex;align-items:center;gap:8px;margin-top:8px;font-size:12px}.hb span{width:56px;overflow:hidden;text-overflow:ellipsis}
.hb div{flex:1;background:#1a2438;border-radius:4px;height:8px}.hb div div{background:#1f6feb;height:8px}.hb em{font-style:normal;color:#9aa7b5;width:28px;text-align:right}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);padding:10px 18px;border-radius:8px;display:none;font-size:13px;z-index:99}
@media(max-width:760px){#side{left:-230px}#side.open{left:0}#main{margin-left:0}#top{display:flex}}
</style></head><body>
<div id="side"><div class="logo">🚀Prabhanjan Exploits <span>Host Bot</span></div>
<button class="nav on" data-p="over">📊 Overview</button><button class="nav" data-p="bots">🤖 Running Bots</button>
<button class="nav" data-p="files">📁 All Files</button><button class="nav" data-p="users">👥 Users</button>
<button class="nav" data-p="cast">📢 Broadcast</button><button class="nav" data-p="set">⚙️ Settings</button>
<a class="nav" href="/" style="text-decoration:none">🌐 Website</a><div class="side-foot">Developed by Prabhanjan Exploits</div></div>
<div id="main"><div id="top"><button class="a b" onclick="$('side').classList.toggle('open')">☰ Menu</button><b>Prabhanjan Exploits Host Bot</b></div>
<h2 id="ttl"></h2><div class="sub" id="st"></div><div id="view"></div></div><div id="toast"></div>
<script>
const $=id=>document.getElementById(id);let D=null,P='over',Q='';
const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(m,ok=true){const t=$('toast');t.textContent=m;t.style.background=ok?'#238636':'#da3633';t.style.display='block';setTimeout(()=>t.style.display='none',2600)}
async function act(b){try{const r=await fetch('/admin/action',{method:'POST',headers:{'Content-Type':'application/json','X-Panel':'1'},body:JSON.stringify(b)});const j=await r.json();toast(j.msg||'Done',j.ok);load()}catch(e){toast('Error',false)}}
document.querySelectorAll('button.nav').forEach(b=>b.onclick=()=>{P=b.dataset.p;document.querySelectorAll('button.nav').forEach(x=>x.classList.toggle('on',x==b));$('side').classList.remove('open');render()});
const card=(l,n)=>`<div class="card"><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`;
function bots(){return `<div class="box wrap"><table><tr><th>User</th><th>File</th><th>Type</th><th>Start</th><th></th></tr>`+(D.running.map(r=>`<tr><td>${r.owner}</td><td>${esc(r.file)}</td><td>${esc(r.type)}</td><td>${esc(r.since)}</td><td><button class="a r" onclick='act({action:"stop_script",key:${JSON.stringify(r.key)}})'>Stop</button></td></tr>`).join('')||'<tr><td colspan=5>Koi bot nahi chal raha</td></tr>')+`</table></div>`}
function render(){if(!D)return;const T={over:'Overview',bots:'Running Bots',files:'All Files',users:'Users',cast:'Broadcast',set:'Settings'};$('ttl').textContent=T[P];
$('st').textContent=(D.locked?'🔒 Maintenance mode':'✅ Bot active')+' • uptime '+D.uptime+' • auto-refresh 15s';let h='';
if(P=='over'){const mx=Math.max(1,...D.joins7.map(x=>x[1])),tt=Math.max(1,...Object.values(D.types));
h=`<div class="grid">${card('👥 Total Members',D.total_users)}${card('🆕 New today',D.new_today)}${card('🔥 Active 24h',D.active24)}${card('🟢 Bots running',D.running_now)}${card('📁 Files',D.total_files)}${card('🚫 Banned',D.banned)}${card('👑 Admins',D.admins)}${card('📤 Uploads',D.uploads)}${card('📥 Downloads',D.downloads)}${card('▶️ Script runs',D.runs)}${card('🧠 RAM MB',D.ram_mb)}${card('⚙️ CPU %',D.cpu)}</div>
<div class="two"><div class="box"><b>📈 Naye members (7 din)</b><div class="bars">${D.joins7.map(x=>`<div class="bc"><i>${x[1]}</i><div class="bar" style="height:${8+x[1]/mx*80}px"></div><small>${x[0]}</small></div>`).join('')}</div></div>
<div class="box"><b>🗂 File types</b>${Object.entries(D.types).map(([k,v])=>`<div class="hb"><span>${esc(k)}</span><div><div style="width:${v/tt*100}%"></div></div><em>${v}</em></div>`).join('')||'<p class="sub">Abhi koi file nahi</p>'}
<b style="display:block;margin-top:14px">🏆 Top users (files)</b>${D.top.map(t=>`<div class="hb"><span>${t[0]}</span><div><div style="width:${t[1]/Math.max(1,D.top[0][1])*100}%"></div></div><em>${t[1]}</em></div>`).join('')||'<p class="sub">-</p>'}</div></div>
<h3>🤖 Abhi chal rahe bots</h3>`+bots()}
if(P=='bots')h=bots();
if(P=='files')h=`<input placeholder="🔍 File ya user ID search..." value="${esc(Q)}" oninput="Q=this.value;render()" style="margin-bottom:10px"><div class="box wrap"><table><tr><th>User</th><th>File</th><th>Type</th><th>Status</th></tr>`+(D.files_list.filter(f=>(f.name+f.owner).toLowerCase().includes(Q.toLowerCase())).map(f=>`<tr><td>${f.owner}</td><td>${esc(f.name)}</td><td>${esc(f.type)}</td><td><span class="tag ${f.on?'t-on':'t-off'}">${f.on?'Running':'Stopped'}</span></td></tr>`).join('')||'<tr><td colspan=4>Koi file nahi</td></tr>')+`</table></div>`;
if(P=='users')h=`<input placeholder="🔍 User ID search..." value="${esc(Q)}" oninput="Q=this.value;render()" style="margin-bottom:10px"><div class="box wrap"><table><tr><th>User ID</th><th>Joined</th><th>Last active</th><th>Files</th><th>Run</th><th>Tags</th><th></th></tr>`+(D.users.filter(u=>String(u.id).includes(Q)).map(u=>`<tr><td>${u.id}</td><td>${esc(u.joined)}</td><td>${esc(u.last)}</td><td>${u.files}</td><td>${u.running}</td><td>${u.banned?'🚫':''}${u.admin?'👑':''}</td><td>${u.banned?`<button class="a g" onclick="act({action:'unban',user_id:${u.id}})">Unban</button>`:`<button class="a r" onclick="act({action:'ban',user_id:${u.id}})">Ban</button>`} <button class="a" onclick="act({action:'stop_user',user_id:${u.id}})">Stop bots</button></td></tr>`).join('')||'<tr><td colspan=7>Koi user nahi</td></tr>')+`</table></div>`;
if(P=='cast')h=`<div class="box"><b>📢 Saare users ko message</b><textarea id="bc" rows="5" placeholder="Message likho..." style="margin:10px 0"></textarea><button class="a b" onclick="cast()">Send to all users</button></div>`;
if(P=='set')h=`<div class="box"><b>Bot control</b><div class="row" style="margin-top:10px"><button class="a b" onclick="act({action:'lock'})">🔒 Lock / Unlock bot</button><button class="a r" onclick="if(confirm('Saare bots stop karein?'))act({action:'stop_all'})">🛑 Stop ALL bots</button></div></div><div class="box"><b>Manual ban / unban</b><div class="row" style="margin-top:10px"><input id="uid" placeholder="User ID" inputmode="numeric"><button class="a r" onclick="m('ban')">Ban</button><button class="a g" onclick="m('unban')">Unban</button></div></div>`;
$('view').innerHTML=h;const i=document.querySelector('#view input[oninput]');if(i&&Q){i.focus();i.setSelectionRange(Q.length,Q.length)}}
function m(a){const u=$('uid').value.trim();if(!u)return toast('User ID daalo',false);act({action:a,user_id:u})}
function cast(){const t=$('bc').value.trim();if(!t)return toast('Message likho',false);if(confirm('Sabko bhejein?')){act({action:'broadcast',text:t});$('bc').value=''}}
async function load(){try{D=await (await fetch('/admin/api')).json();const a=document.activeElement;if(a&&(a.tagName=='TEXTAREA'||a.id=='uid'))return;render()}catch(e){$('st').textContent='Load error'}}
load();setInterval(load,15000);
</script></body></html>"""

async def admin_page(request):
    if not _auth_ok(request):
        return _deny()
    return web.Response(text=ADMIN_HTML, content_type='text/html')

LANDING_HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Prabhanjan Exploits Host Bot - Free Telegram Bot Hosting</title><meta name="description" content="Python aur Node.js bots ko Telegram se free mein host karo. Unlimited uploads, koi premium nahi.">
<style>
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;font-family:system-ui,sans-serif;background:#070b12;color:#e6edf3;line-height:1.55}
nav{display:flex;justify-content:space-between;align-items:center;padding:14px 22px;position:sticky;top:0;background:#070b12ee;backdrop-filter:blur(8px);border-bottom:1px solid #151d2c;z-index:5}
nav b{font-size:18px}nav b span,h1 span,h2 span{color:#58a6ff}nav a{color:#9aa7b5;text-decoration:none;margin-left:16px;font-size:14px}
.hero{text-align:center;padding:70px 20px 40px;background:radial-gradient(circle at 50% 0,#14366a 0,#070b12 62%)}
.badge{display:inline-block;background:#12361f;color:#3fb950;border-radius:20px;padding:5px 14px;font-size:13px;margin-bottom:16px}
h1{font-size:40px;margin:0 0 12px;line-height:1.15}.hero p{color:#9aa7b5;max-width:560px;margin:0 auto 26px;font-size:17px}
.btn{display:inline-block;padding:13px 24px;border-radius:10px;text-decoration:none;font-weight:600;margin:5px;font-size:15px}.p{background:#1f6feb;color:#fff}.s{border:1px solid #2b374d;color:#e6edf3}
.stats{display:flex;justify-content:center;gap:12px;flex-wrap:wrap;padding:6px 20px 30px}.st{background:#0f1624;border:1px solid #1f2a3d;border-radius:14px;padding:16px 30px;text-align:center}.st b{font-size:26px;display:block}.st small{color:#8b949e}
section{max-width:900px;margin:0 auto;padding:36px 20px}h2{text-align:center;font-size:26px;margin:0 0 22px}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px}.f{background:#0f1624;border:1px solid #1f2a3d;border-radius:14px;padding:18px}.f h3{margin:0 0 6px;font-size:16px}.f p{margin:0;color:#9aa7b5;font-size:14px}
.step{display:flex;gap:14px;align-items:flex-start;background:#0f1624;border:1px solid #1f2a3d;border-radius:14px;padding:16px;margin-bottom:10px}.num{background:#1f6feb;border-radius:50%;min-width:34px;height:34px;display:flex;align-items:center;justify-content:center;font-weight:700}
details{background:#0f1624;border:1px solid #1f2a3d;border-radius:12px;padding:14px;margin-bottom:10px}summary{cursor:pointer;font-weight:600}details p{color:#9aa7b5;margin:10px 0 0}
.cta{text-align:center;background:linear-gradient(135deg,#12315f,#0f1624);border-radius:18px;padding:34px 20px}
footer{text-align:center;color:#6b7785;padding:28px 10px;font-size:13px;border-top:1px solid #151d2c}footer a{color:#58a6ff;text-decoration:none;margin:0 6px}
</style></head><body>
<nav><b>🚀Prabhanjan Exploits <span>Host Bot</span></b><div><a href="#features">Features</a><a href="#how">How it works</a><a href="#faq">FAQ</a></div></nav>
<div class="hero"><div class="badge">🆓 100% Free &bull; Unlimited Uploads</div>
<h1>Apne Bots ko <span>24/7</span> Host Karo<br>Seedha Telegram Se</h1>
<p>Python (.py), Node.js (.js), PHP (.php) ya ZIP file bhejo, dependencies khud install hongi aur bot chalu. Koi premium plan nahi, koi limit nahi.</p>
<a class="btn p" href="__BOTLINK__">🚀 Start on Telegram</a><a class="btn s" href="__CHANNEL__">📢 Updates Channel</a></div>
<div class="stats"><div class="st"><b>__USERS__</b><small>Users</small></div><div class="st"><b>__RUNNING__</b><small>Bots running now</small></div><div class="st"><b>__FILES__</b><small>Files hosted</small></div></div>
<section id="features"><h2>Features</h2><div class="g">
<div class="f"><h3>📤 Easy Upload</h3><p>.py, .js aur .zip files seedha Telegram chat se upload karo.</p></div>
<div class="f"><h3>⚙️ Auto Install</h3><p>Zaroori Python packages apne aap install ho jate hain.</p></div>
<div class="f"><h3>▶️ Run &amp; Stop</h3><p>Bot ke buttons se script start, stop aur manage karo.</p></div>
<div class="f"><h3>📁 File Manager</h3><p>Files dekho, search karo, download aur delete karo.</p></div>
<div class="f"><h3>⭐ Favorites</h3><p>Apni important scripts ko favorite mein rakho.</p></div>
<div class="f"><h3>🆓 Totally Free</h3><p>Unlimited uploads, sabke liye same features.</p></div></div></section>
<section id="how"><h2>Kaise use karein</h2>
<div class="step"><div class="num">1</div><div><b>Bot kholo</b><br><span style="color:#9aa7b5">Telegram pe bot open karke /start dabao.</span></div></div>
<div class="step"><div class="num">2</div><div><b>File upload karo</b><br><span style="color:#9aa7b5">Apni .py, .js ya .zip file bhejo.</span></div></div>
<div class="step"><div class="num">3</div><div><b>Run dabao</b><br><span style="color:#9aa7b5">Bot chalu, aur stop bhi kabhi bhi kar sakte ho.</span></div></div></section>
<section id="faq"><h2>FAQ</h2>
<details><summary>Kya ye sach mein free hai?</summary><p>Haan, koi premium plan ya upload limit nahi hai.</p></details>
<details><summary>Kaunsi files chalti hain?</summary><p>Python (.py), Node.js (.js) aur ZIP projects.</p></details>
<details><summary>Problem aaye to?</summary><p>Telegram pe owner @prabhanjanexploits se contact karo.</p></details></section>
<section><div class="cta"><h2 style="margin-bottom:8px">Ready to host? 🚀</h2><p style="color:#9aa7b5;margin:0 0 16px">Abhi Telegram pe bot kholo.</p><a class="btn p" href="__BOTLINK__">Open Bot</a></div></section>
<footer>Developed by <b> Prabhanjan Exploits </b><br><a href="__YT__">YouTube</a>&bull;<a href="__CHANNEL__">Telegram Channel</a>&bull;<a href="https://t.me/__OWNER__">Contact</a></footer></body></html>"""

async def landing_page(request):
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        try:
            _BOT_USERNAME = (await bot.get_me()).username
        except Exception:
            _BOT_USERNAME = ''
    ch = UPDATE_CHANNEL if UPDATE_CHANNEL.startswith('http') else 'https://t.me/' + UPDATE_CHANNEL.lstrip('@')
    page = (LANDING_HTML.replace('__BOTLINK__', f'https://t.me/{_BOT_USERNAME}' if _BOT_USERNAME else ch)
            .replace('__CHANNEL__', html_lib.escape(ch)).replace('__YT__', html_lib.escape(YOUTUBE_CHANNEL))
            .replace('__OWNER__', html_lib.escape(YOUR_USERNAME.lstrip('@')))
            .replace('__USERS__', str(len(active_users))).replace('__RUNNING__', str(len(_running_list())))
            .replace('__FILES__', str(sum(len(f) for f in user_files.values()))))
    return web.Response(text=page, content_type='text/html')

async def keep_alive():
    """Render free service ko sone se bachane ke liye khud ko ping karta hai"""
    url = os.getenv('RENDER_EXTERNAL_URL')
    if not url:
        return
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url + '/health', timeout=aiohttp.ClientTimeout(total=20)) as r:
                    logger.info(f"Keep-alive ping: {r.status}")
        except Exception as e:
            logger.warning(f"Keep-alive failed: {e}")
        await asyncio.sleep(600)  # har 10 minute

async def web_server():
    app = web.Application()
    
    async def health(request):
        return web.Response(text="ok")
    
    app.router.add_get('/', landing_page)
    app.router.add_get('/health', health)
    app.router.add_get('/admin', admin_page)
    app.router.add_get('/admin/api', panel_api)
    app.router.add_post('/admin/action', panel_action)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv('PORT', 5000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"🌐 Web server started on port {port}")

def _normalize_ngrok_domain(raw_domain: str) -> str:
    """Accept a reserved ngrok hostname or full https URL and return hostname only."""
    from urllib.parse import urlparse
    value = (raw_domain or '').strip()
    if not value:
        raise ValueError('Domain khali hai.')
    if '://' in value:
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path
    value = value.split('/')[0].split(':')[0].strip().lower().rstrip('.')
    if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', value):
        raise ValueError('Domain format sahi nahi hai. Example: my-name.ngrok-free.app')
    if '..' in value or value.startswith('.') or value.endswith('.'):
        raise ValueError('Domain format sahi nahi hai.')
    return value


def _terminate_process(proc, timeout=4):
    if not proc:
        return
    try:
        if proc.poll() is None:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.terminate()
                except Exception:
                    pass
            try:
                parent.terminate()
                parent.wait(timeout=timeout)
            except Exception:
                try:
                    parent.kill()
                except Exception:
                    pass
    except Exception:
        try:
            proc.terminate()
        except Exception:
            pass


def _stop_php_project(user_id, forget=True):
    info = php_projects.get(user_id)
    if not info:
        return False
    # Stop tunnel first, then PHP polling worker and web server.
    for key in ('tunnel_process', 'bot_process', 'web_process'):
        _terminate_process(info.get(key))
    for key in ('tunnel_log', 'bot_log', 'web_log'):
        handle = info.get(key)
        try:
            if handle and not handle.closed:
                handle.close()
        except Exception:
            pass
    if forget:
        php_projects.pop(user_id, None)
    return True


async def _start_php_project(user_id: int, token: str, domain: str):
    """Start PHP's web server, ngrok reserved domain and optional bot.php polling process."""
    user_folder = UPLOAD_BOTS_DIR / str(user_id)
    project_dir = user_folder / 'php_project'
    project_dir.mkdir(parents=True, exist_ok=True)

    # Backwards compatibility: stage PHP/assets previously uploaded to the user root.
    for candidate in user_folder.iterdir():
        if candidate.is_file() and candidate.suffix.lower() in PHP_ASSET_EXTENSIONS:
            shutil.copy2(candidate, project_dir / candidate.name)

    php_files = list(project_dir.glob('*.php'))
    if not php_files:
        raise RuntimeError('php_project mein koi .php file nahi mili. Pehle admin.php, bot.php, config.php, index.php upload karein.')
    if not shutil.which('php'):
        raise RuntimeError('PHP runtime installed nahi hai. Dockerfile rebuild/redeploy karein.')
    if not shutil.which('ngrok'):
        raise RuntimeError('ngrok binary nahi mila. Docker image rebuild/redeploy karein.')

    _stop_php_project(user_id)
    # Avoid low privileged users starting arbitrary public services with someone else's token.
    port = 20000 + (int(user_id) % 30000)
    web_log = open(user_folder / 'php-web.log', 'a', encoding='utf-8')
    web_proc = subprocess.Popen(
        ['php', '-S', f'0.0.0.0:{port}', '-t', str(project_dir)],
        cwd=str(project_dir), stdout=web_log, stderr=web_log,
        stdin=subprocess.DEVNULL, start_new_session=True,
        env={**os.environ, 'PHP_CLI_SERVER_WORKERS': '2'}
    )
    await asyncio.sleep(1.2)
    if web_proc.poll() is not None:
        web_log.flush()
        web_log.close()
        raise RuntimeError('PHP server start nahi hua. php-web.log check karein.')

    tunnel_log = open(user_folder / 'ngrok.log', 'a', encoding='utf-8')
    tunnel_env = {**os.environ, 'NGROK_AUTHTOKEN': token, 'HOME': str(user_folder)}
    tunnel_proc = subprocess.Popen(
        ['ngrok', 'http', str(port), '--url', f'https://{domain}'],
        cwd=str(user_folder), env=tunnel_env,
        stdout=tunnel_log, stderr=tunnel_log, stdin=subprocess.DEVNULL,
        start_new_session=True
    )
    await asyncio.sleep(4)
    if tunnel_proc.poll() is not None:
        tunnel_log.flush()
        try:
            tunnel_log.close()
        except Exception:
            pass
        _terminate_process(web_proc)
        web_log.close()
        try:
            tail = (user_folder / 'ngrok.log').read_text(encoding='utf-8', errors='replace')[-1200:]
        except Exception:
            tail = ''
        # Do not include token in error text/logs.
        raise RuntimeError('Ngrok tunnel start nahi hua. Token/domain account mein verify karein.\n' + tail[-900:])

    bot_proc = None
    bot_log = None
    bot_file = project_dir / 'bot.php'
    if bot_file.exists():
        bot_log = open(user_folder / 'php-bot.log', 'a', encoding='utf-8')
        bot_proc = subprocess.Popen(
            ['php', str(bot_file)], cwd=str(project_dir),
            stdout=bot_log, stderr=bot_log, stdin=subprocess.DEVNULL,
            start_new_session=True, env={**os.environ, 'APP_PUBLIC_URL': f'https://{domain}'}
        )

    php_projects[user_id] = {
        'domain': domain, 'url': f'https://{domain}', 'port': port,
        'project_dir': str(project_dir), 'token': token,
        'web_process': web_proc, 'tunnel_process': tunnel_proc,
        'bot_process': bot_proc, 'web_log': web_log,
        'tunnel_log': tunnel_log, 'bot_log': bot_log,
        'started_at': datetime.now()
    }
    return php_projects[user_id]


@dp.callback_query(F.data == 'php_host')
async def callback_php_host(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer('PHP hosting setup sirf bot owner/admin kar sakta hai.', show_alert=True)
        return
    project_dir = UPLOAD_BOTS_DIR / str(user_id) / 'php_project'
    project_dir.mkdir(parents=True, exist_ok=True)
    # Include legacy uploads in the dedicated project directory.
    for candidate in (UPLOAD_BOTS_DIR / str(user_id)).iterdir():
        if candidate.is_file() and candidate.suffix.lower() in PHP_ASSET_EXTENSIONS:
            shutil.copy2(candidate, project_dir / candidate.name)
    if not list(project_dir.glob('*.php')):
        await callback.answer('Pehle apni PHP files upload karein.', show_alert=True)
        return
    php_setup_state[user_id] = {'step': 'token'}
    await callback.message.answer(
        '🔐 <b>Ngrok setup</b>\n\nApna ngrok <b>authtoken</b> bhejein. Token ko bot log/database mein save nahi kiya jayega; ye sirf current process ki memory mein rahega.\n\n⚠️ Sirf private chat mein bhejein. Bot aapke token message ko delete karne ki koshish karega.\n\nCancel karne ke liye /cancel use karein.',
        parse_mode='HTML'
    )
    await callback.answer()


@dp.message(Command('phphost'))
async def cmd_php_host(message: types.Message):
    if message.from_user.id not in admin_ids:
        await message.answer('PHP hosting setup sirf bot owner/admin ke liye hai.')
        return
    project_dir = UPLOAD_BOTS_DIR / str(message.from_user.id) / 'php_project'
    project_dir.mkdir(parents=True, exist_ok=True)
    for candidate in (UPLOAD_BOTS_DIR / str(message.from_user.id)).iterdir():
        if candidate.is_file() and candidate.suffix.lower() in PHP_ASSET_EXTENSIONS:
            shutil.copy2(candidate, project_dir / candidate.name)
    if not list(project_dir.glob('*.php')):
        await message.answer('Pehle admin.php, bot.php, config.php, index.php upload karein; phir /phphost bhejein.')
        return
    php_setup_state[message.from_user.id] = {'step': 'token'}
    await message.answer('🔐 Apna ngrok authtoken bhejein. Token save/log nahi hoga; /cancel se cancel karein.')


@dp.message(Command('cancel'))
async def cmd_cancel_php_setup(message: types.Message):
    php_setup_state.pop(message.from_user.id, None)
    await message.answer('PHP/ngrok setup cancel kar diya.')


@dp.callback_query(F.data == 'php_status')
async def callback_php_status(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer('Not authorized.', show_alert=True)
        return
    info = php_projects.get(user_id)
    if not info:
        await callback.answer('PHP project currently stopped.', show_alert=True)
        return
    def pstate(key):
        proc = info.get(key)
        return '🟢 Running' if proc and proc.poll() is None else ('⚪ Not configured' if proc is None else '🔴 Stopped')
    await callback.message.answer(
        f"🌐 <b>PHP Project Status</b>\n\nURL: {info['url']}\nWeb server: {pstate('web_process')}\nNgrok: {pstate('tunnel_process')}\nPHP bot.php polling: {pstate('bot_process')}\nPort: <code>{info['port']}</code>",
        parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text='🔄 Restart PHP Host', callback_data='php_restart'), InlineKeyboardButton(text='🛑 Stop PHP Host', callback_data='php_stop')],
            [InlineKeyboardButton(text='📋 PHP Logs', callback_data='php_logs')]
        ])
    )
    await callback.answer()


@dp.callback_query(F.data == 'php_stop')
async def callback_php_stop(callback: types.CallbackQuery):
    if callback.from_user.id not in admin_ids:
        await callback.answer('Not authorized.', show_alert=True)
        return
    stopped = _stop_php_project(callback.from_user.id)
    await callback.answer('PHP host stopped.' if stopped else 'PHP host already stopped.', show_alert=True)
    await callback.message.answer('🛑 PHP web server, ngrok tunnel aur bot.php polling process stop kar diye.')


@dp.callback_query(F.data == 'php_restart')
async def callback_php_restart(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer('Not authorized.', show_alert=True)
        return
    info = php_projects.get(user_id)
    if not info:
        await callback.answer('Pehle /phphost se setup karein.', show_alert=True)
        return
    token, domain = info['token'], info['domain']
    try:
        await callback.answer('Restarting...')
        new_info = await _start_php_project(user_id, token, domain)
        await callback.message.answer(f"✅ PHP host restart ho gaya: {new_info['url']}")
    except Exception as exc:
        await callback.message.answer(f'❌ Restart failed: {str(exc)[:1500]}')


@dp.callback_query(F.data == 'php_logs')
async def callback_php_logs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in admin_ids:
        await callback.answer('Not authorized.', show_alert=True)
        return
    folder = UPLOAD_BOTS_DIR / str(user_id)
    chunks = []
    for title, filename in [('PHP web server', 'php-web.log'), ('Ngrok', 'ngrok.log'), ('PHP bot.php', 'php-bot.log')]:
        p = folder / filename
        if p.exists():
            try:
                content = p.read_text(encoding='utf-8', errors='replace')[-1800:]
            except Exception:
                content = 'Log read nahi ho paya.'
            chunks.append(f'<b>{title}</b>\n<pre>{html_lib.escape(content[-1300:])}</pre>')
    text = '\n\n'.join(chunks) or 'Abhi PHP logs nahi mile.'
    await callback.message.answer(text[:3900], parse_mode='HTML')
    await callback.answer()


@dp.message(F.text)
async def handle_php_setup_text(message: types.Message):
    user_id = message.from_user.id
    state = php_setup_state.get(user_id)
    if not state or message.text.startswith('/'):
        return
    if user_id not in admin_ids:
        php_setup_state.pop(user_id, None)
        return
    value = message.text.strip()
    if state.get('step') == 'token':
        if len(value) < 12 or any(ch.isspace() for ch in value):
            await message.answer('❌ Token format sahi nahi lag raha. Ngrok dashboard se authtoken copy karke bhejein, ya /cancel karein.')
            return
        state['token'] = value
        state['step'] = 'domain'
        try:
            await message.delete()
        except Exception:
            pass
        await message.answer('✅ Token receive ho gaya. Ab apna reserved/static ngrok domain bhejein, jaise <code>my-domain.ngrok-free.app</code> ya poora https URL.', parse_mode='HTML')
        return
    if state.get('step') == 'domain':
        try:
            domain = _normalize_ngrok_domain(value)
        except ValueError as exc:
            await message.answer(f'❌ {html_lib.escape(str(exc))}')
            return
        token = state.get('token', '')
        php_setup_state.pop(user_id, None)
        try:
            status = await message.answer('⏳ PHP server aur ngrok tunnel start ho rahe hain...')
            info = await _start_php_project(user_id, token, domain)
            try:
                await status.edit_text(
                    f"✅ <b>PHP HOST STARTED</b>\n\n🌐 Website: {info['url']}\n📁 Project: <code>php_project/</code>\n🐘 PHP web server: Running\n🔗 Ngrok: Running\n{'🤖 bot.php polling: Running' if info.get('bot_process') and info['bot_process'].poll() is None else 'ℹ️ bot.php nahi mila; sirf web server start hua.'}\n\nAgar config.php mein domain chahiye, usmein <code>https://{html_lib.escape(domain)}</code> set karein.",
                    parse_mode='HTML', reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='🌍 Open Website', url=info['url'])],
                        [InlineKeyboardButton(text='📊 Status', callback_data='php_status'), InlineKeyboardButton(text='🛑 Stop', callback_data='php_stop')],
                        [InlineKeyboardButton(text='📋 Logs', callback_data='php_logs'), InlineKeyboardButton(text='🔄 Restart', callback_data='php_restart')]
                    ])
                )
            except Exception:
                await message.answer(f"PHP host start ho gaya: {info['url']}")
        except Exception as exc:
            logger.error('PHP host start failed for user %s: %s', user_id, str(exc)[:500])
            await message.answer(f'❌ PHP host start nahi hua:\n<pre>{html_lib.escape(str(exc)[:2500])}</pre>', parse_mode='HTML')


async def main():
    logger.info("🚀 Starting Advanced File Host Bot...")
    
    asyncio.create_task(web_server())
    asyncio.create_task(keep_alive())
    
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
