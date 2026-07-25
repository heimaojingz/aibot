import sys, io, asyncio, logging

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CallbackQueryHandler,
    ChatMemberHandler, CommandHandler, Defaults, MessageHandler, filters,
)

from config import config
from database import init_db, get_session
from handlers import ALL_HANDLERS
from services.scheduler import auto_unmute_task, vip_expiry_check_task
from handlers.carousel import carousel_scheduler_loop
from handlers.quiet_mode import quiet_mode_scheduler

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout,
)
for lib in ("httpx", "sqlalchemy.engine"):
    logging.getLogger(lib).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# BOT ADDED / REMOVED FROM GROUP
# ═══════════════════════════════════════════════════════════════════════════

async def on_my_chat_member(update, context):
    chat = update.effective_chat
    if not chat or chat.type == "private":
        return
    my_status = update.my_chat_member.new_chat_member.status
    logger.info(f"MY_CHAT_MEMBER: {chat.id} ({chat.title}) -> {my_status}")

    async with get_session() as s:
        from sqlalchemy import select
        from database import Group
        r = await s.execute(select(Group).where(Group.id == chat.id))
        group = r.scalar_one_or_none()

        if my_status in ("member", "administrator"):
            if not group:
                group = Group(id=chat.id, title=chat.title or "", username=chat.username or "", is_active=True)
                s.add(group)
                await s.commit()
                logger.info(f"Group recorded: {chat.id} - {chat.title}")
                # Notify super admins only
                for aid in config.SUPER_ADMIN_IDS:
                    try:
                        await context.bot.send_message(
                            chat_id=aid,
                            text=f"📢 <b>新群组已添加</b>\n\n🏷 名称：<b>{chat.title}</b>\n🆔 ID：<code>{chat.id}</code>\n✅ 已自动记录到数据库",
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            elif not group.is_active:
                group.is_active = True
                group.title = chat.title or group.title
                await s.commit()
        elif my_status in ("left", "kicked"):
            if group:
                group.is_active = False
                await s.commit()

# ═══════════════════════════════════════════════════════════════════════════
# START COMMAND
# ═══════════════════════════════════════════════════════════════════════════

async def cmd_start(update, context):
    user = update.effective_user
    bu = context.bot.username
    logger.info(f"CMD_START user={user.id}")

    async with get_session() as s:
        from sqlalchemy import select, func
        from database import Group
        r = await s.execute(select(func.count(Group.id)).where(Group.is_active == True, Group.owner_id == user.id))
        gc = r.scalar() or 0
        is_adm = user.id in config.SUPER_ADMIN_IDS

    text = (
        f"👋 你好，<b>{user.first_name}</b>！\n\n"
        f"🤖 我是 <b>全能群管机器人</b>\n"
        f"<i>AI 问答 · 轮播广告 · 安全防护 · VIP 变现</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>当前状态</b>\n"
        f"├ 管理群组：<b>{gc}</b> 个\n"
        f"├ Bot 在线：🟢 运行中\n"
        f"└ 版本：v2.0.0\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 <b>快捷操作：</b>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ 添加到群组", url=f"https://t.me/{bu}?startgroup=start"),
            InlineKeyboardButton("📢 添加到频道", url=f"https://t.me/{bu}?startchannel=start"),
        ],
        [
            InlineKeyboardButton("📋 我的群组", callback_data="start:my_groups"),
            InlineKeyboardButton("⚙️ 群组设置", callback_data="start:group_settings"),
        ],
        [
            InlineKeyboardButton("💎 VIP 会员", callback_data="start:vip"),
            InlineKeyboardButton("❓ 帮助说明", callback_data="start:help"),
        ],
        [
            InlineKeyboardButton("📢 官方频道", url="https://t.me/LunBoDs_bot"),
            InlineKeyboardButton("💬 问题反馈", url="https://t.me/doubao007"),
        ],
    ])
    if is_adm:
            rows = list(kb.inline_keyboard); rows.insert(0, [InlineKeyboardButton(chr(0x1f451)+chr(0x7ba1)+chr(0x7406)+chr(0x5458)+chr(0x9762)+chr(0x677f), callback_data=chr(34)+chr(97)+chr(100)+chr(109)+chr(105)+chr(110)+chr(58)+chr(109)+chr(97)+chr(105)+chr(110)+chr(34))]); kb = InlineKeyboardMarkup(rows)

    try:
        await update.message.reply_html(text, reply_markup=kb)
    except Exception as e:
        logger.error(f"Start error: {e}")

