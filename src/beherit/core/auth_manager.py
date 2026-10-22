"""Per-Agent SDK Authentication Session Manager (Auth Isolation)."""

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from beherit.config import settings
from beherit.models.agent import AuthStatus, EngineType


class AgentAuthManager:
    """
    Manages isolated SDK session credentials and tokens per individual agent.
    Supports both:
    1. Automatic OAuth / Device flow handshake
    2. Manual token paste / direct credential injection
    """

    def __init__(self, base_data_dir: Optional[Path] = None):
        self.base_dir = (base_data_dir or settings.agents_dir).resolve()

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
            if not data.get("authenticated", False):
                return False
            # Check expiration if present
            expires_at = data.get("expires_at")
            if expires_at and expires_at < time.time():
                return False
            return True
        except (json.JSONDecodeError, OSError):
            return False

    def get_auth_details(self, agent_slug: str) -> Dict[str, Any]:
        """Return non-sensitive authentication status and metadata."""
        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            return {
                "status": AuthStatus.UNAUTHENTICATED,
                "authenticated": False,
                "provider": None,
                "masked_token": None,
                "expires_at": None,
            }

        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            token = data.get("token") or data.get("access_token") or ""
            masked = f"{token[:6]}...{token[-4:]}" if len(token) > 10 else "***"
            is_valid = self.is_authenticated(agent_slug)

            return {
                "status": AuthStatus.AUTHENTICATED if is_valid else AuthStatus.EXPIRED,
                "authenticated": is_valid,
                "provider": data.get("provider"),
                "auth_method": data.get("auth_method", "manual"),
                "masked_token": masked,
                "expires_at": data.get("expires_at"),
                "profile_id": data.get("profile_id", f"{agent_slug}-profile"),
            }
        except Exception:
            return {
                "status": AuthStatus.UNAUTHENTICATED,
                "authenticated": False,
                "error": "Corrupted session file",
            }

    def save_manual_token(
        self,
        agent_slug: str,
        token: str,
        provider: EngineType = EngineType.ANTIGRAVITY,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Save manually pasted token/credentials securely into the agent's .auth directory."""
        clean_token = token.strip()
        if not clean_token:
            raise ValueError("Token cannot be empty")

        payload = {
            "authenticated": True,
            "provider": provider.value if isinstance(provider, EngineType) else provider,
            "auth_method": "manual",
            "token": clean_token,
            "created_at": time.time(),
            "profile_id": f"{agent_slug}-manual-profile",
            **(extra_metadata or {}),
        }

        session_file = self.get_session_file(agent_slug)
        session_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(session_file, 0o600)
        except OSError:
            pass

        return self.get_auth_details(agent_slug)

    def save_session(self, agent_slug: str, session_data: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compatible helper to save generic session payload."""
        token = session_data.get("token") or session_data.get("access_token") or "dummy_session"
        provider = session_data.get("provider", EngineType.ANTIGRAVITY)
        return self.save_manual_token(agent_slug=agent_slug, token=token, provider=provider, extra_metadata=session_data)

    def start_oauth_flow(
        self,
        agent_slug: str,
        provider: EngineType = EngineType.ANTIGRAVITY,
    ) -> Dict[str, Any]:
        """
        Initiate an automated OAuth / Device Code handshake.
        Returns a verification URL and user code to display in the UI.
        """
        flow_id = f"flow_{agent_slug}_{int(time.time())}"
        user_code = f"BEH-{flow_id[-6:].upper()}"

        # Standard simulated / actual provider authorization endpoint
        if provider == EngineType.ANTIGRAVITY:
            verification_url = "https://accounts.google.com/o/oauth2/auth"
        elif provider == EngineType.CLAUDE_CODE:
            verification_url = "https://claude.ai/oauth/authorize"
        else:
            verification_url = "https://platform.openai.com/oauth"

        # Save pending flow state
        flow_file = self.get_agent_auth_dir(agent_slug) / "flow.json"
        flow_payload = {
            "flow_id": flow_id,
            "provider": provider.value if isinstance(provider, EngineType) else provider,
            "user_code": user_code,
            "verification_url": verification_url,
            "created_at": time.time(),
            "expires_at": time.time() + 600,  # 10 minutes
        }
        flow_file.write_text(json.dumps(flow_payload, indent=2), encoding="utf-8")

        return {
            "flow_id": flow_id,
            "provider": provider,
            "user_code": user_code,
            "verification_url": verification_url,
            "expires_in_seconds": 600,
        }

    def verify_and_save_oauth(
        self,
        agent_slug: str,
        flow_id: str,
        auth_code: str,
    ) -> Dict[str, Any]:
        """Verify the user code / auth code and persist the session."""
        flow_file = self.get_agent_auth_dir(agent_slug) / "flow.json"
        if not flow_file.exists():
            raise ValueError("No active authentication flow found. Please start a new flow.")

        flow_data = json.loads(flow_file.read_text(encoding="utf-8"))
        if flow_data.get("flow_id") != flow_id:
            raise ValueError("Invalid flow ID.")
        if flow_data.get("expires_at", 0) < time.time():
            flow_file.unlink()
            raise ValueError("Authentication flow has expired. Please try again.")

        payload = {
            "authenticated": True,
            "provider": flow_data.get("provider", "antigravity"),
            "auth_method": "oauth_automated",
            "token": auth_code.strip(),
            "created_at": time.time(),
            "expires_at": time.time() + (30 * 86400),  # 30 days session
            "profile_id": f"{agent_slug}-oauth-profile",
        }

        session_file = self.get_session_file(agent_slug)
        session_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.chmod(session_file, 0o600)
            flow_file.unlink()
        except OSError:
            pass

        return self.get_auth_details(agent_slug)

    def clear_session(self, agent_slug: str) -> bool:
        """Remove session credentials and pending flows for an agent."""
        auth_dir = self.get_agent_auth_dir(agent_slug)
        session_file = auth_dir / "session.json"
        flow_file = auth_dir / "flow.json"

        removed = False
        if session_file.exists():
            session_file.unlink()
            removed = True
        if flow_file.exists():
            flow_file.unlink()
        return removed

    def get_sdk_environment(self, agent_slug: str) -> Dict[str, str]:
        """
        Produce isolated environment variables for running the SDK sub-process
        or runner for this specific agent.
        """
        auth_dir = self.get_agent_auth_dir(agent_slug)
        session_file = self.get_session_file(agent_slug)

        env = os.environ.copy()
        env["BEHERIT_AGENT_SLUG"] = agent_slug
        env["BEHERIT_AGENT_AUTH_DIR"] = str(auth_dir)
        env["ANTIGRAVITY_CONFIG_DIR"] = str(auth_dir)
        env["CLAUDE_CONFIG_DIR"] = str(auth_dir)

        if session_file.exists():
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                token = data.get("token") or ""
                env["BEHERIT_SDK_TOKEN"] = token
                # Inject token to provider-specific environment variables for the sub-process
                env["ANTIGRAVITY_SESSION_TOKEN"] = token
                env["CLAUDE_SESSION_TOKEN"] = token
            except Exception:
                pass

        return env
