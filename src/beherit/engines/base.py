"""Abstract Base Class for Agent SDK Engine Adapters."""

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Dict
from beherit.models.agent import AgentModel, AuthStatus


class BaseAgentEngine(ABC):
    """Abstract interface for pluggable Agent SDK runners (Antigravity, Claude Code, Codex)."""

    @abstractmethod
    async def get_auth_status(self, agent_slug: str) -> AuthStatus:
        """Check the authentication status for an agent's isolated SDK profile."""
        pass

    @abstractmethod
    async def start_auth_flow(self, agent_slug: str) -> Dict[str, Any]:
        """Initiate the SDK login flow (Device code, login URL, etc.)."""
        pass

    @abstractmethod
    async def complete_auth_flow(self, agent_slug: str, auth_payload: Dict[str, Any]) -> bool:
        """Verify and store credentials upon user authentication."""
        pass

    @abstractmethod
    async def stream_chat(
        self,
        agent: AgentModel,
        message: str,
        history: list = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute an agent turn with the SDK and yield streaming events
        (token, thought, tool_call, error, done).
        """
        pass
