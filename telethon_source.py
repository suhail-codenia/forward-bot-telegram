"""
Telegram user-client listener (Telethon): reads the source channel without the bot
being a member. The bot account still posts to targets via the Bot API.

Requires: TELEGRAM_API_ID, TELEGRAM_API_HASH, Telethon session file (first login
interactive or TELEGRAM_PHONE + code flow). User account must see the source channel.

Env: SOURCE_LISTENER=telethon to enable (see bot.py).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
)
from telethon import TelegramClient, events
from telethon.tl.types import (
    DocumentAttributeFilename,
    MessageMediaPoll,
    MessageService,
)

from mirror_actions import delete_mirrored_posts
from mirror_db import MirrorDB

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)
DEBUG_LOG_PATH = Path("/Users/codenia-tb/Projects/telegram-bot/.cursor/debug-d48042.log")


# region agent log
def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    try:
        payload = {
            "sessionId": "d48042",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


# endregion

ALBUM_DEBOUNCE_SEC = 0.45

_album_buf: dict[int, list[Any]] = {}
_album_lock = asyncio.Lock()
_album_flush_tasks: dict[int, asyncio.Task[Any]] = {}


def use_telethon_source() -> bool:
    return os.getenv("SOURCE_LISTENER", "bot").strip().lower() in (
        "telethon",
        "user",
        "mtproto",
    )


def telethon_configured() -> bool:
    aid = os.getenv("TELEGRAM_API_ID", "").strip()
    ah = os.getenv("TELEGRAM_API_HASH", "").strip()
    try:
        return bool(ah and int(aid) > 0)
    except ValueError:
        return False


def _source_chat_matches(event_chat_id: int | None, source_chat_id: int | None) -> bool:
    if source_chat_id is None or event_chat_id is None:
        return False
    try:
        return int(event_chat_id) == int(source_chat_id)
    except (TypeError, ValueError):
        return False


async def _download_bytes(client: TelegramClient, msg: Any) -> bytes | None:
    try:
        buf = io.BytesIO()
        await client.download_media(msg, file=buf)
        buf.seek(0)
        data = buf.read()
        return data if data else None
    except Exception:
        logger.exception("download_media failed for msg id=%s", getattr(msg, "id", "?"))
        return None


def _doc_filename(msg: Any) -> str:
    doc = msg.document
    if not doc:
        return "file.bin"
    for attr in doc.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name or "file.bin"
    return "file.bin"


async def _mirror_single_telethon(
    client: TelegramClient,
    bot: Bot,
    msg: Any,
    db: MirrorDB,
) -> None:
    """Mirror one Telethon message to all targets (download + Bot API send)."""
    if isinstance(msg, MessageService):
        return
    if msg.media and isinstance(msg.media, MessageMediaPoll):
        logger.warning("Skipping poll (msg id=%s)", msg.id)
        return

    targets = await db.list_targets()
    if not targets:
        logger.info("No targets — skip Telethon mirror msg=%s", msg.id)
        return

    src_id = msg.id

    for target_chat_id, _title in targets:
        try:
            sent = await _send_one_telethon_to_target(client, bot, msg, target_chat_id)
            if sent is not None:
                await db.set_mapping(src_id, target_chat_id, sent)
                logger.info(
                    "Telethon mirrored src=%s -> tgt=%s:%s",
                    src_id,
                    target_chat_id,
                    sent,
                )
        except Exception:
            logger.exception(
                "Telethon mirror failed src=%s tgt=%s",
                src_id,
                target_chat_id,
            )


async def _send_one_telethon_to_target(
    client: TelegramClient,
    bot: Bot,
    msg: Any,
    target_chat_id: int,
) -> int | None:
    caption = msg.message or None

    if msg.photo:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        m = await bot.send_photo(
            chat_id=target_chat_id,
            photo=BufferedInputFile(data, filename="photo.jpg"),
            caption=caption,
        )
        return m.message_id

    if msg.video:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        m = await bot.send_video(
            chat_id=target_chat_id,
            video=BufferedInputFile(data, filename="video.mp4"),
            caption=caption,
        )
        return m.message_id

    if msg.voice:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        m = await bot.send_voice(
            chat_id=target_chat_id,
            voice=BufferedInputFile(data, filename="voice.ogg"),
            caption=caption,
        )
        return m.message_id

    if msg.audio:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        fname = _doc_filename(msg) if msg.document else "audio.mp3"
        m = await bot.send_audio(
            chat_id=target_chat_id,
            audio=BufferedInputFile(data, filename=fname),
            caption=caption,
        )
        return m.message_id

    if getattr(msg, "sticker", None):
        data = await _download_bytes(client, msg)
        if not data:
            return None
        m = await bot.send_sticker(
            chat_id=target_chat_id,
            sticker=BufferedInputFile(data, filename="sticker.webp"),
        )
        return m.message_id

    if msg.document:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        fname = _doc_filename(msg)
        m = await bot.send_document(
            chat_id=target_chat_id,
            document=BufferedInputFile(data, filename=fname),
            caption=caption,
        )
        return m.message_id

    if msg.video_note:
        data = await _download_bytes(client, msg)
        if not data:
            return None
        m = await bot.send_video_note(
            chat_id=target_chat_id,
            video_note=BufferedInputFile(data, filename="note.mp4"),
        )
        return m.message_id

    text = msg.message or ""
    if text or not msg.media:
        m = await bot.send_message(chat_id=target_chat_id, text=text)
        return m.message_id

    logger.warning("Unhandled Telethon media msg id=%s — skip", msg.id)
    return None


async def _mirror_album_telethon(
    client: TelegramClient,
    bot: Bot,
    messages: list[Any],
    db: MirrorDB,
) -> None:
    messages = sorted(messages, key=lambda m: m.id)
    targets = await db.list_targets()
    if not targets:
        return

    medias: list[Any] = []
    src_ids: list[int] = []
    for i, m in enumerate(messages):
        if isinstance(m, MessageService):
            continue
        if m.media and isinstance(m.media, MessageMediaPoll):
            continue
        data = await _download_bytes(client, m)
        if not data:
            continue
        cap = m.message if i == 0 else None
        fname = f"m{i}.dat"
        if m.photo:
            medias.append(
                InputMediaPhoto(
                    media=BufferedInputFile(data, filename=f"p{i}.jpg"),
                    caption=cap,
                )
            )
            src_ids.append(m.id)
        elif m.video:
            medias.append(
                InputMediaVideo(
                    media=BufferedInputFile(data, filename=f"v{i}.mp4"),
                    caption=cap,
                )
            )
            src_ids.append(m.id)
        elif m.document:
            fname = _doc_filename(m)
            medias.append(
                InputMediaDocument(
                    media=BufferedInputFile(data, filename=fname),
                    caption=cap,
                )
            )
            src_ids.append(m.id)
        else:
            logger.debug("Album piece skipped (unsupported) id=%s", m.id)

    if not medias:
        logger.warning("Album empty after processing — ids %s", [m.id for m in messages])
        return

    for target_chat_id, _title in targets:
        try:
            if len(medias) == 1:
                solo = next(m for m in messages if m.id == src_ids[0])
                mid = await _send_one_telethon_to_target(
                    client, bot, solo, target_chat_id
                )
                if mid is not None:
                    await db.set_mapping(solo.id, target_chat_id, mid)
                continue

            sent_msgs = await bot.send_media_group(
                chat_id=target_chat_id,
                media=medias,
            )
            for sid, dst in zip(src_ids, sent_msgs):
                await db.set_mapping(sid, target_chat_id, dst.message_id)
            logger.info(
                "Telethon album -> tgt=%s (%s parts)",
                target_chat_id,
                len(sent_msgs),
            )
        except Exception:
            logger.exception(
                "Telethon album mirror failed tgt=%s",
                target_chat_id,
            )


async def _flush_album_group(
    grouped_id: int,
    client: TelegramClient,
    bot: Bot,
    db: MirrorDB,
    runtime: Any,
) -> None:
    await asyncio.sleep(ALBUM_DEBOUNCE_SEC)
    async with _album_lock:
        msgs = _album_buf.pop(grouped_id, [])
        _album_flush_tasks.pop(grouped_id, None)
    if not msgs:
        return
    if runtime.source_chat_id is None:
        return
    await _mirror_album_telethon(client, bot, msgs, db)


async def _schedule_album_flush(
    grouped_id: int,
    client: TelegramClient,
    bot: Bot,
    db: MirrorDB,
    runtime: Any,
) -> None:
    async def _run():
        await _flush_album_group(grouped_id, client, bot, db, runtime)

    async with _album_lock:
        old = _album_flush_tasks.pop(grouped_id, None)
        if old:
            old.cancel()
        _album_flush_tasks[grouped_id] = asyncio.create_task(_run())


async def _edit_telethon_message(bot: Bot, msg: Any, db: MirrorDB) -> None:
    mappings = await db.get_mappings(msg.id)
    if not mappings:
        return
    text = msg.message or ""
    has_media = bool(
        msg.photo
        or msg.video
        or msg.document
        or msg.audio
        or msg.voice
        or msg.video_note
        or getattr(msg, "sticker", None)
    )
    for target_chat_id, target_msg_id in mappings:
        try:
            if has_media:
                await bot.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    caption=text,
                )
            else:
                await bot.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    text=text,
                )
            logger.info(
                "Telethon edit mirrored src=%s -> tgt=%s:%s",
                msg.id,
                target_chat_id,
                target_msg_id,
            )
        except Exception:
            logger.exception(
                "Telethon edit failed src=%s tgt=%s:%s",
                msg.id,
                target_chat_id,
                target_msg_id,
            )


async def run_telethon_listener(bot: Bot, db: MirrorDB, runtime: Any) -> None:
    """Connect Telethon and process source-channel updates until disconnect."""
    run_id = f"run-{int(time.time())}"
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
    session_path = os.getenv("TELETHON_SESSION", "telethon.session").strip()
    session_exists = Path(session_path).exists()
    # region agent log
    _debug_log(
        run_id,
        "H2_H4_H5",
        "telethon_source.py:run_telethon_listener:config",
        "telethon listener config snapshot",
        {
            "api_id_present": bool(api_id),
            "api_hash_present": bool(api_hash),
            "session_path": session_path,
            "session_exists": session_exists,
            "phone_present": bool(os.getenv("TELEGRAM_PHONE")),
            "password_present": bool(os.getenv("TELEGRAM_PASSWORD")),
        },
    )
    # endregion

    if not api_id or not api_hash:
        # region agent log
        _debug_log(
            run_id,
            "H2",
            "telethon_source.py:run_telethon_listener:missing_config",
            "telethon missing API config",
            {},
        )
        # endregion
        logger.error("Telethon mode requires TELEGRAM_API_ID and TELEGRAM_API_HASH")
        return

    client = TelegramClient(session_path, api_id, api_hash)

    # region agent log
    _debug_log(
        run_id,
        "H2_H4_H5",
        "telethon_source.py:run_telethon_listener:before_start",
        "calling Telethon client.start",
        {},
    )
    # endregion
    try:
        await client.start(
            phone=os.getenv("TELEGRAM_PHONE"),
            password=os.getenv("TELEGRAM_PASSWORD"),
        )
    except Exception as exc:
        # region agent log
        _debug_log(
            run_id,
            "H2_H4_H5",
            "telethon_source.py:run_telethon_listener:start_exception",
            "Telethon client.start failed",
            {"exc_type": type(exc).__name__, "exc": str(exc)},
        )
        # endregion
        raise

    logger.info("Telethon user session connected (session=%s)", session_path)
    # region agent log
    _debug_log(
        run_id,
        "H2_H4",
        "telethon_source.py:run_telethon_listener:connected",
        "Telethon user session connected",
        {"session_path": session_path},
    )
    # endregion

    @client.on(events.NewMessage)
    async def on_new(event: Any) -> None:
        if not _source_chat_matches(event.chat_id, runtime.source_chat_id):
            return
        msg = event.message
        if isinstance(msg, MessageService):
            return
        if msg.media and isinstance(msg.media, MessageMediaPoll):
            logger.warning("Skipping poll id=%s", msg.id)
            return

        gid = msg.grouped_id
        if gid is not None:
            async with _album_lock:
                _album_buf.setdefault(gid, []).append(msg)
            await _schedule_album_flush(gid, client, bot, db, runtime)
            return

        await _mirror_single_telethon(client, bot, msg, db)

    @client.on(events.MessageEdited)
    async def on_edit(event: Any) -> None:
        if not _source_chat_matches(event.chat_id, runtime.source_chat_id):
            return
        await _edit_telethon_message(bot, event.message, db)

    @client.on(events.MessageDeleted)
    async def on_delete(event: Any) -> None:
        # Channel deletes include chat_id; small-group deletes often omit peer (skip).
        if not _source_chat_matches(event.chat_id, runtime.source_chat_id):
            return
        for mid in event.deleted_ids:
            await delete_mirrored_posts(bot, mid, db)

    await client.run_until_disconnected()
