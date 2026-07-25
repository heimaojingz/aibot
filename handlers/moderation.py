import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from database import (
    BannedWord,
    Group,
    GroupMember,
    ModLog,
    PunishmentType,
    User,
    get_session,
)

from services.spam_detector import dual_engine_check
from keyboards import mod_action_keyboard, confirm_keyboard, EMOJI

logger = logging.getLogger(__name__)

# ── In-memory spam tracking ─────────────────────────────────────────────────
# { (user_id, group_id): [timestamp, timestamp, ...] }
_spam_tracker: dict[tuple[int, int], list[float]] = defaultdict(list)


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _mute_user(bot, group_id: int, user_id: int, duration_seconds: int):
    """Restrict a user from sending messages for a duration."""
    until = datetime.utcnow() + timedelta(seconds=duration_seconds)
    try:
        await bot.restrict_chat_member(
            chat_id=group_id,
            user_id=user_id,
            permissions={"can_send_messages": False},
            until_date=until,
        )
        async with get_session() as session:
            result = await session.execute(
                select(GroupMember).where(
                    GroupMember.group_id == group_id,
                    GroupMember.user_id == user_id,
                    GroupMember.is_active == True,
                )
            )
            gm = result.scalar_one_or_none()
            if gm:
                gm.is_muted = True
                gm.mute_until = until
                await session.commit()
        return True
    except Exception as e:
        logger.error(f"Failed to mute user {user_id}: {e}")
        return False


async def _kick_user(bot, group_id: int, user_id: int):
    """Kick (ban + unban) a user."""
    try:
        await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
        return True
    except Exception as e:
        logger.error(f"Failed to kick user {user_id}: {e}")
        return False


async def _ban_user(bot, group_id: int, user_id: int):
    """Permanently ban a user."""
    try:
        await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
        return True
    except Exception as e:
        logger.error(f"Failed to ban user {user_id}: {e}")
        return False


async def _add_warning(session, user_id: int, group_id: int, reason: str = "") -> int:
    """Add a warning to a group member. Returns new warn count."""
    result = await session.execute(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.is_active == True,
        )
    )
    gm = result.scalar_one_or_none()
    if not gm:
        gm = GroupMember(user_id=user_id, group_id=group_id)
        session.add(gm)
    gm.warn_count += 1
    await session.commit()
    return gm.warn_count


async def _log_moderation(session, group_id: int, user_id: int, moderator_id: int,
                          action: PunishmentType, reason: str = ""):
    log = ModLog(
        group_id=group_id,
        user_id=user_id,
        moderator_id=moderator_id,
        action=action,
        reason=reason,
    )
    session.add(log)
    await session.commit()


# ── Anti-Spam Message Handler ───────────────────────────────────────────────

