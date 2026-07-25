import enum
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from config import config
import logging
logger = logging.getLogger(__name__)

# ── Async engine & session factory ──────────────────────────────────────────
engine = create_async_engine(config.DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


# ── Enums ───────────────────────────────────────────────────────────────────

class PaymentMethod(str, enum.Enum):
    USDT_TRC20 = "usdt_trc20"
    TON = "ton"
    TELEGRAM_STARS = "telegram_stars"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class VIPLevel(str, enum.Enum):
    FREE = "free"
    BASIC = "basic"
    PREMIUM = "premium"


class CarouselType(str, enum.Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    GIF = "gif"


class PunishmentType(str, enum.Enum):
    WARN = "warn"
    MUTE = "mute"
    KICK = "kick"
    BAN = "ban"





# ── New Enums (extended features) ───────────────────────────────────────────

class AIModel(str, enum.Enum):
    """14 AI model presets for the AI Q&A module."""
    GPT4O = "gpt-4o"
    GPT4O_MINI = "gpt-4o-mini"
    GPT4_TURBO = "gpt-4-turbo"
    GPT4 = "gpt-4"
    GPT35_TURBO = "gpt-3.5-turbo"
    CLAUDE_35_SONNET = "claude-3.5-sonnet"
    CLAUDE_3_OPUS = "claude-3-opus"
    CLAUDE_3_HAIKU = "claude-3-haiku"
    GEMINI_15_PRO = "gemini-1.5-pro"
    GEMINI_15_FLASH = "gemini-1.5-flash"
    DEEPSEEK_V3 = "deepseek-v3"
    DEEPSEEK_R1 = "deepseek-r1"
    QWEN_MAX = "qwen-max"
    QWEN_PLUS = "qwen-plus"


class BlacklistType(str, enum.Enum):
    USER = "user"
    WORD = "word"
    PATTERN = "pattern"
# ── Models ──────────────────────────────────────────────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    first_name: Mapped[str] = mapped_column(String(256), default="")
    last_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_group_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    vip_level: Mapped[VIPLevel] = mapped_column(
        Enum(VIPLevel), default=VIPLevel.FREE
    )
    vip_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    balance: Mapped[float] = mapped_column(Float, default=0.0)  # USD equivalent
    credits: Mapped[int] = mapped_column(Integer, default=0)  # internal credits
    language: Mapped[str] = mapped_column(String(16), default="zh")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    warnings: Mapped[list["GroupMember"]] = relationship(
        "GroupMember", back_populates="user", lazy="selectin"
    )


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    title: Mapped[str] = mapped_column(String(256), default="")
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    invite_link: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    vip_level: Mapped[VIPLevel] = mapped_column(Enum(VIPLevel), default=VIPLevel.FREE)
    vip_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    settings: Mapped[str] = mapped_column(Text, default="{}")  # JSON blob
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    @property
    def settings_dict(self) -> dict:
        return json.loads(self.settings) if self.settings else {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        return self.settings_dict.get(key, default)


class GroupMember(Base):
    __tablename__ = "group_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    warn_count: Mapped[int] = mapped_column(Integer, default=0)
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False)
    mute_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    left_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped["User"] = relationship("User", back_populates="warnings", lazy="selectin")


class WelcomeConfig(Base):
    __tablename__ = "welcome_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    message_template: Mapped[str] = mapped_column(Text, default=(
        "👋 欢迎 <b>{user_mention}</b> 加入 <b>{group_title}</b>！\n\n"
        "📋 请先阅读群规并完成验证\n"
        "💬 祝你在这里交流愉快~"
    ))
    media_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    captcha_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    captcha_type: Mapped[str] = mapped_column(String(32), default="button")  # button / math / choice
    auto_delete_after: Mapped[int] = mapped_column(Integer, default=300)  # seconds
    buttons: Mapped[str] = mapped_column(Text, default=json.dumps([
        {"text": "📜 群规", "url": "https://t.me"},
        {"text": "📞 联系管理", "url": "https://t.me/admin"},
    ]))


class CarouselMessage(Base):
    __tablename__ = "carousel_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(256), default="Untitled")
    carousel_type: Mapped[CarouselType] = mapped_column(
        Enum(CarouselType), default=CarouselType.TEXT
    )
    content: Mapped[str] = mapped_column(Text, default="")
    media_file_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    buttons: Mapped[str] = mapped_column(Text, default="[]")  # JSON
    interval: Mapped[int] = mapped_column(Integer, default=600)  # seconds
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sent_message_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # JSON: {"chat_id": msg_id, ...}
    delete_previous: Mapped[bool] = mapped_column(Boolean, default=True)
    pin_message: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_unit: Mapped[str] = mapped_column(String(16), default="minutes")  # hours or minutes
    time_window_start: Mapped[int] = mapped_column(Integer, default=0)  # 0-23, 0=disabled
    time_window_end: Mapped[int] = mapped_column(Integer, default=0)  # 0-23, 0=disabled
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    targets: Mapped[list["CarouselTarget"]] = relationship(
        "CarouselTarget", back_populates="carousel", lazy="selectin",
        cascade="all, delete-orphan"
    )


