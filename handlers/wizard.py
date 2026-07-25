import asyncio
import json
import logging
from datetime import datetime

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, MessageHandler, filters

from config import config
from database import CarouselMessage, CarouselTarget, CarouselType, Group, WelcomeConfig, get_session
from keyboards import parse_buttons, EMOJI

logger = logging.getLogger(__name__)


async def unified_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    ud = context.user_data

    if msg.text == "/cancel":
        cleared = []
        for key in list(ud.keys()):
            if any(key.startswith(prefix) for prefix in (
                "carousel_wizard", "creating_carousel", "awaiting_carousel",
                "carousel_draft", "carousel_draft_step",
                "awaiting_welcome", "editing_welcome", "awaiting_broadcast",
            )):
                cleared.append(key)
                del ud[key]
        if cleared:
            await msg.reply_text(f"{EMOJI.CHECK} 已取消当前操作。")
        else:
            await msg.reply_text(f"{EMOJI.CHECK} 无需取消的操作。")
        return

    if "carousel_wizard" in ud:
        return await _handle_carousel_wizard_step(update, context)
    if context.user_data.get("carousel_draft_step"):
        return await _handle_carousel_draft_step(update, context)
    if "creating_carousel" in ud:
        return await _handle_creating_carousel(update, context)
    if "awaiting_carousel_targets" in ud:
        return await _handle_carousel_targets(update, context)
    if "awaiting_carousel_interval" in ud:
        return await _handle_carousel_interval(update, context)
    if "editing_welcome" in ud:
        return await _handle_welcome_edit(update, context)
    if "awaiting_welcome_text" in ud:
        return await _handle_welcome_text_input(update, context)
    if "awaiting_welcome_delete" in ud:
        return await _handle_welcome_delete_input(update, context)
    if "awaiting_welcome_buttons" in ud:
        return await _handle_welcome_buttons_input(update, context)
    if "awaiting_broadcast" in ud:
        return await _handle_broadcast_input(update, context)


async def unified_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    ud = context.user_data
    if "carousel_wizard" in ud:
        wizard = ud["carousel_wizard"]
        if wizard.get("step") == "media":
            return await _handle_carousel_wizard_media(update, context)
    if ud.get("carousel_draft_step") == "media":
        return await _handle_carousel_draft_media(update, context)
    if "awaiting_welcome_media" in ud:
        return await _handle_welcome_media_input(update, context)


# ===== CAROUSEL WIZARD STEP HANDLER =====