async def on_message_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check every group message for spam / banned content."""
    if not update.message or not update.effective_chat:
        return

    chat = update.effective_chat
    user = update.effective_user
    msg = update.message

    if chat.type == "private":
        return

    # Skip admins
    try:
        member = await chat.get_member(user.id)
        if member.status in ("creator", "administrator"):
            return
    except Exception:
        pass

    # ── Rate limiting ───────────────────────────────────────────────────
    if config.ANTI_SPAM_ENABLED:
        key = (user.id, chat.id)
        now = time.time()

        # Clean old entries
        _spam_tracker[key] = [
            ts for ts in _spam_tracker[key]
            if now - ts < config.SPAM_WINDOW_SECONDS
        ]
        _spam_tracker[key].append(now)

        # Check rate
        if len(_spam_tracker[key]) > config.MAX_MESSAGES_PER_WINDOW:
            await msg.delete()
            try:
                await _mute_user(context.bot, chat.id, user.id, 600)
                mute_msg = await msg.reply_text(
                    f"{EMOJI.MUTE} <b>反刷屏</b>\n"
                    f"用户 {user.mention_html()} 因刷屏被自动禁言 10 分钟。",
                    parse_mode=ParseMode.HTML,
                )
                # Auto delete the mute notification
                context.job_queue.run_once(
                    lambda ctx: ctx.bot.delete_message(chat.id, mute_msg.message_id)
                    if hasattr(ctx, 'bot') else None,
                    when=30,
                )
            except Exception:
                pass
            return


    # ── Dual-Engine Spam Detection (AI + Keyword) ───────────────────────
    if msg.text:
        is_spam, reason = await dual_engine_check(
            text=msg.text,
            user_id=user.id,
            group_id=chat.id,
            username=user.username,
        )
        if is_spam:
            await msg.delete()
            try:
                warn_msg = await msg.reply_html(
                    f"{EMOJI.SECURITY} <b>垃圾消息检测</b>\n"
                    f"检测到违规内容，消息已自动删除。\n"
                    f"<i>原因：{reason}</i>"
                )
                # Auto-delete notification
                from database import GroupSettings
                async with get_session() as _gs:
                    gs_result = await _gs.execute(
                        select(GroupSettings).where(GroupSettings.group_id == chat.id)
                    )
                    gs = gs_result.scalar_one_or_none()
                    delete_after = gs.notification_auto_delete if gs else 0
                if delete_after > 0:
                    context.job_queue.run_once(
                        lambda ctx: ctx.bot.delete_message(chat.id, warn_msg.message_id),
                        when=delete_after,
                    )
            except Exception:
                pass
            return
    # ── Banned words check ──────────────────────────────────────────────
    if msg.text:
        async with get_session() as session:
            result = await session.execute(
                select(BannedWord).where(BannedWord.group_id == chat.id)
            )
            banned_words = list(result.scalars().all())

            for bw in banned_words:
                if bw.match_mode == "exact" and msg.text.lower() == bw.word.lower():
                    await msg.delete()
                    warn_msg = await msg.reply_text(
                        f"{EMOJI.WARN} <b>违禁词检测</b>\n"
                        f"检测到违禁词，消息已删除。",
                        parse_mode=ParseMode.HTML,
                    )
                    context.job_queue.run_once(
                        lambda ctx: ctx.bot.delete_message(chat.id, warn_msg.message_id),
                        when=10,
                    )
                    return
                elif bw.match_mode == "contains" and bw.word.lower() in msg.text.lower():
                    await msg.delete()
                    warn_msg = await msg.reply_text(
                        f"{EMOJI.WARN} <b>违禁词检测</b>\n"
                        f"检测到违禁词，消息已删除。",
                        parse_mode=ParseMode.HTML,
                    )
                    context.job_queue.run_once(
                        lambda ctx: ctx.bot.delete_message(chat.id, warn_msg.message_id),
                        when=10,
                    )
                    return
                elif bw.match_mode == "regex":
                    try:
                        if re.search(bw.word, msg.text, re.IGNORECASE):
                            await msg.delete()
                            await msg.reply_text(
                                f"{EMOJI.WARN} <b>违禁词检测</b>\n消息已删除。",
                                parse_mode=ParseMode.HTML,
                            )
                            return
                    except re.error:
                        pass

    # ── Link detection (basic) ──────────────────────────────────────────
    if msg.text and ("http://" in msg.text or "https://" in msg.text or "t.me/" in msg.text):
        async with get_session() as session:
            group_result = await session.execute(select(Group).where(Group.id == chat.id))
            group = group_result.scalar_one_or_none()
            if group and group.get_setting("block_links", False):
                await msg.delete()
                warn_msg = await msg.reply_text(
                    f"{EMOJI.LINK} <b>链接检测</b>\n"
                    f"本群禁止发送链接，消息已删除。",
                    parse_mode=ParseMode.HTML,
                )
                context.job_queue.run_once(
                    lambda ctx: ctx.bot.delete_message(chat.id, warn_msg.message_id),
                    when=10,
                )
                return

    # ── Forward detection ───────────────────────────────────────────────
    if msg.forward_date:
        async with get_session() as session:
            group_result = await session.execute(select(Group).where(Group.id == chat.id))
            group = group_result.scalar_one_or_none()
            if group and group.get_setting("block_forwards", False):
                await msg.delete()
                warn_msg = await msg.reply_text(
                    f"{EMOJI.WARN} <b>转发检测</b>\n"
                    f"本群禁止转发消息，消息已删除。",
                    parse_mode=ParseMode.HTML,
                )
                context.job_queue.run_once(
                    lambda ctx: ctx.bot.delete_message(chat.id, warn_msg.message_id),
                    when=10,
                )


# ── Moderation Commands ─────────────────────────────────────────────────────

async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /warn [reply] [reason]"""
    await _handle_mod_action(update, context, PunishmentType.WARN)

