import os
import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple, List

import yt_dlp
from telegram import Update, InputMediaPhoto
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# កំណត់កម្រិត logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- កំណត់តម្លៃថេរ ដើម្បីងាយស្រួលកែប្រែ ---

# Bot Token (ផ្លាស់ប្តូរនៅទីនេះបើចាំបាច់)
BOT_TOKEN_ENV = "BOT_TOKEN"

# កំណត់ទំហំឯកសារអតិបរមា (MB)
FILE_SIZE_LIMIT_MB = 50

# ផ្លូវទៅកាន់ ffmpeg (សម្រាប់ Koyeb ឬស្រដៀងគ្នា)
FFMPEG_PATH = "/usr/bin/ffmpeg"

# ចំណងជើងសម្រាប់វីដេអូ ឬរូបភាព (ប្រើ HTML ដើម្បីធ្វើឲ្យ @username អាចចុចបាន)
BOT_CAPTION = "ដោនឡូតវីដេអូដោយ <a href=\"https://t.me/Apple_Downloader_bot\">@Apple_Downloader_bot</a>"

# វេទិកាដែលគាំទ្រ (តែ TikTok តែប៉ុណ្ណោះ)
SUPPORTED_PLATFORMS = ['tiktok']

# សារស្វាគមន៍សម្រាប់ /start
WELCOME_MESSAGE = "សូមផ្ញើ Link video TikTok មកខ្ញុំ💚 ខ្ញុំនឹងទាញយកវីដេអូយ៉ាងច្បាស់ជូនអ្នក!"

# សារប្រាប់ថាតំណមិនត្រឹមត្រូវ
INVALID_URL_MESSAGE = "សូមផ្ញើតំណដែលត្រឹមត្រូវចាប់ផ្តើមដោយ http:// ឬ https://។"

# សារប្រាប់ថាមិនគាំទ្រវេទិកា
UNSUPPORTED_PLATFORM_MESSAGE = "សូមអភ័យទោស ខ្ញុំអាចទាញយកបានតែវីដេអូ TikTok ប៉ុណ្ណោះ"

# សារស្ថានភាព
FETCHING_INFO_MESSAGE = "កំពុងទាញយកព័ត៌មានវីដេអូ... 🔄"
DOWNLOAD_START_MESSAGE = "កំពុងចាប់ផ្តើមទាញយក... 0% ⏳"
DOWNLOAD_FINISHED_MESSAGE = "ទាញយករួចរាល់។ កំពុងផ្ញើ... ✅"

# សារជោគជ័យសម្រាប់វីដេអូ
VIDEO_SUCCESS_MESSAGE = "វីដេអូមានគុណភាពខ្ពស់របស់អ្នកត្រូវបានទាញយកជោគជ័យហើយ💚💚"

# សារជោគជ័យសម្រាប់រូបភាព
IMAGE_SUCCESS_MESSAGE = "រូបភាពមានគុណភាពខ្ពស់របស់អ្នកត្រូវបានទាញយកជោគជ័យហើយ💚💚"

# សារស្នើសុំតំណបន្ទាប់
NEXT_DOWNLOAD_MESSAGE = "បើអ្នកចង់ទាញយកវីដេអូផ្សេងទៀត សូមផ្ញើរ Link មកខ្ញុំ💚💚"

# សារសម្រាប់ឯកសារធំពេក
FILE_TOO_LARGE_MESSAGE = "✅ ទាញយករួចរាល់ ប៉ុន្តែឯកសារធំពេកដើម្បីផ្ញើ។\n\n**ទំហំ:** {size:.2f} MB\n**កំណត់:** {limit} MB\n\nឯកសារត្រូវបានរក្សាទុកនៅលើម៉ាស៊ីនមេរបស់បូត (កន្លែងផ្ទុកគឺបណ្តោះអាសន្ន)។"

