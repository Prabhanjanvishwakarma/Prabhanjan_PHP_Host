import os
import time
import shutil
import zipfile
import asyncio
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import Flask, jsonify
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ["8742162183:AAH8o_Ukjvtnic8mmIhUMtBc_6iwuUFqOYY"]
ADMIN_ID = int(os.environ["6146027296"])
NGROK_TOKEN = os.environ["3JDkuTVRkOBjmdxSxxS0TutPXKS_cF3wu3wdvh1U1cYa7pVc"]

PHP_PORT = int(os.getenv("PHP_PORT", "8080"))
RENDER_PORT = int(os.getenv("PORT", "10000"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "20"))

SITE = Path(os.getenv("SITE_DIR", "/data/site"))
LOGS = Path(os.getenv("LOG_DIR", "/data/logs"))
SITE.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)

php_proc = None
ngrok_proc = None
public_url = None
lock = threading.Lock()

health = Flask(__name__)


@health.get("/")
@health.get("/health")
def health_check():
    return jsonify({
        "service": "Prabhanjan Exploits PHP Hosting",
        "status": "running",
    })


def admin_only(update):
    return bool(
        update.effective_user
        and update.effective_user.id == ADMIN_ID
    )


def stop_process(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def stop_hosting():
    global php_proc, ngrok_proc, public_url

    with lock:
        stop_process(ngrok_proc)
        stop_process(php_proc)
        ngrok_proc = None
        php_proc = None
        public_url = None


def validate_and_extract(zip_path, destination):
    with zipfile.ZipFile(zip_path) as archive:
        entries = archive.infolist()

        if len(entries) > 500:
            raise ValueError("Maximum 500 ZIP entries allowed hain.")

        total_size = 0
        has_index = False

        for entry in entries:
            name = entry.filename.replace("\\", "/")
            parts = Path(name).parts

            if (
                not name
                or name.startswith("/")
                or ".." in parts
                or "\x00" in name
                or (parts and ":" in parts[0])
            ):
                raise ValueError("ZIP mein unsafe file path mila.")

            mode = entry.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                raise ValueError("ZIP symbolic links allowed nahi hain.")

            total_size += entry.file_size
            if total_size > 50 * 1024 * 1024:
                raise ValueError("Extracted ZIP limit 50 MB hai.")

            if name == "index.php":
                has_index = True

        if not has_index:
            raise ValueError(
                "ZIP ke root mein index.php rakhein."
            )

        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()

        for entry in entries:
            target = (destination / entry.filename).resolve()
            if os.path.commonpath(
                [str(root), str(target)]
            ) != str(root):
                raise ValueError("ZIP path destination se bahar hai.")

        archive.extractall(destination)


def start_php():
    global php_proc

    if not (SITE / "index.php").is_file():
        raise RuntimeError("Pehle valid PHP ZIP upload karein.")

    if php_proc and php_proc.poll() is None:
        return

    with open(LOGS / "php.log", "ab") as log:
        php_proc = subprocess.Popen(
            [
                "php", "-S", f"127.0.0.1:{PHP_PORT}",
                "-t", str(SITE),
            ],
            cwd=str(SITE),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    for _ in range(15):
        if php_proc.poll() is not None:
            raise RuntimeError("PHP server start nahi hua.")

        try:
            requests.get(
                f"http://127.0.0.1:{PHP_PORT}/",
                timeout=1,
            )
            return
        except requests.RequestException:
            time.sleep(1)

    raise RuntimeError("PHP server response nahi de raha.")


def start_ngrok():
    global ngrok_proc, public_url

    if ngrok_proc and ngrok_proc.poll() is None:
        if public_url:
            return public_url

    with open(LOGS / "ngrok.log", "ab") as log:
        ngrok_proc = subprocess.Popen(
            ["ngrok", "http", str(PHP_PORT), "--log=stdout"],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    for _ in range(30):
        if ngrok_proc.poll() is not None:
            raise RuntimeError("Ngrok start nahi hua. Logs check karein.")

        try:
            response = requests.get(
                "http://127.0.0.1:4040/api/tunnels",
                timeout=2,
            )
            response.raise_for_status()

            for tunnel in response.json().get("tunnels", []):
                candidate = tunnel.get("public_url", "")
                parsed = urlparse(candidate)

                if parsed.scheme == "https" and parsed.netloc:
                    public_url = candidate
                    return candidate

        except (requests.RequestException, ValueError):
            pass

        time.sleep(1)

    raise RuntimeError("Ngrok public URL nahi mila.")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        await update.effective_message.reply_text(
            "⛔ Unauthorized. Ye bot admin-only hai."
        )
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📦 Upload PHP ZIP", callback_data="upload"
        )],
        [InlineKeyboardButton(
            "🚀 Start Hosting", callback_data="host"
        )],
        [
            InlineKeyboardButton("📊 Status", callback_data="status"),
            InlineKeyboardButton("🛑 Stop", callback_data="stop"),
        ],
        [InlineKeyboardButton(
            "🔗 Public URL", callback_data="url"
        )],
    ])

    await update.effective_message.reply_text(
        "🛡️ PRABHANJAN EXPLOITS\n"
        "PHP HOSTING BOT\n\n"
        "📦 PHP website ki ZIP upload karein.\n"
        "🚀 Start Hosting dabayein.\n"
        "🌐 Ngrok public URL receive karein.\n\n"
        "/upload /host /status /url /stop",
        reply_markup=keyboard,
    )