async def cmd_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /mute [reply] [minutes]"""
    await _handle_mod_action(update, context, PunishmentType.MUTE)

async def cmd_kick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /kick [reply]"""
    await _handle_mod_action(update, context, PunishmentType.KICK)

async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /ban [reply]"""
    await _handle_mod_action(update, context, PunishmentType.BAN)


async def _handle_mod_action(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             action: PunishmentType):
    """Generic handler for moderation commands."""
    chat = update.effective_chat
    moderator = update.effective_user
    msg = update.message

    if chat.type == "private":
        await msg.reply_text("请在群组中使用此命令。")
        return

    # Check moderator permissions
    try:
        mod_member = await chat.get_member(moderator.id)
        if mod_member.status not in ("creator", "administrator"):
            await msg.reply_text(f"{EMOJI.LOCK} 此命令仅限管理员使用。")
            return
    except Exception:
        return

    # Get target user
    target_user = None
    if msg.reply_to_message:
        target_user = msg.reply_to_message.from_user
    elif context.args and len(context.args) > 0:
        # Try user ID or username
        try:
            uid = int(context.args[0])
            target_user = await context.bot.get_chat(uid)
        except (ValueError, Exception):
            await msg.reply_text(f"{EMOJI.CROSS} 无法找到该用户。")
            return
    else:
        await msg.reply_text(
            f"{EMOJI.CROSS} 请回复目标用户的消息，或提供用户 ID。"
        )
        return

    if target_user.is_bot:
        await msg.reply_text(f"{EMOJI.CROSS} 不能对机器人执行操作。")
        return

    reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    async with get_session() as session:
        if action == PunishmentType.WARN:
            count = await _add_warning(session, target_user.id, chat.id, reason)
            await _log_moderation(session, chat.id, target_user.id, moderator.id, action, reason)

            if count >= config.DEFAULT_WARN_LIMIT:
                await _ban_user(context.bot, chat.id, target_user.id)
                await msg.reply_html(
                    f"{EMOJI.BAN} <b>自动封禁</b>\n"
                    f"用户 {target_user.mention_html()} 累计 {count} 次警告，已自动封禁。"
                )
            else:
                await msg.reply_html(
                    f"{EMOJI.WARN} <b>警告</b>\n"
                    f"用户 {target_user.mention_html()} 收到第 {count} 次警告 "
                    f"（满 {config.DEFAULT_WARN_LIMIT} 次自动封禁）\n"
                    f"原因：{reason or '未指定'}"
                )

        elif action == PunishmentType.MUTE:
            # Parse duration (default from config, or from args)
            duration = config.DEFAULT_MUTE_DURATION
            if context.args and len(context.args) > 0:
                try:
                    duration = int(context.args[0]) * 60  # convert minutes to seconds
                except ValueError:
                    pass
            success = await _mute_user(context.bot, chat.id, target_user.id, duration)
            await _log_moderation(session, chat.id, target_user.id, moderator.id, action, reason)
            if success:
                minutes = duration // 60
                await msg.reply_html(
                    f"{EMOJI.MUTE} <b>禁言</b>\n"
                    f"用户 {target_user.mention_html()} 已被禁言 {minutes} 分钟。\n"
                    f"原因：{reason or '未指定'}"
                )

        elif action == PunishmentType.KICK:
            success = await _kick_user(context.bot, chat.id, target_user.id)
            await _log_moderation(session, chat.id, target_user.id, moderator.id, action, reason)
            if success:
                await msg.reply_html(
                    f"{EMOJI.KICK} <b>踢出</b>\n"
                    f"用户 {target_user.mention_html()} 已被踢出群组。"
                )

        elif action == PunishmentType.BAN:
            success = await _ban_user(context.bot, chat.id, target_user.id)
            await _log_moderation(session, chat.id, target_user.id, moderator.id, action, reason)
            if success:
                await msg.reply_html(
                    f"{EMOJI.BAN} <b>封禁</b>\n"
                    f"用户 {target_user.mention_html()} 已被永久封禁。\n"
                    f"原因：{reason or '未指定'}"
                )


# ── Banned Word Management ──────────────────────────────────────────────────

async def cmd_add_banned(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Command: /addbanned [word]"""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("请在群组中使用。")
        return

    try:
        member = await chat.get_member(user.id)
        if member.status not in ("creator", "administrator"):
            await update.message.reply_text(f"{EMOJI.LOCK} 仅限管理员。")
            return
    except Exception:
        return

    if not context.args:
        await update.message.reply_text("用法：/addbanned <违禁词> [exact|contains|regex]")
        return

    word = context.args[0]
    mode = context.args[1] if len(context.args) > 1 else "contains"
    if mode not in ("exact", "contains", "regex"):
        mode = "contains"

    async with get_session() as session:
        bw = BannedWord(group_id=chat.id, word=word, match_mode=mode)
        session.add(bw)
        await session.commit()

    await update.message.reply_text(
        f"{EMOJI.CHECK} 违禁词已添加：<code>{word}</code>（模式：{mode}）",
        parse_mode=ParseMode.HTML,
    )