# សារកំហុសទូទៅ
DEFAULT_ERROR_MESSAGE = "❌ កំហុសក្នុងការទាញយកវីដេអូ។ តំណអាចជាឯកជន ឬមិនត្រឹមត្រូវ។"
BLOCKED_ERROR_MESSAGE = "❌ TikTok កំពុងរារាំងការទាញយក។ សូមព្យាយាមវីដេអូផ្សេង ឬរង់ចាំបន្តិច។"
PRIVATE_ERROR_MESSAGE = "❌ វីដេអូនេះជាឯកជន មានកំណត់អាយុ ឬមិនអាចប្រើបាន។ សូមព្យាយាមវីដេអូផ្សេង។"
RATE_LIMIT_ERROR_MESSAGE = "❌ ត្រូវបានកំណត់អត្រាដោយ TikTok។ សូមរង់ចាំ 5-10 នាទី ហើយព្យាយាមម្តងទៀត។"
UNEXPECTED_ERROR_MESSAGE = "❌ កំហុសមិនរំពឹងទុកបានកើតឡើង: {error}។ សូមព្យាយាមម្តងទៀត។"

# ទ្រង់ទ្រាយសម្រាប់វីដេអូ (កែប្រែគុណភាពនៅទីនេះ)
VIDEO_FORMAT = 'bestvideo[height>=1080][fps>=30][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>=720][fps>=30][ext=mp4]+bestaudio[ext=m4a]/best'

# --- មុខងារដើម ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(WELCOME_MESSAGE)