# ═══════════════════════════════════════════════════════════════════════════
# START MENU CALLBACKS
# ═══════════════════════════════════════════════════════════════════════════

async def on_start_callback(update, context):
    q = update.callback_query
    await q.answer()
    action = q.data.split(":")[1]
    logger.info(f"START_CB action={action}")

    if action == "my_groups":
        async with get_session() as s:
            from sqlalchemy import select
            from database import Group
            r = await s.execute(select(Group).where(Group.is_active == True, Group.owner_id == user.id).order_by(Group.id))
            groups = list(r.scalars().all())

        if not groups:
            await q.edit_message_text(
                "📋 <b>我的群组</b>\n\n暂无管理的群组。\n\n👆 点击上方按钮将机器人添加到你的群组！\n\n⚠️ 添加后请设为<b>管理员</b>，然后重新进入此页面。",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 返回主菜单", callback_data="start:main")
                ]]),
            )
            return

        btns = []
        for g in groups[:30]:
            icon = "👑" if g.vip_level.value != "free" else "👥"
            btns.append([InlineKeyboardButton(
                f"{icon} {g.title[:32]}",
                callback_data=f"start:manage:{g.id}"
            )])
        btns.append([InlineKeyboardButton("🔙 返回主菜单", callback_data="start:main")])
        await q.edit_message_text(
            f"📋 <b>管理的群组</b>（共 {len(groups)} 个）\n\n👇 点击群组进入管理面板：",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(btns),
        )

    elif action == "group_settings":
        await q.edit_message_text(
            "⚙️ <b>群组功能说明</b>\n\n所有群组管理功能现在都可以在机器人私聊中完成！\n\n"
            "1️⃣ 点击「📋 我的群组」\n"
            "2️⃣ 点击你要管理的群组\n"
            "3️⃣ 在管理面板中配置各项功能\n\n"
            "无需在群组内发送命令 ✅",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回主菜单", callback_data="start:main")
            ]]),
        )

    elif action == "vip":
        from keyboards import vip_plan_menu
        await q.edit_message_text(
            "💎 <b>VIP 会员中心</b>\n\n升级 VIP 解锁更多功能：\n• 无水印轮播广告\n• AI 智能问答优先\n• 更多投放群组\n• 专属技术支持\n\n请选择套餐：",
            parse_mode="HTML", reply_markup=vip_plan_menu(),
        )

    elif action == "help":
        await q.edit_message_text(
            "🤖 <b>帮助菜单</b>\n\n"
            "📋 群组命令（管理员）：\n/welcome /blacklist /aiconfig /quietmode /warn /mute /kick /ban /addbanned\n\n"
            "💎 个人命令：\n/vip /ai /verify\n\n"
            "👑 管理员：\n/admin /carousel /gencodes /confirmpay",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回主菜单", callback_data="start:main")
            ]]),
        )

    elif action == "manage":
        gid = int(q.data.split(":")[2])
        async with get_session() as s:
            from sqlalchemy import select
            from database import Group, GroupSettings, AIConfig, QuietModeConfig
            gr = await s.execute(select(Group).where(Group.id == gid))
            group = gr.scalar_one_or_none()
            if not group:
                await q.answer("群组不存在", show_alert=True)
                return
            user = q.from_user
            if group.owner_id and group.owner_id != user.id and user.id not in config.SUPER_ADMIN_IDS:
                await q.answer(chr(20320)+chr(27809)+chr(26377)+chr(26435)+chr(38480)+chr(31649)+chr(29702)+chr(27492)+chr(32676)+chr(32452), show_alert=True)
                return
            gsr = await s.execute(select(GroupSettings).where(GroupSettings.group_id == gid))
            gs = gsr.scalar_one_or_none()
            air = await s.execute(select(AIConfig).where(AIConfig.group_id == gid))
            ai_cfg = air.scalar_one_or_none()
            qcr = await s.execute(select(QuietModeConfig).where(QuietModeConfig.group_id == gid))
            qc = qcr.scalar_one_or_none()

        math_s = "✅" if (gs and gs.captcha_math_enabled) else "❌"
        ai_s = "✅" if (ai_cfg and ai_cfg.enabled) else "❌"
        quiet_s = "✅" if (qc and qc.enabled) else "❌"
        notify_s = f"{gs.notification_auto_delete}s" if (gs and gs.notification_auto_delete > 0) else "关闭"

        await q.edit_message_text(
            f"⚙️ <b>群组管理面板</b>\n\n"
            f"🏷 <b>{group.title}</b>\n"
            f"🆔 ID: <code>{gid}</code>\n"
            f"📊 状态: {'🟢 活跃' if group.is_active else '🔴 非活跃'}\n\n"
            f"📋 <b>功能设置：</b>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🔔 欢迎系统", callback_data=f"grpcfg:welcome:{gid}"),
                    InlineKeyboardButton("🚫 黑名单", callback_data=f"grpcfg:blacklist:{gid}"),
                ],
                [
                    InlineKeyboardButton(f"🤖 AI配置 {ai_s}", callback_data=f"grpcfg:ai:{gid}"),
                    InlineKeyboardButton(f"🔇 安静模式 {quiet_s}", callback_data=f"grpcfg:quiet:{gid}"),
                ],
                [
                    InlineKeyboardButton(f"🧮 算术验证 {math_s}", callback_data=f"grpcfg:math:{gid}"),
                    InlineKeyboardButton("🛡️ 敏感词", callback_data=f"grpcfg:sensitive:{gid}"),
                ],
                [
                    InlineKeyboardButton("🔗 违禁词", callback_data=f"grpcfg:banned:{gid}"),
                    InlineKeyboardButton("📣 轮播设置", callback_data=f"grpcfg:carousel:{gid}"),
                ],
                [
                    InlineKeyboardButton(f"🔔 通知删除 {notify_s}", callback_data=f"grpcfg:notify:{gid}"),
                ],
                [
                    InlineKeyboardButton("🔙 返回群组列表", callback_data="start:my_groups"),
                    InlineKeyboardButton("🏠 主菜单", callback_data="start:main"),
                ],
            ]),
        )

    elif action == "main":
        # Re-render start for callback
        user = q.from_user
        bu = context.bot.username
        async with get_session() as s:
            from sqlalchemy import select, func
            from database import Group
            r = await s.execute(select(func.count(Group.id)).where(Group.is_active == True, Group.owner_id == user.id))
            gc = r.scalar() or 0
            is_adm = user.id in config.SUPER_ADMIN_IDS
        text = (
            f"👋 你好，<b>{user.first_name}</b>！\n\n"
            f"🤖 我是 <b>全能群管机器人</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"├ 管理群组：<b>{gc}</b> 个\n"
            f"├ Bot 在线：🟢\n"
            f"└ 版本：v2.0.0\n"
            f"━━━━━━━━━━━━━━━━━━\n\n📋 <b>快捷操作：</b>"
        )
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("➕ 添加到群组", url=f"https://t.me/{bu}?startgroup=start"),
                InlineKeyboardButton("📢 添加到频道", url=f"https://t.me/{bu}?startchannel=start"),
            ],
            [
                InlineKeyboardButton("📋 我的群组", callback_data="start:my_groups"),
                InlineKeyboardButton("⚙️ 群组设置", callback_data="start:group_settings"),
            ],
            [
                InlineKeyboardButton("💎 VIP 会员", callback_data="start:vip"),
                InlineKeyboardButton("❓ 帮助说明", callback_data="start:help"),
            ],
            [
                InlineKeyboardButton("📢 官方频道", url="https://t.me/LunBoDs_bot"),
                InlineKeyboardButton("💬 问题反馈", url="https://t.me/doubao007"),
            ],
        ])
        if is_adm:
            rows = list(kb.inline_keyboard); rows.insert(0, [InlineKeyboardButton(chr(0x1f451)+chr(0x7ba1)+chr(0x7406)+chr(0x5458)+chr(0x9762)+chr(0x677f), callback_data=chr(34)+chr(97)+chr(100)+chr(109)+chr(105)+chr(110)+chr(58)+chr(109)+chr(97)+chr(105)+chr(110)+chr(34))]); kb = InlineKeyboardMarkup(rows)
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)

    elif action == "noop":
        await q.answer("请点击具体群组", show_alert=True)

