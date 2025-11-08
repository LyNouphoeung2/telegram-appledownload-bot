import os
import asyncio
import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Optional, Tuple

import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

# Configure logging for better debugging and monitoring
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# --- NO CHANGE NEEDED HERE ---
# The code correctly loads your token from the server's environment variables.
# Go to your Koyeb "Secrets" and set BOT_TOKEN to your new token.
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    logger.critical("FATAL: BOT_TOKEN environment variable is not set.")
    exit()
# ----------------------------------------------------

# Telegram bot API file size limit (50 MB for videos and documents sent by bots)
FILE_SIZE_LIMIT_MB = 50

# Permanent download directory for large files
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# Define the callback data prefix for audio downloads
AUDIO_CALLBACK_PREFIX = "download_audio|"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message when the /start command is issued."""
    await update.message.reply_text(
        "Send me a video URL from YouTube, TikTok, or Facebook, and I'll download it!"
    )


def run_download_blocking(
    url: str, temp_dir: str, loop, context, chat_id, message_id
) -> Tuple[Optional[Path], dict]:
    """
    Synchronous function to run yt_dlp in a separate thread.
    Tries to get pre-merged MP4 formats to avoid FFmpeg.
    Includes a progress hook to update the user.
    """
    temp_path = Path(temp_dir)
    last_update_time = 0
    last_percent = -1

    def progress_hook(d):
        """Hook to send progress updates back to the async loop."""
        nonlocal last_update_time, last_percent
        if d['status'] == 'downloading':
            current_time = time.time()
            percent_str = d.get('_percent_str')
            if not percent_str:
                return  # No percentage string available

            try:
                percent = float(percent_str.strip().replace('%', ''))
            except ValueError:
                percent = 0.0  # Default on parsing error

            # Throttle updates: 2.5 seconds or >10% change
            if current_time - last_update_time > 2.5 or abs(percent - last_percent) > 10:
                text = f"Download in progress... {percent_str} ⏳"
                try:
                    # Schedule the coroutine on the main event loop
                    coro = context.bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id, text=text
                    )
                    asyncio.run_coroutine_threadsafe(coro, loop)
                    last_update_time = current_time
                    last_percent = percent
                except Exception as e:
                    logger.warning(f"Error sending progress update: {e}")

    ydl_opts = {
        # Try to get the best pre-merged MP4 (up to 720p) to avoid FFmpeg merge
        "format": "best[ext=mp4][height<=720]/best[ext=mp4]/best",
        "outtmpl": str(temp_path / "%(id)s.%(ext)s"),
        "paths": {"home": temp_dir, "temp": temp_dir},
        "no_merge": True,  # Explicitly disable merging
        "progress_hooks": [progress_hook], # Add the hook
        # 'quiet': True, # Removed to allow progress hook to work reliably
        # 'no_warnings': True, # Removed
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        # Find the actual downloaded file
        downloaded_files = list(temp_path.glob(f"{info['id']}*"))

        # Prioritize mp4 or common video extensions
        video_file = next((f for f in downloaded_files if f.suffix.lower() in ['.mp4', '.mkv', '.webm']), None)

        if not video_file:
            prepared_fn = temp_path / ydl.prepare_filename(info)
            if prepared_fn.exists():
                video_file = prepared_fn
            else:
                logger.warning("No suitable pre-merged file was downloaded.")
                raise FileNotFoundError("Could not find a pre-merged video file. The video might require merging (FFmpeg).")

        return video_file, info


async def download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download the video from the provided URL and send it back to the user."""
    url = update.message.text.strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("Please send a valid URL starting with http:// or https://.")
        return

    # 1. Initial status message
    status_message = await update.message.reply_text("Fetching video details and preparing download... 🔄")

    temp_dir = None
    video_file = None
    info = None

    try:
        temp_dir = tempfile.mkdtemp()
        loop = asyncio.get_event_loop()

        # 2. Update status and execute blocking download in a separate thread
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text="Download starting... 0% ⏳",
        )

        # Run the synchronous download function asynchronously
        video_file, info = await asyncio.to_thread(
            run_download_blocking,
            url,
            temp_dir,
            loop,
            context,
            status_message.chat_id,
            status_message.message_id
        )

        # 3. Post-download processing and file size check
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text="Download finished. Checking file size... ✅",
        )

        file_size_mb = video_file.stat().st_size / (1024 * 1024)
        title = info.get('title', 'Video Download')

        # 4. Handle Telegram size limit
        if file_size_mb <= FILE_SIZE_LIMIT_MB:
            logger.info(f"Sending video: {video_file} (size: {file_size_mb:.2f} MB)")

            with open(video_file, "rb") as f:
                # Send the video
                sent_message = await update.message.reply_video(
                    video=f,
                    caption=f"✅ **{title}**\n\n*Size: {file_size_mb:.2f} MB*",
                    parse_mode=ParseMode.MARKDOWN,
                    supports_streaming=True,
                    read_timeout=100,
                    write_timeout=100,
                )

            # Delete the temporary status message
            await context.bot.delete_message(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id
            )

            # 5. Add Audio Download Button
            callback_data = f"{AUDIO_CALLBACK_PREFIX}{url}"
            keyboard = [[
                InlineKeyboardButton("🎧 Download as Voice Message", callback_data=callback_data)
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Edit the sent video message to include the audio button
            await sent_message.edit_reply_markup(reply_markup=reply_markup)

        else:
            # For larger files, move to permanent download directory
            permanent_path = DOWNLOAD_DIR / video_file.name
            shutil.move(video_file, permanent_path)

            await update.message.reply_text(
                f"Video is too large to send ({file_size_mb:.2f} MB, Max: {FILE_SIZE_LIMIT_MB} MB).\n"
                f"File saved locally to the bot server at: `{permanent_path.resolve()}`"
            )
            # Delete the temporary status message
            await context.bot.delete_message(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id
            )

    except yt_dlp.utils.DownloadError as e:
        logger.error(f"DownloadError: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text="❌ Error downloading video. This might be because the video requires merging (FFmpeg) which is not supported, or the URL is invalid."
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=f"❌ An unexpected error occurred: {str(e)}. Please try again."
        )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"Cleanup of temp directory: {temp_dir}")


def run_audio_download_blocking(url: str, temp_dir: str) -> Tuple[Path, dict]:
    """
    Synchronous function to run yt_dlp to extract audio.
    Converts to Ogg/Opus for use as a Telegram Voice Message.
    REQUIRES FFMPEG.
    """
    temp_path = Path(temp_dir)
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": str(temp_path / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "paths": {"home": temp_dir, "temp": temp_dir},
        # Add postprocessor to convert to Ogg/Opus
        "postprocessors": [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'opus', # Opus codec for voice messages
            'preferredquality': '64',  # Quality for voice
        }],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

        # Find the actual downloaded file (should be .opus)
        audio_file = next((f for f in temp_path.glob(f"{info['id']}*") if f.suffix.lower() in ['.opus', '.ogg']), None)

        if not audio_file or not audio_file.exists():
            # Fallback to the prepared filename
            audio_file = temp_path / ydl.prepare_filename(info)
            # Yt-dlp might name it .opus
            if not audio_file.exists() and audio_file.suffix != ".opus":
                 audio_file = audio_file.with_suffix(".opus")
            
            if not audio_file.exists():
                raise FileNotFoundError("Downloaded audio file (.opus) not found. Check if 'ffmpeg' is installed.")

        return audio_file, info


