import os
import re
import time
import shutil
import asyncio
import logging
from datetime import datetime
from PIL import Image
from pyrogram import Client, filters
from plugins.antinsfw import check_anti_nsfw
from helper.utils import progress_for_pyrogram
from helper.database import codeflixbots

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
renaming_operations = {}

SEASON_EPISODE_PATTERNS = [
    (re.compile(r"S(\d+)(?:E|EP)(\d+)", re.I), ("season", "episode")),
    (re.compile(r"S(\d+)[\s-]*(?:E|EP)(\d+)", re.I), ("season", "episode")),
    (re.compile(r"Season\s*(\d+)\s*Episode\s*(\d+)", re.I), ("season", "episode")),
    (re.compile(r"\[S(\d+)\]\[E(\d+)\]", re.I), ("season", "episode")),
    (re.compile(r"(?:E|EP|Episode)\s*(\d+)", re.I), (None, "episode")),
]

QUALITY_PATTERNS = [
    (re.compile(r"\b(\d{3,4}[pi])\b", re.I), lambda m: m.group(1)),
    (re.compile(r"\b(4k|2160p)\b", re.I), lambda m: "4k"),
    (re.compile(r"\b(2k|1440p)\b", re.I), lambda m: "2k"),
    (re.compile(r"\b(HDRip|HDTV)\b", re.I), lambda m: m.group(1)),
]


def extract_season_episode(filename):
    for pattern, groups in SEASON_EPISODE_PATTERNS:
        match = pattern.search(filename)
        if match:
            season_group, episode_group = groups
            season = match.group(1) if season_group else None
            episode = match.group(2) if episode_group and season_group else match.group(1)
            return season, episode
    return None, None


def extract_quality(filename):
    for pattern, extractor in QUALITY_PATTERNS:
        match = pattern.search(filename)
        if match:
            return extractor(match)
    return "Unknown"


def metadata_is_enabled(value):
    # Existing user records may store True; the settings UI stores "On"/"Off".
    return value is True or str(value).strip().lower() in {"on", "true", "1", "yes"}


async def cleanup_files(*paths):
    for path in paths:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except Exception:
            logger.exception("Could not remove temporary file %s", path)


async def process_thumbnail(thumb_path):
    if not thumb_path or not os.path.exists(thumb_path):
        return None
    try:
        with Image.open(thumb_path) as img:
            img.convert("RGB").resize((320, 320)).save(thumb_path, "JPEG")
        return thumb_path
    except Exception:
        logger.exception("Thumbnail processing failed")
        await cleanup_files(thumb_path)
        return None


def get_ffmpeg_path():
    # Prefer a system FFmpeg install; fall back to the binary bundled by imageio-ffmpeg.
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        raise RuntimeError(
            "FFmpeg is unavailable. Install the imageio-ffmpeg dependency and redeploy."
        ) from exc


async def add_metadata(input_path, output_path, user_id):
    ffmpeg = get_ffmpeg_path()
    metadata = {
        "title": await codeflixbots.get_title(user_id),
        "artist": await codeflixbots.get_artist(user_id),
        "author": await codeflixbots.get_author(user_id),
        "video_title": await codeflixbots.get_video(user_id),
        "audio_title": await codeflixbots.get_audio(user_id),
        "subtitle": await codeflixbots.get_subtitle(user_id),
    }

    # -y prevents FFmpeg from failing when a previous attempt left the output file.
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-map", "0", "-c", "copy",
        "-metadata", f"title={metadata['title'] or ''}",
        "-metadata", f"artist={metadata['artist'] or ''}",
        "-metadata", f"author={metadata['author'] or ''}",
        "-metadata:s:v", f"title={metadata['video_title'] or ''}",
        "-metadata:s:a", f"title={metadata['audio_title'] or ''}",
        "-metadata:s:s", f"title={metadata['subtitle'] or ''}",
        "-loglevel", "error", output_path,
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "FFmpeg failed")


@Client.on_message(filters.private & (filters.document | filters.video | filters.audio))
async def auto_rename_files(client, message):
    user_id = message.from_user.id
    format_template = await codeflixbots.get_format_template(user_id)
    if not format_template:
        return await message.reply_text("Please set a rename format using /autorename")

    if message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name or "file"
        media_type = "document"
    elif message.video:
        file_id = message.video.file_id
        file_name = message.video.file_name or "video"
        media_type = "video"
    else:
        file_id = message.audio.file_id
        file_name = message.audio.file_name or "audio"
        media_type = "audio"

    if await check_anti_nsfw(file_name, message):
        return await message.reply_text("File could not be processed.")

    if file_id in renaming_operations:
        if (datetime.now() - renaming_operations[file_id]).total_seconds() < 10:
            return
    renaming_operations[file_id] = datetime.now()

    download_path = metadata_path = thumb_path = None
    status = None
    try:
        season, episode = extract_season_episode(file_name)
        quality = extract_quality(file_name)
        replacements = {
            "{season}": season or "XX",
            "{episode}": episode or "XX",
            "{quality}": quality,
            "Season": season or "XX",
            "Episode": episode or "XX",
            "QUALITY": quality,
        }
        for placeholder, value in replacements.items():
            format_template = format_template.replace(placeholder, value)

        ext = os.path.splitext(file_name)[1] or (
            ".mp4" if media_type == "video" else ".mp3" if media_type == "audio" else ".mkv"
        )
        new_filename = f"{format_template}{ext}"
        os.makedirs("downloads", exist_ok=True)
        os.makedirs("metadata", exist_ok=True)
        download_path = os.path.join("downloads", new_filename)
        metadata_path = os.path.join("metadata", new_filename)

        status = await message.reply_text("**Downloading...**")
        file_path = await client.download_media(
            message,
            file_name=download_path,
            progress=progress_for_pyrogram,
            progress_args=("Downloading...", status, time.time()),
        )

        metadata_setting = await codeflixbots.get_metadata(user_id)
        if metadata_is_enabled(metadata_setting):
            await status.edit("**Applying metadata...**")
            await add_metadata(file_path, metadata_path, user_id)
            file_path = metadata_path
        else:
            await status.edit("**Metadata is OFF — keeping the original media streams...**")

        await status.edit("**Preparing upload...**")
        caption = await codeflixbots.get_caption(user_id) or f"**{new_filename}**"
        thumb = await codeflixbots.get_thumbnail(user_id)
        if thumb:
            thumb_path = await client.download_media(thumb)
        elif media_type == "video" and message.video.thumbs:
            thumb_path = await client.download_media(message.video.thumbs[0].file_id)
        thumb_path = await process_thumbnail(thumb_path)

        await status.edit("**Uploading...**")
        upload_params = {
            "chat_id": message.chat.id,
            "caption": caption,
            "thumb": thumb_path,
            "progress": progress_for_pyrogram,
            "progress_args": ("Uploading...", status, time.time()),
        }
        if media_type == "document":
            await client.send_document(document=file_path, **upload_params)
        elif media_type == "video":
            await client.send_video(video=file_path, **upload_params)
        else:
            await client.send_audio(audio=file_path, **upload_params)

        await status.delete()
    except Exception as exc:
        logger.exception("File rename/metadata processing failed")
        error_text = str(exc)
        if status:
            try:
                await status.edit(f"❌ Processing failed:\n`{error_text[:2500]}`")
            except Exception:
                await message.reply_text(f"❌ Processing failed:\n`{error_text[:2500]}`")
        else:
            await message.reply_text(f"❌ Processing failed:\n`{error_text[:2500]}`")
    finally:
        await cleanup_files(download_path, metadata_path, thumb_path)
        renaming_operations.pop(file_id, None)