async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    await update.effective_message.reply_text(
        f"Apni PHP website ki .zip file bhejein.\n"
        f"Maximum upload: {MAX_UPLOAD_MB} MB.\n"
        "ZIP ke root mein index.php honi chahiye."
    )


async def receive_zip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    doc = update.effective_message.document

    if not doc.file_name or not doc.file_name.lower().endswith(".zip"):
        await update.effective_message.reply_text(
            "❌ Sirf .zip file upload karein."
        )
        return

    if doc.file_size and doc.file_size > MAX_UPLOAD_MB * 1024 * 1024:
        await update.effective_message.reply_text(
            f"❌ Maximum file size {MAX_UPLOAD_MB} MB hai."
        )
        return

    zip_path = Path("/tmp/prabhanjan-upload.zip")
    temp_dir = Path("/tmp/prabhanjan-extracted")

    try:
        await update.effective_message.reply_text(
            "⏳ ZIP download aur validate ho rahi hai..."
        )

        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(custom_path=str(zip_path))

        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        await asyncio.to_thread(
            validate_and_extract, zip_path, temp_dir
        )

        stop_hosting()

        if SITE.exists():
            shutil.rmtree(SITE)

        shutil.move(str(temp_dir), str(SITE))

        await update.effective_message.reply_text(
            "✅ PHP project upload ho gaya!\n\n"
            "Ab /host bhejein ya Start Hosting dabayein."
        )

    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Upload error: {exc}"
        )
    finally:
        zip_path.unlink(missing_ok=True)
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)


async def host(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    await update.effective_message.reply_text(
        "⏳ PHP server aur Ngrok start ho rahe hain..."
    )

    try:
        start_php()
        url = await asyncio.to_thread(start_ngrok)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Open Website", url=url)],
            [InlineKeyboardButton("🛑 Stop Hosting", callback_data="stop")],
        ])

        await update.effective_message.reply_text(
            f"✅ Hosting started!\n\n"
            f"🌐 Public URL:\n{url}",
            reply_markup=keyboard,
        )

    except Exception as exc:
        stop_hosting()
        await update.effective_message.reply_text(
            f"❌ Hosting error: {exc}"
        )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    php_up = php_proc is not None and php_proc.poll() is None
    ngrok_up = ngrok_proc is not None and ngrok_proc.poll() is None

    await update.effective_message.reply_text(
        f"📊 PRABHANJAN HOSTING STATUS\n\n"
        f"PHP: {'🟢 Running' if php_up else '🔴 Stopped'}\n"
        f"Ngrok: {'🟢 Running' if ngrok_up else '🔴 Stopped'}\n"
        f"URL: {public_url or 'Not available'}"
    )


async def url_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    await update.effective_message.reply_text(
        public_url or "❌ URL nahi mila. Pehle /host chalayein."
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not admin_only(update):
        return

    await asyncio.to_thread(stop_hosting)
    await update.effective_message.reply_text(
        "🛑 PHP server aur Ngrok stop kar diye gaye."
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not admin_only(update):
        await query.message.reply_text("⛔ Unauthorized.")
        return

    actions = {
        "upload": upload,
        "host": host,
        "status": status,
        "url": url_cmd,
        "stop": stop_cmd,
    }

    action = actions.get(query.data)
    if action:
        await action(update, context)


def main():
    subprocess.run(
        ["ngrok", "config", "add-authtoken", NGROK_TOKEN],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    threading.Thread(
        target=lambda: health.run(
            host="0.0.0.0",
            port=RENDER_PORT,
            use_reloader=False,
        ),
        daemon=True,
    ).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("upload", upload))
    app.add_handler(CommandHandler("host", host))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("url", url_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(
        filters.Document.ALL, receive_zip
    ))

    print("Prabhanjan Exploits PHP Hosting Bot started.")

    try:
        app.run_polling()
    finally:
        stop_hosting()


if __name__ == "__main__":
    main()
