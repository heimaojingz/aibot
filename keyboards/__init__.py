import json
from typing import Optional

from config import config

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


# ── Emoji constants ─────────────────────────────────────────────────────────

class EMOJI:
    SECURITY = "🛡️"
    AD = "📢"
    SETTINGS = "⚙️"
    CHECK = "✅"
    CROSS = "❌"
    WARN = "⚠️"
    BAN = "🚫"
    MUTE = "🔇"
    UNMUTE = "🔊"
    KICK = "👢"
    MONEY = "💎"
    CROWN = "👑"
    CARD = "🎫"
    CHART = "📊"
    BROADCAST = "📣"
    ROBOT = "🤖"
    HOME = "🏠"
    BACK = "⬅️"
    NEXT = "➡️"
    TRASH = "🗑️"
    EDIT = "✏️"
    PLUS = "➕"
    MINUS = "➖"
    CLOCK = "⏰"
    PHOTO = "🖼️"
    VIDEO = "🎬"
    GLOBE = "🌐"
    LINK = "🔗"
    KEY = "🔑"
    STAR = "⭐"
    SPARKLES = "✨"
    FIRE = "🔥"
    SHIELD = "🛡️"
    BELL = "🔔"
    LOCK = "🔒"
    UNLOCK = "🔓"


# ── Main Menu (Admin Private Chat) ──────────────────────────────────────────

def admin_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHART} 数据面板", callback_data="admin:dashboard"
            ),
            InlineKeyboardButton(
                f"{EMOJI.AD} 轮播管理", callback_data="admin:carousel_list"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BROADCAST} 群发公告", callback_data="admin:broadcast"
            ),
            InlineKeyboardButton(
                f"{EMOJI.ROBOT} 群组管理", callback_data="admin:groups"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.MONEY} 变现管理", callback_data="admin:monetization"
            ),
            InlineKeyboardButton(
                f"{EMOJI.CARD} 卡密系统", callback_data="admin:cards"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.SETTINGS} 系统设置", callback_data="admin:settings"
            ),
        ],
    ])


# ── Welcome Config ──────────────────────────────────────────────────────────

def welcome_config_menu(group_id: int, enabled: bool, captcha_enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHECK if enabled else EMOJI.CROSS} 欢迎消息: {'开' if enabled else '关'}",
                callback_data=f"welcome:toggle:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.EDIT} 编辑欢迎词",
                callback_data=f"welcome:edit_text:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.PHOTO} 设置媒体",
                callback_data=f"welcome:set_media:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.ROBOT} 验证码: {'开' if captcha_enabled else '关'}",
                callback_data=f"welcome:toggle_captcha:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.CLOCK} 自动删除",
                callback_data=f"welcome:auto_delete:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.LINK} 按钮设置",
                callback_data=f"welcome:buttons:{group_id}"
            ),
        ],
    ])


# ── CAPTCHA Buttons ─────────────────────────────────────────────────────────

