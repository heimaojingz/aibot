import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select, update
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import config
from database import (
    AsyncSession,
    WelcomeConfig,
    CarouselMessage,
    CarouselTarget,
    CarouselType,
    Group,
    User,
    get_session,
)
from keyboards import (
    carousel_list_menu,
    carousel_detail_menu,
    parse_buttons,
    EMOJI,
)

logger = logging.getLogger(__name__)

# Track active carousel jobs by carousel_id
_active_carousel_tasks: dict[int, asyncio.Task] = {}


# Helpers

async def _get_carousel(session: AsyncSession, carousel_id: int) -> Optional[CarouselMessage]:
    result = await session.execute(
        select(CarouselMessage).where(CarouselMessage.id == carousel_id)
    )
    return result.scalar_one_or_none()


async def _get_all_active_carousels(session: AsyncSession) -> list[CarouselMessage]:
    result = await session.execute(
        select(CarouselMessage).where(CarouselMessage.enabled == True)
    )
    return list(result.scalars().all())


async def _send_carousel_to_group(
    bot, carousel: CarouselMessage, group_id: int
) -> Optional[int]:
    """Send carousel message to a target group. Returns message_id or None."""
    try:
        rows = parse_buttons(carousel.buttons)
        reply_markup = InlineKeyboardMarkup(rows) if rows else None

        sent_msg: Optional[Message] = None

        # Parse media files JSON (supports legacy single file_id)
        media_files = {}
        if carousel.media_file_id:
            try:
                media_files = json.loads(carousel.media_file_id)
            except (json.JSONDecodeError, TypeError):
                media_files = {"photo": carousel.media_file_id}

        has_content = bool(carousel.content)
        has_photo = bool(media_files.get("photo"))
        has_video = bool(media_files.get("video"))
        has_gif = bool(media_files.get("gif"))

        if has_photo:
            sent_msg = await bot.send_photo(
                chat_id=group_id,
                photo=media_files["photo"],
                caption=carousel.content[:1024] if carousel.content else None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup if not (has_video or has_gif) else None,
            )
        if has_video:
            sv = await bot.send_video(
                chat_id=group_id,
                video=media_files["video"],
                caption=carousel.content[:1024] if carousel.content and not has_photo else None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup if not has_gif else None,
            )
            if sv: sent_msg = sv
        if has_gif:
            sg = await bot.send_animation(
                chat_id=group_id,
                animation=media_files["gif"],
                caption=carousel.content[:1024] if carousel.content else None,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
            if sg: sent_msg = sg
        if not (has_photo or has_video or has_gif) and has_content:
            sent_msg = await bot.send_message(
                chat_id=group_id,
                text=carousel.content,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                link_preview_options=None,
            )

        return sent_msg.message_id if sent_msg else None

    except Exception as e:
        logger.warning(f"Failed to send carousel {carousel.id} to group {group_id}: {e}")
        return None


async def _delete_previous_carousel_messages(
    bot, carousel: CarouselMessage
):
    """Delete previously sent carousel messages from target groups."""
    try:
        last_msgs = json.loads(carousel.last_sent_message_id or "{}")
        for chat_id_str, msg_id in list(last_msgs.items()):
            try:
                await bot.delete_message(
                    chat_id=int(chat_id_str),
                    message_id=msg_id,
                )
            except Exception:
                pass
        carousel.last_sent_message_id = "{}"
        async with get_session() as session:
            c = await _get_carousel(session, carousel.id)
            if c:
                c.last_sent_message_id = "{}"
                await session.commit()
    except Exception as e:
        logger.warning(f"Error cleaning previous carousel messages: {e}")


# Core: run one carousel cycle

async def _run_carousel_cycle(carousel_id: int, bot):
    """Execute one cycle: send carousel to all target groups."""
    async with get_session() as session:
        carousel = await _get_carousel(session, carousel_id)
        if not carousel or not carousel.enabled:
            return

        # Check date range
        now = datetime.utcnow()
        if carousel.start_date and now < carousel.start_date:
            return
        if carousel.end_date and now > carousel.end_date:
            return

        # Check time window
        if carousel.time_window_start > 0 or carousel.time_window_end > 0:
            hour = now.hour
            if hour < carousel.time_window_start or hour >= carousel.time_window_end:
                return

        # Delete previous messages if configured
        if carousel.delete_previous:
            await _delete_previous_carousel_messages(bot, carousel)

        # Send to each target group
        sent_map = {}
        for target in carousel.targets:
            msg_id = await _send_carousel_to_group(bot, carousel, target.group_id)
            if msg_id:
                sent_map[str(target.group_id)] = msg_id
                # Pin if configured
                if carousel.pin_message:
                    try:
                        await bot.pin_chat_message(
                            chat_id=target.group_id,
                            message_id=msg_id,
                            disable_notification=True,
                        )
                    except Exception:
                        pass

        # Save sent message IDs for future cleanup
        carousel.last_sent_message_id = json.dumps(sent_map)
        await session.commit()


# Scheduler loop (runs indefinitely)

async def carousel_scheduler_loop(bot, interval_seconds: int = 15):
    """Background task that checks and dispatches carousel messages."""
    logger.info("Carousel scheduler started")
    while True:
        try:
            async with get_session() as session:
                carousels = await _get_all_active_carousels(session)
                now = datetime.utcnow()

                for carousel in carousels:
                    # Determine if it's time to run this carousel
                    last_run = None
                    if carousel.last_sent_message_id and carousel.last_sent_message_id != "{}":
                        # Use updated_at as proxy for last run
                        last_run = carousel.updated_at

                    if last_run is None:
                        # Never run before — run if within time window
                        should_run = True
                    else:
                        elapsed = (now - last_run).total_seconds()
                        should_run = elapsed >= carousel.interval

                    if should_run:
                        # Check date range
                        if carousel.start_date and now < carousel.start_date:
                            continue
                        if carousel.end_date and now > carousel.end_date:
                            continue

                        # Check time window
                        if carousel.time_window_start > 0 or carousel.time_window_end > 0:
                            hour = now.hour
                            if hour < carousel.time_window_start or hour >= carousel.time_window_end:
                                continue

                        await _run_carousel_cycle(carousel.id, bot)

        except Exception as e:
            logger.error(f"Carousel scheduler error: {e}")

        await asyncio.sleep(interval_seconds)


# Admin Commands

async def cmd_carousel_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: list all carousels."""
    user = update.effective_user
    if False:
        await update.message.reply_text(f"{EMOJI.LOCK} 仅限超级管理员。")
        return

    async with get_session() as session:
        result = await session.execute(select(CarouselMessage).order_by(CarouselMessage.id))
        carousels = list(result.scalars().all())

    if not carousels:
        await update.message.reply_text(
            f"{EMOJI.AD} <b>轮播消息管理</b>\n\n暂无轮播消息。点击下方按钮创建。",
            parse_mode=ParseMode.HTML,
            reply_markup=carousel_list_menu([], 0),
        )
        return

    text = f"{EMOJI.AD} <b>轮播消息管理</b>\n\n共 {len(carousels)} 条轮播：\n"
    for i, c in enumerate(carousels, 1):
        status = "✅" if c.enabled else "⏸️"
        text += f"\n{i}. {status} <b>{c.name}</b> | {c.carousel_type.value} | 每{c.interval}秒"

    await update.message.reply_html(
        text,
        reply_markup=carousel_list_menu(carousels, 0),
    )


# Callback: Carousel Management

async def on_carousel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all carousel-related callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[1]

    if action == "list":
        await cmd_carousel_list(update, context)
        return

    elif action == "create":
        context.user_data["carousel_wizard"] = {"step": "name"}
        # If coming from a group context, store the group_id for auto-binding
        if "carousel_wizard_group_id" in context.user_data:
            pass  # Keep existing group_id from grpcfg:carousel_add
        await query.edit_message_text(
            "➕ <b>📣 创建轮播消息</b>\n\n"
            "<b>第1步：</b>请发送轮播名称\n"
            "例如：<code>每日福利推送</code>\n\n"
            "发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "detail":
        carousel_id = int(data[2])
        async with get_session() as session:
            c = await _get_carousel(session, carousel_id)
            if not c:
                await query.answer("轮播不存在", show_alert=True)
                return
            # Get target groups
            tgt_result = await session.execute(
                select(CarouselTarget).where(CarouselTarget.carousel_id == carousel_id)
            )
            targets = list(tgt_result.scalars().all())
            target_names = []
            for t in targets[:5]:
                gr = await session.execute(select(Group).where(Group.id == t.group_id))
                g = gr.scalar_one_or_none()
                target_names.append(g.title[:20] if g else f"<code>{t.group_id}</code>")

        interval_str = f"每{c.interval}秒"
        tw_str = f"{c.time_window_start}:00-{c.time_window_end}:00" if c.time_window_start or c.time_window_end else "不限"
        date_str = f"{c.start_date or '不限'} ~ {c.end_date or '不限'}"
        text = (
            f"📣 <b>轮播详情</b>\n\n"
            f"🏷 名称：<b>{c.name}</b>\n"
            f"🆔 ID：{c.id}\n"
            f"📝 类型：{c.carousel_type.value}\n"
            f"💬 文案：{c.content[:100] if c.content else '无'}\n"
            f"🖼 媒体：{'有' if c.media_file_id else '无'}\n"
            f"⏱ 间隔：{interval_str}\n"
            f"🗑 删除上条：{'是' if c.delete_previous else '否'}\n"
            f"📌 置顶：{'是' if c.pin_message else '否'}\n"
            f"🕐 时段：{tw_str}\n"
            f"📅 日期：{date_str}\n"
            f"🎯 目标群组（{len(targets)}个）：\n" +
            "\n".join([f"  • {n}" for n in (target_names or ["无"])]) if target_names else "🎯 目标群组：无\n"
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=carousel_detail_menu(c.id, c.enabled),
        )

    elif action == "edit":
        # Start editing carousel content
        carousel_id = int(data[2])
        context.user_data["editing_carousel"] = carousel_id
        await query.edit_message_text(
            "📝 <b>编辑轮播文案</b>\n\n"
            "请发送新的文案内容（支持 HTML 格式）：\n\n"
            "发送 /cancel 取消",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回", callback_data=f"carousel:detail:{carousel_id}")
            ]]),
        )

    elif action == "delete":
        carousel_id = int(data[2])
        async with get_session() as session:
            c = await _get_carousel(session, carousel_id)
            if c:
                await session.delete(c)
                await session.commit()
        await query.edit_message_text(
            f"{EMOJI.TRASH} 轮播消息已删除。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    f"{EMOJI.BACK} 返回列表", callback_data="admin:carousel_list"
                ),
            ]]),
        )

    elif action == "toggle":
        carousel_id = int(data[2])
        async with get_session() as session:
            c = await _get_carousel(session, carousel_id)
            if c:
                c.enabled = not c.enabled
                await session.commit()
                new_status = "✅ 已启用" if c.enabled else "⏸️ 已暂停"
                await query.answer(new_status, show_alert=True)
                # Refresh detail view
                query2 = update.callback_query
                query2.data = f"carousel:detail:{carousel_id}"
                await on_carousel_callback(update, context)

    elif action == "detail_group":
        carousel_id = int(data[2])
        group_id = int(data[3])
        async with get_session() as session:
            c = await _get_carousel(session, carousel_id)
            if not c:
                await query.answer("轮播不存在", show_alert=True)
                return
            # Get target groups
            tgt_result = await session.execute(
                select(CarouselTarget).where(CarouselTarget.carousel_id == carousel_id)
            )
            targets = list(tgt_result.scalars().all())
            target_names = []
            for t in targets[:5]:
                gr = await session.execute(select(Group).where(Group.id == t.group_id))
                g = gr.scalar_one_or_none()
                target_names.append(g.title[:20] if g else f"<code>{t.group_id}</code>")

        interval_str = f"每{c.interval}秒"
        tw_str = f"{c.time_window_start}:00-{c.time_window_end}:00" if c.time_window_start or c.time_window_end else "不限"
        date_str = f"{c.start_date or '不限'} ~ {c.end_date or '不限'}"
        text = (
            f"📣 <b>轮播详情</b>\n\n"
            f"🏷 名称：<b>{c.name}</b>\n"
            f"🆔 ID：{c.id}\n"
            f"📝 类型：{c.carousel_type.value}\n"
            f"💬 文案：{c.content[:100] if c.content else '无'}\n"
            f"🖼 媒体：{'有' if c.media_file_id else '无'}\n"
            f"⏱ 间隔：{interval_str}\n"
            f"🗑 删除上条：{'是' if c.delete_previous else '否'}\n"
            f"📌 置顶：{'是' if c.pin_message else '否'}\n"
            f"🕐 时段：{tw_str}\n"
            f"📅 日期：{date_str}\n"
            f"🎯 目标群组（{len(targets)}个）：\n" +
            "\n".join([f"  • {n}" for n in (target_names or ["无"])])
        )
        # Show detail menu with group-specific actions
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"{EMOJI.CHECK if c.enabled else EMOJI.CROSS} {'启用' if c.enabled else '暂停'}",
                        callback_data=f"carousel:toggle:{carousel_id}"
                    ),
                ],
                [
                    InlineKeyboardButton("📝 编辑", callback_data=f"carousel:edit:{carousel_id}"),
                    InlineKeyboardButton("⏱ 间隔", callback_data=f"carousel:interval:{carousel_id}"),
                ],
                [
                    InlineKeyboardButton("🗑️ 从此群移除绑定", callback_data=f"grpcfg:carousel_del:{group_id}:{carousel_id}"),
                ],
                [
                    InlineKeyboardButton("🔙 返回轮播列表", callback_data=f"grpcfg:carousel:{group_id}"),
                ],
            ]),
        )

    elif action == "targets":
        carousel_id = int(data[2])
        context.user_data["awaiting_carousel_targets"] = carousel_id
        await query.edit_message_text(
            f"{EMOJI.GLOBE} <b>设置目标群组</b>\n\n"
            f"请发送群组 ID（多个用逗号分隔）：\n"
            f"例如：<code>-1001234567890, -1009876543210</code>\n\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "interval_custom":
        wizard["step"] = "interval_custom"
        await q.edit_message_text(
            "✏️ <b>自定义间隔</b>\n\n请发送间隔分钟数，例如 120 表示2小时",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⏭️ 跳过", callback_data="carousel_wiz:skip"),
                InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
            ]]),
        )
    elif action == "interval":
        carousel_id = int(data[2])
        context.user_data["awaiting_carousel_interval"] = carousel_id
        await query.edit_message_text(
            f"{EMOJI.CLOCK} <b>设置轮播间隔</b>\n\n"
            f"请发送间隔秒数（最小 30 秒）：\n"
            f"例如：<code>600</code>（10分钟）\n\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "page":
        page = int(data[2])
        async with get_session() as session:
            result = await session.execute(select(CarouselMessage).order_by(CarouselMessage.id))
            carousels = list(result.scalars().all())
        text = f"{EMOJI.AD} <b>轮播消息管理</b>\n\n共 {len(carousels)} 条轮播：\n"
        for i, c in enumerate(carousels, 1):
            status = "✅" if c.enabled else "⏸️"
            text += f"\n{i}. {status} <b>{c.name}</b> | {c.carousel_type.value} | 每{c.interval}秒"
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=carousel_list_menu(carousels, page),
        )


# Carousel Wizard Callback

async def on_carousel_wizard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard callbacks for the carousel creation wizard."""
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action = parts[1]
    wizard = context.user_data.get("carousel_wizard", {})

    nav_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ 跳过", callback_data="carousel_wiz:skip"),
                InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
    ]])

    if action == "cancel":
        context.user_data.pop("carousel_wizard", None)
        await q.edit_message_text("❌ 已取消创建。")

    elif action == "skip":
        step = wizard.get("step", "")
        if step == "text_content":
            wizard["step"] = "media"
            await q.edit_message_text(
                "📣 <b>第3步：</b>发送媒体（图片/视频/GIF）\n\n直接发送媒体文件，或点“跳过”",
                parse_mode=ParseMode.HTML, reply_markup=nav_kb,
            )
        elif step == "media":
            wizard["step"] = "buttons"
            await q.edit_message_text(
                "🔗 <b>第4步：</b>设置按钮（可选）\n\n格式：<code>按钮文字 | URL</code>\n例如：<code>立即抢购 | https://t.me/+abc123</code>\n\n多行设置多个按钮，或点“跳过”",
                parse_mode=ParseMode.HTML, reply_markup=nav_kb,
            )
        elif step == "buttons":
            wizard["buttons"] = "[]"
            wizard["step"] = "delete_prev"
            await q.edit_message_text(
                "🗑️ <b>第5步：清理旧消息</b>\n\n"
                "每次发送新轮播前，是否自动删除上一轮的消息？\n\n"
                "💡 建议开启，避免轮播消息刷屏",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ 是（保持群面整洁）", callback_data="carousel_wiz:delprev:yes"),
                    InlineKeyboardButton("❌ 否（保留旧消息）", callback_data="carousel_wiz:delprev:no"),
                ]]),
            )
        elif step == "time_window":
            wizard["time_window_start"] = 0
            wizard["time_window_end"] = 0
            wizard.pop("_tw_start", None)
            wizard.pop("_tw_selecting", None)
            wizard["step"] = "dates"
            await q.edit_message_text(
                "📅 <b>第9步：有效日期</b>\n\n"
                "设置轮播的起止日期，超出范围自动停止\n\n"
                "格式：<code>2026-07-01-2026-12-31</code>\n\n"
                "💡 不需要限制日期可点「跳过」",
                parse_mode=ParseMode.HTML, reply_markup=nav_kb,
            )
        elif step == "dates":
            await _show_wizard_confirm(q, wizard)
        else:
            await q.answer("无法跳过", show_alert=True)

    elif action == "delprev":
        wizard["delete_previous"] = parts[2] == "yes"
        wizard["step"] = "pin_choose"
        await q.edit_message_text(
            "📌 <b>第6步：置顶消息</b>\n\n"
            "发送轮播后是否自动置顶该消息？\n\n"
            "💡 置顶后消息会始终显示在群聊顶部",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📌 是（置顶消息）", callback_data="carousel_wiz:pin:yes"),
                InlineKeyboardButton("❌ 否（不置顶）", callback_data="carousel_wiz:pin:no"),
            ]]),
        )
        return

    elif action == "pin":
        wizard["pin_message"] = parts[2] == "yes"
        wizard["step"] = "interval_choose"
        await q.edit_message_text(
            "⏱️ <b>第7步：重复频率</b>\n\n"
            "选择轮播消息的发送频率，即每隔多久发送一次",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("每1分钟", callback_data="carousel_wiz:interval:1:minutes")],
                [InlineKeyboardButton("每5分钟", callback_data="carousel_wiz:interval:5:minutes")],
                [InlineKeyboardButton("每10分钟", callback_data="carousel_wiz:interval:10:minutes")],
                [InlineKeyboardButton("每30分钟", callback_data="carousel_wiz:interval:30:minutes")],
                [InlineKeyboardButton("每1小时", callback_data="carousel_wiz:interval:1:hours")],
                [InlineKeyboardButton("每3小时", callback_data="carousel_wiz:interval:3:hours")],
                [InlineKeyboardButton("每6小时", callback_data="carousel_wiz:interval:6:hours")],
                [InlineKeyboardButton("每12小时", callback_data="carousel_wiz:interval:12:hours")],
                [InlineKeyboardButton("每24小时", callback_data="carousel_wiz:interval:24:hours")],
                [InlineKeyboardButton("✏️ 自定义分钟数", callback_data="carousel_wiz:interval_custom")],
            ]),
        )

    elif action == "interval":
        val = int(parts[2])
        unit = parts[3]
        wizard["interval"] = val * 3600 if unit == "hours" else val * 60
        wizard["repeat_unit"] = unit
        wizard["step"] = "time_window"
        await _show_time_window_grid(q, wizard)
        return


    elif action == "interval_custom":
        wizard["step"] = "interval_custom"
        await q.edit_message_text(
            "⏱️ <b>自定义间隔</b>\n\n"
            "请输入间隔分钟数，例如：\n"
            "<code>120</code> = 每2小时\n"
            "<code>45</code> = 每45分钟\n\n"
            "💡 发送数字即可",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
            ]]),
        )
        return

    elif action == "time_window_pick":
        # Two-phase: first click sets start, second click sets end
        sel = wizard.get("_tw_selecting", "start")
        h = int(parts[2])
        if sel == "start":
            wizard["_tw_start"] = h
            wizard["_tw_selecting"] = "end"
            await q.answer(f"起始时间已选：{h}:00，现在点击结束时间", show_alert=True)
            await _show_time_window_grid(q, wizard)
        else:
            start_h = wizard.get("_tw_start", 0)
            end_h = h
            if end_h <= start_h:
                await q.answer("结束时间必须大于起始时间！请重新选择", show_alert=True)
                wizard["_tw_selecting"] = "start"
                await _show_time_window_grid(q, wizard)
            else:
                wizard["time_window_start"] = start_h
                wizard["time_window_end"] = end_h
                wizard.pop("_tw_start", None)
                wizard.pop("_tw_selecting", None)
                wizard["step"] = "dates"
                await q.edit_message_text(
                    f"{EMOJI.CHECK} 时段已设置：<b>{start_h}:00 - {end_h}:00</b>\n\n"
                    "📅 <b>第9步：有效日期</b>\n\n"
                    "设置轮播的起止日期，超出范围自动停止\n\n"
                    "格式：<code>2026-07-01-2026-12-31</code>\n\n"
                    "💡 不需要限制日期可点「跳过」",
                    parse_mode=ParseMode.HTML, reply_markup=nav_kb,
                )
        return

    elif action == "time_window_set":
        wizard["time_window_start"] = int(parts[2])
        wizard["time_window_end"] = int(parts[3])
        wizard.pop("_tw_start", None)
        wizard.pop("_tw_selecting", None)
        wizard["step"] = "dates"
        await q.edit_message_text(
            "📅 <b>第9步：有效日期</b>\n\n"
            "设置轮播的起止日期，超出范围自动停止\n\n"
            "格式：<code>2026-07-01-2026-12-31</code>\n\n"
            "💡 不需要限制日期可点「跳过」",
            parse_mode=ParseMode.HTML, reply_markup=nav_kb,
        )
        return

    elif action == "window":
        try:
            if len(parts) >= 4:
                wizard["time_window_start"] = int(parts[2])
                wizard["time_window_end"] = int(parts[3])
        except (ValueError, IndexError):
            pass
        wizard["step"] = "dates"
        await q.edit_message_text(
            "📅 <b>第9步：有效日期</b>\n\n"
            "设置轮播的起止日期，超出范围自动停止\n\n"
            "格式：<code>2026-07-01-2026-12-31</code>\n\n"
            "💡 不需要限制日期可点「跳过」",
            parse_mode=ParseMode.HTML, reply_markup=nav_kb,
        )
        return

    elif action == "confirm":
        await _persist_carousel(update, context)
        return

    elif action == "type":
        ctype = parts[2]
        wizard["carousel_type"] = ctype
        wizard["step"] = "content"
        await q.edit_message_text(
            "📝 <b>第3步：</b>请发送轮播文本内容\n\n"
            "支持 HTML 格式。发送 /skip 跳过",
            parse_mode=ParseMode.HTML,
        )



