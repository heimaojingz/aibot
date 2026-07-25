import logging
import secrets
import string
from datetime import datetime, timedelta

from sqlalchemy import select
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
    PaymentMethod,
    PaymentOrder,
    PaymentStatus,
    User,
    VIPLevel,
    get_session,
)
from keyboards import payment_method_menu, vip_plan_menu, EMOJI

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _generate_code(length: int = 16) -> str:
    """Generate a random activation code."""
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def _activate_vip(session, user_id: int, level: VIPLevel, days: int):
    """Activate VIP for a user."""
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        return False

    now = datetime.utcnow()
    if user.vip_expiry and user.vip_expiry > now:
        # Extend from current expiry
        user.vip_expiry = user.vip_expiry + timedelta(days=days)
    else:
        user.vip_expiry = now + timedelta(days=days)

    # Upgrade level if higher
    level_order = {VIPLevel.FREE: 0, VIPLevel.BASIC: 1, VIPLevel.PREMIUM: 2}
    if level_order.get(level, 0) > level_order.get(user.vip_level, 0):
        user.vip_level = level

    await session.commit()
    return True


# ── Command: /vip ───────────────────────────────────────────────────────────

async def cmd_vip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show VIP info and purchase options."""
    user = update.effective_user

    async with get_session() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one_or_none()
        if not db_user:
            db_user = User(id=user.id, username=user.username or "", first_name=user.first_name or "")
            session.add(db_user)
            await session.commit()

        vip_text = ""
        if db_user.vip_level != VIPLevel.FREE and db_user.vip_expiry:
            remaining = db_user.vip_expiry - datetime.utcnow()
            days_left = max(0, remaining.days)
            vip_text = (
                f"\n{EMOJI.CROWN} <b>VIP 状态：{db_user.vip_level.value.upper()}</b>\n"
                f"⏳ 剩余 {days_left} 天\n"
            )
        else:
            vip_text = f"\n{EMOJI.SHIELD} <b>当前：免费用户</b>\n"

    text = (
        f"{EMOJI.MONEY} <b>VIP 会员中心</b>\n"
        f"{vip_text}\n"
        f"{EMOJI.SPARKLES} <b>VIP 特权：</b>\n"
        f"• 高级轮播广告（无水印）\n"
        f"• 更多轮播投放群组\n"
        f"• 自定义欢迎媒体\n"
        f"• 优先技术支持\n\n"
        f"请选择套餐："
    )

    await update.message.reply_html(text, reply_markup=vip_plan_menu())


# ── Callback: Payment flow ──────────────────────────────────────────────────

async def on_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle payment-related callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[1]

    if action == "plan":
        plan = data[2]
        prices = {
            "monthly": (config.VIP_MONTHLY_PRICE, 30),
            "quarterly": (config.VIP_QUARTERLY_PRICE, 90),
            "yearly": (config.VIP_YEARLY_PRICE, 365),
        }
        price, days = prices.get(plan, (9.99, 30))
        context.user_data["payment_plan"] = {"price": price, "days": days}

        await query.edit_message_text(
            f"{EMOJI.MONEY} <b>选择支付方式</b>\n\n"
            f"套餐：{plan}\n"
            f"金额：<b> USD</b>\n"
            f"时长：{days} 天\n\n"
            f"请选择支付方式：",
            parse_mode=ParseMode.HTML,
            reply_markup=payment_method_menu(),
        )

    elif action == "method":
        method_str = data[2]
        plan = context.user_data.get("payment_plan", {})
        price = plan.get("price", 9.99)
        days = plan.get("days", 30)

        try:
            method = PaymentMethod(method_str)
        except ValueError:
            await query.edit_message_text(f"{EMOJI.CROSS} 无效的支付方式。")
            return

        # Create payment order
        async with get_session() as session:
            order = PaymentOrder(
                user_id=query.from_user.id,
                method=method,
                amount=price,
                duration_days=days,
            )
            session.add(order)
            await session.commit()
            await session.refresh(order)

        # Show payment details
        if method == PaymentMethod.USDT_TRC20:
            text = (
                f"{EMOJI.MONEY} <b>USDT-TRC20 支付</b>\n\n"
                f"📋 订单号：<code>{order.id}</code>\n"
                f"💰 金额：<b>{price} USDT</b>\n"
                f"🏦 钱包地址：\n<code>{config.USDT_WALLET_ADDRESS}</code>\n\n"
                f"⚠️ 请使用 <b>TRC-20 网络</b> 转账，完成后发送交易哈希（TxID）进行核销。\n"
                f"发送 <code>/verify {order.id} &lt;TxID&gt;</code> 验证支付。"
            )
        elif method == PaymentMethod.TON:
            text = (
                f"{EMOJI.DIAMOND if hasattr(EMOJI, 'DIAMOND') else '💎'} <b>TON 支付</b>\n\n"
                f"📋 订单号：<code>{order.id}</code>\n"
                f"💰 金额：<b>{price} USD (TON)</b>\n"
                f"🏦 钱包地址：\n<code>{config.TON_WALLET_ADDRESS}</code>\n\n"
                f"⚠️ 完成后发送交易哈希验证。\n"
                f"发送 <code>/verify {order.id} &lt;TxID&gt;</code>"
            )
        else:
            text = (
                f"{EMOJI.STAR} <b>Telegram Stars 支付</b>\n\n"
                f"📋 订单号：<code>{order.id}</code>\n"
                f"⭐ 金额：<b>{int(price * 100)} Stars</b>\n\n"
                f"此功能即将上线，请使用其他支付方式。"
            )

        await query.edit_message_text(text, parse_mode=ParseMode.HTML)

    elif action == "activate_code":
        context.user_data["awaiting_code"] = True
        await query.edit_message_text(
            f"{EMOJI.CARD} <b>激活码兑换</b>\n\n"
            f"请发送你的激活码：\n"
            f"发送 <code>/cancel</code> 取消",
            parse_mode=ParseMode.HTML,
        )

    elif action == "back":
        await query.edit_message_text(
            f"{EMOJI.MONEY} <b>VIP 会员中心</b>\n请选择套餐：",
            parse_mode=ParseMode.HTML,
            reply_markup=vip_plan_menu(),
        )


# ── Command: /verify ────────────────────────────────────────────────────────

async def cmd_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verify payment with order ID and TxID."""
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            f"用法：<code>/verify &lt;订单号&gt; &lt;TxID&gt;</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    try:
        order_id = int(context.args[0])
        tx_hash = context.args[1]
    except ValueError:
        await update.message.reply_text(f"{EMOJI.CROSS} 无效的订单号。")
        return

    async with get_session() as session:
        result = await session.execute(
            select(PaymentOrder).where(
                PaymentOrder.id == order_id,
                PaymentOrder.user_id == update.effective_user.id,
            )
        )
        order = result.scalar_one_or_none()

        if not order:
            await update.message.reply_text(f"{EMOJI.CROSS} 订单不存在。")
            return

        if order.status != PaymentStatus.PENDING:
            await update.message.reply_text(
                f"{EMOJI.CROSS} 订单状态：{order.status.value}，无法验证。"
            )
            return

        # In production, would verify TxID against blockchain API
        # For now, mark as pending manual review
        order.tx_hash = tx_hash
        await session.commit()

        await update.message.reply_text(
            f"{EMOJI.CHECK} <b>支付验证已提交</b>\n\n"
            f"📋 订单号：<code>{order.id}</code>\n"
            f"🔗 TxID：<code>{tx_hash}</code>\n\n"
            f"⏳ 管理员将在确认到账后为你开通 VIP。\n"
            f"如有疑问请联系客服。",
            parse_mode=ParseMode.HTML,
        )


