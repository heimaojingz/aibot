import asyncio
import json
import random
import time
from datetime import datetime

from sqlalchemy import select
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
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
from database import AsyncSession, Group, WelcomeConfig, GroupMember, User, get_session
from keyboards import (
    captcha_button_keyboard,
    captcha_math_keyboard,
    parse_buttons,
    EMOJI,
)

# ── In-memory CAPTCHA store ─────────────────────────────────────────────────
# { (user_id, group_id): {"answer": int, "expires": float, "type": str} }
_captcha_store: dict[tuple[int, int], dict] = {}


async def _ensure_group(session: AsyncSession, chat_id: int, title: str = "") -> Group:
    """Get or create a group record."""
    result = await session.execute(select(Group).where(Group.id == chat_id))
    group = result.scalar_one_or_none()
    if not group:
        group = Group(id=chat_id, title=title)
        session.add(group)
        await session.commit()
        await session.refresh(group)
    return group


async def _ensure_user(session: AsyncSession, user_id: int, username: str = "",
                       first_name: str = "") -> User:
    """Get or create a user record."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        user = User(id=user_id, username=username, first_name=first_name)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    return user


async def _ensure_welcome_config(session: AsyncSession, group_id: int) -> WelcomeConfig:
    """Get or create welcome config for a group."""
    result = await session.execute(
        select(WelcomeConfig).where(WelcomeConfig.group_id == group_id)
    )
    wc = result.scalar_one_or_none()
    if not wc:
        wc = WelcomeConfig(group_id=group_id)
        session.add(wc)
        await session.commit()
        await session.refresh(wc)
    return wc


async def _build_welcome_text(wc: WelcomeConfig, user_mention: str, group_title: str) -> str:
    """Fill template placeholders."""
    text = wc.message_template
    text = text.replace("{user_mention}", user_mention)
    text = text.replace("{user_name}", user_mention)
    text = text.replace("{group_title}", group_title)
    text = text.replace("{group_name}", group_title)
    return text


# ── Handler: new member joins ───────────────────────────────────────────────

async def on_new_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggered when a user joins the group (ChatMemberHandler)."""
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return

    # ChatMemberHandler: get the joining user from chat_member update
    chat_member_update = update.chat_member
    if not chat_member_update:
        return

    old_status = chat_member_update.old_chat_member.status
    new_status = chat_member_update.new_chat_member.status
    new_member = chat_member_update.new_chat_member.user

    # Only process actual joins (not admin promotions or other status changes)
    if new_status not in ("member", "restricted"):
        return
    if old_status in ("member", "administrator", "creator"):
        return

    if new_member.is_bot:
        return

    async with get_session() as session:
        group = await _ensure_group(session, chat.id, chat.title or "")
        wc = await _ensure_welcome_config(session, chat.id)

        if not wc.enabled:
            return

        user_mention = new_member.mention_html()
        nice_name = new_member.full_name

        # Persist user and member
        user = await _ensure_user(
            session, new_member.id,
            new_member.username or "",
            new_member.first_name or "",
        )
        gm = GroupMember(
            user_id=new_member.id,
            group_id=chat.id,
        )
        session.add(gm)
        await session.commit()

        welcome_text = await _build_welcome_text(wc, user_mention, chat.title)

        # Build reply markup
        reply_markup = None
        if wc.captcha_enabled:
            captcha_type = wc.captcha_type or "button"
            if captcha_type == "math":
                a = random.randint(1, 20)
                b = random.randint(1, 20)
                correct = a + b
                _captcha_store[(new_member.id, chat.id)] = {
                    "answer": correct,
                    "expires": time.time() + config.CAPTCHA_TIMEOUT,
                    "type": "math",
                }
                welcome_text += (
                    f"\n\n\U0001f9ee <b>\u4eba\u673a\u9a8c\u8bc1</b>\n"
                    f"\u8bf7\u56de\u7b54\uff1a<code>{a} + {b} = ?</code>\n"
                    f"\u23f0 \u8bf7\u5728 {config.CAPTCHA_TIMEOUT} \u79d2\u5185\u5b8c\u6210"
                )
                reply_markup = captcha_math_keyboard(
                    new_member.id, chat.id, correct
                )
            else:
                _captcha_store[(new_member.id, chat.id)] = {
                    "answer": 1,
                    "expires": time.time() + config.CAPTCHA_TIMEOUT,
                    "type": "button",
                }
                welcome_text += (
                    f"\n\n\U0001f9ee <b>\u4eba\u673a\u9a8c\u8bc1</b>\n"
                    f"\u8bf7\u70b9\u51fb\u4e0b\u65b9\u6309\u94ae\u5b8c\u6210\u9a8c\u8bc1\n"
                    f"\u23f0 \u8bf7\u5728 {config.CAPTCHA_TIMEOUT} \u79d2\u5185\u5b8c\u6210"
                )
                reply_markup = captcha_button_keyboard(new_member.id, chat.id)
        else:
            rows = parse_buttons(wc.buttons)
            if rows:
                reply_markup = InlineKeyboardMarkup(rows)

        # Send welcome message (with or without media)
        sent_msg: Message | None = None
        try:
            if wc.media_file_id and wc.media_type:
                if wc.media_type == "photo":
                    sent_msg = await context.bot.send_photo(
                        chat_id=chat.id,
                        photo=wc.media_file_id,
                        caption=welcome_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                elif wc.media_type == "video":
                    sent_msg = await context.bot.send_video(
                        chat_id=chat.id,
                        video=wc.media_file_id,
                        caption=welcome_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                elif wc.media_type == "animation":
                    sent_msg = await context.bot.send_animation(
                        chat_id=chat.id,
                        animation=wc.media_file_id,
                        caption=welcome_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
                else:
                    sent_msg = await context.bot.send_message(
                        chat_id=chat.id,
                        text=welcome_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup,
                    )
            else:
                sent_msg = await context.bot.send_message(
                    chat_id=chat.id,
                    text=welcome_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )

            # Auto-delete scheduling
            if sent_msg and wc.auto_delete_after > 0:
                context.job_queue.run_once(
                    _delete_welcome_message,
                    when=wc.auto_delete_after,
                    data={"chat_id": chat.id, "message_id": sent_msg.message_id},
                )

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                f"Failed to send welcome message: {e}"
            )


async def _delete_welcome_message(context: ContextTypes.DEFAULT_TYPE):
    """Auto-delete a welcome message after a delay."""
    data = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"],
            message_id=data["message_id"],
        )
    except Exception:
        pass  # Already deleted or no permission


# ── Handler: CAPTCHA callback ───────────────────────────────────────────────

async def on_captcha_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle captcha verification button clicks."""
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    # Format: captcha:verify:user_id:group_id
    #       OR captcha:math:chosen:correct:user_id:group_id
    action = data[1]  # "verify" or "math"
    callback_user_id = int(data[2])

    if action == "verify":
        group_id = int(data[3])
        correct = True
    elif action == "math":
        chosen = int(data[2])
        correct_answer = int(data[3])
        callback_user_id = int(data[4])
        group_id = int(data[5])
        correct = (chosen == correct_answer)
    else:
        return

    # Check if it's the right user clicking
    if query.from_user.id != callback_user_id:
        await query.answer(
            f"⚠️ 此验证不属于你，请等待你自己的验证消息�?,
            show_alert=True,
        )
        return

    # Check CAPTCHA store
    key = (callback_user_id, group_id)
    captcha_entry = _captcha_store.get(key)
    if not captcha_entry:
        await query.edit_message_text(
            f"{EMOJI.CROSS} 验证已过期或不存在�?,
            parse_mode=ParseMode.HTML,
        )
        return

    if time.time() > captcha_entry["expires"]:
        _captcha_store.pop(key, None)
        await query.edit_message_text(
            f"{EMOJI.CROSS} 验证超时，你已被移出群组�?,
            parse_mode=ParseMode.HTML,
        )
        try:
            await context.bot.ban_chat_member(
                chat_id=group_id, user_id=callback_user_id
            )
            await context.bot.unban_chat_member(
                chat_id=group_id, user_id=callback_user_id
            )
        except Exception:
            pass
        return

    if correct:
        _captcha_store.pop(key, None)
        await query.edit_message_text(
            f"{EMOJI.CHECK} 验证通过！欢迎加入本群！\n"
            f"{EMOJI.SPARKLES} 祝你在这里交流愉快~",
            parse_mode=ParseMode.HTML,
        )
    else:
        await query.answer(
            f"{EMOJI.CROSS} 回答错误，请重试�?,
            show_alert=True,
        )


# ── Handler: welcome config command ─────────────────────────────────────────

async def cmd_welcome_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /welcome command in groups �?show welcome settings."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        await update.message.reply_text("请在群组中使用此命令�?)
        return

    # Check if user is admin
    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        await update.message.reply_text(
            f"{EMOJI.LOCK} 此命令仅限群管理员使用�?
        )
        return

    async with get_session() as session:
        wc = await _ensure_welcome_config(session, chat.id)
        status = "�?已启�? if wc.enabled else "�?已关�?
        captcha = "�?已启�? if wc.captcha_enabled else "�?已关�?
        media = f"📎 {wc.media_type}" if wc.media_file_id else "无媒�?

        text = (
            f"{EMOJI.BELL} <b>欢迎系统设置</b>\n\n"
            f"�?状态：{status}\n"
            f"�?验证码：{captcha}\n"
            f"�?媒体附件：{media}\n"
            f"�?自动删除：{wc.auto_delete_after} 秒后\n"
            f"�?欢迎模板：\n<blockquote expandable>{wc.message_template[:200]}</blockquote>"
        )

        from keyboards import welcome_config_menu
        reply_markup = welcome_config_menu(
            chat.id, wc.enabled, wc.captcha_enabled
        )
        await update.message.reply_html(text, reply_markup=reply_markup)


# ── Handler: welcome settings callback ──────────────────────────────────────

async def on_welcome_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle welcome config callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[1]
    group_id = int(data[2]) if len(data) > 2 else 0

    async with get_session() as session:
        wc = await _ensure_welcome_config(session, group_id or update.effective_chat.id)

        if action == "toggle":
            wc.enabled = not wc.enabled
            await session.commit()
            await query.edit_message_reply_markup(
                reply_markup=welcome_config_menu(
                    group_id, wc.enabled, wc.captcha_enabled
                )
            )

        elif action == "toggle_captcha":
            wc.captcha_enabled = not wc.captcha_enabled
            await session.commit()
            await query.edit_message_reply_markup(
                reply_markup=welcome_config_menu(
                    group_id, wc.enabled, wc.captcha_enabled
                )
            )

        elif action == "edit_text":
            await query.edit_message_text(
                f"{EMOJI.EDIT} 请发送新的欢迎消息模板：\n\n"
                f"<i>可用变量�?/i>\n"
                f"�?<code>{'{user_mention}'}</code> - 用户mention\n"
                f"�?<code>{'{group_title}'}</code> - 群名称\n\n"
                f"发�?<code>/cancel</code> 取消操作",
                parse_mode=ParseMode.HTML,
            )
            context.user_data["awaiting_welcome_text"] = group_id

        elif action == "set_media":
            context.user_data["awaiting_welcome_media"] = group_id
            await query.edit_message_text(
                f"{EMOJI.PHOTO} 请发送一张图片、视频或 GIF 作为欢迎媒体附件。\n\n"
                f"发�?<code>/cancel</code> 取消操作",
                parse_mode=ParseMode.HTML,
            )

        elif action == "auto_delete":
            context.user_data["awaiting_welcome_delete"] = group_id
            await query.edit_message_text(
                f"{EMOJI.CLOCK} 请发送自动删除倒计时（秒）：\n"
                f"�?0 = 永不删除\n"
                f"�?300 = 5分钟后删除\n\n"
                f"发�?<code>/cancel</code> 取消操作",
                parse_mode=ParseMode.HTML,
            )

        elif action == "buttons":
            context.user_data["awaiting_welcome_buttons"] = group_id
            await query.edit_message_text(
                f"{EMOJI.LINK} 请以 JSON 格式发送按钮配置：\n\n"
                f"示例：\n"
                f'<code>[[{{"text":"📜 群规","url":"https://t.me"}},'
                f'{{"text":"📞 客服","url":"https://t.me/admin"}}]]</code>\n\n'
                f"发�?<code>/cancel</code> 取消操作",
                parse_mode=ParseMode.HTML,
            )


# ── Handler: text input for welcome settings ────────────────────────────────

async def on_welcome_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture user text input for welcome config."""
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        for key in list(context.user_data.keys()):
            if key.startswith("awaiting_welcome_"):
                del context.user_data[key]
        await msg.reply_text(f"{EMOJI.CHECK} 已取消操作�?)
        return

    if "awaiting_welcome_text" in context.user_data:
        group_id = context.user_data.pop("awaiting_welcome_text")
        async with get_session() as session:
            wc = await _ensure_welcome_config(session, group_id)
            wc.message_template = msg.text_html or msg.text
            await session.commit()
        await msg.reply_text(f"{EMOJI.CHECK} 欢迎消息模板已更新！")

    elif "awaiting_welcome_delete" in context.user_data:
        group_id = context.user_data.pop("awaiting_welcome_delete")
        try:
            seconds = int(msg.text.strip())
            async with get_session() as session:
                wc = await _ensure_welcome_config(session, group_id)
                wc.auto_delete_after = seconds
                await session.commit()
            await msg.reply_text(f"{EMOJI.CHECK} 自动删除时间已设置为 {seconds} 秒�?)
        except ValueError:
            await msg.reply_text(f"{EMOJI.CROSS} 请输入有效的数字�?)


# ── Handler: media input for welcome ────────────────────────────────────────

async def on_welcome_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture media for welcome config."""
    if "awaiting_welcome_media" not in context.user_data:
        return

    group_id = context.user_data.pop("awaiting_welcome_media")
    msg = update.message

    file_id = None
    media_type = None

    if msg.photo:
        file_id = msg.photo[-1].file_id
        media_type = "photo"
    elif msg.video:
        file_id = msg.video.file_id
        media_type = "video"
    elif msg.animation:
        file_id = msg.animation.file_id
        media_type = "animation"
    else:
        await msg.reply_text(f"{EMOJI.CROSS} 请发送图片、视频或 GIF。操作已取消�?)
        return

    async with get_session() as session:
        wc = await _ensure_welcome_config(session, group_id)
        wc.media_file_id = file_id
        wc.media_type = media_type
        await session.commit()

    await msg.reply_text(f"{EMOJI.CHECK} 欢迎媒体附件已更新为 {media_type}�?)


# ── Handler: button JSON input for welcome ──────────────────────────────────

async def on_welcome_buttons_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture button JSON for welcome config."""
    if "awaiting_welcome_buttons" not in context.user_data:
        return

    group_id = context.user_data.pop("awaiting_welcome_buttons")
    msg = update.message

    try:
        json.loads(msg.text)
    except json.JSONDecodeError:
        await msg.reply_text(f"{EMOJI.CROSS} JSON 格式无效，操作已取消�?)
        return

    async with get_session() as session:
        wc = await _ensure_welcome_config(session, group_id)
        wc.buttons = msg.text
        await session.commit()

    await msg.reply_text(f"{EMOJI.CHECK} 欢迎消息按钮已更新！")


# ── Registration ────────────────────────────────────────────────────────────

WELCOME_HANDLERS = [
    ChatMemberHandler(on_new_chat_member, ChatMemberHandler.CHAT_MEMBER),
    CommandHandler("welcome", cmd_welcome_config),
    CallbackQueryHandler(on_welcome_callback, pattern=r"^welcome:"),
    CallbackQueryHandler(on_captcha_callback, pattern=r"^captcha:"),
]
