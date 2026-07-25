import logging

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode, ChatType
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import config
from database import AIConfig, AIModel, get_session, GroupSettings
from services.ai_service import (
    chat_completion,
    add_to_history,
    get_history,
    clear_history,
    get_model_info,
    list_available_models,
    MODEL_REGISTRY,
)
from keyboards import ai_config_menu, ai_model_select_keyboard, EMOJI

logger = logging.getLogger(__name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

async def _get_ai_config(session, group_id: int) -> AIConfig:
    result = await session.execute(
        select(AIConfig).where(AIConfig.group_id == group_id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = AIConfig(group_id=group_id)
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


# ── Command: /ai ────────────────────────────────────────────────────────────

async def cmd_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /ai command — AI chat with the bot."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat

    if not context.args:
        await msg.reply_html(
            f"🤖 <b>AI 问答助手</b>\n\n"
            f"用法：<code>/ai &lt;你的问题&gt;</code>\n\n"
            f"支持在群组中 @机器人 直接提问。\n\n"
            f"当前可用模型（共 {len(MODEL_REGISTRY)} 个）：\n"
            + "\n".join([f"• {v['name']} (<code>{k}</code>)" for k, v in list(MODEL_REGISTRY.items())[:5]])
            + f"\n... 共 {len(MODEL_REGISTRY)} 个"
        )
        return

    question = " ".join(context.args)

    # Determine group context
    group_id = chat.id if chat.type != ChatType.PRIVATE else user.id

    async with get_session() as session:
        ai_cfg = await _get_ai_config(session, group_id)

        if not ai_cfg.enabled:
            await msg.reply_text("🤖 AI 问答功能在本群未启用。")
            return

        if not config.AI_ENABLED:
            await msg.reply_text("🤖 AI 服务全局未启用。")
            return

        # Send typing indicator
        await context.bot.send_chat_action(chat_id=chat.id, action="typing")

        # Build messages with history
        history = get_history(group_id, ai_cfg.max_history)
        messages = history + [{"role": "user", "content": question}]

        model_name = get_model_info(ai_cfg.model.value)["name"]
        response = await chat_completion(
            model_id=ai_cfg.model.value,
            messages=messages,
            api_key=ai_cfg.api_key or "",
            api_base=ai_cfg.api_base or "",
            system_prompt=ai_cfg.system_prompt,
            temperature=ai_cfg.temperature,
            max_tokens=ai_cfg.max_tokens,
        )

        # Store history
        add_to_history(group_id, "user", question, ai_cfg.max_history)
        add_to_history(group_id, "assistant", response, ai_cfg.max_history)

        # Send response (split if too long)
        prefix = f"🤖 <b>{model_name}</b>：\n\n"
        if len(response) > 3800:
            response = response[:3800] + "\n\n...（回复过长，已截断）"
        await msg.reply_html(prefix + response)


# ── Handler: @bot mention in groups ─────────────────────────────────────────

async def on_bot_mention(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle messages that @mention the bot."""
    msg = update.message
    if not msg or not msg.text:
        return

    bot_username = context.bot.username
    mention_1 = f"@{bot_username}"
    mention_2 = f"@{bot_username.lower()}"

    if mention_1 not in msg.text and mention_2 != msg.text and mention_2 not in msg.text:
        return

    # Extract question (remove the mention)
    question = msg.text.replace(mention_1, "").replace(mention_2, "").strip()
    if not question:
        await msg.reply_html(
            f"🤖 你好！我是 AI 助手。\n"
            f"请 <code>@{bot_username} 你的问题</code> 来向我提问。\n"
            f"或使用 <code>/ai 你的问题</code>"
        )
        return

    chat = update.effective_chat
    user = update.effective_user
    group_id = chat.id

    async with get_session() as session:
        ai_cfg = await _get_ai_config(session, group_id)

        if not ai_cfg.enabled or not config.AI_ENABLED:
            await msg.reply_html("🤖 AI 问答功能未启用。")
            return

        await context.bot.send_chat_action(chat_id=chat.id, action="typing")

        history = get_history(group_id, ai_cfg.max_history)
        messages = history + [{"role": "user", "content": question}]

        model_name = get_model_info(ai_cfg.model.value)["name"]
        response = await chat_completion(
            model_id=ai_cfg.model.value,
            messages=messages,
            api_key=ai_cfg.api_key or "",
            api_base=ai_cfg.api_base or "",
            system_prompt=ai_cfg.system_prompt,
            temperature=ai_cfg.temperature,
            max_tokens=ai_cfg.max_tokens,
        )

        add_to_history(group_id, "user", question, ai_cfg.max_history)
        add_to_history(group_id, "assistant", response, ai_cfg.max_history)

        prefix = f"🤖 <b>{model_name}</b>：\n\n"
        if len(response) > 3800:
            response = response[:3800] + "\n\n...（回复过长，已截断）"
        await msg.reply_html(prefix + response)


# ── AI Config Callback ──────────────────────────────────────────────────────

async def on_ai_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    action = parts[1]
    group_id = int(parts[2])

    async with get_session() as session:
        ai_cfg = await _get_ai_config(session, group_id)

        if action == "toggle":
            ai_cfg.enabled = not ai_cfg.enabled
            await session.commit()
            await query.edit_message_reply_markup(
                reply_markup=ai_config_menu(group_id, ai_cfg.enabled, ai_cfg.model.value)
            )

        elif action == "model_select":
            page = int(parts[3]) if len(parts) > 3 else 0
            await query.edit_message_text(
                f"🤖 <b>选择 AI 模型</b>\n\n请从以下模型中选择（共 {len(MODEL_REGISTRY)} 个）：",
                parse_mode=ParseMode.HTML,
                reply_markup=ai_model_select_keyboard(group_id, page),
            )

        elif action == "set_model":
            model_id = parts[3]
            if model_id in MODEL_REGISTRY:
                ai_cfg.model = AIModel(model_id)
                await session.commit()
                await query.edit_message_text(
                    f"{EMOJI.CHECK} AI 模型已切换为：<b>{MODEL_REGISTRY[model_id]['name']}</b>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=ai_config_menu(group_id, ai_cfg.enabled, model_id),
                )

        elif action == "config":
            await query.edit_message_text(
                f"🤖 <b>AI 问答配置</b>\n\n"
                f"状态：{'✅ 启用' if ai_cfg.enabled else '❌ 关闭'}\n"
                f"当前模型：<b>{get_model_info(ai_cfg.model.value)['name']}</b>\n"
                f"最大历史：{ai_cfg.max_history} 轮\n"
                f"温度：{ai_cfg.temperature}\n"
                f"系统提示词：\n<blockquote expandable>{ai_cfg.system_prompt[:200]}</blockquote>",
                parse_mode=ParseMode.HTML,
                reply_markup=ai_config_menu(group_id, ai_cfg.enabled, ai_cfg.model.value),
            )

        elif action == "prompt":
            context.user_data["awaiting_ai_prompt"] = group_id
            await query.edit_message_text(
                f"📝 <b>设置系统提示词</b>\n\n"
                f"请发送新的系统提示词（支持 HTML）：\n"
                f"当前：<code>{ai_cfg.system_prompt[:100]}</code>\n\n"
                f"发送 <code>/cancel</code> 取消",
                parse_mode=ParseMode.HTML,
            )

        elif action == "info":
            model_info = get_model_info(ai_cfg.model.value)
            await query.answer(
                f"模型：{model_info['name']}\n"
                f"提供商：{model_info['provider']}\n"
                f"最大输出：{model_info['max_tokens']} tokens",
                show_alert=True,
            )


# ── AI Prompt Text Input ────────────────────────────────────────────────────

async def on_ai_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    if msg.text == "/cancel":
        context.user_data.pop("awaiting_ai_prompt", None)
        await msg.reply_text(f"{EMOJI.CHECK} 已取消。")
        return

    if "awaiting_ai_prompt" in context.user_data:
        group_id = context.user_data.pop("awaiting_ai_prompt")
        async with get_session() as session:
            ai_cfg = await _get_ai_config(session, group_id)
            ai_cfg.system_prompt = msg.text
            await session.commit()
        await msg.reply_text(
            f"{EMOJI.CHECK} 系统提示词已更新。",
            reply_markup=ai_config_menu(group_id, ai_cfg.enabled, ai_cfg.model.value),
        )


# ── Command: /clearai ───────────────────────────────────────────────────────

async def cmd_clearai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear AI conversation history."""
    chat = update.effective_chat
    group_id = chat.id if chat.type != ChatType.PRIVATE else update.effective_user.id
    clear_history(group_id)
    await update.message.reply_text(
        f"{EMOJI.CHECK} AI 对话历史已清空。"
    )


# ── AI Config command ───────────────────────────────────────────────────────

async def cmd_aiconfig(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show AI config panel."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == ChatType.PRIVATE:
        group_id = user.id
    else:
        member = await chat.get_member(user.id)
        if member.status not in ("creator", "administrator"):
            await update.message.reply_text(f"{EMOJI.LOCK} 仅限管理员。")
            return
        group_id = chat.id

    async with get_session() as session:
        ai_cfg = await _get_ai_config(session, group_id)
        model_name = get_model_info(ai_cfg.model.value)["name"]

    text = (
        f"🤖 <b>AI 问答配置</b>\n\n"
        f"▸ 状态：{'✅ 启用' if ai_cfg.enabled else '❌ 关闭'}\n"
        f"▸ 模型：{model_name} (<code>{ai_cfg.model.value}</code>)\n"
        f"▸ 最大历史：{ai_cfg.max_history} 轮\n"
        f"▸ 温度：{ai_cfg.temperature}\n"
    )
    await update.message.reply_html(
        text,
        reply_markup=ai_config_menu(group_id, ai_cfg.enabled, ai_cfg.model.value),
    )


# ── Registration ────────────────────────────────────────────────────────────

AI_HANDLERS = [
    CommandHandler("ai", cmd_ai),
    CommandHandler("clearai", cmd_clearai),
    CommandHandler("aiconfig", cmd_aiconfig),
    CallbackQueryHandler(on_ai_callback, pattern=r"^ai:"),
    MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.GROUPS,
        on_bot_mention,
    ),
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        on_ai_text_input,
    ),
]