class CarouselTarget(Base):
    __tablename__ = "carousel_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    carousel_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("carousel_messages.id")
    )
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    next_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    carousel: Mapped["CarouselMessage"] = relationship(
        "CarouselMessage", back_populates="targets"
    )


class ActivationCode(Base):
    __tablename__ = "activation_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    vip_level: Mapped[VIPLevel] = mapped_column(Enum(VIPLevel), default=VIPLevel.BASIC)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    is_used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    method: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod))
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(16), default="USD")
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), default=PaymentStatus.PENDING
    )
    tx_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    vip_level: Mapped[VIPLevel] = mapped_column(Enum(VIPLevel), default=VIPLevel.BASIC)
    duration_days: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class BannedWord(Base):
    __tablename__ = "banned_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    word: Mapped[str] = mapped_column(String(256))
    match_mode: Mapped[str] = mapped_column(String(32), default="contains")  # exact/contains/regex
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ModLog(Base):
    __tablename__ = "mod_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    moderator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    action: Mapped[PunishmentType] = mapped_column(Enum(PunishmentType))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())



# ── Extended Feature Models ─────────────────────────────────────────────────


class BlacklistEntry(Base):
    __tablename__ = "blacklist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("groups.id"), nullable=True)
    entry_type: Mapped[BlacklistType] = mapped_column(Enum(BlacklistType))
    value: Mapped[str] = mapped_column(String(512))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class SensitiveWord(Base):
    __tablename__ = "sensitive_words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"))
    word: Mapped[str] = mapped_column(String(512))
    match_mode: Mapped[str] = mapped_column(String(32), default="contains")
    severity: Mapped[str] = mapped_column(String(32), default="delete")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AIConfig(Base):
    __tablename__ = "ai_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("groups.id"), unique=True, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    model: Mapped[AIModel] = mapped_column(Enum(AIModel), default=AIModel.GPT4O_MINI)
    system_prompt: Mapped[str] = mapped_column(Text, default="你是一个有帮助的群组助手。")
    api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    api_base: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_history: Mapped[int] = mapped_column(Integer, default=10)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    max_tokens: Mapped[int] = mapped_column(Integer, default=2048)


class QuietModeConfig(Base):
    __tablename__ = "quiet_mode_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    start_hour: Mapped[int] = mapped_column(Integer, default=0)
    start_minute: Mapped[int] = mapped_column(Integer, default=0)
    end_hour: Mapped[int] = mapped_column(Integer, default=6)
    end_minute: Mapped[int] = mapped_column(Integer, default=0)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    cron_expression: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    days_of_week: Mapped[str] = mapped_column(String(64), default="0,1,2,3,4,5,6")
    auto_notify: Mapped[bool] = mapped_column(Boolean, default=True)
    mute_new_members: Mapped[bool] = mapped_column(Boolean, default=False)


class WebAPIKey(Base):
    __tablename__ = "web_api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    api_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="Default Key")
    permissions: Mapped[str] = mapped_column(Text, default="read")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GroupSettings(Base):
    __tablename__ = "group_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), unique=True)
    welcome_default_text: Mapped[str] = mapped_column(Text, default="机器人问题反馈联系 @doubao007")
    ad_default_text: Mapped[str] = mapped_column(Text, default="机器人问题反馈联系 @doubao007")
    notification_auto_delete: Mapped[int] = mapped_column(Integer, default=0)
    anti_spam_ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    anti_spam_keyword_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    captcha_math_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    captcha_auto_mute: Mapped[bool] = mapped_column(Boolean, default=True)
    block_stickers: Mapped[bool] = mapped_column(Boolean, default=False)
    block_gifs: Mapped[bool] = mapped_column(Boolean, default=False)
    block_voice: Mapped[bool] = mapped_column(Boolean, default=False)
    block_videos: Mapped[bool] = mapped_column(Boolean, default=False)
    block_documents: Mapped[bool] = mapped_column(Boolean, default=False)
    block_polls: Mapped[bool] = mapped_column(Boolean, default=False)
    max_message_length: Mapped[int] = mapped_column(Integer, default=0)
    delete_previous: Mapped[bool] = mapped_column(Boolean, default=True)
    pin_message: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat_unit: Mapped[str] = mapped_column(String(16), default="minutes")  # hours or minutes
    time_window_start: Mapped[int] = mapped_column(Integer, default=0)  # 0-23, 0=disabled
    time_window_end: Mapped[int] = mapped_column(Integer, default=0)  # 0-23, 0=disabled
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


# ── Helper ──────────────────────────────────────────────────────────────────

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def get_session() -> AsyncSession:
    return async_session()