async def _handle_carousel_wizard_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    wizard = context.user_data["carousel_wizard"]
    step = wizard.get("step", "name")

    skip_btn = InlineKeyboardButton("⏭️ 跳过", callback_data="carousel_wiz:skip")
    cancel_btn = InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel")
    nav_kb = InlineKeyboardMarkup([[skip_btn, cancel_btn]])

    if step == "name":
        wizard["name"] = msg.text.strip()
        wizard["step"] = "text_content"
        wizard["media_files"] = {}
        await msg.reply_html(
            '📝 <b>第2步：轮播文案</b>\n\n'
            '轮播每次发送时显示的文字内容，支持 HTML 格式\n'
            '例如：\n<code>&lt;b&gt;今日福利&lt;/b&gt;\n限时抢购，先到先得！</code>\n\n'
            '💡 提示：不需要文案可点“跳过”',
            reply_markup=nav_kb,
        )

    elif step == "text_content":
        wizard["content"] = msg.text_html or msg.text
        wizard["step"] = "media"
        await msg.reply_html(
            '📣 <b>第3步：媒体附件</b>\n\n'
            '发送图片、视频或 GIF，会将其作为轮播的配图附件\n\n'
            '💡 提示：不需要附件可点「跳过」',
            reply_markup=nav_kb,
        )

    elif step == "buttons":
        if msg.text.strip() != "/skip":
            buttons = _parse_simple_buttons(msg.text)
            if not buttons:
                await msg.reply_html(
                    f"{EMOJI.CROSS} <b>格式错误</b>\n\n每行格式：<code>按钮文字 | URL</code>\n例如：<code>立即抢购 | https://t.me/xxx</code>\n\n请重新发送，或点“跳过”",
                    reply_markup=nav_kb,
                )
                return
            wizard["buttons"] = json.dumps(buttons)
        else:
            wizard["buttons"] = "[]"
        wizard["step"] = "delete_prev"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 是（保持群面整洁）", callback_data="carousel_wiz:delprev:yes"),
             InlineKeyboardButton("❌ 否（保留旧消息）", callback_data="carousel_wiz:delprev:no")],
        ])
        await msg.reply_html(
            '🗑️ <b>第5步：清理旧消息</b>\n\n'
            '每次发送新轮播前，是否自动删除上一条轮播消息？\n\n'
            '💡 建议开启，避免轮播消息刷屏',
            reply_markup=kb,
        )

    elif step == "interval_custom":
        try:
            mins = int(msg.text.strip())
            wizard["interval"] = mins * 60
            wizard["repeat_unit"] = "minutes"
            wizard["step"] = "time_window"
            # Use time window button grid
            text = (
                "🕐 <b>第8步：发送时段</b>\n\n"
                "选择轮播每天在哪个时间段内发送\n"
                "例如选择 8:00 - 22:00，则只在白天发送\n\n"
                "<b>请先点击「起始时间」再点击「结束时间」</b>"
            )
            wizard["_tw_selecting"] = "start"
            buttons = []
            for row_start in (0, 12):
                row = []
                for h in range(row_start, row_start + 12):
                    row.append(InlineKeyboardButton(
                        f"{h:02d}:00", callback_data=f"carousel_wiz:time_window_pick:{h}"
                    ))
                buttons.append(row)
            buttons.append([
                InlineKeyboardButton("⏭️ 跳过（不限时段）", callback_data="carousel_wiz:time_window_set:0:24"),
            ])
            buttons.append([
                InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
            ])
            await msg.reply_html(text, reply_markup=InlineKeyboardMarkup(buttons))
        except ValueError:
            await msg.reply_html(
                f"{EMOJI.CROSS} 请输入有效数字（分钟）。例如：<code>120</code>",
                reply_markup=InlineKeyboardMarkup([[cancel_btn]]),
            )
        return

    elif step == "time_window":
        if msg.text.strip() != "/skip":
            parts = msg.text.strip().split("-")
            if len(parts) == 2:
                try:
                    wizard["time_window_start"] = int(parts[0])
                    wizard["time_window_end"] = int(parts[1])
                except ValueError:
                    await msg.reply_html(
                        f"{EMOJI.CROSS} <b>格式错误</b>\n\n请用按钮选择或输入 8-22 格式",
                        reply_markup=nav_kb,
                    )
                    return
        else:
            wizard["time_window_start"] = 0
            wizard["time_window_end"] = 0
        wizard["step"] = "dates"
        await msg.reply_html(
            '📅 <b>第9步：有效日期</b>\n\n'
            '设置轮播的起止日期，超出日期范围后自动停止\n\n'
            '格式：<code>2026-07-01-2026-12-31</code>\n\n'
            '💡 提示：不需要限制日期可点“跳过”',
            reply_markup=nav_kb,
        )

    elif step == "dates":
        text = msg.text.strip()
        parts = text.split("-")
        if len(parts) >= 6:
            start_str = "-".join(parts[:3])
            end_str = "-".join(parts[3:6])
            wizard["start_date"] = start_str
            wizard["end_date"] = end_str
        await _show_carousel_summary(update, context)

