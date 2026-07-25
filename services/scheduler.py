import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from config import config
from database import GroupMember, get_session

logger = logging.getLogger(__name__)


async def auto_unmute_task(bot):
    """Periodically check and auto-unmute members whose mute has expired."""
    logger.info("Auto-unmute background task started")

    while True:
        try:
            async with get_session() as session:
                now = datetime.utcnow()
                result = await session.execute(
                    select(GroupMember).where(
                        GroupMember.is_muted == True,
                        GroupMember.mute_until != None,
                        GroupMember.mute_until <= now,
                    )
                )
                expired = list(result.scalars().all())

                for gm in expired:
                    try:
                        await bot.restrict_chat_member(
                            chat_id=gm.group_id,
                            user_id=gm.user_id,
                            permissions={
                                "can_send_messages": True,
                                "can_send_media_messages": True,
                                "can_send_other_messages": True,
                                "can_add_web_page_previews": True,
                            },
                        )
                        gm.is_muted = False
                        gm.mute_until = None
                        logger.info(f"Auto-unmuted user {gm.user_id} in group {gm.group_id}")
                    except Exception as e:
                        logger.warning(f"Failed to unmute user {gm.user_id}: {e}")

                await session.commit()

        except Exception as e:
            logger.error(f"Auto-unmute task error: {e}", exc_info=True)

        await asyncio.sleep(30)  # Check every 30 seconds


async def vip_expiry_check_task(bot):
    """Periodically check and downgrade expired VIP users."""
    logger.info("VIP expiry check background task started")

    while True:
        try:
            async with get_session() as session:
                from database import User, VIPLevel
                now = datetime.utcnow()
                result = await session.execute(
                    select(User).where(
                        User.vip_level != VIPLevel.FREE,
                        User.vip_expiry != None,
                        User.vip_expiry <= now,
                    )
                )
                expired = list(result.scalars().all())

                for user in expired:
                    user.vip_level = VIPLevel.FREE
                    user.vip_expiry = None
                    logger.info(f"VIP expired for user {user.id}")
                    try:
                        await bot.send_message(
                            chat_id=user.id,
                            text=(
                                "👑 <b>VIP 已过期</b>\n\n"
                                "你的 VIP 会员已到期，已恢复为免费用户。\n"
                                "发送 /vip 续费以继续享受特权。"
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass

                await session.commit()

        except Exception as e:
            logger.error(f"VIP expiry check error: {e}", exc_info=True)

        await asyncio.sleep(3600)  # Check every hour
