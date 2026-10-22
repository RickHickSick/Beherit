"""Per-Agent SDK Authentication Session Manager (Auth Isolation)."""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional
from beherit.config import settings
from beherit.models.agent import AuthStatus


class AgentAuthManager:
    """Manages isolated SDK session credentials and tokens per individual agent."""

    def __init__(self, base_data_dir: Optional[Path] = None):
        self.base_dir = base_data_dir or settings.agents_dir

    def get_agent_auth_dir(self, agent_slug: str) -> Path:
        """Return the .auth directory path for the given agent, ensuring safe permissions."""
        auth_dir = self.base_dir / agent_slug / ".auth"
        if not auth_dir.exists():
            auth_dir.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(auth_dir, 0o700)
            except OSError:
                pass
        return auth_dir

    def get_session_file(self, agent_slug: str) -> Path:
        """Return path to session.json for the agent."""
        return self.get_agent_auth_dir(agent_slug) / "session.json"

    def is_authenticated(self, agent_slug: str) -> bool:
        """Check if an active session file exists for this agent."""
        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            return False
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return bool(data.get("authenticated", False))
        except (json.JSONDecodeError, OSError):
            return False

    def save_session(self, agent_slug: str, session_data: Dict[str, Any]) -> None:
        """Save session credentials securely with 0600 permissions."""
        session_file = self.get_session_file(agent_slug)
        session_payload = {**session_data, "authenticated": True}
        session_file.write_text(json.dumps(session_payload, indent=2), encoding="utf-8")
        try:
            os.chmod(session_file, 0o600)
        except OSError:
            pass

    def clear_session(self, agent_slug: str) -> None:
        """Remove session credentials for an agent."""
        session_file = self.get_session_file(agent_slug)
        if session_file.exists():
            session_file.unlink()

    def get_sdk_environment(self, agent_slug: str) -> Dict[str, str]:
        """
        Produce isolated environment variables for running the SDK sub-process
        or runner for this specific agent.
        """
        auth_dir = self.get_agent_auth_dir(agent_slug)
        env = os.environ.copy()
        # Direct SDK configuration paths to the agent's isolated auth folder
        env["BEHERIT_AGENT_AUTH_DIR"] = str(auth_dir)
        env["ANTIGRAVITY_CONFIG_DIR"] = str(auth_dir)
        env["CLAUDE_CONFIG_DIR"] = str(auth_dir)
        return env
