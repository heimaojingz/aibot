# ── New Enums for extended features ─────────────────────────────────────────

class AIModel(str, enum.Enum):
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


# ── New Models ──────────────────────────────────────────────────────────────

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
    match_mode: Mapped[str] = mapped_column(String(32), default="contains")  # exact/contains/regex
    severity: Mapped[str] = mapped_column(String(32), default="delete")  # delete / warn / mute / kick / ban
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
    permissions: Mapped[str] = mapped_column(Text, default="read")  # read / write / admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_used: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GroupSettings(Base):
    __tablename__ = "group_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("groups.id"), unique=True)
    welcome_default_text: Mapped[str] = mapped_column(Text, default=(
        "机器人问题反馈联系 @doubao007"
    ))
    ad_default_text: Mapped[str] = mapped_column(Text, default=(
        "机器人问题反馈联系 @doubao007"
    ))
    notification_auto_delete: Mapped[int] = mapped_column(Integer, default=0)  # 0=never
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
    max_message_length: Mapped[int] = mapped_column(Integer, default=0)  # 0=unlimited
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
