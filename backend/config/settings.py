from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI


# ============================================================
# PROJECT PATHS
# ============================================================

CONFIG_DIR = Path(__file__).resolve().parent

PROJECT_ROOT = CONFIG_DIR.parent

ENV_FILE = CONFIG_DIR / ".env"


# ============================================================
# ENVIRONMENT LOADING
# ============================================================

load_dotenv(
    dotenv_path=ENV_FILE,
)


# ============================================================
# LOGGING
# ============================================================

LOG_LEVEL = os.getenv(
    "LOG_LEVEL",
    "INFO",
).upper()

logging.basicConfig(
    level=getattr(
        logging,
        LOG_LEVEL,
        logging.INFO,
    ),
    format=(
        "%(asctime)s - %(levelname)s - "
        "%(message)s"
    ),
)

logger = logging.getLogger(__name__)


# ============================================================
# OPENAI CONFIGURATION
# ============================================================

OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY"
)


# ============================================================
# ORACLE CONFIGURATION
# ============================================================

ORACLE_USER = os.getenv(
    "ORACLE_USER"
)

ORACLE_PASS = os.getenv(
    "ORACLE_PASS"
)

WALLET_PATH = os.getenv(
    "WALLET_PATH"
)

TNS_ALIAS = os.getenv(
    "TNS_ALIAS"
)

ORACLE_DSN = os.getenv(
    "ORACLE_DSN"
)

CS = os.getenv(
    "cs"
)

ORACLE_WALLET_PASSWORD = os.getenv(
    "ORACLE_WALLET_PASSWORD"
)


# ============================================================
# EMAIL CONFIGURATION
# ============================================================

MAIL_SERVER = os.getenv(
    "MAIL_SERVER"
)

MAIL_PORT = int(
    os.getenv(
        "MAIL_PORT",
        "587",
    )
)

MAIL_USE_TLS = os.getenv(
    "MAIL_USE_TLS",
    "true",
).strip().lower() in {"1", "true", "yes", "on"}

MAIL_USERNAME = os.getenv(
    "MAIL_USERNAME"
)

MAIL_PASSWORD = os.getenv(
    "MAIL_PASSWORD"
)

MAIL_DEFAULT_SENDER = os.getenv(
    "MAIL_DEFAULT_SENDER"
)


# ============================================================
# OTP CONFIGURATION
# ============================================================

OTP_EXPIRY_SECONDS = int(
    os.getenv(
        "OTP_EXPIRY_SECONDS",
        "300",
    )
)


# ============================================================
# AUTHENTICATION CONFIGURATION
# ============================================================

AUTH_JWT_SECRET = os.getenv(
    "AUTH_JWT_SECRET"
)

AUTH_JWT_ALGORITHM = os.getenv(
    "AUTH_JWT_ALGORITHM",
    "HS256",
)

AUTH_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.getenv(
        "AUTH_ACCESS_TOKEN_EXPIRE_MINUTES",
        "480",
    )
)

OTP_MAX_ATTEMPTS = int(
    os.getenv(
        "OTP_MAX_ATTEMPTS",
        "5",
    )
)


logger.info(
    "Application configuration loaded."
)


# ============================================================
# OPENAI CLIENT
# ============================================================

def get_openai_client() -> OpenAI:
    return OpenAI(
        api_key=OPENAI_API_KEY
    )