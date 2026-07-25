from .wizard import WIZARD_HANDLERS
from .welcome import WELCOME_HANDLERS
from .carousel import CAROUSEL_HANDLERS
from .moderation import MODERATION_HANDLERS
from .payment import PAYMENT_HANDLERS
from .admin import ADMIN_HANDLERS
from .captcha import MATH_CAPTCHA_HANDLERS
from .blacklist import BLACKLIST_HANDLERS
from .ai import AI_HANDLERS
from .quiet_mode import QUIET_MODE_HANDLERS

ALL_HANDLERS = (
    WIZARD_HANDLERS
    + WELCOME_HANDLERS
    + CAROUSEL_HANDLERS
    + MODERATION_HANDLERS
    + PAYMENT_HANDLERS
    + ADMIN_HANDLERS
    + MATH_CAPTCHA_HANDLERS
    + BLACKLIST_HANDLERS
    + AI_HANDLERS
    + QUIET_MODE_HANDLERS
)
