import asyncio
import logging
from datetime import datetime, time

from sqlalchemy import select
from telegram import Update
from telegram import ChatPermissions
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from database import QuietModeConfig, Group, get_session
from keyboards import quiet_mode_menu, EMOJI

logger = logging.getLogger(__name__)

# Track current quiet state per group
_quiet_state: dict[int, bool] = {}


async def _get_quiet_config(session, group_id: int) -> QuietModeConfig:
    result = await session.execute(
        select(QuietModeConfig).where(QuietModeConfig.group_id == group_id)
    )
    qc = result.scalar_one_or_none()
    if not qc:
        qc = QuietModeConfig(group_id=group_id)
        session.add(qc)
        await session.commit()
        await session.refresh(qc)
    return qc


# ── Background: quiet mode scheduler ────────────────────────────────────────

async def quiet_mode_scheduler(bot):
    """Background loop that checks and enforces quiet mode schedules."""
    logger.info("Quiet mode scheduler started")

    while True:
        try:
            async with get_session() as session:
                result = await session.execute(
                    select(QuietModeConfig).where(QuietModeConfig.enabled == True)
                )
                configs = list(result.scalars().all())

                now = datetime.now()
                current_time = now.time()

                for qc in configs:
                    # Parse days of week
                    days = [int(d.strip()) for d in qc.days_of_week.split(",") if d.strip()]
                    if now.weekday() not in days:
                        # Not a scheduled day — ensure permissions are open
                        if _quiet_state.get(qc.group_id, False):
                            await _open_chat(bot, qc.group_id, now)
                            _quiet_state[qc.group_id] = False
                        continue

                    start = time(qc.start_hour, qc.start_minute)
                    end = time(qc.end_hour, qc.end_minute)

                    in_quiet_window = False
                    if start <= end:
                        in_quiet_window = start <= current_time <= end
                    else:
                        # Overnight: e.g., 22:00 - 06:00
                        in_quiet_window = current_time >= start or current_time <= end

                    if in_quiet_window and not _quiet_state.get(qc.group_id, False):
                        await _close_chat(bot, qc.group_id, now, qc.auto_notify)
                        _quiet_state[qc.group_id] = True
                    elif not in_quiet_window and _quiet_state.get(qc.group_id, False):
                        await _open_chat(bot, qc.group_id, now)
                        _quiet_state[qc.group_id] = False

        except Exception as e:
            logger.error(f"Quiet mode scheduler error: {e}", exc_info=True)

        await asyncio.sleep(config.QUIET_MODE_CHECK_INTERVAL)


async def _close_chat(bot, group_id: int, now: datetime, notify: bool = True):
    """Restrict all members from sending messages."""
    try:
        await bot.set_chat_permissions(
            chat_id=group_id,
            permissions=ChatPermissions(can_send_messages=False),
        )
        if notify:
            async with get_session() as session:
                qc_result = await session.execute(
                    select(QuietModeConfig).where(QuietModeConfig.group_id == group_id)
                )
                qc = qc_result.scalar_one_or_none()
                end_str = f"{qc.end_hour:02d}:{qc.end_minute:02d}" if qc else "06:00"
            await bot.send_message(
                chat_id=group_id,
                text=f"🔇 <b>安静模式已开启</b>\n\n"
                     f"当前时段禁止发言。\n"
                     f"⏰ 预计恢复时间：<b>{end_str}</b>",
                parse_mode=ParseMode.HTML,
            )
        logger.info(f"Quiet mode activated for group {group_id}")
    except Exception as e:
        logger.warning(f"Failed to activate quiet mode for {group_id}: {e}")