# ── New member scan (detect suspicious accounts) ────────────────────────────

async def on_new_member_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan new members for suspicious patterns (no username, ad names, etc.)."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    async with get_session() as session:
        group_result = await session.execute(select(Group).where(Group.id == chat.id))
        group = group_result.scalar_one_or_none()

        if not group or not group.get_setting("auto_scan_new_members", False):
            return

    for member in update.message.new_chat_members:
        if member.is_bot:
            continue

        suspicious = False
        reasons = []

        # Check no username
        if not member.username and group.get_setting("require_username", False):
            suspicious = True
            reasons.append("未设置用户名")

        # Check ad-like name (contains suspicious keywords)
        ad_patterns = [
            r"广告", r"推广", r"代发", r"引流", r"加好友", r"兼职",
            r"赚钱", r"投资", r"福利", r"免费领取",
        ]
        for pat in ad_patterns:
            if re.search(pat, member.full_name, re.IGNORECASE):
                suspicious = True
                reasons.append(f"名称含敏感词：{pat}")
                break

        if suspicious:
            try:
                await _kick_user(context.bot, chat.id, member.id)
                alert = await update.message.reply_html(
                    f"{EMOJI.SECURITY} <b>安全扫描</b>\n"
                    f"疑似广告账号 {member.mention_html()} 已被自动移除。\n"
                    f"原因：{', '.join(reasons)}"
                )
                context.job_queue.run_once(
                    lambda ctx: ctx.bot.delete_message(chat.id, alert.message_id),
                    when=30,
                )
            except Exception:
                pass


# ── Registration ────────────────────────────────────────────────────────────

MODERATION_HANDLERS = [
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        on_message_check,
    ),
    MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS,
        on_new_member_scan,
    ),
    CommandHandler("warn", cmd_warn),
    CommandHandler("mute", cmd_mute),
    CommandHandler("kick", cmd_kick),
    CommandHandler("ban", cmd_ban),
    CommandHandler("addbanned", cmd_add_banned),
]


