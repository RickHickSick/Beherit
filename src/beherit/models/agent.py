"""Pydantic Domain Models for Markdown-first Agent Definitions."""

from datetime import date
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


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


class AgentCapabilities(BaseModel):
    """Execution permissions granted to an agent."""
    terminal: bool = Field(default=False)
    file_read: bool = Field(default=True)
    file_write: bool = Field(default=False)
    database_read: bool = Field(default=False)


class AgentSchedule(BaseModel):
    """Autonomous background execution schedule."""
    enabled: bool = Field(default=False)
    cron: Optional[str] = Field(default="0 8 * * *")


class AgentModel(BaseModel):
    """Full representation of an Agent loaded from Markdown and frontmatter."""
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
