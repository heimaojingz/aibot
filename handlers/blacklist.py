import logging
from typing import Optional

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from database import BlacklistEntry, BlacklistType, Group, get_session
from keyboards import blacklist_menu, confirm_keyboard, EMOJI

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _is_admin(chat, user_id: int) -> bool:
    try:
        member = await chat.get_member(user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


# ── Command: /blacklist ─────────────────────────────────────────────────────

async def cmd_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open blacklist management panel."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("请在群组中使用此命令。")
        return

    if not await _is_admin(chat, user.id):
        await update.message.reply_text(f"{EMOJI.LOCK} 仅限管理员使用。")
        return

    await update.message.reply_text(
        f"🚫 <b>黑名单管理</b>\n\n请选择操作：",
        parse_mode=ParseMode.HTML,
        reply_markup=blacklist_menu(chat.id),
    )


# ── Callback: blacklist actions ─────────────────────────────────────────────

async def on_blacklist_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    group_id = int(parts[2])

    if action == "add":
        context.user_data["blacklist_action"] = "add"
        context.user_data["blacklist_group_id"] = group_id
        await query.edit_message_text(
            f"🚫 <b>添加黑名单</b>\n\n"
            f"请发送以下格式（支持多行或逗号分隔批量添加）：\n\n"
            f"<b>用户黑名单：</b>\n"
            f"<code>@username</code> 或 <code>用户ID</code>\n\n"
            f"<b>关键词黑名单：</b>\n"
            f"<code>keyword:广告词</code>\n\n"
            f"<b>模式黑名单：</b>\n"
            f"<code>regex:正则表达式</code>\n\n"
            f"支持多行输入，每行一个条目。\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "remove":
        context.user_data["blacklist_action"] = "remove"
        context.user_data["blacklist_group_id"] = group_id
        await query.edit_message_text(
            f"🗑️ <b>删除黑名单条目</b>\n\n"
            f"请发送要删除的条目内容（精确匹配）：\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "list":
        async with get_session() as session:
            result = await session.execute(
                select(BlacklistEntry).where(
                    (BlacklistEntry.group_id == group_id) |
                    (BlacklistEntry.group_id == None)
                ).order_by(BlacklistEntry.created_at.desc()).limit(50)
            )
            entries = list(result.scalars().all())

        if not entries:
            await query.edit_message_text(
                f"📋 <b>黑名单列表</b>\n\n目前没有黑名单条目。",
                parse_mode=ParseMode.HTML,
                reply_markup=blacklist_menu(group_id),
            )
            return

        lines = [f"📋 <b>黑名单列表</b>（共 {len(entries)} 条）\n"]
        for i, e in enumerate(entries, 1):
            scope = "🌐 全局" if e.group_id is None else "👥 本群"
            lines.append(
                f"{i}. [{e.entry_type.value}] <code>{e.value[:60]}</code> {scope}"
            )
        await query.edit_message_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=blacklist_menu(group_id),
        )

    elif action == "clear":
        context.user_data["blacklist_action"] = "clear"
        context.user_data["blacklist_group_id"] = group_id
        await query.edit_message_text(
            f"⚠️ <b>确认清空黑名单？</b>\n\n"
            f"将删除本群所有黑名单条目，此操作不可恢复。",
            parse_mode=ParseMode.HTML,
            reply_markup=confirm_keyboard(f"blacklist_clear:{group_id}"),
        )


# ── Callback: confirm clear ─────────────────────────────────────────────────

async def on_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]

    if action.startswith("blacklist_clear"):
        group_id = int(parts[2])
        async with get_session() as session:
            result = await session.execute(
                select(BlacklistEntry).where(BlacklistEntry.group_id == group_id)
            )
            entries = list(result.scalars().all())
            count = len(entries)
            for e in entries:
                await session.delete(e)
            await session.commit()

        await query.edit_message_text(
            f"{EMOJI.CHECK} 已清空本群黑名单（共 {count} 条）。",
            parse_mode=ParseMode.HTML,
            reply_markup=blacklist_menu(group_id),
        )


# ── Text input: blacklist add/remove ────────────────────────────────────────

async def on_blacklist_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        for k in list(context.user_data.keys()):
            if k.startswith("blacklist_"):
                del context.user_data[k]
        await msg.reply_text(f"{EMOJI.CHECK} 已取消。")
        return

    baction = context.user_data.get("blacklist_action")
    group_id = context.user_data.get("blacklist_group_id")
    if not baction or not group_id:
        return

    # Clean up
    del context.user_data["blacklist_action"]
    del context.user_data["blacklist_group_id"]

    if baction == "add":
        # Support multi-line or comma-separated bulk add
        lines = msg.text.replace(",", "\n").split("\n")
        added = 0
        user_id = update.effective_user.id

        async with get_session() as session:
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if line.lower().startswith("keyword:"):
                    word = line[len("keyword:"):].strip()
                    if word:
                        session.add(BlacklistEntry(
                            group_id=group_id,
                            entry_type=BlacklistType.WORD,
                            value=word,
                            created_by=user_id,
                        ))
                        added += 1
                elif line.lower().startswith("regex:"):
                    pattern = line[len("regex:"):].strip()
                    if pattern:
                        session.add(BlacklistEntry(
                            group_id=group_id,
                            entry_type=BlacklistType.PATTERN,
                            value=pattern,
                            created_by=user_id,
                        ))
                        added += 1
                elif line.startswith("@"):
                    session.add(BlacklistEntry(
                        group_id=group_id,
                        entry_type=BlacklistType.USER,
                        value=line,
                        created_by=user_id,
                    ))
                    added += 1
                elif line.lstrip("-").isdigit():
                    session.add(BlacklistEntry(
                        group_id=group_id,
                        entry_type=BlacklistType.USER,
                        value=line.strip(),
                        created_by=user_id,
                    ))
                    added += 1
                else:
                    # Treat as keyword by default
                    session.add(BlacklistEntry(
                        group_id=group_id,
                        entry_type=BlacklistType.WORD,
                        value=line,
                        created_by=user_id,
                    ))
                    added += 1
            await session.commit()

        await msg.reply_text(
            f"{EMOJI.CHECK} 批量添加完成！共添加 <b>{added}</b> 条黑名单。",
            parse_mode=ParseMode.HTML,
        )

    elif baction == "remove":
        value = msg.text.strip()
        async with get_session() as session:
            result = await session.execute(
                select(BlacklistEntry).where(
                    BlacklistEntry.group_id == group_id,
                    BlacklistEntry.value == value,
                )
            )
            entry = result.scalar_one_or_none()
            if entry:
                await session.delete(entry)
                await session.commit()
                await msg.reply_text(
                    f"{EMOJI.CHECK} 已删除：<code>{value}</code>",
                    parse_mode=ParseMode.HTML,
                )
            else:
                await msg.reply_text(f"{EMOJI.CROSS} 未找到该条目。")


# ── Check blacklist on join ─────────────────────────────────────────────────

async def on_member_join_blacklist_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check new members against blacklist."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        async with get_session() as session:
            # Check user blacklist (global + group-specific)
            result = await session.execute(
                select(BlacklistEntry).where(
                    (BlacklistEntry.group_id == chat.id) | (BlacklistEntry.group_id == None),
                    BlacklistEntry.entry_type == BlacklistType.USER,
                )
            )
            entries = list(result.scalars().all())

            for e in entries:
                if e.value == str(member.id) or e.value == f"@{member.username}" or e.value == member.username:
                    try:
                        await context.bot.ban_chat_member(chat.id, member.id)
                        await context.bot.unban_chat_member(chat.id, member.id)
                        logger.info(f"Blacklisted user {member.id} kicked from {chat.id}")
                    except Exception:
                        pass
                    break


# ── Registration ────────────────────────────────────────────────────────────

BLACKLIST_HANDLERS = [
    CommandHandler("blacklist", cmd_blacklist),
    CallbackQueryHandler(on_blacklist_callback, pattern=r"^blacklist:"),
    CallbackQueryHandler(on_confirm_callback, pattern=r"^confirm:blacklist_"),
    ChatMemberHandler(on_member_join_blacklist_check, ChatMemberHandler.CHAT_MEMBER),
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        on_blacklist_text_input,
    ),
]

