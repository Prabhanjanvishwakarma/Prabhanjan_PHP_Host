# Prabhanjan PHP Host Bot (Render + ngrok)

## Features
- Existing Python/Node upload and runner UI retained.
- Upload PHP files (`admin.php`, `bot.php`, `config.php`, `index.php`) one by one, or upload a ZIP then use Extract ZIP.
- PHP website assets (`css`, `html`, `js`, images, JSON/XML) are staged under `upload_bots/<telegram_user_id>/php_project/`.
- Admin/owner runs `/phphost` or presses **PHP + Ngrok Host**.
- Bot asks for ngrok authtoken, then the reserved/static domain.
- Starts `php -S` for the website, `ngrok http --domain ...`, and runs `bot.php` as a separate PHP CLI polling worker if that file exists.
- `/cancel`, plus Telegram buttons for status, logs, stop and restart.

## Deploy
1. Upload these files to your GitHub repository: `app.py`, `Dockerfile`, `requirements.txt`, `render.yaml`, `.gitignore`, `.env.example`.
2. In Render, create a Web Service from the repository (Docker runtime) or use the Blueprint `render.yaml`.
3. Set `BOT_TOKEN`, `OWNER_ID`, `ADMIN_ID`, `UPDATE_CHANNEL`, `YOUR_USERNAME`, and `ADMIN_PASSWORD` in Render Environment. Never commit `.env` or a real token.
4. Deploy. Check `/health` on the Render service URL.
5. Open your Telegram bot as the owner/admin and upload `admin.php`, `bot.php`, `config.php`, `index.php` (and any assets). Each file can be sent as a separate document; alternatively upload a ZIP and press Extract ZIP.
6. Press **PHP + Ngrok Host** or send `/phphost`. Send ngrok authtoken and then the exact reserved domain hostname.
7. Put `https://YOUR-RESERVED-DOMAIN` in your own `config.php` wherever your app expects its public URL. The bot does not rewrite unknown config formats automatically.

## Important limitations
- `bot.php` is executed as a PHP CLI background process; `index.php` and other PHP pages are served by the PHP built-in web server through ngrok.
- The PHP/ngrok host setup is restricted to the configured owner/admin IDs because it runs uploaded code and consumes a tunnel credential.
- Token is held in memory for the current service lifetime and is not saved to SQLite or logs. The bot tries to delete the token message after receiving it. Telegram chat history may still retain messages in some circumstances, so rotate a token if it was exposed.
- Render Free services can sleep/restart, and their local filesystem is ephemeral. This means uploaded files and running PHP/ngrok processes are **not guaranteed to persist 24/7**. For durable hosting use a paid always-on service with persistent disk or an external persistent storage/worker setup.
- This is a single project folder per Telegram user. Do not upload code you do not trust; PHP scripts run with the same OS permissions as the app container.
- Ngrok domain must already be reserved/available to the token's account. A domain from another account or unsupported free plan will fail.
