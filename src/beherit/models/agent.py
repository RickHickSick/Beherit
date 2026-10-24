"""Pydantic Domain Models for Markdown-first Agent Definitions."""

from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict


class EngineType(str, Enum):
    ANTIGRAVITY = "antigravity"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


class AuthStatus(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"


class AgentAuthConfig(BaseModel):
    """Isolated authentication configuration per agent."""
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)
    status: AuthStatus = Field(default=AuthStatus.UNAUTHENTICATED)
    profile_id: Optional[str] = Field(default=None)
    auth_method: Optional[str] = Field(default="manual")
    masked_token: Optional[str] = Field(default=None)
    expires_at: Optional[float] = Field(default=None)


class TerminalMode(str, Enum):
    DISABLED = "disabled"
    RESTRICTED = "restricted"      # Workspace sandbox only
    UNRESTRICTED = "unrestricted"  # Full unrestricted host shell execution


class AgentCapabilities(BaseModel):
    """Execution permissions granted to an agent."""
    terminal: bool = Field(default=False)
    terminal_mode: TerminalMode = Field(
        default=TerminalMode.RESTRICTED,
        description="Level of terminal access: disabled, restricted (workspace only), or unrestricted",
    )
    file_read: bool = Field(default=True)
    file_write: bool = Field(default=False)
    database_read: bool = Field(default=False)


class ScheduleReporting(BaseModel):
    """Multi-channel reporting configuration for autonomous background tasks."""
    sql_history: bool = Field(default=True, description="Save job execution to SQLite history")
    markdown_report: bool = Field(default=True, description="Generate Markdown report in agent folder")
    telegram_alert: bool = Field(default=False, description="Send notification to Telegram")
    telegram_on_error_only: bool = Field(default=True, description="Only notify on failures or anomalies")


class AgentSchedule(BaseModel):
    """Autonomous background execution schedule."""
    enabled: bool = Field(default=False)
    cron: Optional[str] = Field(default="0 8 * * *")
    prompt: Optional[str] = Field(default=None, description="Optional custom prompt for scheduled turn")
    reporting: ScheduleReporting = Field(default_factory=ScheduleReporting)


class AgentModel(BaseModel):
    """Full representation of an Agent loaded from Markdown and frontmatter."""
    model_config = ConfigDict(extra="allow")

    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str = Field(default="")
    engine: EngineType = Field(default=EngineType.ANTIGRAVITY)
    model: str = Field(default="gemini-3.8-flash")
    created_at: date = Field(default_factory=date.today)
    auth: AgentAuthConfig = Field(default_factory=AgentAuthConfig)
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    schedule: AgentSchedule = Field(default_factory=AgentSchedule)
    master_prompt: str = Field(default="")
    instructions: Optional[str] = Field(default=None)
    
    # Metadata fields for runtime observation
    file_path: Optional[str] = Field(default=None, description="Absolute path on disk to agent.md")
    is_valid: bool = Field(default=True, description="False if file has syntax warnings or errors")
    validation_error: Optional[str] = Field(default=None, description="Error details if invalid")
