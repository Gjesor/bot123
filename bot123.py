import os, asyncio, datetime, uuid
from html import escape
from dotenv import load_dotenv
import yt_dlp
from aiohttp import web
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram import F
from typing import Dict

# 🌍 Конфигурация
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
BASE_WEBHOOK_URL = os.getenv("BASE_WEBHOOK_URL")
PORT = int(os.getenv("PORT", 8080))
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "D:/ffmpeg/bin/ffmpeg.exe")
COOKIES_PATH = os.getenv("COOKIES_PATH", "cookies.txt")
MAX_FILE_SIZE = 50 * 1024 * 1024
DAILY_LIMIT = 5

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# 🧠 Словари
download_counter: Dict[int, tuple] = {}
url_storage: Dict[str, str] = {}
user_language: Dict[int, str] = {}
user_platform: Dict[int, str] = {}

translations = {
    "start": {
        "ru": "👋 Привет! Выберите устройство и отправьте ссылку на YouTube, TikTok или Instagram.",
        "en": "👋 Hello! Choose your device and send a link from YouTube, TikTok or Instagram."
    },
    "limit": {
        "ru": "⚠️ Ты достиг лимита на сегодня.",
        "en": "⚠️ You’ve reached today’s limit."
    },
    "format": {
        "ru": "Выберите формат:",
        "en": "Choose format:"
    },
    "saved": {
        "ru": "✅ Платформа сохранена. Можете отправить ссылку на видео.",
        "en": "✅ Platform saved. You can now send a video link."
    }
}

def get_lang(user_id):
    return user_language.get(user_id, "ru")

# 🚦 Команды и обработчики
@dp.message(F.text, commands=["start"])
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    lang_code = message.from_user.language_code
    user_language[user_id] = lang_code if lang_code in translations["start"] else "ru"
    lang = get_lang(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 iPhone", callback_data="platform_ios")],
        [InlineKeyboardButton(text="🤖 Android", callback_data="platform_android")],
        [InlineKeyboardButton(text="💻 ПК", callback_data="platform_pc")]
    ])
    await message.answer(translations["start"][lang], reply_markup=keyboard)

@dp.callback_query(F.data.startswith("platform_"))
async def handle_platform(callback: CallbackQuery):
    user_id = callback.from_user.id
    platform = callback.data.split("_")[1]
    user_platform[user_id] = platform
    lang = get_lang(user_id)
    await callback.message.answer(translations["saved"][lang])
    await callback.answer()

@dp.message()
async def handle_link(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id
    lang = get_lang(user_id)

    today = datetime.date.today()
    date, count = download_counter.get(user_id, (today, 0))
    if date == today and count >= DAILY_LIMIT:
        await message.answer(translations["limit"][lang])
        return
    download_counter[user_id] = (today, count + 1)

    try:
        ydl_opts_info = {
            "ffmpeg_location": FFMPEG_PATH,
            "quiet": True,
            "skip_download": True
        }
        if os.path.exists(COOKIES_PATH):
            ydl_opts_info["cookies"] = COOKIES_PATH

        with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise Exception("Видео не найдено.")
            title = escape(info.get("title", "Без названия"))
            duration = info.get("duration", 0)
            mins, secs = divmod(duration, 60)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {escape(str(e))}")
        return

    key = str(uuid.uuid4())
    url_storage[key] = url
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 480p", callback_data=f"video_480_{key}")],
        [InlineKeyboardButton(text="🎥 720p", callback_data=f"video_720_{key}")],
        [InlineKeyboardButton(text="🎧 MP3", callback_data=f"audio_{key}")]
    ])
    await message.answer(f"<b>{title}</b>\n⏱ {mins}m {secs}s\n{translations['format'][lang]}", reply_markup=keyboard)

@dp.callback_query(F.data.startswith(("video_", "audio_")))
async def handle_download(callback: CallbackQuery):
    parts = callback.data.split("_")
    mode = parts[0]
    res = parts[1] if mode == "video" else None
    key = parts[2]
    user_id = callback.from_user.id
    lang = get_lang(user_id)
    url = url_storage.get(key)

    await callback.message.answer("⏳ Загрузка...")
    filename = f"{key}.mp3" if mode == "audio" else f"{key}.mp4"

    ydl_opts = {
        "outtmpl": filename,
        "quiet": True,
        "ffmpeg_location": FFMPEG_PATH,
        "ignoreerrors": True
    }

    if os.path.exists(COOKIES_PATH):
        ydl_opts["cookies"] = COOKIES_PATH

    if mode == "audio":
        ydl_opts.update({
            "format": "bestaudio[filesize>1M]",
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192"
            }]
        })
    else:
        ydl_opts.update({
            "format": f"bestvideo[height<={res}]+bestaudio/best",
            "merge_output_format": "mp4"
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        if not os.path.exists(filename):
            raise Exception("Файл не найден.")
        if os.path.getsize(filename) > MAX_FILE_SIZE:
            await bot.send_message(user_id, "🚫 Файл превышает лимит в 50MB.")
            os.remove(filename)
            return

        with open(filename, "rb") as f:
            file_bytes = f.read()
        file_input = types.input_file.BufferedInputFile(file_bytes, filename=os.path.basename(filename))
        await bot.send_document(user_id, document=file_input)

        os.remove(filename)
        url_storage.pop(key, None)
    except Exception as e:
        await bot.send_message(user_id, f"❌ Ошибка: {escape(str(e))}")
    await callback.answer()

# 🌐 Webhook Render
async def webhook_handler(request):
    data = await request.json()
    await dp.feed_webhook_update(bot, data)
    return web.Response()

async def on_startup(app):
    webhook_url = f"{BASE_WEBHOOK_URL}/webhook"
    await bot.set_webhook(webhook_url)

app = web.Application()
app.router.add_post("/webhook", webhook_handler)
app.on_startup.append(on_startup)

if __name__ == "__main__":
    web.run_app(app, port=PORT)