async def _open_chat(bot, group_id: int, now: datetime):
    """Restore full chat permissions."""
    try:
        await bot.set_chat_permissions(
            chat_id=group_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
        await bot.send_message(
            chat_id=group_id,
            text=f"🔊 <b>安静模式已结束</b>\n\n大家现在可以自由发言了！",
            parse_mode=ParseMode.HTML,
        )
        logger.info(f"Quiet mode deactivated for group {group_id}")
    except Exception as e:
        logger.warning(f"Failed to deactivate quiet mode for {group_id}: {e}")


# ── Command: /quietmode ─────────────────────────────────────────────────────

async def cmd_quiet_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show quiet mode config panel."""
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
        qc = await _get_quiet_config(session, chat.id)
        status = "✅ 已启用" if qc.enabled else "❌ 已关闭"
        time_range = f"{qc.start_hour:02d}:{qc.start_minute:02d} - {qc.end_hour:02d}:{qc.end_minute:02d}"

        text = (
            f"🔇 <b>安静模式设置</b>\n\n"
            f"▸ 状态：{status}\n"
            f"▸ 时间段：{time_range}\n"
            f"▸ 已开启通知：{'✅' if qc.auto_notify else '❌'}\n"
            f"▸ Cron：<code>{qc.cron_expression or '未设置'}</code>\n"
        )
        await update.message.reply_html(
            text, reply_markup=quiet_mode_menu(chat.id, qc.enabled)
        )


# ── Callback: quiet mode actions ────────────────────────────────────────────

async def on_quiet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    group_id = int(parts[2])

    async with get_session() as session:
        qc = await _get_quiet_config(session, group_id)

        if action == "toggle":
            qc.enabled = not qc.enabled
            await session.commit()
            await query.edit_message_reply_markup(
                reply_markup=quiet_mode_menu(group_id, qc.enabled)
            )

        elif action == "set_time":
            context.user_data["awaiting_quiet_time"] = group_id
            await query.edit_message_text(
                f"⏰ <b>设置安静时间段</b>\n\n"
                f"请发送格式：<code>HH:MM-HH:MM</code>\n"
                f"例如：<code>23:00-06:00</code>（过夜模式）\n"
                f"或：<code>12:00-14:00</code>（午休模式）\n\n"
                f"发送 <code>/cancel</code> 取消",
                parse_mode=ParseMode.HTML,
            )

        elif action == "set_days":
            context.user_data["awaiting_quiet_days"] = group_id
            await query.edit_message_text(
                f"📅 <b>设置生效日期</b>\n\n"
                f"请发送星期数字（逗号分隔）：\n"
                f"• 0=周一, 1=周二, ..., 6=周日\n"
                f"例如：<code>0,1,2,3,4</code>（工作日）\n"
                f"或：<code>0,1,2,3,4,5,6</code>（每天）\n\n"
                f"发送 <code>/cancel</code> 取消",
                parse_mode=ParseMode.HTML,
            )

        elif action == "toggle_notify":
            qc.auto_notify = not qc.auto_notify
            await session.commit()
            await query.answer(f"通知已{'开启' if qc.auto_notify else '关闭'}")


# ── Text input for quiet mode ───────────────────────────────────────────────

async def on_quiet_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        for k in list(context.user_data.keys()):
            if k.startswith("awaiting_quiet_"):
                del context.user_data[k]
        await msg.reply_text(f"{EMOJI.CHECK} 已取消。")
        return

    # Time input
    if "awaiting_quiet_time" in context.user_data:
        group_id = context.user_data.pop("awaiting_quiet_time")
        try:
            parts = msg.text.strip().split("-")
            start_h, start_m = map(int, parts[0].strip().split(":"))
            end_h, end_m = map(int, parts[1].strip().split(":"))
            if not (0 <= start_h <= 23 and 0 <= start_m <= 59):
                raise ValueError
            if not (0 <= end_h <= 23 and 0 <= end_m <= 59):
                raise ValueError

            async with get_session() as session:
                qc = await _get_quiet_config(session, group_id)
                qc.start_hour = start_h
                qc.start_minute = start_m
                qc.end_hour = end_h
                qc.end_minute = end_m
                await session.commit()

            await msg.reply_text(
                f"{EMOJI.CHECK} 安静时间段已设置为 "
                f"{start_h:02d}:{start_m:02d} - {end_h:02d}:{end_m:02d}"
            )
        except (ValueError, IndexError):
            await msg.reply_text(f"{EMOJI.CROSS} 格式错误。请使用 HH:MM-HH:MM 格式。")

    # Days input
    elif "awaiting_quiet_days" in context.user_data:
        group_id = context.user_data.pop("awaiting_quiet_days")
        days_str = msg.text.strip()
        # Validate
        try:
            days = [int(d.strip()) for d in days_str.split(",")]
            if not all(0 <= d <= 6 for d in days):
                raise ValueError
        except ValueError:
            await msg.reply_text(f"{EMOJI.CROSS} 格式错误。请使用 0-6 的数字，逗号分隔。")
            return

        async with get_session() as session:
            qc = await _get_quiet_config(session, group_id)
            qc.days_of_week = days_str
            await session.commit()

        day_names = ["一", "二", "三", "四", "五", "六", "日"]
        names = [f"周{day_names[d]}" for d in days]
        await msg.reply_text(
            f"{EMOJI.CHECK} 生效日期已更新：{', '.join(names)}"
        )


# ── Manual quiet mode toggle ────────────────────────────────────────────────

async def cmd_quiet_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually toggle quiet mode immediately."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        return

    member = await chat.get_member(user.id)
    if member.status not in ("creator", "administrator"):
        return

    if _quiet_state.get(chat.id, False):
        await _open_chat(context.bot, chat.id, datetime.now())
        _quiet_state[chat.id] = False
        await update.message.reply_text(f"🔊 安静模式已手动关闭。")
    else:
        await _close_chat(context.bot, chat.id, datetime.now(), notify=True)
        _quiet_state[chat.id] = True
        await update.message.reply_text(f"🔇 安静模式已手动开启。")

    # Auto-delete notification
    async with get_session() as session:
        from database import GroupSettings
        gs_result = await session.execute(
            select(GroupSettings).where(GroupSettings.group_id == chat.id)
        )
        gs = gs_result.scalar_one_or_none()
        if gs and gs.notification_auto_delete > 0:
            context.job_queue.run_once(
                lambda ctx: ctx.bot.delete_message(chat.id, update.message.message_id),
                when=gs.notification_auto_delete,
            )


# ── Registration ────────────────────────────────────────────────────────────

QUIET_MODE_HANDLERS = [
    CommandHandler("quietmode", cmd_quiet_mode),
    CommandHandler("quietnow", cmd_quiet_now),
    CallbackQueryHandler(on_quiet_callback, pattern=r"^quiet:"),
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        on_quiet_text_input,
    ),
]

