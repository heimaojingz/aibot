import hashlib
import hmac
import secrets
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Query, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select, func

from config import config
from database import (
    get_session,
    User,
    Group,
    GroupSettings,
    WelcomeConfig,
    CarouselMessage,
    PaymentOrder,
    PaymentStatus,
    VIPLevel,
    WebAPIKey,
    BlacklistEntry,
    SensitiveWord,
    QuietModeConfig,
    AIConfig,
    ModLog,
    ModLog as _ModLog,
)

# ── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Telegram Group Bot Admin API",
    version="2.0.0",
    docs_url="/docs" if config.WEB_PANEL_ENABLED else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer(auto_error=False)


# ── Auth ────────────────────────────────────────────────────────────────────

async def verify_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> dict:
    """Validate API key and return user info."""
    if not config.WEB_PANEL_ENABLED:
        raise HTTPException(status_code=503, detail="Web panel is disabled")

    if not credentials:
        raise HTTPException(status_code=401, detail="API key required")

    api_key = credentials.credentials
    async with get_session() as session:
        result = await session.execute(
            select(WebAPIKey).where(
                WebAPIKey.api_key == api_key,
                WebAPIKey.is_active == True,
            )
        )
        key_entry = result.scalar_one_or_none()
        if not key_entry:
            raise HTTPException(status_code=403, detail="Invalid API key")

        key_entry.last_used = func.now()
        await session.commit()

        user_result = await session.execute(
            select(User).where(User.id == key_entry.user_id)
        )
        user = user_result.scalar_one_or_none()

    return {"user_id": key_entry.user_id, "permissions": key_entry.permissions, "user": user}


# ── Pydantic Schemas ────────────────────────────────────────────────────────

class GroupSettingsUpdate(BaseModel):
    welcome_default_text: Optional[str] = None
    ad_default_text: Optional[str] = None
    notification_auto_delete: Optional[int] = None
    anti_spam_ai_enabled: Optional[bool] = None
    anti_spam_keyword_enabled: Optional[bool] = None
    captcha_math_enabled: Optional[bool] = None
    captcha_auto_mute: Optional[bool] = None
    block_stickers: Optional[bool] = None
    block_gifs: Optional[bool] = None
    block_voice: Optional[bool] = None
    block_videos: Optional[bool] = None
    block_documents: Optional[bool] = None
    block_polls: Optional[bool] = None
    max_message_length: Optional[int] = None


class WelcomeConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    message_template: Optional[str] = None
    captcha_enabled: Optional[bool] = None
    captcha_type: Optional[str] = None
    auto_delete_after: Optional[int] = None
    buttons: Optional[str] = None


class AIUpdate(BaseModel):
    enabled: Optional[bool] = None
    model: Optional[str] = None
    system_prompt: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    max_history: Optional[int] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


class QuietModeUpdate(BaseModel):
    enabled: Optional[bool] = None
    start_hour: Optional[int] = None
    start_minute: Optional[int] = None
    end_hour: Optional[int] = None
    end_minute: Optional[int] = None
    days_of_week: Optional[str] = None
    auto_notify: Optional[bool] = None


class SensitiveWordAdd(BaseModel):
    words: list[str]
    match_mode: str = "contains"
    severity: str = "delete"


class BlacklistAdd(BaseModel):
    entries: list[dict]  # [{type: "user"/"word"/"pattern", value: "..."}]


class GenerateAPIKey(BaseModel):
    name: str = "Web API Key"
    permissions: str = "read"


# ── Health ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": "2.0.0"}


# ── Dashboard ───────────────────────────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        total_groups = (await session.execute(
            select(func.count(Group.id)).where(Group.is_active == True)
        )).scalar() or 0
        total_users = (await session.execute(
            select(func.count(User.id))
        )).scalar() or 0
        vip_users = (await session.execute(
            select(func.count(User.id)).where(User.vip_level != VIPLevel.FREE)
        )).scalar() or 0
        pending_orders = (await session.execute(
            select(func.count(PaymentOrder.id)).where(PaymentOrder.status == PaymentStatus.PENDING)
        )).scalar() or 0
        revenue = (await session.execute(
            select(func.sum(PaymentOrder.amount)).where(PaymentOrder.status == PaymentStatus.CONFIRMED)
        )).scalar() or 0.0

    return {
        "total_groups": total_groups,
        "total_users": total_users,
        "vip_users": vip_users,
        "pending_orders": pending_orders,
        "total_revenue": round(float(revenue), 2),
    }


# ── Groups ──────────────────────────────────────────────────────────────────