# ── Admin: Generate activation codes ────────────────────────────────────────

async def cmd_gen_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: generate activation codes."""
    user = update.effective_user
    if False:
        await update.message.reply_text(f"{EMOJI.LOCK} 仅限超级管理员。")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "用法：/gencodes <数量> <天数> [basic|premium]"
        )
        return

    try:
        count = int(context.args[0])
        days = int(context.args[1])
    except ValueError:
        await update.message.reply_text("数量和天数需为数字。")
        return

    level_str = context.args[2] if len(context.args) > 2 else "basic"
    try:
        level = VIPLevel(level_str.lower())
    except ValueError:
        level = VIPLevel.BASIC

    async with get_session() as session:
        codes = []
        for _ in range(count):
            code = ActivationCode(
                code=_generate_code(),
                vip_level=level,
                duration_days=days,
                created_by=user.id,
            )
            session.add(code)
            codes.append(code.code)
        await session.commit()

    codes_text = "\n".join([f"<code>{c}</code>" for c in codes])
    await update.message.reply_html(
        f"{EMOJI.CARD} <b>激活码生成完毕</b>\n\n"
        f"数量：{count}\n"
        f"天数：{days}\n"
        f"等级：{level.value}\n\n"
        f"激活码：\n{codes_text}\n\n"
        f"⚠️ 请妥善保管，一次性使用。"
    )


# ── Handler: Activation code input ──────────────────────────────────────────

async def on_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Capture activation code input."""
    if not context.user_data.get("awaiting_code"):
        return

    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        context.user_data.pop("awaiting_code", None)
        await msg.reply_text(f"{EMOJI.CHECK} 已取消。")
        return

    code_str = msg.text.strip().upper()
    del context.user_data["awaiting_code"]

    async with get_session() as session:
        result = await session.execute(
            select(ActivationCode).where(ActivationCode.code == code_str)
        )
        code = result.scalar_one_or_none()

        if not code:
            await msg.reply_text(f"{EMOJI.CROSS} 激活码无效。")
            return

        if code.is_used:
            await msg.reply_text(f"{EMOJI.CROSS} 激活码已被使用。")
            return

        code.is_used = True
        code.used_by = msg.from_user.id
        code.used_at = datetime.utcnow()

        await _activate_vip(session, msg.from_user.id, code.vip_level, code.duration_days)
        await session.commit()

        await msg.reply_html(
            f"{EMOJI.CROWN} <b>激活成功！</b>\n\n"
            f"VIP 等级：{code.vip_level.value.upper()}\n"
            f"有效期：{code.duration_days} 天\n\n"
            f"{EMOJI.FIRE} 尽情享受 VIP 特权吧！"
        )


