"""Pydantic Domain Models for Markdown-first Agent Definitions."""

import time
from datetime import date
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EngineType(str, Enum):
    ANTIGRAVITY = "antigravity"
    CLAUDE_CODE = "claude-code"
    CODEX = "codex"


class MCPServerConfig(BaseModel):
    """External Model Context Protocol (MCP) server configuration."""
    command: str = Field(..., description="Executable command e.g. npx, python, or binary path")
    args: list[str] = Field(default_factory=list, description="Arguments passed to MCP server")
    env: dict[str, str] = Field(default_factory=dict, description="Environment variables for the server")


class AuthStatus(str, Enum):
    UNAUTHENTICATED = "unauthenticated"
    AUTHENTICATED = "authenticated"
    EXPIRED = "expired"


class AgentAuthConfig(BaseModel):
    """Isolated authentication configuration per agent."""
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)
    status: AuthStatus = Field(default=AuthStatus.UNAUTHENTICATED)
    profile_id: str | None = Field(default=None)
    auth_method: str | None = Field(default="manual")
    masked_token: str | None = Field(default=None)
    expires_at: float | None = Field(default=None)


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


class ScheduledJob(BaseModel):
    """Individual scheduled job entry in multi-cron schedule (BEH-045)."""
    id: str = Field(default_factory=lambda: f"job_{int(time.time()*1000)%1000000}")
    name: str = Field(default="Zadanie cykliczne", max_length=100)
    cron: str = Field(default="0 8 * * *")
    prompt: str = Field(default="", description="Dedicated prompt to execute for this schedule")
    enabled: bool = Field(default=True)
    destination_file: str | None = Field(default=None, description="Optional target file to save output")


class AgentSchedule(BaseModel):
    """Autonomous background execution schedule supporting multi-cron."""
    enabled: bool = Field(default=False)
    cron: str | None = Field(default="0 8 * * *")
    prompt: str | None = Field(default=None, description="Optional default prompt for autonomous turns")
    jobs: list[ScheduledJob] = Field(default_factory=list, description="List of multiple scheduled jobs")
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
    instructions: str | None = Field(default=None)
    mcp_servers: dict[str, MCPServerConfig] = Field(
        default_factory=dict,
        description="Dictionary of external MCP servers configured for this agent",
    )
    
    # Metadata fields for runtime observation
    file_path: str | None = Field(default=None, description="Absolute path on disk to agent.md")
    is_valid: bool = Field(default=True, description="False if file has syntax warnings or errors")
    validation_error: str | None = Field(default=None, description="Error details if invalid")
