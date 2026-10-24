"""Markdown-First Agent Repository: Parser, Serializer, and Storage with Error Recovery."""

from pathlib import Path

import frontmatter
from pydantic import ValidationError

from beherit.config import settings
from beherit.core.auth_manager import AgentAuthManager
from beherit.models.agent import AgentAuthConfig, AgentModel


class AgentRepository:
    """
    Handles bidirectional serialization between Markdown files and Python domain models.
    Tolerates syntax errors in manually edited files (in Obsidian/VS Code) without crashing.
    """

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or settings.agents_dir).resolve()
        self.auth_manager = AgentAuthManager(self.base_dir)

    def list_agents(self) -> list[AgentModel]:
        """Discover and load all valid or degraded agents found in the storage directory."""
        if not self.base_dir.exists():
            return []

        agents = []
        for agent_dir in sorted(self.base_dir.iterdir()):
            if agent_dir.is_dir() and (agent_dir / "agent.md").exists():
                agent = self.load_agent(agent_dir.name)
                if agent:
                    agents.append(agent)
        return agents

    def load_agent(self, slug: str) -> AgentModel | None:
        """Load an agent by its directory slug with fault-tolerant error recovery."""
        agent_dir = self.base_dir / slug
        agent_file = agent_dir / "agent.md"
        instructions_file = agent_dir / "instructions.md"

        if not agent_file.exists():
            return None

        # Fetch active auth details from .auth/
        auth_details = self.auth_manager.get_auth_details(slug)

        try:
            post = frontmatter.load(str(agent_file))
            metadata = dict(post.metadata)

            # Ensure slug matches directory name
            metadata["slug"] = slug
            if "name" not in metadata:
                metadata["name"] = slug.replace("-", " ").title()

            # Merge frontmatter auth metadata with live .auth/ storage state
            auth_meta = metadata.get("auth") or {}
            auth_meta["status"] = auth_details["status"]
            auth_meta["auth_method"] = auth_details.get("auth_method")
            auth_meta["masked_token"] = auth_details.get("masked_token")
            auth_meta["expires_at"] = auth_details.get("expires_at")
            metadata["auth"] = auth_meta

            metadata["master_prompt"] = post.content.strip()

            # Load instructions.md if present
            if instructions_file.exists():
                metadata["instructions"] = instructions_file.read_text(encoding="utf-8").strip()

            metadata["file_path"] = str(agent_file.resolve())
            metadata["is_valid"] = True
            metadata["validation_error"] = None

            return AgentModel(**metadata)

        except ValidationError as e:
            # Fallback degraded agent representation to inform UI without crashing
            raw_text = agent_file.read_text(encoding="utf-8", errors="replace")
            return AgentModel(
                name=f"[Invalid YAML] {slug}",
                slug=slug,
                description="This agent file has validation errors in frontmatter.",
                master_prompt=raw_text,
                file_path=str(agent_file.resolve()),
                is_valid=False,
                validation_error=str(e),
                auth=AgentAuthConfig(status=auth_details["status"]),
            )
        except Exception as e:
            raw_text = agent_file.read_text(encoding="utf-8", errors="replace")
            return AgentModel(
                name=f"[Syntax Error] {slug}",
                slug=slug,
                description="This agent file has syntax errors.",
                master_prompt=raw_text,
                file_path=str(agent_file.resolve()),
                is_valid=False,
                validation_error=str(e),
                auth=AgentAuthConfig(status=auth_details["status"]),
            )

    def save_agent(self, agent: AgentModel, target_dir: Path | None = None) -> Path:
        """
        Serialize AgentModel into agent.md and instructions.md.
        Allows specifying an optional target directory (e.g. custom user vault).
        """
        dest_base = target_dir or self.base_dir
        agent_dir = dest_base / agent.slug
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "knowledge").mkdir(exist_ok=True)
        (agent_dir / "skills").mkdir(exist_ok=True)

        # Prepare frontmatter metadata (exclude runtime-only attributes)
        metadata = agent.model_dump(
            mode="json",
            exclude={"master_prompt", "instructions", "file_path", "is_valid", "validation_error"},
        )

        post = frontmatter.Post(content=agent.master_prompt, **metadata)
        agent_file = agent_dir / "agent.md"
        agent_file.write_text(frontmatter.dumps(post), encoding="utf-8")

        # Save instructions.md if provided
        if agent.instructions is not None:
            instructions_file = agent_dir / "instructions.md"
            instructions_file.write_text(agent.instructions, encoding="utf-8")

        # Sync mcp.json for CLI engines: include user-defined mcp_servers + built-in beherit-sandbox
        import json
        import sys

        mcp_servers_dict = {}
        if agent.mcp_servers:
            for name, server in agent.mcp_servers.items():
                mcp_servers_dict[name] = server.model_dump(mode="json")

        # Automatically inject Beherit CapabilitiesGuard MCP server
        mcp_servers_dict["beherit-sandbox"] = {
            "command": sys.executable,
            "args": ["-m", "beherit.cli", "mcp", "--agent-slug", agent.slug],
            "env": {
                "BEHERIT_AGENT_SLUG": agent.slug
            }
        }

        mcp_data = {"mcpServers": mcp_servers_dict}
        (agent_dir / "mcp.json").write_text(json.dumps(mcp_data, indent=2), encoding="utf-8")

        return agent_dir

    def delete_agent(self, slug: str) -> bool:
        """Delete an agent directory."""
        import shutil

        agent_dir = self.base_dir / slug
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
            return True
        return False