async def _show_time_window_grid(q, wizard):
    """Show a button grid to select time window (hour range)."""
    sel = wizard.get("_tw_selecting", "start")
    sel_label = "起始时间" if sel == "start" else "结束时间"
    if sel == "start":
        text = (
            "🕐 <b>第8步：发送时段</b>\n\n"
            "设置轮播每天在哪个时间段内发送\n"
            "例如选择 8:00 - 22:00，则只在白天发送\n\n"
            "👇 <b>请点击起始时间</b>"
        )
    else:
        start_h = wizard.get("_tw_start", 0)
        text = (
            "🕐 <b>第8步：发送时段</b>\n\n"
            f"✅ 起始时间已选：<b>{start_h}:00</b>\n"
            "👇 <b>请点击结束时间</b>\n\n"
            "💡 结束时间必须大于起始时间"
        )
    buttons = []
    for row_start in (0, 12):
        row = []
        for h in range(row_start, row_start + 12):
            label = f"{h:02d}:00"
            if sel == "start" and wizard.get("_tw_start") == h:
                label = f"✅ {label}"
            row.append(InlineKeyboardButton(
                label, callback_data=f"carousel_wiz:time_window_pick:{h}"
            ))
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton("⏭️ 跳过（不限时段）", callback_data="carousel_wiz:time_window_set:0:24"),
    ])
    buttons.append([
        InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
    ])
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def _show_wizard_confirm(q, wizard):
    """Show confirmation summary before creating carousel."""
    wizard["step"] = "confirm"
    interval_val = wizard.get("interval", 600)
    unit = wizard.get("repeat_unit", "minutes")
    if unit == "hours":
        interval_str = f"每{interval_val // 3600}小时"
    else:
        interval_str = f"每{interval_val // 60}分钟"
    tw_start = wizard.get("time_window_start", 0)
    tw_end = wizard.get("time_window_end", 0)
    if tw_start == 0 and tw_end == 0:
        tw_str = "不限"
    elif tw_start == 0 and tw_end == 24:
        tw_str = "全天"
    else:
        tw_str = f"{tw_start}:00 - {tw_end}:00"
    text = (
        "📋 <b>轮播预览确认</b>\n\n"
        f'🏷 名称：{wizard.get("name","")}\n'
        f'💬 文案：{wizard.get("content","")[:80] or "无"}\n'
        f'🖼 媒体：{"有" if wizard.get("media_files") else "无"}\n'
        f'🔗 按钮：{"有" if wizard.get("buttons") and wizard.get("buttons") != "[]" else "无"}\n'
        f'⏱ 间隔：{interval_str}\n'
        f'🗑 删除上条：{"是" if wizard.get("delete_previous",True) else "否"}\n'
        f'📌 置顶：{"是" if wizard.get("pin_message",False) else "否"}\n'
        f'🕐 时段：{tw_str}\n'
        f'📅 日期：{wizard.get("start_date","不限")} ~ {wizard.get("end_date","不限")}\n'
    )
    await q.edit_message_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认创建", callback_data="carousel_wiz:confirm")],
            [InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel")],
        ]),
    )


