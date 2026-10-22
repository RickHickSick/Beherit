"""Markdown-First Agent Repository: Parser, Serializer, and Storage."""

from pathlib import Path
from typing import List, Optional
import frontmatter
from beherit.config import settings
from beherit.core.auth_manager import AgentAuthManager
from beherit.models.agent import AgentModel, AuthStatus


class AgentRepository:
    """Handles bidirectional serialization between Markdown files and Python domain models."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or settings.agents_dir
        self.auth_manager = AgentAuthManager(self.base_dir)

    def list_agents(self) -> List[AgentModel]:
        """Discover and load all valid agents found in the storage directory."""
        if not self.base_dir.exists():
            return []

        agents = []
        for agent_dir in sorted(self.base_dir.iterdir()):
            if agent_dir.is_dir() and (agent_dir / "agent.md").exists():
                try:
                    agent = self.load_agent(agent_dir.name)
                    if agent:
                        agents.append(agent)
                except Exception:
                    continue
        return agents

    def load_agent(self, slug: str) -> Optional[AgentModel]:
        """Load an agent by its directory slug."""
        agent_dir = self.base_dir / slug
        agent_file = agent_dir / "agent.md"
        instructions_file = agent_dir / "instructions.md"

        if not agent_file.exists():
            return None

        post = frontmatter.load(str(agent_file))
        metadata = dict(post.metadata)

        # Sync runtime auth status with session file presence
        is_authed = self.auth_manager.is_authenticated(slug)
        if "auth" not in metadata:
            metadata["auth"] = {}
        if is_authed:
            metadata["auth"]["status"] = AuthStatus.AUTHENTICATED
        elif metadata["auth"].get("status") == AuthStatus.AUTHENTICATED and not is_authed:
            metadata["auth"]["status"] = AuthStatus.UNAUTHENTICATED

        metadata["master_prompt"] = post.content.strip()

        # Load optional instructions.md
        if instructions_file.exists():
            metadata["instructions"] = instructions_file.read_text(encoding="utf-8").strip()

        return AgentModel(**metadata)

    def save_agent(self, agent: AgentModel) -> Path:
        """Serialize AgentModel into agent.md and instructions.md."""
        agent_dir = self.base_dir / agent.slug
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "knowledge").mkdir(exist_ok=True)
        (agent_dir / "skills").mkdir(exist_ok=True)

        # Prepare frontmatter metadata
        metadata = agent.model_dump(
            mode="json",
            exclude={"master_prompt", "instructions"},
        )

        post = frontmatter.Post(content=agent.master_prompt, **metadata)
        agent_file = agent_dir / "agent.md"
        agent_file.write_text(frontmatter.dumps(post), encoding="utf-8")

        # Save instructions.md if provided
        if agent.instructions is not None:
            instructions_file = agent_dir / "instructions.md"
            instructions_file.write_text(agent.instructions, encoding="utf-8")

        return agent_dir

    def delete_agent(self, slug: str) -> bool:
        """Delete an agent directory."""
        import shutil

        agent_dir = self.base_dir / slug
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
            return True
        return False