# ═══════════════════════════════════════════════════════════════════════════
# GROUP CONFIG CALLBACKS (grpcfg:*)
# ═══════════════════════════════════════════════════════════════════════════

async def on_group_config_callback(update, context):
    q = update.callback_query
    await q.answer()
    parts = q.data.split(":")
    action = parts[1]
    gid = int(parts[2])
    user = q.from_user
    logger.info(f"GRPCFG action={action} group={gid}")

    async with get_session() as s:
        from sqlalchemy import select
        from database import Group, GroupSettings, WelcomeConfig, AIConfig, QuietModeConfig, SensitiveWord, BannedWord, BlacklistEntry

        gsr = await s.execute(select(GroupSettings).where(GroupSettings.group_id == gid))
        gs = gsr.scalar_one_or_none()
        if not gs:
            gs = GroupSettings(group_id=gid)
            s.add(gs)
            await s.commit()

        # Check ownership
        gr = await s.execute(select(Group).where(Group.id == gid))
        group = gr.scalar_one_or_none()
        if group and group.owner_id and group.owner_id != user.id and user.id not in config.SUPER_ADMIN_IDS:
            await q.answer(chr(20320)+chr(27809)+chr(26377)+chr(26435)+chr(38480)+chr(31649)+chr(29702)+chr(27492)+chr(32676)+chr(32452), show_alert=True)
            return

        if action == "welcome":
            wcr = await s.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == gid))
            wc = wcr.scalar_one_or_none()
            if not wc:
                wc = WelcomeConfig(group_id=gid)
                s.add(wc)
                await s.commit()
            ws = "✅" if wc.enabled else "❌"
            cs = "✅" if wc.captcha_enabled else "❌"
            await q.edit_message_text(
                f"🔔 <b>欢迎系统设置</b>\n\n状态: {ws}\n验证码: {cs}\n媒体: {'有' if wc.media_file_id else '无'}\n自动删除: {wc.auto_delete_after}秒",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(f"{'✅' if wc.enabled else '❌'} 欢迎开关", callback_data=f"grpcfg:toggle_welcome:{gid}")],
                    [InlineKeyboardButton(f"{'✅' if wc.captcha_enabled else '❌'} 验证码", callback_data=f"grpcfg:toggle_captcha:{gid}")],
                    [InlineKeyboardButton("📝 编辑欢迎词", callback_data=f"grpcfg:edit_welcome:{gid}")],
                    [InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")],
                ]),
            )

        elif action == "toggle_welcome":
            wcr = await s.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == gid))
            wc = wcr.scalar_one_or_none()
            if wc:
                wc.enabled = not wc.enabled
                await s.commit()
                await q.answer(f"欢迎系统: {'开' if wc.enabled else '关'}")
                q2 = update.callback_query
                q2.data = f"grpcfg:welcome:{gid}"
                await on_group_config_callback(update, context)

        elif action == "toggle_captcha":
            wcr = await s.execute(select(WelcomeConfig).where(WelcomeConfig.group_id == gid))
            wc = wcr.scalar_one_or_none()
            if wc:
                wc.captcha_enabled = not wc.captcha_enabled
                await s.commit()
                await q.answer(f"验证码: {'开' if wc.captcha_enabled else '关'}")
                q2 = update.callback_query
                q2.data = f"grpcfg:welcome:{gid}"
                await on_group_config_callback(update, context)

        elif action == "edit_welcome":
            context.user_data["editing_welcome"] = gid
            await q.edit_message_text(
                "📝 <b>编辑欢迎词</b>\n\n请发送新的欢迎模板（支持 HTML）：\n\n变量: <code>{user_mention}</code> <code>{group_title}</code>\n\n发送 /cancel 取消",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 返回", callback_data=f"grpcfg:welcome:{gid}")
                ]]),
            )

        elif action == "math":
            gs.captcha_math_enabled = not gs.captcha_math_enabled
            await s.commit()
            await q.answer(f"算术验证: {'开' if gs.captcha_math_enabled else '关'}")
            q2 = update.callback_query
            q2.data = f"group:manage:{gid}"
            await on_start_callback(update, context)

        elif action == "quiet":
            qcr = await s.execute(select(QuietModeConfig).where(QuietModeConfig.group_id == gid))
            qc = qcr.scalar_one_or_none()
            if not qc:
                qc = QuietModeConfig(group_id=gid)
                s.add(qc)
            qc.enabled = not qc.enabled
            await s.commit()
            await q.answer(f"安静模式: {'开' if qc.enabled else '关'}")
            q2 = update.callback_query
            q2.data = f"group:manage:{gid}"
            await on_start_callback(update, context)

        elif action == "ai":
            air = await s.execute(select(AIConfig).where(AIConfig.group_id == gid))
            ai_cfg = air.scalar_one_or_none()
            if not ai_cfg:
                ai_cfg = AIConfig(group_id=gid)
                s.add(ai_cfg)
            ai_cfg.enabled = not ai_cfg.enabled
            await s.commit()
            await q.answer(f"AI配置: {'开' if ai_cfg.enabled else '关'}")
            q2 = update.callback_query
            q2.data = f"group:manage:{gid}"
            await on_start_callback(update, context)

        elif action == "notify":
            gs.notification_auto_delete = 30 if gs.notification_auto_delete == 0 else 0
            await s.commit()
            await q.answer(f"通知自动删除: {'30秒' if gs.notification_auto_delete > 0 else '关闭'}")
            q2 = update.callback_query
            q2.data = f"group:manage:{gid}"
            await on_start_callback(update, context)

        elif action == "sensitive":
            sr = await s.execute(select(SensitiveWord).where(SensitiveWord.group_id == gid))
            words = list(sr.scalars().all())
            text = f"🛡️ <b>敏感词列表</b>（共 {len(words)} 个）\n" + "\n".join([f"• <code>{w.word[:40]}</code> [{w.severity}]" for w in words[:20]]) if words else "🛡️ <b>敏感词</b>\n\n暂无敏感词。"
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")
            ]]))

        elif action == "banned":
            br = await s.execute(select(BannedWord).where(BannedWord.group_id == gid))
            words = list(br.scalars().all())
            text = f"🔗 <b>违禁词列表</b>（共 {len(words)} 个）\n" + "\n".join([f"• <code>{w.word[:40]}</code>" for w in words[:20]]) if words else "🔗 <b>违禁词</b>\n\n暂无违禁词。"
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")
            ]]))

        elif action == "blacklist":
            br = await s.execute(select(BlacklistEntry).where((BlacklistEntry.group_id == gid) | (BlacklistEntry.group_id == None)))
            entries = list(br.scalars().all())
            text = f"🚫 <b>黑名单</b>（共 {len(entries)} 条）\n" + "\n".join([f"• {'🌐' if e.group_id is None else '👥'} <code>{e.value[:30]}</code>" for e in entries[:20]]) if entries else "🚫 <b>黑名单</b>\n\n暂无条目。"
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")
            ]]))
        elif action == "carousel":
            from database import CarouselMessage, CarouselTarget
            cr = await s.execute(
                select(CarouselTarget).where(CarouselTarget.group_id == gid)
            )
            targets = list(cr.scalars().all())
            carousel_ids = [t.carousel_id for t in targets]

            kb_rows = []

            # Add new carousel button
            kb_rows.append([InlineKeyboardButton("➕ 添加轮播", callback_data=f"grpcfg:carousel_add:{gid}")])

            if carousel_ids:
                cmsr = await s.execute(
                    select(CarouselMessage).where(CarouselMessage.id.in_(carousel_ids))
                )
                cms = list(cmsr.scalars().all())
                text = f"📣 <b>轮播设置</b>\n\n当前绑定了 <b>{len(cms)}</b> 条轮播：\n"
                for cm in cms:
                    status = "✅" if cm.enabled else "⏸️"
                    text += f"\n{status} <b>{cm.name}</b> | {cm.carousel_type.value} | 每{cm.interval}秒"
                    kb_rows.append([InlineKeyboardButton(
                        f"{status} {cm.name[:28]}",
                        callback_data=f"carousel:detail_group:{cm.id}:{gid}"
                    )])
            else:
                text = "📣 <b>轮播设置</b>\n\n该群组暂未绑定任何轮播。\n\n点击下方按钮创建新轮播："

            kb_rows.append([InlineKeyboardButton("🔙 返回群组", callback_data=f"start:manage:{gid}")])

            await q.edit_message_text(
                text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(kb_rows),
            )

        elif action == "carousel_add":
            # Start carousel creation wizard, auto-bind to this group on completion
            context.user_data["carousel_wizard"] = {"step": "name"}
            context.user_data["carousel_wizard_group_id"] = gid
            await q.edit_message_text(
                "➕ <b>📣 创建轮播消息</b>\n\n"
                "<b>第1步：</b>请发送轮播名称\n"
                "例如：<code>每日福利推送</code>\n\n"
                "发送 <code>/cancel</code> 取消",
                parse_mode="HTML",
            )

        elif action == "carousel_bind":
            # Bind this group to the selected carousel
            from database import CarouselTarget
            carousel_id = int(parts[3])
            # Check if already bound
            cr = await s.execute(
                select(CarouselTarget).where(
                    CarouselTarget.group_id == gid,
                    CarouselTarget.carousel_id == carousel_id,
                )
            )
            if not cr.scalar_one_or_none():
                s.add(CarouselTarget(carousel_id=carousel_id, group_id=gid))
                await s.commit()
                await q.answer("✅ 已绑定轮播", show_alert=True)
            else:
                await q.answer("该轮播已绑定此群组", show_alert=True)
            # Return to carousel panel
            q2 = update.callback_query
            q2.data = f"grpcfg:carousel:{gid}"
            await on_group_config_callback(update, context)

        elif action == "carousel_del":
            # Remove this group from the selected carousel
            from database import CarouselTarget
            carousel_id = int(parts[3])
            cr = await s.execute(
                select(CarouselTarget).where(
                    CarouselTarget.group_id == gid,
                    CarouselTarget.carousel_id == carousel_id,
                )
            )
            target = cr.scalar_one_or_none()
            if target:
                await s.delete(target)
                await s.commit()
                await q.answer("✅ 已移除轮播绑定", show_alert=True)
            else:
                await q.answer("未找到该绑定", show_alert=True)
            # Return to carousel panel
            q2 = update.callback_query
            q2.data = f"grpcfg:carousel:{gid}"
            await on_group_config_callback(update, context)