async def _persist_carousel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the wizard data to database."""
    wizard = context.user_data.pop("carousel_wizard", None)
    if not wizard:
        await update.callback_query.edit_message_text("❌ 数据丢失，请重新创建。")
        return

    # Check if created from a group context (grpcfg:carousel_add)
    group_id = context.user_data.pop("carousel_wizard_group_id", None)

    async with get_session() as session:
        from datetime import datetime as dt
        carousel = CarouselMessage(
            owner_id=update.effective_user.id,
            name=wizard.get("name", "Untitled"),
            carousel_type=CarouselType("text"),
            content=wizard.get("content", ""),
            media_file_id=json.dumps(wizard.get("media_files", {})),
            buttons=wizard.get("buttons", "[]"),
            interval=wizard.get("interval", 600),
            repeat_unit=wizard.get("repeat_unit", "minutes"),
            delete_previous=wizard.get("delete_previous", True),
            pin_message=wizard.get("pin_message", False),
            time_window_start=wizard.get("time_window_start", 0),
            time_window_end=wizard.get("time_window_end", 0),
            start_date=dt.strptime(wizard["start_date"], "%Y-%m-%d") if wizard.get("start_date") else None,
            end_date=dt.strptime(wizard["end_date"], "%Y-%m-%d") if wizard.get("end_date") else None,
        )
        session.add(carousel)
        await session.commit()
        await session.refresh(carousel)

        # Auto-bind to group if created from group context
        bound_group_title = None
        if group_id:
            existing = await session.execute(
                select(CarouselTarget).where(
                    CarouselTarget.carousel_id == carousel.id,
                    CarouselTarget.group_id == group_id,
                )
            )
            if not existing.scalar_one_or_none():
                session.add(CarouselTarget(carousel_id=carousel.id, group_id=group_id))
                await session.commit()
            # Get group title
            gr = await session.execute(select(Group).where(Group.id == group_id))
            g = gr.scalar_one_or_none()
            if g:
                bound_group_title = g.title

    interval_str = f"每{carousel.interval}秒"
    tw_str = f"{carousel.time_window_start}-{carousel.time_window_end}时" if carousel.time_window_start or carousel.time_window_end else "不限"

    if bound_group_title:
        text = (
            "✅ <b>轮播创建成功！</b>\n\n"
            f"🏷 名称：{carousel.name}\n"
            f"🆔 ID：{carousel.id}\n"
            f"⏱️ 间隔：{interval_str}\n"
            f"🕐 时段：{tw_str}\n"
            f"📅 {carousel.start_date or '不限'} ~ {carousel.end_date or '不限'}\n\n"
            f"🎯 已自动绑定群组：<b>{bound_group_title}</b>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 返回轮播设置", callback_data=f"grpcfg:carousel:{group_id}")],
            [InlineKeyboardButton("🏠 返回群组面板", callback_data=f"start:manage:{group_id}")],
        ])
    else:
        text = (
            "✅ <b>轮播创建成功！</b>\n\n"
            f"🏷 名称：{carousel.name}\n"
            f"🆔 ID：{carousel.id}\n"
            f"⏱️ 间隔：{interval_str}\n"
            f"🕐 时段：{tw_str}\n"
            f"📅 {carousel.start_date or '不限'} ~ {carousel.end_date or '不限'}\n\n"
            "⚠️ 请设置目标群组，否则轮播不会发送。"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎯 设置目标群组", callback_data=f"carousel:targets:{carousel.id}")],
            [InlineKeyboardButton("📋 返回轮播列表", callback_data="admin:carousel_list")],
        ])

    await update.callback_query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=kb,
    )


CAROUSEL_HANDLERS = [
    CommandHandler("carousel", cmd_carousel_list),
    CallbackQueryHandler(on_carousel_wizard_callback, pattern=r"^carousel_wiz:"),
    CallbackQueryHandler(on_carousel_callback, pattern=r"^carousel:"),
]