def captcha_button_keyboard(user_id: int, group_id: int) -> InlineKeyboardMarkup:
    """Simple 'I am human' button captcha."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.SHIELD} 点此验证：我是人类",
                callback_data=f"captcha:verify:{user_id}:{group_id}"
            ),
        ],
    ])


def captcha_math_keyboard(user_id: int, group_id: int, correct_answer: int) -> InlineKeyboardMarkup:
    """Math captcha with 4 answer choices."""
    import random
    options = {correct_answer}
    while len(options) < 4:
        options.add(random.randint(max(1, correct_answer - 10), correct_answer + 10))
    opts = list(options)
    random.shuffle(opts)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                str(opt),
                callback_data=f"captcha:math:{opt}:{correct_answer}:{user_id}:{group_id}"
            )
            for opt in opts[:2]
        ],
        [
            InlineKeyboardButton(
                str(opt),
                callback_data=f"captcha:math:{opt}:{correct_answer}:{user_id}:{group_id}"
            )
            for opt in opts[2:]
        ],
    ])


# ── Carousel Management ─────────────────────────────────────────────────────

def carousel_list_menu(carousels: list, page: int = 0) -> InlineKeyboardMarkup:
    """Show list of carousel messages with pagination."""
    buttons = []
    for c in carousels:
        status = f"{EMOJI.CHECK}" if c.enabled else f"{EMOJI.CROSS}"
        buttons.append([
            InlineKeyboardButton(
                f"{status} {c.name} ({c.carousel_type.value})",
                callback_data=f"carousel:detail:{c.id}"
            ),
        ])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            f"{EMOJI.BACK} 上一页", callback_data=f"carousel:page:{page - 1}"
        ))
    nav.append(InlineKeyboardButton(
        f"{EMOJI.PLUS} 新建轮播", callback_data="carousel:create"
    ))
    buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(
            f"{EMOJI.BACK} 返回主菜单", callback_data="admin:main"
        ),
    ])
    return InlineKeyboardMarkup(buttons)


def carousel_detail_menu(carousel_id: int, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHECK if enabled else EMOJI.CROSS} {'启用' if enabled else '暂停'}",
                callback_data=f"carousel:toggle:{carousel_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.EDIT} 编辑内容",
                callback_data=f"carousel:edit:{carousel_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.GLOBE} 目标群组",
                callback_data=f"carousel:targets:{carousel_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.CLOCK} 间隔设置",
                callback_data=f"carousel:interval:{carousel_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.TRASH} 删除此轮播",
                callback_data=f"carousel:delete:{carousel_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回列表", callback_data="admin:carousel_list"
            ),
        ],
    ])


# ── Moderation ──────────────────────────────────────────────────────────────

def mod_action_keyboard(user_id: int, group_id: int, warn_count: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.WARN} 警告 ({warn_count})",
                callback_data=f"mod:warn:{user_id}:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.MUTE} 禁言",
                callback_data=f"mod:mute:{user_id}:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.KICK} 踢出",
                callback_data=f"mod:kick:{user_id}:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.BAN} 封禁",
                callback_data=f"mod:ban:{user_id}:{group_id}"
            ),
        ],
    ])


# ── Payment / VIP ───────────────────────────────────────────────────────────

def payment_method_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.MONEY} USDT (TRC-20)",
                callback_data="payment:method:usdt_trc20"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.DIAMOND if hasattr(EMOJI, 'DIAMOND') else '💎'} TON",
                callback_data="payment:method:ton"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.STAR} Telegram Stars",
                callback_data="payment:method:telegram_stars"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.CARD} 使用激活码",
                callback_data="payment:activate_code"
            ),
        ],
    ])


def vip_plan_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🥉 月度 VIP - ",
                callback_data="payment:plan:monthly"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🥈 季度 VIP - ",
                callback_data="payment:plan:quarterly"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🥇 年度 VIP - ",
                callback_data="payment:plan:yearly"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回",
                callback_data="payment:back"
            ),
        ],
    ])


# ── Group Settings ──────────────────────────────────────────────────────────

def group_settings_menu(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.BELL} 欢迎设置",
                callback_data=f"group:welcome:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.SECURITY} 安全设置",
                callback_data=f"group:security:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.AD} 轮播设置",
                callback_data=f"group:carousel:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.LOCK} 违禁词管理",
                callback_data=f"group:banned_words:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.MONEY} VIP 升级",
                callback_data=f"group:vip_upgrade:{group_id}"
            ),
        ],
    ])


def confirm_keyboard(action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHECK} 确认",
                callback_data=f"confirm:{action}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.CROSS} 取消",
                callback_data="confirm:cancel"
            ),
        ],
    ])



# ── Blacklist Management ────────────────────────────────────────────────────

def blacklist_menu(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.PLUS} 添加黑名单",
                callback_data=f"blacklist:add:{group_id}"
            ),
            InlineKeyboardButton(
                f"{EMOJI.TRASH} 删除黑名单",
                callback_data=f"blacklist:remove:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"📋 查看黑名单",
                callback_data=f"blacklist:list:{group_id}"
            ),
            InlineKeyboardButton(
                f"🗑️ 一键清空",
                callback_data=f"blacklist:clear:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回",
                callback_data=f"group:settings:{group_id}"
            ),
        ],
    ])

# ── AI Configuration ────────────────────────────────────────────────────────

def ai_config_menu(group_id: int, enabled: bool, model: str = "gpt-4o-mini") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHECK if enabled else EMOJI.CROSS} AI问答: {'开' if enabled else '关'}",
                callback_data=f"ai:toggle:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🤖 切换模型",
                callback_data=f"ai:model_select:{group_id}:0"
            ),
            InlineKeyboardButton(
                f"📝 系统提示词",
                callback_data=f"ai:prompt:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"📊 当前: {model}",
                callback_data=f"ai:info:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回",
                callback_data=f"group:settings:{group_id}"
            ),
        ],
    ])


def ai_model_select_keyboard(group_id: int, page: int = 0) -> InlineKeyboardMarkup:
    """Model selection with pagination (14 models, 4 per page)."""
    models = [
        ("GPT-4o", "gpt-4o"), ("GPT-4o Mini", "gpt-4o-mini"),
        ("GPT-4 Turbo", "gpt-4-turbo"), ("GPT-4", "gpt-4"),
        ("GPT-3.5 Turbo", "gpt-3.5-turbo"), ("Claude 3.5 Sonnet", "claude-3.5-sonnet"),
        ("Claude 3 Opus", "claude-3-opus"), ("Claude 3 Haiku", "claude-3-haiku"),
        ("Gemini 1.5 Pro", "gemini-1.5-pro"), ("Gemini 1.5 Flash", "gemini-1.5-flash"),
        ("DeepSeek V3", "deepseek-v3"), ("DeepSeek R1", "deepseek-r1"),
        ("Qwen Max", "qwen-max"), ("Qwen Plus", "qwen-plus"),
    ]
    per_page = 4
    start = page * per_page
    page_models = models[start:start + per_page]
    buttons = []
    for i in range(0, len(page_models), 2):
        row = []
        for name, code in page_models[i:i+2]:
            row.append(InlineKeyboardButton(
                f"{name}", callback_data=f"ai:set_model:{group_id}:{code}"
            ))
        buttons.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(
            f"{EMOJI.BACK} 上页", callback_data=f"ai:model_select:{group_id}:{page - 1}"
        ))
    if start + per_page < len(models):
        nav.append(InlineKeyboardButton(
            f"下页 {EMOJI.NEXT}", callback_data=f"ai:model_select:{group_id}:{page + 1}"
        ))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(
        f"{EMOJI.BACK} 返回AI设置", callback_data=f"ai:config:{group_id}"
    )])
    return InlineKeyboardMarkup(buttons)

# ── Quiet Mode ──────────────────────────────────────────────────────────────

def quiet_mode_menu(group_id: int, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.CHECK if enabled else EMOJI.CROSS} 安静模式: {'开' if enabled else '关'}",
                callback_data=f"quiet:toggle:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"⏰ 设置时间段",
                callback_data=f"quiet:set_time:{group_id}"
            ),
            InlineKeyboardButton(
                f"📅 生效日期",
                callback_data=f"quiet:set_days:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BELL} 通知开关",
                callback_data=f"quiet:toggle_notify:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回",
                callback_data=f"group:settings:{group_id}"
            ),
        ],
    ])

# ── Sensitive Words ─────────────────────────────────────────────────────────

def sensitive_words_menu(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"{EMOJI.PLUS} 添加敏感词",
                callback_data=f"sensitive:add:{group_id}"
            ),
            InlineKeyboardButton(
                f"📋 查看列表",
                callback_data=f"sensitive:list:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🗑️ 清空全部",
                callback_data=f"sensitive:clear:{group_id}"
            ),
            InlineKeyboardButton(
                f"⚡ 测试检测",
                callback_data=f"sensitive:test:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回",
                callback_data=f"group:settings:{group_id}"
            ),
        ],
    ])

# ── Extended Group Settings ─────────────────────────────────────────────────

def extended_group_settings_menu(group_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"🤖 AI问答", callback_data=f"group:ai_config:{group_id}"
            ),
            InlineKeyboardButton(
                f"🔇 安静模式", callback_data=f"group:quiet:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🚫 黑名单", callback_data=f"group:blacklist:{group_id}"
            ),
            InlineKeyboardButton(
                f"🛡️ 敏感词", callback_data=f"group:sensitive:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"🧮 算术验证", callback_data=f"group:math_captcha:{group_id}"
            ),
            InlineKeyboardButton(
                f"🔔 通知设置", callback_data=f"group:notifications:{group_id}"
            ),
        ],
        [
            InlineKeyboardButton(
                f"{EMOJI.BACK} 返回主设置",
                callback_data=f"group:settings:{group_id}"
            ),
        ],
    ])

# ── Helper ──────────────────────────────────────────────────────────────────

def parse_buttons(json_str: str) -> list[list[InlineKeyboardButton]]:
    """Parse JSON button config into InlineKeyboardButton rows."""
    try:
        data = json.loads(json_str)
        rows = []
        for row in data:
            btns = []
            for btn in row:
                if "url" in btn:
                    btns.append(InlineKeyboardButton(
                        text=btn.get("text", ""), url=btn["url"]
                    ))
                elif "callback_data" in btn:
                    btns.append(InlineKeyboardButton(
                        text=btn.get("text", ""),
                        callback_data=btn["callback_data"]
                    ))
            if btns:
                rows.append(btns)
        return rows
    except (json.JSONDecodeError, TypeError):
        return []