def run_download_blocking(
    url: str, temp_dir: str, loop, context, chat_id, message_id
) -> Tuple[Optional[Path], List[Path], dict]:
    temp_path = Path(temp_dir)
    last_update_time = 0
    last_percent = -1

    def progress_hook(d):
        nonlocal last_update_time, last_percent
        if d['status'] == 'downloading':
            current_time = time.time()
            percent_str = d.get('_percent_str')
            if not percent_str:
                return

            try:
                percent = float(percent_str.strip().replace('%', ''))
            except ValueError:
                percent = 0.0

            if current_time - last_update_time > 2.5 or abs(percent - last_percent) > 10:
                text = f"កំពុងទាញយក... {percent_str} ⏳"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    last_update_time = current_time
                    last_percent = percent
                except Exception as e:
                    logger.warning(f"កំហុសក្នុងការផ្ញើការធ្វើបច្ចុប្បន្នភាពវឌ្ឍនភាព: {e}")
        
        elif d['status'] == 'finished':
            if d.get('postprocessor') == 'Merger':
                text = "ទាញយករួចរាល់។ កំពុងបញ្ចូលវីដេអូនិងសំឡេង... 🔄"
                try:
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except Exception as e:
                    logger.warning(f"កំហុសក្នុងការផ្ញើការធ្វើបច្ចុប្បន្នភាពបញ្ចូល: {e}")

    common_opts = {
        'nocheckcertificate': True,
        'quiet': True,
        'no_warnings': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'extractor_retries': 5,
        'retry_sleep': 5,
        'sleep_interval': 1,
        'max_sleep_interval': 5,
        'socket_timeout': 30,
        'fragment_retries': 10,
        'paths': {"home": temp_dir, "temp": temp_dir},
    }

    ydl_opts_info = common_opts.copy()
    with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
        time.sleep(2)
        info = ydl.extract_info(url, download=False)

    is_video = any(f.get('vcodec', 'none') != 'none' for f in info.get('formats', []))

    if is_video:
        ydl_opts = common_opts.copy()
        ydl_opts.update({
            'format': VIDEO_FORMAT,
            'outtmpl': str(temp_path / "%(id)s.%(ext)s"),
            'ffmpeg_location': FFMPEG_PATH,
            'progress_hooks': [progress_hook],
            'postprocessors': [{
                'key': 'FFmpegVideoRemuxer',
                'preferedformat': 'mp4',
            }],
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        video_files = list(temp_path.glob('*.mp4'))
        video_file = video_files[0] if video_files else None
        images = []

    else:
        ydl_opts = common_opts.copy()
        ydl_opts.update({
            'outtmpl': str(temp_path / "%(id)s.%(ext)s"),
            'write_all_thumbnails': True,
            'skip_download': True,
            'progress_hooks': [progress_hook],
        })
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        images = list(temp_path.glob('*.jpg')) + list(temp_path.glob('*.jpeg')) + list(temp_path.glob('*.png'))
        images.sort(key=lambda p: p.name)
        video_file = None

    if video_file is None and not images:
        raise FileNotFoundError("No video or images found after download")

    return video_file, images, info


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text(INVALID_URL_MESSAGE)
        return

    lower_url = url.lower()
    if not any(platform in lower_url for platform in SUPPORTED_PLATFORMS):
        await update.message.reply_text(UNSUPPORTED_PLATFORM_MESSAGE)
        return

    status_message = await update.message.reply_text(FETCHING_INFO_MESSAGE)

    temp_dir = None
    video_file = None
    images = []
    info = None

    try:
        temp_dir = tempfile.mkdtemp()
        loop = asyncio.get_event_loop()

        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=DOWNLOAD_START_MESSAGE,
        )

        video_file, images, info = await asyncio.to_thread(
            run_download_blocking,
            url,
            temp_dir,
            loop,
            context,
            status_message.chat_id,
            status_message.message_id
        )

        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=DOWNLOAD_FINISHED_MESSAGE,
        )

        if video_file:
            file_size_mb = video_file.stat().st_size / (1024 * 1024)

            if file_size_mb <= FILE_SIZE_LIMIT_MB:
                logger.info(f"កំពុងផ្ញើវីដេអូ: {video_file} (ទំហំ: {file_size_mb:.2f} MB)")

                await update.message.reply_text(VIDEO_SUCCESS_MESSAGE)

                with open(video_file, "rb") as f:
                    await update.message.reply_video(
                        video=f,
                        caption=BOT_CAPTION,
                        parse_mode=ParseMode.HTML,
                        supports_streaming=True,
                        read_timeout=100,
                        write_timeout=100,
                    )

                await update.message.reply_text(NEXT_DOWNLOAD_MESSAGE)
            
            else:
                permanent_path = DOWNLOAD_DIR / video_file.name
                shutil.move(video_file, permanent_path)

                await update.message.reply_text(
                    FILE_TOO_LARGE_MESSAGE.format(size=file_size_mb, limit=FILE_SIZE_LIMIT_MB),
                    parse_mode=ParseMode.MARKDOWN
                )

        elif images:
            await update.message.reply_text(IMAGE_SUCCESS_MESSAGE)

            media_group = []
            for i, img_path in enumerate(images):
                caption = BOT_CAPTION if i == 0 else None
                media_group.append(InputMediaPhoto(open(img_path, 'rb'), caption=caption, parse_mode=ParseMode.HTML if caption else None))

            await update.message.reply_media_group(media=media_group)

            await update.message.reply_text(NEXT_DOWNLOAD_MESSAGE)

        await context.bot.delete_message(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id
        )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"DownloadError: {str(e)}")
        error_text = DEFAULT_ERROR_MESSAGE
        error_msg = str(e).lower()
        if "confirm you're not a bot" in error_msg:
            error_text = BLOCKED_ERROR_MESSAGE
        elif "private video" in error_msg or "unavailable" in error_msg:
            error_text = PRIVATE_ERROR_MESSAGE
        elif "rate limit" in error_msg or "too many requests" in error_msg:
            error_text = RATE_LIMIT_ERROR_MESSAGE
            
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=error_text
        )
    except Exception as e:
        logger.error(f"កំហុសមិនរំពឹងទុក: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=UNEXPECTED_ERROR_MESSAGE.format(error=str(e))
        )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"សម្អាតថតបណ្តោះអាសន្ន: {temp_dir}")


def main() -> None:
    global DOWNLOAD_DIR
    DOWNLOAD_DIR = Path("downloads")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    logger.info(f"ប្រើថតទាញយក: {DOWNLOAD_DIR.resolve()}")
    
    application = Application.builder().token(os.environ.get(BOT_TOKEN_ENV)).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send))

    logger.info("កំពុងចាប់ផ្តើម bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