# HELP
# ═══════════════════════════════════════════════════════════════════════════


async def cmd_skip(update, context):
    """Forward /skip to wizard handler."""
    from handlers.wizard import unified_text_handler
    return await unified_text_handler(update, context)


async def cmd_cancel(update, context):
    """Clear all pending wizard/editing state."""
    ud = context.user_data
    wizard_keys = [k for k in ud if k.startswith(("carousel_wizard", "creating_carousel", "awaiting_carousel", "carousel_draft", "awaiting_welcome", "editing_welcome", "awaiting_broadcast"))]
    for key in wizard_keys:
        del ud[key]
    await update.message.reply_text("✅ 已取消当前操作。")


async def cmd_help(update, context):
    await update.message.reply_html(
        "🤖 <b>帮助菜单</b>\n\n"
        "📋 /welcome /blacklist /aiconfig /quietmode\n"
        "/warn /mute /kick /ban /addbanned\n\n"
        "💎 /vip /ai /verify\n\n"
        "👑 /admin /carousel /gencodes /confirmpay"
    )

# ═══════════════════════════════════════════════════════════════════════════
# ERROR HANDLER
# ═══════════════════════════════════════════════════════════════════════════

async def error_handler(update, context):
    logger.error("Exception:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ 处理请求时发生错误，请稍后重试。")
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

