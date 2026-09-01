"""
Application configuration module using pydantic-settings.
Loads environment variables for database, API keys, and security settings.
"""

import json
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    All sensitive configuration is externalized for security.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database Configuration
    DB_URL: str

    # DeepSeek API Configuration
    DEEPSEEK_API_KEY: str

    # Security Configuration
    SECRET_KEY: str

    # JWT Configuration
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Application Configuration
    APP_NAME: str = "Game Boosting Platform"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    BACKEND_CORS_ORIGINS: str = (
        "http://localhost,http://127.0.0.1,http://localhost:3000,http://127.0.0.1:3000"
    )

    # Bootstrap admin account – credentials MUST be set via environment variables.
    # The defaults below are intentionally invalid so the app cannot start with
    # a well-known password.
    DEFAULT_ADMIN_EMAIL: str = "admin@gameboost.com"
    DEFAULT_ADMIN_USERNAME: str = "SystemAdmin"
    DEFAULT_ADMIN_PASSWORD: str = ""

    # File upload
    UPLOAD_DIR: str = "uploads"

    # Wallet: platform commission rate charged on order settlement.
    # Booster income = order_price * (1 - COMMISSION_RATE), rounded to cents.
    COMMISSION_RATE: float = 0.0

    @property
    def cors_origins(self) -> list[str]:
        """
        Parse CORS origins from environment.
        Supports either:
        - comma-separated string
        - JSON array string
        """
        raw = (self.BACKEND_CORS_ORIGINS or "").strip()
        if not raw:
            return []

        if raw.startswith("["):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed if str(item).strip()]
            except json.JSONDecodeError:
                pass

        return [item.strip() for item in raw.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    """
    Returns cached settings instance.
    Using lru_cache ensures settings are only loaded once.
    """
    return Settings()


settings = get_settings()
