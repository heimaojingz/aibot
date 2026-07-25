import logging
import re
from typing import Optional

from sqlalchemy import select

from database import BlacklistEntry, BlacklistType, SensitiveWord, get_session, GroupSettings
from services.ai_service import ai_spam_check

logger = logging.getLogger(__name__)


async def dual_engine_check(
    text: str,
    user_id: int,
    group_id: int,
    username: Optional[str] = None,
    ai_key: str = "",
) -> tuple[bool, str]:
    """
    Dual-engine spam detection:
    1. AI engine: checks if message is spam/advertisement
    2. Keyword engine: checks against blacklist and sensitive words

    Returns (is_spam, reason)
    """
    reasons = []

    # ── Engine 1: AI-based detection ────────────────────────────────────
    async with get_session() as session:
        gs_result = await session.execute(
            select(GroupSettings).where(GroupSettings.group_id == group_id)
        )
        gs = gs_result.scalar_one_or_none()

        if gs and gs.anti_spam_ai_enabled and len(text) > 20:
            is_spam = await ai_spam_check(text, ai_key)
            if is_spam:
                reasons.append("AI 检测: 疑似垃圾/广告消息")

    # ── Engine 2: Keyword-based detection ───────────────────────────────
    async with get_session() as session:
        # Check blacklist words
        blk_result = await session.execute(
            select(BlacklistEntry).where(
                (BlacklistEntry.group_id == group_id) | (BlacklistEntry.group_id == None),
                BlacklistEntry.entry_type == BlacklistType.WORD,
            )
        )
        for entry in blk_result.scalars().all():
            if entry.value.lower() in text.lower():
                reasons.append(f"黑名单关键词: {entry.value}")

        # Check blacklist patterns (regex)
        blk_pattern_result = await session.execute(
            select(BlacklistEntry).where(
                (BlacklistEntry.group_id == group_id) | (BlacklistEntry.group_id == None),
                BlacklistEntry.entry_type == BlacklistType.PATTERN,
            )
        )
        for entry in blk_pattern_result.scalars().all():
            try:
                if re.search(entry.value, text, re.IGNORECASE):
                    reasons.append(f"黑名单模式: {entry.value}")
            except re.error:
                pass

        # Check sensitive words
        sens_result = await session.execute(
            select(SensitiveWord).where(SensitiveWord.group_id == group_id)
        )
        for sw in sens_result.scalars().all():
            if sw.match_mode == "exact" and sw.word.lower() == text.lower():
                reasons.append(f"敏感词(精确): {sw.word}")
            elif sw.match_mode == "contains" and sw.word.lower() in text.lower():
                reasons.append(f"敏感词(包含): {sw.word}")
            elif sw.match_mode == "regex":
                try:
                    if re.search(sw.word, text, re.IGNORECASE):
                        reasons.append(f"敏感词(正则): {sw.word}")
                except re.error:
                    pass

    is_spam = len(reasons) > 0
    return is_spam, " | ".join(reasons) if reasons else ""


async def _get_severity(session, group_id: int, text: str) -> str:
    """Determine highest severity action for matching sensitive words."""
    max_severity = "delete"
    severity_order = {"delete": 0, "warn": 1, "mute": 2, "kick": 3, "ban": 4}

    result = await session.execute(
        select(SensitiveWord).where(SensitiveWord.group_id == group_id)
    )
    for sw in result.scalars().all():
        matched = False
        if sw.match_mode == "exact" and sw.word.lower() == text.lower():
            matched = True
        elif sw.match_mode == "contains" and sw.word.lower() in text.lower():
            matched = True
        elif sw.match_mode == "regex":
            try:
                if re.search(sw.word, text, re.IGNORECASE):
                    matched = True
            except re.error:
                pass

        if matched and severity_order.get(sw.severity, 0) > severity_order.get(max_severity, 0):
            max_severity = sw.severity

    return max_severity