async def _handle_creating_carousel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    parts = [p.strip() for p in msg.text.split("|")]
    if len(parts) < 3:
        await msg.reply_text(
            f'{EMOJI.CROSS} 格式错误。请使用：<code>名称 | 类型 | 间隔秒数</code>',
            parse_mode=ParseMode.HTML,
        )
        return
    name, ctype_str, interval_str = parts[0], parts[1], parts[2]
    try:
        ctype = CarouselType(ctype_str.lower())
    except ValueError:
        await msg.reply_text(f"{EMOJI.CROSS} 无效类型。可选：text / photo / video / gif")
        return
    try:
        interval = int(interval_str)
    except ValueError:
        await msg.reply_text(f"{EMOJI.CROSS} 间隔应为数字（秒）。")
        return
    del context.user_data["creating_carousel"]
    context.user_data["carousel_draft"] = {"name": name, "carousel_type": ctype.value, "interval": interval}
    context.user_data["carousel_draft_step"] = "content"
    await msg.reply_html(
        f"{EMOJI.EDIT} <b>轮播基本信息已记录</b>\n\n名称：{name}\n类型：{ctype.value}\n间隔：{interval}s\n\n"
        "现在请发送轮播的文本内容（支持 HTML 格式）：",
    )


async def _handle_carousel_draft_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    step = context.user_data["carousel_draft_step"]
    if step == "content":
        context.user_data["carousel_draft"]["content"] = msg.text_html or msg.text
        if context.user_data["carousel_draft"]["carousel_type"] == "text":
            await _finalize_carousel_creation(update, context)
        else:
            context.user_data["carousel_draft_step"] = "media"
            await msg.reply_html(
                f"{EMOJI.PHOTO} 现在请发送媒体文件（图片/视频/GIF），或发送 /skip 跳过：",
            )
    elif step == "buttons":
        try:
            json.loads(msg.text)
        except json.JSONDecodeError:
            await msg.reply_text(f"{EMOJI.CROSS} JSON 格式无效，请重新发送。")
            return
        context.user_data["carousel_draft"]["buttons"] = msg.text
        await _finalize_carousel_creation(update, context)


async def _finalize_carousel_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = context.user_data.pop("carousel_draft", None)
    context.user_data.pop("carousel_draft_step", None)
    if not draft:
        return
    async with get_session() as session:
        carousel = CarouselMessage(
            owner_id=update.effective_user.id, name=draft["name"],
            carousel_type=CarouselType(draft["carousel_type"]),
            content=draft.get("content", ""), interval=draft["interval"],
            buttons=draft.get("buttons", "[]"),
        )
        session.add(carousel)
        await session.commit()
        await session.refresh(carousel)
        await update.message.reply_text(
            f"{EMOJI.CHECK} 轮播消息创建成功！\n\nID：{carousel.id}\n名称：{carousel.name}\n"
            f"类型：{carousel.carousel_type.value}\n间隔：{carousel.interval}s\n\n"
            "⚠️ 请记得设置目标群组，否则轮播不会发送。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🌐 设置目标群组", callback_data=f"carousel:targets:{carousel.id}"),
                InlineKeyboardButton("⬅️ 返回列表", callback_data="admin:carousel_list"),
            ]]),
        )


async def _handle_carousel_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    carousel_id = context.user_data.pop("awaiting_carousel_targets")
    group_ids = [gid.strip() for gid in msg.text.split(",") if gid.strip()]
    async with get_session() as session:
        r = await session.execute(select(CarouselMessage).where(CarouselMessage.id == carousel_id))
        c = r.scalar_one_or_none()
        if not c:
            await msg.reply_text("轮播不存在。")
            return
        for t in c.targets:
            await session.delete(t)
        for gid_str in group_ids:
            try:
                gid = int(gid_str)
                gr = await session.execute(select(Group).where(Group.id == gid))
                if not gr.scalar_one_or_none():
                    session.add(Group(id=gid, title=f"Group {gid}"))
                c.targets.append(CarouselTarget(carousel_id=carousel_id, group_id=gid))
            except ValueError:
                await msg.reply_text(f"{EMOJI.CROSS} 无效群组 ID：{gid_str}")
        await session.commit()
    await msg.reply_text(
        f"{EMOJI.CHECK} 目标群组已更新！共 {len(group_ids)} 个群。",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("⬅️ 返回轮播详情", callback_data=f"carousel:detail:{carousel_id}"),
        ]]),
    )


