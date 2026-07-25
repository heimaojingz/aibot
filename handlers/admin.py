import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
    ActivationCode,
    Group,
    GroupMember,
    ModLog,
    PaymentOrder,
    PaymentStatus,
    User,
    VIPLevel,
    get_session,
)
from keyboards import admin_main_menu, confirm_keyboard, EMOJI

logger = logging.getLogger(__name__)


# ── Admin Main Menu ─────────────────────────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command — show the main admin console."""
    user = update.effective_user
    if user.id not in config.SUPER_ADMIN_IDS:
        await update.message.reply_text(f"{EMOJI.LOCK} 此命令仅限超级管理员。")
        return

    text = (
        f"{EMOJI.CROWN} <b>管理员控制台</b>\n\n"
        f"欢迎回来，{user.mention_html()}！\n"
        f"请选择要管理的模块："
    )

    await update.message.reply_html(text, reply_markup=admin_main_menu())


# ── Admin Callback Router ───────────────────────────────────────────────────

async def on_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route admin callbacks to the correct handler."""
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[1]

    if action == "main":
        text = (
            f"{EMOJI.CROWN} <b>管理员控制台</b>\n\n"
            f"请选择要管理的模块："
        )
        await query.edit_message_text(
            text, parse_mode=ParseMode.HTML, reply_markup=admin_main_menu()
        )

    elif action == "dashboard":
        await _show_dashboard(query)

    elif action == "broadcast":
        context.user_data["awaiting_broadcast"] = True
        await query.edit_message_text(
            f"{EMOJI.BROADCAST} <b>群发公告</b>\n\n"
            f"请发送你要群发的公告内容（支持 HTML）：\n\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "groups":
        await _show_groups_list(query)

    elif action == "monetization":
        await _show_monetization(query)

    elif action == "cards":
        await _show_cards_management(query)

    elif action == "settings":
        await _show_settings(query)

    elif action == "carousel_list":
        # Delegate to carousel list display
        from sqlalchemy import select
        async with get_session() as session:
            result = await session.execute(select(CarouselMessage).order_by(CarouselMessage.id))
            carousels = list(result.scalars().all())
        if not carousels:
            await query.edit_message_text(
                "📣 <b>轮播消息管理</b>\n\n暂无轮播消息。点击下方按钮创建。",
                parse_mode=ParseMode.HTML,
                reply_markup=carousel_list_menu([], 0),
            )
        else:
            text = "📣 <b>轮播消息管理</b>\n\n共 {len(carousels)} 条轮播：\n"
            for i, c in enumerate(carousels, 1):
                status = "✅" if c.enabled else "⏸️"
                text += f"\n{i}. {status} <b>{c.name}</b> | {c.carousel_type.value} | 每{c.interval}秒"
            await query.edit_message_text(
                text, parse_mode=ParseMode.HTML,
                reply_markup=carousel_list_menu(carousels, 0),
            )


# ── Dashboard ───────────────────────────────────────────────────────────────

async def _show_dashboard(query):
    """Show admin dashboard with statistics."""
    async with get_session() as session:
        # Total groups
        group_count = (await session.execute(
            select(func.count(Group.id)).where(Group.is_active == True)
        )).scalar() or 0

        # Total users
        user_count = (await session.execute(
            select(func.count(User.id))
        )).scalar() or 0

        # VIP users
        vip_count = (await session.execute(
            select(func.count(User.id)).where(User.vip_level != VIPLevel.FREE)
        )).scalar() or 0

        # Today's moderation actions
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        mod_count = (await session.execute(
            select(func.count(ModLog.id)).where(ModLog.created_at >= today_start)
        )).scalar() or 0

        # Revenue (confirmed payments this month)
        month_start = today_start.replace(day=1)
        revenue_result = await session.execute(
            select(func.sum(PaymentOrder.amount)).where(
                PaymentOrder.status == PaymentStatus.CONFIRMED,
                PaymentOrder.confirmed_at >= month_start,
            )
        )
        revenue = revenue_result.scalar() or 0.0

        # Pending payments
        pending_count = (await session.execute(
            select(func.count(PaymentOrder.id)).where(
                PaymentOrder.status == PaymentStatus.PENDING
            )
        )).scalar() or 0

    text = (
        f"{EMOJI.CHART} <b>数据面板</b>\n\n"
        f"📊 <b>统计概览</b>\n"
        f"├ 管理群组：<b>{group_count}</b> 个\n"
        f"├ 总用户数：<b>{user_count}</b> 人\n"
        f"├ VIP 用户：<b>{vip_count}</b> 人\n"
        f"├ 今日处置：<b>{mod_count}</b> 次\n"
        f"├ 本月营收：<b></b>\n"
        f"└ 待处理订单：<b>{pending_count}</b> 单\n"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主菜单",
                callback_data="admin:main"
            ),
        ]]),
    )


# ── Groups Management ───────────────────────────────────────────────────────

async def _show_groups_list(query):
    """Show list of managed groups."""
    async with get_session() as session:
        result = await session.execute(
            select(Group).where(Group.is_active == True).order_by(Group.id)
        )
        groups = list(result.scalars().all())

    if not groups:
        text = (
            f"{EMOJI.ROBOT} <b>群组管理</b>\n\n"
            f"暂无管理的群组。将机器人添加到群组即可。"
        )
    else:
        lines = [f"{EMOJI.ROBOT} <b>群组管理</b>（共 {len(groups)} 个）\n"]
        for i, g in enumerate(groups[:20], 1):
            vip_badge = "👑" if g.vip_level != VIPLevel.FREE else ""
            lines.append(
                f"{i}. {vip_badge}<code>{g.id}</code> {g.title[:30]}"
            )
        if len(groups) > 20:
            lines.append(f"\n...还有 {len(groups) - 20} 个群组")
        text = "\n".join(lines)

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主菜单",
                callback_data="admin:main"
            ),
        ]]),
    )


# ── Monetization ────────────────────────────────────────────────────────────

async def _show_monetization(query):
    """Show monetization management."""
    async with get_session() as session:
        pending = (await session.execute(
            select(func.count(PaymentOrder.id)).where(
                PaymentOrder.status == PaymentStatus.PENDING
            )
        )).scalar() or 0

        total_revenue = (await session.execute(
            select(func.sum(PaymentOrder.amount)).where(
                PaymentOrder.status == PaymentStatus.CONFIRMED
            )
        )).scalar() or 0.0

    text = (
        f"{EMOJI.MONEY} <b>变现管理</b>\n\n"
        f"💰 累计营收：<b></b>\n"
        f"📋 待处理订单：<b>{pending}</b> 单\n\n"
        f"使用命令：\n"
        f"• <code>/confirmpay &lt;订单号&gt;</code> — 确认支付\n"
        f"• <code>/gencodes &lt;数量&gt; &lt;天数&gt;</code> — 生成激活码\n"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主菜单",
                callback_data="admin:main"
            ),
        ]]),
    )


# ── Cards Management ────────────────────────────────────────────────────────

async def _show_cards_management(query):
    """Show activation cards management."""
    async with get_session() as session:
        total = (await session.execute(
            select(func.count(ActivationCode.id))
        )).scalar() or 0

        used = (await session.execute(
            select(func.count(ActivationCode.id)).where(
                ActivationCode.is_used == True
            )
        )).scalar() or 0

        unused = total - used

    text = (
        f"{EMOJI.CARD} <b>卡密管理</b>\n\n"
        f"🎫 总卡密：<b>{total}</b> 张\n"
        f"✅ 已使用：<b>{used}</b> 张\n"
        f"🔑 可用：<b>{unused}</b> 张\n\n"
        f"生成新卡密：<code>/gencodes &lt;数量&gt; &lt;天数&gt; [basic|premium]</code>"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主菜单",
                callback_data="admin:main"
            ),
        ]]),
    )


# ── Settings ────────────────────────────────────────────────────────────────

async def _show_settings(query):
    """Show system settings."""
    text = (
        f"{EMOJI.SETTINGS} <b>系统设置</b>\n\n"
        f"▸ 反垃圾：{'✅ 开启' if config.ANTI_SPAM_ENABLED else '❌ 关闭'}\n"
        f"▸ 消息冷却：{config.MESSAGE_COOLDOWN}s\n"
        f"▸ 刷屏阈值：{config.MAX_MESSAGES_PER_WINDOW}条/{config.SPAM_WINDOW_SECONDS}s\n"
        f"▸ 警告上限：{config.DEFAULT_WARN_LIMIT} 次\n"
        f"▸ 默认禁言：{config.DEFAULT_MUTE_DURATION}s\n"
        f"▸ 验证码超时：{config.CAPTCHA_TIMEOUT}s\n"
        f"▸ 欢迎自动删除：{config.WELCOME_AUTO_DELETE}s\n"
        f"▸ 默认轮播间隔：{config.DEFAULT_CAROUSEL_INTERVAL}s\n\n"
        f"修改设置请编辑 <code>.env</code> 文件并重启机器人。"
    )

    await query.edit_message_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主菜单",
                callback_data="admin:main"
            ),
        ]]),
    )


# ── Broadcast Handler ───────────────────────────────────────────────────────

async def on_broadcast_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle broadcast message input from admin."""
    if not context.user_data.get("awaiting_broadcast"):
        return

    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        context.user_data.pop("awaiting_broadcast", None)
        await msg.reply_text(f"{EMOJI.CHECK} 已取消。")
        return

    del context.user_data["awaiting_broadcast"]

    # Send to all active groups
    async with get_session() as session:
        result = await session.execute(
            select(Group.id).where(Group.is_active == True)
        )
        group_ids = [row[0] for row in result.fetchall()]

    success = 0
    failed = 0
    for gid in group_ids:
        try:
            await context.bot.send_message(
                chat_id=gid,
                text=f"{EMOJI.BROADCAST} <b>📣 管理员公告</b>\n\n{msg.text_html or msg.text}",
                parse_mode=ParseMode.HTML,
            )
            success += 1
        except Exception:
            failed += 1

    await msg.reply_text(
        f"{EMOJI.CHECK} <b>群发完成</b>\n\n"
        f"✅ 成功：{success} 个群\n"
        f"❌ 失败：{failed} 个群",
        parse_mode=ParseMode.HTML,
    )


# ── Registration ────────────────────────────────────────────────────────────

ADMIN_HANDLERS = [
    CommandHandler("admin", cmd_admin),
    CallbackQueryHandler(on_admin_callback, pattern=r"^admin:"),
]

# Need import for _show_cards_management
from database import ActivationCode

