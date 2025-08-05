import os
import asyncio
import datetime
import uuid
from html import escape
from dotenv import load_dotenv
import yt_dlp
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.client.bot import DefaultBotProperties

# 🔐 Загружаем переменные из .env
load_dotenv()

API_TOKEN = os.getenv("API_TOKEN")
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "/usr/bin/ffmpeg")
COOKIES_PATH = os.getenv("COOKIES_PATH", "cookies.txt")
MAX_FILE_SIZE = 50 * 1024 * 1024
DAILY_LIMIT = 5

bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
download_counter = {}
url_storage = {}
user_language = {}
user_platform = {}

def get_lang(user_id):
    return user_language.get(user_id, "ru")

translations = {
    "start_msg": {
        "ru": "👋 Привет! Выберите устройство и отправьте ссылку на YouTube, TikTok или Instagram.",
        "en": "👋 Hello! Choose your device and send a link from YouTube, TikTok or Instagram."
    },
    "limit_msg": {
        "ru": "⚠️ Ты достиг лимита на сегодня.",
        "en": "⚠️ You’ve reached today’s limit."
    },
    "choose_format": {
        "ru": "Выберите формат:",
        "en": "Choose format:"
    }
}

async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_language[user_id] = message.from_user.language_code if message.from_user.language_code in ["en", "ru"] else "ru"
    lang = get_lang(user_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 iPhone", callback_data="platform_ios")],
        [InlineKeyboardButton(text="🤖 Android", callback_data="platform_android")],
        [InlineKeyboardButton(text="💻 ПК", callback_data="platform_pc")]
    ])
    await message.answer(translations["start_msg"][lang], reply_markup=keyboard)

async def handle_platform(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    platform = callback.data.split("_")[1]
    user_platform[user_id] = platform
    await bot.send_message(user_id, "✅ Платформа сохранена. Можете отправить ссылку на видео.")

async def handle_link(message: types.Message):
    url = message.text.strip()
    user_id = message.from_user.id
    lang = get_lang(user_id)

    today = datetime.date.today()
    date, count = download_counter.get(user_id, (today, 0))
    if date == today and count >= DAILY_LIMIT:
        await message.answer(translations["limit_msg"][lang])
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
        if "cookies" in str(e).lower() or "Login" in str(e):
            await message.answer("🔒 Это видео требует входа в TikTok. Пожалуйста, отправьте cookies файл или выберите другое видео.")
        else:
            await message.answer(f"❌ Ошибка: {escape(str(e))}")
        return

    key = str(uuid.uuid4())
    url_storage[key] = url
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 480p", callback_data=f"video480|{key}"),
         InlineKeyboardButton(text="🎥 720p", callback_data=f"video720|{key}")],
        [InlineKeyboardButton(text="🎧 mp3", callback_data=f"audio|{key}")]
    ])
    await message.answer(f"<b>{title}</b>\n⏱ {mins}m {secs}s\n{translations['choose_format'][lang]}", reply_markup=keyboard)

async def handle_download(callback: CallbackQuery):
    await callback.answer()
    action, key = callback.data.split("|")
    url = url_storage.get(key)
    user_id = callback.from_user.id
    lang = get_lang(user_id)
    platform = user_platform.get(user_id, "ios")

    if not url:
        await bot.send_message(user_id, "⚠️ Ссылка устарела.")
        return

    await bot.send_message(user_id, "⏳ Загрузка...")
    file_id = str(uuid.uuid4())
    ext = ".mp3" if action == "audio" else ".mp4"
    filename = f"{file_id}{ext}"
    res = action.replace("video", "")

    video_codec = "libx264"
    audio_codec = "aac"

    ydl_opts = {
        "outtmpl": filename,
        "quiet": True,
        "ffmpeg_location": FFMPEG_PATH,
        "ignoreerrors": True,
    }

    if os.path.exists(COOKIES_PATH):
        ydl_opts["cookies"] = COOKIES_PATH

    if action == "audio":
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
            "merge_output_format": "mp4",
            "postprocessors": [{
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4"
            }],
            "postprocessor_args": [
                "-c:v", video_codec,
                "-c:a", audio_codec,
                "-movflags", "+faststart"
            ]
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

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
        await callback.message.delete()
    except yt_dlp.utils.DownloadError as e:
        if "cookies" in str(e).lower() or "Login" in str(e):
            await bot.send_message(user_id, "🔐 Это видео требует авторизацию. Обновите cookies-файл и попробуйте снова.")
        else:
            await bot.send_message(user_id, f"❌ Ошибка загрузки: {escape(str(e))}")
    except Exception as e:
        await bot.send_message(user_id, f"❌ Ошибка: {escape(str(e))}")

async def handle_document(message: types.Message):
    if message.document and "tiktok.com" in message.document.file_name:
        file = await bot.get_file(message.document.file_id)
        path = await bot.download_file(file.file_path)
        with open(COOKIES_PATH, "wb") as f:
            f.write(path.read())
        await message.answer("✅ cookies-файл сохранён. Можете отправить видео.")
    else:
        await message.answer("📁 Пожалуйста, отправьте файл cookies от TikTok.")

def main():
    dp.message.register(cmd_start, Command(commands=["start"]))
    dp.message.register(handle_link)
    dp.message.register(handle_document)
    dp.callback_query.register(handle_platform, lambda c: c.data.startswith("platform_"))
    dp.callback_query.register(handle_download, lambda c: c.data.startswith(("video480|", "video720|", "audio|")))

    print("✅ Бот запущен")
    asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    main()