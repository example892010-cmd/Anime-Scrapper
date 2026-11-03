from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from config import Txt
from helper.database import codeflixbots as db


def _is_enabled(value):
    # Older database records used boolean True; newer records use "On"/"Off".
    return value is True or str(value).strip().lower() in {"on", "true", "1", "yes"}


async def _metadata_text(user_id):
    enabled = _is_enabled(await db.get_metadata(user_id))
    values = {
        "Title": await db.get_title(user_id),
        "Author": await db.get_author(user_id),
        "Artist": await db.get_artist(user_id),
        "Audio": await db.get_audio(user_id),
        "Subtitle": await db.get_subtitle(user_id),
        "Video": await db.get_video(user_id),
    }
    lines = [
        f"**㊋ Metadata: {'ON ✅' if enabled else 'OFF ❌'}**",
        "",
    ]
    for label, value in values.items():
        value = value or "Not set"
        safe_value = str(value).replace("`", "'")
        lines.append(f"**◈ {label} ▹** `{safe_value}`")
    return "\n".join(lines), enabled


def _keyboard(enabled):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"On{' ✅' if enabled else ''}", callback_data="on_metadata"
            ),
            InlineKeyboardButton(
                f"Off{' ✅' if not enabled else ''}", callback_data="off_metadata"
            ),
        ],
        [InlineKeyboardButton("How to Set Metadata", callback_data="metainfo")],
    ])


@Client.on_message(filters.private & filters.command("metadata"))
async def metadata_command(client, message):
    if not message.from_user:
        return
    try:
        text, enabled = await _metadata_text(message.from_user.id)
        await message.reply_text(
            text,
            reply_markup=_keyboard(enabled),
            disable_web_page_preview=True,
        )
    except Exception:
        await message.reply_text(
            "❌ Could not load metadata settings. Please check the MongoDB connection "
            "and try /start once, then send /metadata again."
        )


@Client.on_callback_query(filters.regex(r"^(on_metadata|off_metadata|metainfo)$"))
async def metadata_callback(client, query: CallbackQuery):
    user_id = query.from_user.id
    action = query.data

    try:
        if action == "metainfo":
            await query.answer()
            await query.message.edit_text(
                Txt.META_TXT,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("Home", callback_data="home"),
                    InlineKeyboardButton("Close", callback_data="close"),
                ]]),
            )
            return

        await db.set_metadata(user_id, "On" if action == "on_metadata" else "Off")
        text, enabled = await _metadata_text(user_id)
        await query.answer("Metadata enabled" if enabled else "Metadata disabled")
        await query.message.edit_text(
            text,
            reply_markup=_keyboard(enabled),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        await query.answer("Could not update metadata. Check the database.", show_alert=True)


async def _save_value(message, setter, label):
    if len(message.command) < 2:
        await message.reply_text(
            f"Send a value after the command.\nExample: `/{message.command[0]} Your {label}`"
        )
        return
    value = message.text.split(maxsplit=1)[1].strip()
    if not value:
        await message.reply_text(f"Please provide a {label.lower()}.")
        return
    try:
        await setter(message.from_user.id, value)
        await message.reply_text(f"✅ {label} saved.")
    except Exception:
        await message.reply_text("❌ Could not save that value. Please check the database.")


@Client.on_message(filters.private & filters.command("settitle"))
async def set_title(client, message):
    await _save_value(message, db.set_title, "Title")


@Client.on_message(filters.private & filters.command("setauthor"))
async def set_author(client, message):
    await _save_value(message, db.set_author, "Author")


@Client.on_message(filters.private & filters.command("setartist"))
async def set_artist(client, message):
    await _save_value(message, db.set_artist, "Artist")


@Client.on_message(filters.private & filters.command("setaudio"))
async def set_audio(client, message):
    await _save_value(message, db.set_audio, "Audio title")


@Client.on_message(filters.private & filters.command("setsubtitle"))
async def set_subtitle(client, message):
    await _save_value(message, db.set_subtitle, "Subtitle title")


@Client.on_message(filters.private & filters.command("setvideo"))
async def set_video(client, message):
    await _save_value(message, db.set_video, "Video title")
