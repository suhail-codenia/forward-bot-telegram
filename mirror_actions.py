"""Mirror / edit / delete helpers using the Bot API (targets only)."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import Message

from mirror_db import MirrorDB

logger = logging.getLogger(__name__)


async def mirror_message(
    bot: Bot, message: Message, db: MirrorDB, source_chat_id: int
):
    """Copy each source post into targets (bot-owned copies — editable/deletable).

    Uses ``copyMessage``, not ``forwardMessage``, so mirrored posts can be edited
    and deleted via the Bot API. Copies appear as the bot's posts (no forward header).

    Requires the bot to be able to read the source message (must be source admin).
    """
    targets = await db.list_targets()
    if not targets:
        logger.info("No target channels registered – skipping mirror")
        return

    for target_chat_id, _title in targets:
        try:
            copied = await bot.copy_message(
                chat_id=target_chat_id,
                from_chat_id=source_chat_id,
                message_id=message.message_id,
            )
            await db.set_mapping(
                message.message_id, target_chat_id, copied.message_id
            )
            logger.info(
                "Mirrored  src=%s  ->  tgt=%s:%s  (type=%s)",
                message.message_id,
                target_chat_id,
                copied.message_id,
                message.content_type,
            )
        except Exception:
            logger.exception(
                "Failed to mirror %s to %s",
                message.message_id,
                target_chat_id,
            )


async def delete_mirrored_posts(bot: Bot, source_message_id: int, db: MirrorDB):
    """Delete bot-owned copies in all targets; clear DB mappings on success."""
    mappings = await db.get_mappings(source_message_id)
    if not mappings:
        logger.warning(
            "No mirrored copies tracked for source message %s", source_message_id
        )
        return

    for target_chat_id, target_msg_id in mappings:
        try:
            await bot.delete_message(
                chat_id=target_chat_id, message_id=target_msg_id
            )
            await db.delete_mapping(source_message_id, target_chat_id)
            logger.info(
                "Deleted mirror  src=%s  ->  tgt=%s:%s",
                source_message_id,
                target_chat_id,
                target_msg_id,
            )
        except Exception:
            logger.exception(
                "Failed to delete mirror %s:%s for source %s",
                target_chat_id,
                target_msg_id,
                source_message_id,
            )


async def edit_message(bot: Bot, message: Message, db: MirrorDB):
    """Mirror text/caption edits to copied posts in targets."""
    mappings = await db.get_mappings(message.message_id)
    if not mappings:
        logger.warning(
            "No mappings for edited source message %s",
            message.message_id,
        )
        return

    for target_chat_id, target_msg_id in mappings:
        try:
            if message.text is not None:
                await bot.edit_message_text(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    text=message.text,
                    entities=message.entities,
                    link_preview_options=message.link_preview_options,
                )
            elif message.caption is not None:
                await bot.edit_message_caption(
                    chat_id=target_chat_id,
                    message_id=target_msg_id,
                    caption=message.caption,
                    caption_entities=message.caption_entities,
                    show_caption_above_media=message.show_caption_above_media,
                )
            else:
                logger.debug(
                    "Edit has no text/caption to mirror for src=%s",
                    message.message_id,
                )
                continue

            logger.info(
                "Edited  src=%s  ->  tgt=%s:%s",
                message.message_id,
                target_chat_id,
                target_msg_id,
            )
        except Exception:
            logger.exception(
                "Failed to edit target %s:%s for source %s",
                target_chat_id,
                target_msg_id,
                message.message_id,
            )
