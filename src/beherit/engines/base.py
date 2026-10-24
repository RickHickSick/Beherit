"""Abstract Base Class for Agent SDK Engine Adapters."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

from beherit.models.agent import AgentModel, AuthStatus


class BaseAgentEngine(ABC):
    """Abstract interface for pluggable Agent SDK runners (Antigravity, Claude Code, Codex)."""

    @classmethod
    def build_system_context(cls, agent: AgentModel, query: str | None = None) -> str:
        """Construct full system context with master prompt, high-priority RAG knowledge base, instructions, and MCP servers."""
        parts = []
        if agent.master_prompt:
            parts.append(agent.master_prompt.strip())

        # Ingest knowledge documents using Local RAG (Primary context / Source of Truth)
        from beherit.core.rag import KnowledgeRAG

        rag = KnowledgeRAG()
        knowledge_context = rag.build_knowledge_context(agent, query=query)
        if knowledge_context:
            parts.append(knowledge_context)

        if agent.instructions and agent.instructions.strip():
            parts.append(f"## Operational Guidelines\n{agent.instructions.strip()}")

        # Add MCP servers information if configured
        if agent.mcp_servers:
            mcp_items = []
            for name, cfg in agent.mcp_servers.items():
                mcp_items.append(f"- **{name}**: `{cfg.command} {' '.join(cfg.args)}`")
            parts.append("## Dostępne Narzędzia MCP (Model Context Protocol)\n" + "\n".join(mcp_items))

        return "\n\n".join(parts)

    @abstractmethod
    async def get_auth_status(self, agent_slug: str) -> AuthStatus:
        """Check the authentication status for an agent's isolated SDK profile."""

    @abstractmethod
    async def start_auth_flow(self, agent_slug: str) -> dict[str, Any]:
        """Initiate the SDK login flow (Device code, login URL, etc.)."""

    @abstractmethod
    async def complete_auth_flow(self, agent_slug: str, auth_payload: dict[str, Any]) -> bool:
        """Verify and store credentials upon user authentication."""

    @abstractmethod
    async def stream_chat(
        self,
        agent: AgentModel,
        message: str,
        history: list | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute an agent turn with the SDK and yield streaming events
        (token, thought, tool_call, error, done).
        """

    async def cancel_turn(self, agent_slug: str) -> bool:
        """Cancel any running turn / process for the given agent."""
        return False
