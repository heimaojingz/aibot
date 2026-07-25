import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    """Centralised bot configuration loaded from environment variables."""

    # --- Telegram ---
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    BOT_USERNAME: str = os.getenv("BOT_USERNAME", "")

    # --- Database ---
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", "sqlite+aiosqlite:///bot_data.db"
    )

    # --- Super Admin ---
    SUPER_ADMIN_IDS: list[int] = field(default_factory=lambda: [
        int(uid) for uid in os.getenv("SUPER_ADMIN_IDS", "").split(",") if uid
    ])

    # --- Payment ---
    USDT_WALLET_ADDRESS: str = os.getenv("USDT_WALLET_ADDRESS", "")
    TON_WALLET_ADDRESS: str = os.getenv("TON_WALLET_ADDRESS", "")
    TELEGRAM_STARS_ENABLED: bool = os.getenv("TELEGRAM_STARS_ENABLED", "0") == "1"

    VIP_MONTHLY_PRICE: float = float(os.getenv("VIP_MONTHLY_PRICE", "9.99"))
    VIP_QUARTERLY_PRICE: float = float(os.getenv("VIP_QUARTERLY_PRICE", "24.99"))
    VIP_YEARLY_PRICE: float = float(os.getenv("VIP_YEARLY_PRICE", "79.99"))

    # --- Carousel ---
    DEFAULT_CAROUSEL_INTERVAL: int = int(os.getenv("DEFAULT_CAROUSEL_INTERVAL", "600"))

    # --- Moderation ---
    DEFAULT_WARN_LIMIT: int = int(os.getenv("DEFAULT_WARN_LIMIT", "3"))
    DEFAULT_MUTE_DURATION: int = int(os.getenv("DEFAULT_MUTE_DURATION", "3600"))

    # --- Anti-spam ---
    ANTI_SPAM_ENABLED: bool = os.getenv("ANTI_SPAM_ENABLED", "1") == "1"
    MESSAGE_COOLDOWN: float = float(os.getenv("MESSAGE_COOLDOWN", "1.5"))
    MAX_MESSAGES_PER_WINDOW: int = int(os.getenv("MAX_MESSAGES_PER_WINDOW", "5"))
    SPAM_WINDOW_SECONDS: float = float(os.getenv("SPAM_WINDOW_SECONDS", "3.0"))

    # --- Welcome ---
    CAPTCHA_ENABLED: bool = os.getenv("CAPTCHA_ENABLED", "1") == "1"
    CAPTCHA_TIMEOUT: int = int(os.getenv("CAPTCHA_TIMEOUT", "120"))
    WELCOME_AUTO_DELETE: int = int(os.getenv("WELCOME_AUTO_DELETE", "300"))


    # --- AI Service ---
    AI_DEFAULT_MODEL: str = os.getenv("AI_DEFAULT_MODEL", "gpt-4o-mini")
    AI_DEFAULT_API_KEY: str = os.getenv("AI_DEFAULT_API_KEY", "")
    AI_DEFAULT_API_BASE: str = os.getenv("AI_DEFAULT_API_BASE", "")
    AI_MAX_HISTORY: int = int(os.getenv("AI_MAX_HISTORY", "10"))
    AI_ENABLED: bool = os.getenv("AI_ENABLED", "1") == "1"

    # --- Web Admin Panel ---
    WEB_PANEL_ENABLED: bool = os.getenv("WEB_PANEL_ENABLED", "0") == "1"
    WEB_PANEL_HOST: str = os.getenv("WEB_PANEL_HOST", "0.0.0.0")
    WEB_PANEL_PORT: int = int(os.getenv("WEB_PANEL_PORT", "8080"))
    WEB_PANEL_SECRET: str = os.getenv("WEB_PANEL_SECRET", "change-me-to-a-random-string")

    # --- Quiet Mode ---
    QUIET_MODE_CHECK_INTERVAL: int = int(os.getenv("QUIET_MODE_CHECK_INTERVAL", "60"))

    # --- Notification ---
    NOTIFICATION_AUTO_DELETE: int = int(os.getenv("NOTIFICATION_AUTO_DELETE", "0"))
    # --- General ---
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Shanghai")


config = Config()

