"""Application Configuration with strict environment decoupling (Zero-Leak Policy)."""

from pathlib import Path

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

    # Security & Access Protection
    BEHERIT_SECRET_KEY: str = Field(
        default="beherit-default-insecure-secret-key-please-change-in-production",
        description="Secret key for signing sessions and tokens",
    )
    BEHERIT_ACCESS_TOKEN: str | None = Field(
        default=None,
        description="Optional access PIN/token guarding the Web UI and API (status 401 if unauthorized)",
    )

    # Storage Paths
    BEHERIT_DATA_DIR: Path = Field(
        default=Path("./data"),
        description="Base path for data storage (sqlite db, sessions)",
    )
    BEHERIT_AGENTS_DIR: Path | None = Field(
        default=None,
        description="Custom path for agents (e.g. Obsidian vault folder). If not set, defaults to BEHERIT_DATA_DIR / 'agents'",
    )
    BEHERIT_DB_PATH: Path = Field(
        default=Path("./data/beherit.db"),
        description="Path to SQLite database",
    )

    # CORS
    BEHERIT_CORS_ORIGINS: list[str] = Field(
        default=["*"],
        description="CORS allowed origins",
    )

    # Optional OAuth 2.0 Credentials (RFC 8628 Device Flow)
    GOOGLE_OAUTH_CLIENT_ID: str | None = Field(
        default=None,
        description="Optional Google Cloud OAuth 2.0 Client ID for RFC 8628 Device Authorization",
    )
    GOOGLE_OAUTH_CLIENT_SECRET: str | None = Field(
        default=None,
        description="Optional Google Cloud OAuth 2.0 Client Secret",
    )

    # Optional Database Connector
    DATABASE_URL: str | None = Field(
        default=None,
        description="Optional database URL for external database tools",
    )

    # Optional Telegram Reporting Alerts & Bot (BEH-068)
    TELEGRAM_BOT_TOKEN: str | None = Field(
        default=None,
        description="Optional Telegram bot token for interactive bot and job alerts",
    )
    TELEGRAM_CHAT_ID: str | None = Field(
        default=None,
        description="Optional Telegram chat ID for job alerts and owner recognition",
    )
    TELEGRAM_ALLOWED_USER_IDS: str | None = Field(
        default=None,
        description="Optional comma-separated Telegram user IDs allowed to interact with the bot",
    )

    # Plane Project Management Integration (BEH-065)
    PLANE_API_URL: str = Field(
        default="http://192.168.1.60:80/api/v1",
        description="Base URL for Plane API",
    )
    PLANE_API_KEY: str | None = Field(
        default=None,
        description="API token for Plane (X-Api-Key)",
    )
    PLANE_WORKSPACE_SLUG: str = Field(
        default="arch",
        description="Default Plane workspace slug",
    )
    PLANE_PROJECT_ID: str = Field(
        default="e428cd83-e3a2-49b3-bfe6-a1c26c4c0993",
        description="Default Plane project UUID (BEH)",
    )

    @property
    def agents_dir(self) -> Path:
        """Return the directory path where agent folders are stored."""
        if self.BEHERIT_AGENTS_DIR:
            return self.BEHERIT_AGENTS_DIR.resolve()
        return (self.BEHERIT_DATA_DIR / "agents").resolve()


settings = Settings()