async def _handle_carousel_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    carousel_id = context.user_data.pop("awaiting_carousel_interval")
    try:
        interval = int(msg.text.strip())
        if interval < 30:
            await msg.reply_text(f"{EMOJI.CROSS} 间隔不能小于 30 秒。")
            return
        async with get_session() as session:
            r = await session.execute(select(CarouselMessage).where(CarouselMessage.id == carousel_id))
            c = r.scalar_one_or_none()
            if c:
                c.interval = interval
                await session.commit()
        await msg.reply_text(
            f"{EMOJI.CHECK} 轮播间隔已更新为 {interval} 秒。",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("⬅️ 返回轮播详情", callback_data=f"carousel:detail:{carousel_id}"),
            ]]),
        )
    except ValueError:
        await msg.reply_text(f"{EMOJI.CROSS} 请输入有效数字。")


async def _handle_carousel_wizard_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    wizard = context.user_data["carousel_wizard"]
    media_files = wizard.get("media_files", {})
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
        media_type = "gif"
    else:
        await msg.reply_text(f"{EMOJI.CROSS} 请发送图片、视频或 GIF。")
        return

    media_files[media_type] = file_id
    wizard["media_files"] = media_files
    wizard["step"] = "buttons"

    nav_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⏭️ 跳过", callback_data="carousel_wiz:skip"),
        InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel"),
    ]])
    await msg.reply_html(
        f"{EMOJI.CHECK} {media_type} 已接收！\n\n"
        '🔗 <b>第4步：按钮链接</b>\n\n'
        '轮播消息下方带的链接按钮，用户点击后跳转到指定链接\n\n'
        '格式：每行一个，<code>按钮文字 | URL</code>\n'
        '例如：<code>立即抢购 | https://t.me/xxx</code>\n\n'
        '💡 提示：多行可设多个按钮，不需要点“跳过”',
        reply_markup=nav_kb,
    )


async def _handle_carousel_draft_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if msg.text and msg.text == "/skip":
        context.user_data["carousel_draft_step"] = "buttons"
        await msg.reply_html(
            f"{EMOJI.LINK} 请以 JSON 格式发送按钮配置（或发送 /skip 跳过）：\n\n"
            '示例：<code>[["text":"立即购买","url":"https://..."]]</code>',
        )
        return
    file_id = None
    if msg.photo:
        file_id = msg.photo[-1].file_id
    elif msg.video:
        file_id = msg.video.file_id
    elif msg.animation:
        file_id = msg.animation.file_id
    else:
        await msg.reply_text(f"{EMOJI.CROSS} 请发送图片、视频或 GIF。")
        return
    context.user_data["carousel_draft"]["media_file_id"] = file_id
    context.user_data["carousel_draft_step"] = "buttons"
    await msg.reply_html(
        f"{EMOJI.CHECK} 媒体已接收！\n\n"
        f"{EMOJI.LINK} 现在请以 JSON 格式发送按钮配置（或发送 /skip 跳过）：\n\n"
        '示例：<code>[["text":"立即购买","url":"https://..."]]</code>',
    )


async def _handle_welcome_edit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    gid = context.user_data.pop("editing_welcome")
    async with get_session() as s:
        r = await s.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == gid))
        wc = r.scalar_one_or_none()
        if not wc:
            wc = WelcomeConfig(group_id=gid)
            s.add(wc)
        wc.message_template = msg.text_html or msg.text
        await s.commit()
    await msg.reply_text("✅ 欢迎词已更新！", reply_markup=InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")
    ]]))


async def _handle_welcome_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    group_id = context.user_data.pop("awaiting_welcome_text")
    async with get_session() as session:
        r = await session.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == group_id))
        wc = r.scalar_one_or_none()
        if not wc:
            wc = WelcomeConfig(group_id=group_id)
            session.add(wc)
        wc.message_template = msg.text_html or msg.text
        await session.commit()
    await msg.reply_text(f"{EMOJI.CHECK} 欢迎消息模板已更新！")


