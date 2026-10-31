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

        # Ingest persistent long-term memory (BEH-062)
        try:
            from beherit.core.memory import AgentMemoryManager
            mem_mgr = AgentMemoryManager()
            mem_content = mem_mgr.read_memory(agent)
            if mem_content:
                parts.append(f"## Pamięć Długoterminowa i Profil Użytkownika\n{mem_content}")
        except Exception:
            pass

        # Ingest active task plan and scratchpad (BEH-063)
        try:
            from beherit.core.todo import AgentTodoManager
            todo_mgr = AgentTodoManager()
            todo_items = todo_mgr.read_todos(agent)
            if todo_items:
                raw_todo = todo_mgr.read_raw(agent)
                parts.append(
                    "## Aktywny Plan Działań i Scratchpad (todo.md)\n"
                    "Posiadasz dynamiczny scratchpad zadań. Używaj narzędzia `manage_todo(action='update', item_id=..., status='done')` "
                    "do aktualizowania postępu w miarę realizacji kolejnych etapów:\n"
                    f"{raw_todo}"
                )
        except Exception:
            pass

        # Add available peer agents for delegation (BEH-060)
        try:
            from beherit.core.repository import AgentRepository
            repo = AgentRepository()
            other_agents = [a for a in repo.list_agents() if a.slug != agent.slug]
            if other_agents:
                agent_items = [
                    f"- **{a.name}** (slug: `{a.slug}`): {a.description or 'Wyspecjalizowany agent pomocniczy'}"
                    for a in other_agents
                ]
                parts.append(
                    "## Dostępni Agenci do Współpracy (Narzędzie call_agent)\n"
                    "Posiadasz możliwość delegowania podzadań do innych zarejestrowanych agentów Beherit za pomocą narzędzia `call_agent(agent_slug, prompt, context)`:\n"
                    + "\n".join(agent_items)
                )
        except Exception:
            pass

        # Ingest dynamic agent skills (BEH-066)
        try:
            from beherit.core.skills import SkillManager
            skill_mgr = SkillManager()
            skills = skill_mgr.list_skills(agent)
            if skills:
                skill_items = [
                    f"- **{s.name}** (`{s.filename}`, język: {s.skill_type}): {s.description}"
                    for s in skills
                ]
                parts.append(
                    "## Dostępne Umiejętności Skryptowe (katalog skills/)\n"
                    "Posiadasz dedykowane skrypty umiejętności w swoim katalogu workspace. "
                    "Możesz je wywoływać bezpośrednio za pomocą narzędzia `run_skill(skill_name='...', args={...})` "
                    "lub dedykowanych funkcji `skill_<nazwa>(...)`:\n"
                    + "\n".join(skill_items)
                )
        except Exception:
            pass

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
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute an agent turn with the SDK and yield streaming events
        (token, thought, tool_call, error, done).
        """

    async def cancel_turn(self, agent_slug: str) -> bool:
        """Cancel any running turn / process for the given agent."""
        return False