@app.get("/api/groups")
async def list_groups(auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        result = await session.execute(
            select(Group).where(Group.is_active == True).order_by(Group.id)
        )
        groups = list(result.scalars().all())
    return [{
        "id": g.id,
        "title": g.title,
        "username": g.username,
        "vip_level": g.vip_level.value,
        "created_at": g.created_at.isoformat(),
    } for g in groups]


@app.get("/api/groups/{group_id}/settings")
async def get_group_settings(group_id: int, auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        gs_result = await session.execute(
            select(GroupSettings).where(GroupSettings.group_id == group_id)
        )
        gs = gs_result.scalar_one_or_none()
        if not gs:
            return {"error": "Group not configured"}

        wc_result = await session.execute(
            select(WelcomeConfig).where(WelcomeConfig.group_id == group_id)
        )
        wc = wc_result.scalar_one_or_none()

        ai_result = await session.execute(
            select(AIConfig).where(AIConfig.group_id == group_id)
        )
        ai = ai_result.scalar_one_or_none()

        qc_result = await session.execute(
            select(QuietModeConfig).where(QuietModeConfig.group_id == group_id)
        )
        qc = qc_result.scalar_one_or_none()

    return {
        "group_settings": {
            "welcome_default_text": gs.welcome_default_text,
            "ad_default_text": gs.ad_default_text,
            "notification_auto_delete": gs.notification_auto_delete,
            "anti_spam_ai_enabled": gs.anti_spam_ai_enabled,
            "anti_spam_keyword_enabled": gs.anti_spam_keyword_enabled,
            "captcha_math_enabled": gs.captcha_math_enabled,
            "captcha_auto_mute": gs.captcha_auto_mute,
            "block_stickers": gs.block_stickers,
            "block_gifs": gs.block_gifs,
            "block_voice": gs.block_voice,
            "block_videos": gs.block_videos,
            "block_documents": gs.block_documents,
            "block_polls": gs.block_polls,
            "max_message_length": gs.max_message_length,
        },
        "welcome_config": {
            "enabled": wc.enabled if wc else False,
            "message_template": wc.message_template if wc else "",
            "captcha_enabled": wc.captcha_enabled if wc else False,
            "captcha_type": wc.captcha_type if wc else "button",
            "auto_delete_after": wc.auto_delete_after if wc else 300,
        } if wc else None,
        "ai_config": {
            "enabled": ai.enabled if ai else False,
            "model": ai.model.value if ai else "gpt-4o-mini",
            "system_prompt": ai.system_prompt if ai else "",
            "max_history": ai.max_history if ai else 10,
        } if ai else None,
        "quiet_mode": {
            "enabled": qc.enabled if qc else False,
            "start": f"{qc.start_hour:02d}:{qc.start_minute:02d}" if qc else "00:00",
            "end": f"{qc.end_hour:02d}:{qc.end_minute:02d}" if qc else "06:00",
            "days": qc.days_of_week if qc else "0,1,2,3,4,5,6",
        } if qc else None,
    }


@app.put("/api/groups/{group_id}/settings")
async def update_group_settings(
    group_id: int,
    update_data: GroupSettingsUpdate = Body(...),
    auth: dict = Depends(verify_api_key),
):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        gs_result = await session.execute(
            select(GroupSettings).where(GroupSettings.group_id == group_id)
        )
        gs = gs_result.scalar_one_or_none()
        if not gs:
            gs = GroupSettings(group_id=group_id)
            session.add(gs)

        for field, value in update_data.model_dump(exclude_unset=True).items():
            setattr(gs, field, value)

        await session.commit()

    return {"status": "updated", "group_id": group_id}


@app.put("/api/groups/{group_id}/welcome")
async def update_welcome_config(
    group_id: int,
    update_data: WelcomeConfigUpdate = Body(...),
    auth: dict = Depends(verify_api_key),
):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        wc_result = await session.execute(
            select(WelcomeConfig).where(WelcomeConfig.group_id == group_id)
        )
        wc = wc_result.scalar_one_or_none()
        if not wc:
            wc = WelcomeConfig(group_id=group_id)
            session.add(wc)

        for field, value in update_data.model_dump(exclude_unset=True).items():
            setattr(wc, field, value)

        await session.commit()

    return {"status": "updated", "group_id": group_id}


@app.put("/api/groups/{group_id}/ai")
async def update_ai_config(
    group_id: int,
    update_data: AIUpdate = Body(...),
    auth: dict = Depends(verify_api_key),
):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        ai_result = await session.execute(
            select(AIConfig).where(AIConfig.group_id == group_id)
        )
        ai = ai_result.scalar_one_or_none()
        if not ai:
            ai = AIConfig(group_id=group_id)
            session.add(ai)

        for field, value in update_data.model_dump(exclude_unset=True).items():
            if field == "model" and value:
                from database import AIModel
                setattr(ai, field, AIModel(value))
            else:
                setattr(ai, field, value)

        await session.commit()

    return {"status": "updated", "group_id": group_id}


@app.put("/api/groups/{group_id}/quiet-mode")
async def update_quiet_mode(
    group_id: int,
    update_data: QuietModeUpdate = Body(...),
    auth: dict = Depends(verify_api_key),
):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        qc_result = await session.execute(
            select(QuietModeConfig).where(QuietModeConfig.group_id == group_id)
        )
        qc = qc_result.scalar_one_or_none()
        if not qc:
            qc = QuietModeConfig(group_id=group_id)
            session.add(qc)

        for field, value in update_data.model_dump(exclude_unset=True).items():
            setattr(qc, field, value)

        await session.commit()

    return {"status": "updated", "group_id": group_id}


# ── Sensitive Words ─────────────────────────────────────────────────────────

@app.get("/api/groups/{group_id}/sensitive-words")
async def list_sensitive_words(group_id: int, auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        result = await session.execute(
            select(SensitiveWord).where(SensitiveWord.group_id == group_id)
        )
        words = list(result.scalars().all())
    return [{"id": w.id, "word": w.word, "match_mode": w.match_mode, "severity": w.severity} for w in words]


@app.post("/api/groups/{group_id}/sensitive-words")
async def add_sensitive_words(
    group_id: int,
    data: SensitiveWordAdd = Body(...),
    auth: dict = Depends(verify_api_key),
):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        added = 0
        for word in data.words:
            sw = SensitiveWord(
                group_id=group_id,
                word=word,
                match_mode=data.match_mode,
                severity=data.severity,
            )
            session.add(sw)
            added += 1
        await session.commit()

    return {"status": "added", "count": added}


@app.delete("/api/groups/{group_id}/sensitive-words")
async def clear_sensitive_words(group_id: int, auth: dict = Depends(verify_api_key)):
    if auth["permissions"] not in ("write", "admin"):
        raise HTTPException(status_code=403, detail="Write permission required")

    async with get_session() as session:
        result = await session.execute(
            select(SensitiveWord).where(SensitiveWord.group_id == group_id)
        )
        for sw in result.scalars().all():
            await session.delete(sw)
        await session.commit()

    return {"status": "cleared"}


# ── Blacklist ───────────────────────────────────────────────────────────────

@app.get("/api/groups/{group_id}/blacklist")
async def list_blacklist(group_id: int, auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        result = await session.execute(
            select(BlacklistEntry).where(
                (BlacklistEntry.group_id == group_id) | (BlacklistEntry.group_id == None)
            )
        )
        entries = list(result.scalars().all())
    return [{
        "id": e.id,
        "type": e.entry_type.value,
        "value": e.value,
        "group_id": e.group_id,
        "reason": e.reason,
    } for e in entries]


# ── Carousels ───────────────────────────────────────────────────────────────

@app.get("/api/carousels")
async def list_carousels(auth: dict = Depends(verify_api_key)):
    async with get_session() as session:
        result = await session.execute(
            select(CarouselMessage).order_by(CarouselMessage.id)
        )
        carousels = list(result.scalars().all())
    return [{
        "id": c.id,
        "name": c.name,
        "type": c.carousel_type.value,
        "interval": c.interval,
        "enabled": c.enabled,
        "content": c.content[:200],
    } for c in carousels]


# ── API Keys ────────────────────────────────────────────────────────────────

@app.post("/api/keys")
async def generate_api_key(data: GenerateAPIKey, auth: dict = Depends(verify_api_key)):
    if auth["permissions"] != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")

    new_key = secrets.token_urlsafe(32)
    async with get_session() as session:
        key_entry = WebAPIKey(
            user_id=auth["user_id"],
            api_key=new_key,
            name=data.name,
            permissions=data.permissions,
        )
        session.add(key_entry)
        await session.commit()

    return {"api_key": new_key, "name": data.name, "permissions": data.permissions}


# ── Global Config ───────────────────────────────────────────────────────────

@app.get("/api/config/ai-models")
async def list_ai_models(auth: dict = Depends(verify_api_key)):
    from services.ai_service import MODEL_REGISTRY
    return [{"id": k, **v} for k, v in MODEL_REGISTRY.items()]
