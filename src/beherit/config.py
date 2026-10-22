"""Application Configuration with strict environment decoupling (Zero-Leak Policy)."""

from pathlib import Path
from typing import List, Union
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central settings for Beherit server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server Network
    BEHERIT_HOST: str = Field(default="0.0.0.0", description="Host interface to bind")
    BEHERIT_PORT: int = Field(default=8650, description="Port to listen on")
    BEHERIT_ENV: str = Field(default="development", description="Runtime environment")

    # Security
    BEHERIT_SECRET_KEY: str = Field(
        default="beherit-default-insecure-secret-key-please-change-in-production",
        description="Secret key for signing sessions and tokens",
    )

    # Storage Paths
    BEHERIT_DATA_DIR: Path = Field(
        default=Path("./data"),
        description="Base path for data storage (sqlite db, sessions)",
    )
    BEHERIT_AGENTS_DIR: Union[Path, None] = Field(
        default=None,
        description="Custom path for agents (e.g. Obsidian vault folder). If not set, defaults to BEHERIT_DATA_DIR / 'agents'",
    )
    BEHERIT_DB_PATH: Path = Field(
        default=Path("./data/beherit.db"),
        description="Path to SQLite database",
    )

    # CORS
    BEHERIT_CORS_ORIGINS: List[str] = Field(
        default=["*"],
        description="CORS allowed origins",
    )

    # Optional Database Connector
    DATABASE_URL: Union[str, None] = Field(
        default=None,
        description="Optional database URL for external database tools",
    )

    # Optional Telegram Reporting Alerts
    TELEGRAM_BOT_TOKEN: Union[str, None] = Field(
        default=None,
        description="Optional Telegram bot token for job alerts",
    )
    TELEGRAM_CHAT_ID: Union[str, None] = Field(
        default=None,
        description="Optional Telegram chat ID for job alerts",
    )

    @property
    def agents_dir(self) -> Path:
        """Return the directory path where agent folders are stored."""
        if self.BEHERIT_AGENTS_DIR:
            return self.BEHERIT_AGENTS_DIR.resolve()
        return (self.BEHERIT_DATA_DIR / "agents").resolve()


settings = Settings()
