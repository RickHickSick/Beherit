"""Abstract Base Class for Agent SDK Engine Adapters."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, AsyncIterator, Dict
from beherit.models.agent import AgentModel, AuthStatus


class BaseAgentEngine(ABC):
    """Abstract interface for pluggable Agent SDK runners (Antigravity, Claude Code, Codex)."""

    @classmethod
    def build_system_context(cls, agent: AgentModel) -> str:
        """Construct full system context with master prompt, instructions, knowledge base, and MCP servers."""
        parts = []
        if agent.master_prompt:
            parts.append(agent.master_prompt.strip())

        if agent.instructions and agent.instructions.strip():
            parts.append(f"## Operational Guidelines\n{agent.instructions.strip()}")

        # Ingest knowledge documents from agent's knowledge/ directory
        if agent.file_path:
            agent_dir = Path(agent.file_path).parent
            knowledge_dir = agent_dir / "knowledge"
            if knowledge_dir.exists() and knowledge_dir.is_dir():
                knowledge_docs = []
                for doc in sorted(knowledge_dir.iterdir()):
                    if doc.is_file() and doc.suffix.lower() in (".md", ".txt", ".json", ".yaml", ".yml", ".csv"):
                        try:
                            content = doc.read_text(encoding="utf-8", errors="replace")[:50000]
                            knowledge_docs.append(f"### [Baza Wiedzy: {doc.name}]\n{content}")
                        except Exception:
                            pass
                if knowledge_docs:
                    parts.append("## Baza Wiedzy Agenta (Knowledge Base & Referencje)\n" + "\n\n".join(knowledge_docs))

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