async def download_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the callback query for downloading the audio as a voice message."""
    query = update.callback_query
    await query.answer("Starting audio conversion for voice message...")

    # Extract URL from callback data
    try:
        url = query.data.split(AUDIO_CALLBACK_PREFIX, 1)[1]
    except IndexError:
        await query.edit_message_caption(caption="Error: Could not retrieve URL.", parse_mode=ParseMode.MARKDOWN)
        return

    # Acknowledge and update the message with status
    status_message = await query.message.reply_text("Converting to voice message format... 🎧 (Requires ffmpeg)")

    temp_dir = None
    audio_file = None

    try:
        temp_dir = tempfile.mkdtemp()
        # Run the synchronous audio download function asynchronously
        audio_file, info = await asyncio.to_thread(run_audio_download_blocking, url, temp_dir)

        file_size_mb = audio_file.stat().st_size / (1024 * 1024)
        title = info.get('title', 'Audio Track')

        if file_size_mb <= FILE_SIZE_LIMIT_MB:
            await context.bot.edit_message_text(
                chat_id=status_message.chat_id,
                message_id=status_message.message_id,
                text="Sending voice message... 📤",
            )

            with open(audio_file, "rb") as f:
                # Send as voice message
                await query.message.reply_voice(
                    voice=f,
                    caption=f"🎧 **{title}** (Voice)\n\n*Size: {file_size_mb:.2f} MB*",
                    parse_mode=ParseMode.MARKDOWN,
                    read_timeout=100,
                    write_timeout=100,
                )
        else:
            await query.message.reply_text(
                f"Audio file is also too large ({file_size_mb:.2f} MB) to send via Telegram API."
            )

        # Remove the intermediate status message
        await context.bot.delete_message(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id
        )

    except Exception as e:
        logger.error(f"Audio download error: {str(e)}")
        await context.bot.edit_message_text(
            chat_id=status_message.chat_id,
            message_id=status_message.message_id,
            text=f"❌ An error occurred. This may be due to a missing 'ffmpeg' (required for voice conversion). Error: {str(e)}"
        )
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)


# --- CHANGE 2: SIMPLIFY THE MAIN FUNCTION ---
def main() -> None:
    """Initialize and run the Telegram bot."""
    # Use a relative directory. This will be ephemeral on Koyeb.
    global DOWNLOAD_DIR
    DOWNLOAD_DIR = Path("downloads")
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    logger.info(f"Using download directory: {DOWNLOAD_DIR.resolve()}")
    
    application = Application.builder().token(BOT_TOKEN).build()

    # Register command, message, and callback handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, download_and_send))
    # Handler for the "Download Audio" button click
    application.add_handler(CallbackQueryHandler(download_audio, pattern=f"^{AUDIO_CALLBACK_PREFIX}"))

    # Start polling for updates
    logger.info("Starting bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)
# ----------------------------------------------


if __name__ == "__main__":
    main()