async def main():
    logger.info("=" * 60)
    logger.info("Starting Telegram Group Management Bot")
    logger.info("=" * 60)
    await init_db()
    logger.info("Database OK")

    defaults = Defaults(parse_mode="HTML", link_preview_options=None)
    application = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .defaults(defaults)
        .build()
    )

    # Handlers
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("cancel", cmd_cancel))
    application.add_handler(CommandHandler("skip", cmd_skip))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(ChatMemberHandler(on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    application.add_handler(CallbackQueryHandler(on_group_config_callback, pattern=r"^grpcfg:"))
    application.add_handler(CallbackQueryHandler(on_start_callback, pattern=r"^start:"))
    for h in ALL_HANDLERS:
        application.add_handler(h)
    application.add_error_handler(error_handler)

    # Background tasks
    logger.info("Starting background tasks...")
    bot = application.bot
    loop = asyncio.get_event_loop()
    ct = loop.create_task(carousel_scheduler_loop(bot, interval_seconds=15), name="carousel")
    ut = loop.create_task(auto_unmute_task(bot), name="unmute")
    vt = loop.create_task(vip_expiry_check_task(bot), name="vip_expiry")
    qt = loop.create_task(quiet_mode_scheduler(bot), name="quiet_mode")
    wt = None
    if config.WEB_PANEL_ENABLED:
        import uvicorn
        from web import app as wa
        wc = uvicorn.Config(wa, host=config.WEB_PANEL_HOST, port=config.WEB_PANEL_PORT, log_level="info")
        wt = loop.create_task(uvicorn.Server(wc).serve(), name="web")
        logger.info(f"Web panel on {config.WEB_PANEL_HOST}:{config.WEB_PANEL_PORT}")
    logger.info("Background tasks: carousel, unmute, vip, quiet" + (" + web" if wt else ""))

    logger.info("Starting polling...")
    try:
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"]
        )
        logger.info("✅ Bot is running! Press Ctrl+C to stop.")
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        for t in (ct, ut, vt, qt, wt):
            if t:
                t.cancel()
        try:
            await application.updater.stop()
            await application.stop()
            await application.shutdown()
        except Exception:
            pass
        logger.info("Bot stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Goodbye!")
        sys.exit(0)










