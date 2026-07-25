import asyncio
import logging
import random
import time
from datetime import datetime, timedelta

from sqlalchemy import select
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram import ChatPermissions
from telegram.ext import (
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from database import Group, GroupSettings, get_session
from keyboards import EMOJI

logger = logging.getLogger(__name__)

# In-memory captcha store
_math_captcha_store: dict[tuple[int, int], dict] = {}


async def _ensure_group_settings(session, group_id: int) -> GroupSettings:
    result = await session.execute(
        select(GroupSettings).where(GroupSettings.group_id == group_id)
    )
    gs = result.scalar_one_or_none()
    if not gs:
        gs = GroupSettings(group_id=group_id)
        session.add(gs)
        await session.commit()
        await session.refresh(gs)
    return gs


# ── Math Captcha: new member joins → auto-mute → math challenge ─────────────

async def on_new_member_math_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-mute new member, then present math captcha to unlock."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    async with get_session() as session:
        gs = await _ensure_group_settings(session, chat.id)
        if not gs.captcha_math_enabled:
            return

        for new_member in update.message.new_chat_members:
            if new_member.is_bot:
                continue

            # Auto-mute the new member
            if gs.captcha_auto_mute:
                try:
                    await context.bot.restrict_chat_member(
                        chat_id=chat.id,
                        user_id=new_member.id,
                        permissions=ChatPermissions(can_send_messages=False),
                    )
                except Exception as e:
                    logger.warning(f"Failed to mute new member {new_member.id}: {e}")
                    continue

            # Generate math challenge
            a = random.randint(5, 30)
            b = random.randint(1, 20)
            op_choice = random.choice(["+", "-"])
            if op_choice == "+":
                correct = a + b
                question = f"{a} + {b} = ?"
            else:
                # Ensure positive result
                if a < b:
                    a, b = b, a
                correct = a - b
                question = f"{a} - {b} = ?"

            _math_captcha_store[(new_member.id, chat.id)] = {
                "answer": correct,
                "expires": time.time() + config.CAPTCHA_TIMEOUT,
            }

            # Generate 4 answer choices
            choices = {correct}
            while len(choices) < 4:
                fake = correct + random.randint(-10, 10)
                if fake != correct and fake > 0:
                    choices.add(fake)
            choice_list = list(choices)
            random.shuffle(choice_list)

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        str(c), callback_data=f"mathcap:ans:{c}:{correct}:{new_member.id}:{chat.id}"
                    )
                    for c in choice_list[:2]
                ],
                [
                    InlineKeyboardButton(
                        str(c), callback_data=f"mathcap:ans:{c}:{correct}:{new_member.id}:{chat.id}"
                    )
                    for c in choice_list[2:]
                ],
            ])

            text = (
                f"🧮 <b>人机验证</b>\n\n"
                f"欢迎 <b>{new_member.full_name}</b>！\n"
                f"请回答以下算术题以解除禁言：\n\n"
                f"<b><code>{question}</code></b>\n\n"
                f"⏰ 请在 {config.CAPTCHA_TIMEOUT} 秒内完成"
            )

            sent = await update.message.reply_html(text, reply_markup=keyboard)

            # Schedule timeout - kick if not answered
            context.job_queue.run_once(
                _math_captcha_timeout,
                when=config.CAPTCHA_TIMEOUT,
                data={
                    "chat_id": chat.id,
                    "user_id": new_member.id,
                    "message_id": sent.message_id,
                },
            )


async def _math_captcha_timeout(context: ContextTypes.DEFAULT_TYPE):
    """Kick user if captcha not completed in time."""
    data = context.job.data
    key = (data["user_id"], data["chat_id"])
    if key in _math_captcha_store:
        _math_captcha_store.pop(key, None)
        try:
            await context.bot.ban_chat_member(
                chat_id=data["chat_id"], user_id=data["user_id"]
            )
            await context.bot.unban_chat_member(
                chat_id=data["chat_id"], user_id=data["user_id"]
            )
        except Exception:
            pass
        try:
            await context.bot.delete_message(
                chat_id=data["chat_id"], message_id=data["message_id"]
            )
        except Exception:
            pass


# ── Math Captcha callback ───────────────────────────────────────────────────

async def on_math_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle math captcha answer."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    chosen = int(parts[2])
    correct = int(parts[3])
    user_id = int(parts[4])
    group_id = int(parts[5])

    if query.from_user.id != user_id:
        await query.answer("⚠️ 请回答你自己的验证问题！", show_alert=True)
        return

    key = (user_id, group_id)
    entry = _math_captcha_store.pop(key, None)
    if not entry or time.time() > entry.get("expires", 0):
        await query.edit_message_text(
            f"{EMOJI.CROSS} 验证已过期。",
            parse_mode=ParseMode.HTML,
        )
        return

    if chosen == correct:
        # Unmute the user
        try:
            await context.bot.restrict_chat_member(
                chat_id=group_id,
                user_id=user_id,
                permissions=ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
        except Exception as e:
            logger.warning(f"Failed to unmute {user_id}: {e}")

        user_info = query.from_user
        await query.edit_message_text(
            f"{EMOJI.CHECK} <b>验证通过！</b>\n\n"
            f"欢迎 <b>{user_info.full_name}</b> 加入群组！🎉",
            parse_mode=ParseMode.HTML,
        )
    else:
        # Wrong answer - re-present the same question
        correct_ans = entry["answer"]
        choices = {correct_ans}
        while len(choices) < 4:
            fake = correct_ans + random.randint(-10, 10)
            if fake != correct_ans and fake > 0:
                choices.add(fake)
        choice_list = list(choices)
        random.shuffle(choice_list)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    str(c), callback_data=f"mathcap:ans:{c}:{correct_ans}:{user_id}:{group_id}"
                )
                for c in choice_list[:2]
            ],
            [
                InlineKeyboardButton(
                    str(c), callback_data=f"mathcap:ans:{c}:{correct_ans}:{user_id}:{group_id}"
                )
                for c in choice_list[2:]
            ],
        ])

        _math_captcha_store[key] = entry  # Restore
        await query.edit_message_text(
            f"{EMOJI.CROSS} 回答错误，请重试！\n\n"
            f"<b><code>请重新选择答案</code></b>",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )


# ── Command: toggle math captcha ─────────────────────────────────────────────

async def cmd_math_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle math captcha for a group."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("请在群组中使用此命令。")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        await update.message.reply_text(f"{EMOJI.LOCK} 仅限管理员。")
        return

    async with get_session() as session:
        gs = await _ensure_group_settings(session, chat.id)
        gs.captcha_math_enabled = not gs.captcha_math_enabled
        await session.commit()
        status = "✅ 已开启" if gs.captcha_math_enabled else "❌ 已关闭"
        await update.message.reply_text(
            f"🧮 <b>算术验证</b>：{status}\n"
            f"新成员需计算算术题才能发送消息。",
            parse_mode=ParseMode.HTML,
        )


# ── Registration ────────────────────────────────────────────────────────────

MATH_CAPTCHA_HANDLERS = [
    ChatMemberHandler(on_new_member_math_captcha, ChatMemberHandler.CHAT_MEMBER),
    CallbackQueryHandler(on_math_captcha_callback, pattern=r"^mathcap:"),
    CommandHandler("mathcaptcha", cmd_math_captcha),
]