async def _handle_welcome_delete_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    group_id = context.user_data.pop("awaiting_welcome_delete")
    try:
        seconds = int(msg.text.strip())
        async with get_session() as session:
            r = await session.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == group_id))
            wc = r.scalar_one_or_none()
            if wc:
                wc.auto_delete_after = seconds
                await session.commit()
        await msg.reply_text(f"{EMOJI.CHECK} 自动删除时间已设置为 {seconds} 秒。")
    except ValueError:
        await msg.reply_text(f"{EMOJI.CROSS} 请输入有效的数字。")


async def _handle_welcome_buttons_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    group_id = context.user_data.pop("awaiting_welcome_buttons")
    try:
        json.loads(msg.text)
    except json.JSONDecodeError:
        await msg.reply_text(f"{EMOJI.CROSS} JSON 格式无效，操作已取消。")
        return
    async with get_session() as session:
        r = await session.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == group_id))
        wc = r.scalar_one_or_none()
        if wc:
            wc.buttons = msg.text
            await session.commit()
    await msg.reply_text(f"{EMOJI.CHECK} 欢迎消息按钮已更新！")


async def _handle_welcome_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    group_id = context.user_data.pop("awaiting_welcome_media")
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
        await msg.reply_text(f"{EMOJI.CROSS} 请发送图片、视频或 GIF。操作已取消。")
        return
    async with get_session() as session:
        r = await session.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == group_id))
        wc = r.scalar_one_or_none()
        if wc:
            wc.media_file_id = file_id
            wc.media_type = media_type
            await session.commit()
    await msg.reply_text(f"{EMOJI.CHECK} 欢迎媒体附件已更新为 {media_type}！")


async def _handle_broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    del context.user_data["awaiting_broadcast"]
    async with get_session() as session:
        result = await session.execute(select(Group.id).where(Group.is_active == True))
        group_ids = [row[0] for row in result.fetchall()]
    success = 0
    failed = 0
    for gid in group_ids:
        try:
            await context.bot.send_message(
                chat_id=gid,
                text=f'{EMOJI.BROADCAST} <b>📣 管理员公告</b>\n\n{msg.text_html or msg.text}',
                parse_mode=ParseMode.HTML,
            )
            success += 1
        except Exception:
            failed += 1
    await msg.reply_text(
        f"{EMOJI.CHECK} <b>群发完成</b>\n\n✅ 成功：{success} 个群\n❌ 失败：{failed} 个群",
        parse_mode=ParseMode.HTML,
    )


def _parse_simple_buttons(text: str) -> list[list[dict]]:
    rows = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            rows.append([{"text": parts[0].strip(), "url": parts[1].strip()}])
    return rows


async def _show_carousel_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wizard = context.user_data["carousel_wizard"]
    wizard["step"] = "confirm"
    text = (
        "📋 <b>轮播预览确认</b>\n\n"
        f'🏷 名称：{wizard.get("name","")}\n'
        f'📝 类型：{wizard.get("carousel_type","text")}\n'
        f'💬 内容：{wizard.get("content","")[:100]}\n'
        f'⏱ 间隔：{wizard.get("interval",600)}秒\n'
        f'🗑 删除上条：{"是" if wizard.get("delete_previous",True) else "否"}\n'
        f'📌 置顶：{"是" if wizard.get("pin_message",False) else "否"}\n'
        f'🕐 时段：{wizard.get("time_window_start",0)}-{wizard.get("time_window_end",0)}时\n'
        f'📅 日期：{wizard.get("start_date","不限")} ~ {wizard.get("end_date","不限")}\n'
    )
    await update.message.reply_html(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ 确认创建", callback_data="carousel_wiz:confirm")],
            [InlineKeyboardButton("❌ 取消", callback_data="carousel_wiz:cancel")],
        ]),
    )


WIZARD_HANDLERS = [
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & ~filters.UpdateType.CHANNEL_POST,
        unified_text_handler,
    ),
    MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.ANIMATION,
        unified_media_handler,
    ),
]