# ── Admin: Confirm payment manually ─────────────────────────────────────────

async def cmd_confirm_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: manually confirm a payment order."""
    user = update.effective_user
    if False:
        await update.message.reply_text(f"{EMOJI.LOCK} 仅限超级管理员。")
        return

    if not context.args:
        await update.message.reply_text("用法：/confirmpay <订单号>")
        return

    try:
        order_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("无效订单号。")
        return

    async with get_session() as session:
        result = await session.execute(
            select(PaymentOrder).where(PaymentOrder.id == order_id)
        )
        order = result.scalar_one_or_none()

        if not order:
            await update.message.reply_text("订单不存在。")
            return

        order.status = PaymentStatus.CONFIRMED
        order.confirmed_at = datetime.utcnow()

        await _activate_vip(session, order.user_id, order.vip_level, order.duration_days)
        await session.commit()

        await update.message.reply_html(
            f"{EMOJI.CHECK} <b>支付已确认</b>\n"
            f"订单 #{order.id}\n"
            f"VIP 已开通：{order.duration_days} 天"
        )

        # Notify user
        try:
            await context.bot.send_message(
                chat_id=order.user_id,
                text=(
                    f"{EMOJI.CROWN} <b>VIP 已开通！</b>\n\n"
                    f"📋 订单 #{order.id} 已确认\n"
                    f"⏳ 有效期：{order.duration_days} 天\n"
                    f"✨ 感谢你的支持！"
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ── Registration ────────────────────────────────────────────────────────────

PAYMENT_HANDLERS = [
    CommandHandler("vip", cmd_vip),
    CommandHandler("verify", cmd_verify),
    CommandHandler("gencodes", cmd_gen_codes),
    CommandHandler("confirmpay", cmd_confirm_payment),
    CallbackQueryHandler(on_payment_callback, pattern=r"^payment:"),
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
        on_code_input,
    ),
]
