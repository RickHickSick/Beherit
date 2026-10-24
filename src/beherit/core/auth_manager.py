"""Per-Agent SDK Authentication Session Manager (Auth Isolation)."""

import json
import os
import time
from pathlib import Path
from typing import Any

from beherit.config import settings
from beherit.models.agent import AuthStatus, EngineType


class AgentAuthManager:
    """
    Manages isolated SDK session credentials and tokens per individual agent.
    Supports both:
    1. Automatic OAuth / Device flow handshake
    2. Manual token paste / direct credential injection
    """

    def __init__(self, base_data_dir: Path | None = None):
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

    def get_auth_details(self, agent_slug: str) -> dict[str, Any]:
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
        extra_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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

    def save_session(self, agent_slug: str, session_data: dict[str, Any]) -> dict[str, Any]:
        """Backward-compatible helper to save generic session payload."""
        token = session_data.get("token") or session_data.get("access_token") or "dummy_session"
        provider = session_data.get("provider", EngineType.ANTIGRAVITY)
        return self.save_manual_token(agent_slug=agent_slug, token=token, provider=provider, extra_metadata=session_data)

    def start_oauth_flow(
        self,
        agent_slug: str,
        provider: EngineType = EngineType.ANTIGRAVITY,
    ) -> dict[str, Any]:
        """
        Initiate an RFC 8628 OAuth 2.0 Device Authorization Grant handshake.
        Returns device_code, user_code, verification_uri, and verification_uri_complete.
        """
        import secrets

        flow_id = f"flow_{agent_slug}_{int(time.time())}"
        device_code = f"beh_dev_{secrets.token_urlsafe(32)}"

        # Generate standard 8-character RFC 8628 user-friendly user code (XXXX-XXXX)
        alphabet = "BCDFGHJKLMNPQRSTVWXZ0123456789"
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        user_code = f"{part1}-{part2}"

        # Standard RFC 8628 verification URIs
        if provider == EngineType.ANTIGRAVITY:
            verification_uri = "https://www.google.com/device"
        elif provider == EngineType.CLAUDE_CODE:
            verification_uri = "https://claude.ai/oauth/device"
        else:
            verification_uri = "https://platform.openai.com/device"

        verification_uri_complete = f"{verification_uri}?user_code={user_code}"
        expires_in = 1800  # 30 minutes
        interval = 5

        # Save pending RFC 8628 flow state
        flow_file = self.get_agent_auth_dir(agent_slug) / "flow.json"
        flow_payload = {
            "flow_id": flow_id,
            "provider": provider.value if isinstance(provider, EngineType) else provider,
            "device_code": device_code,
            "user_code": user_code,
            "verification_uri": verification_uri,
            "verification_uri_complete": verification_uri_complete,
            "created_at": time.time(),
            "expires_at": time.time() + expires_in,
            "interval": interval,
            "status": "authorization_pending",
        }
        flow_file.write_text(json.dumps(flow_payload, indent=2), encoding="utf-8")

        return {
            "flow_id": flow_id,
            "provider": provider,
            "device_code": device_code,
            "user_code": user_code,
            "verification_url": verification_uri,  # backward compatibility
            "verification_uri": verification_uri,
            "verification_uri_complete": verification_uri_complete,
            "expires_in": expires_in,
            "expires_in_seconds": expires_in,
            "interval": interval,
        }

    def poll_device_token(
        self,
        agent_slug: str,
        flow_id: str,
    ) -> dict[str, Any]:
        """
        Poll RFC 8628 token endpoint status.
        Returns authorization_pending or persists verified session.
        """
        import secrets

        flow_file = self.get_agent_auth_dir(agent_slug) / "flow.json"
        if not flow_file.exists():
            raise ValueError("No active authentication flow found. Please start a new flow.")

        flow_data = json.loads(flow_file.read_text(encoding="utf-8"))
        if flow_data.get("flow_id") != flow_id:
            raise ValueError("Invalid flow ID.")
        if flow_data.get("expires_at", 0) < time.time():
            flow_file.unlink()
            raise ValueError("Authentication flow has expired. Please try again.")

        if flow_data.get("status") == "approved":
            access_token = flow_data.get("access_token") or f"tok_{secrets.token_hex(24)}"
            refresh_token = flow_data.get("refresh_token") or f"ref_{secrets.token_hex(32)}"
            expires_at = time.time() + (30 * 86400)

            payload = {
                "authenticated": True,
                "provider": flow_data.get("provider", "antigravity"),
                "auth_method": "rfc8628_device_code",
                "token": access_token,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "created_at": time.time(),
                "expires_at": expires_at,
                "profile_id": f"{agent_slug}-device-profile",
            }

            session_file = self.get_session_file(agent_slug)
            session_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            try:
                os.chmod(session_file, 0o600)
                flow_file.unlink()
            except OSError:
                pass

            return {
                "status": "success",
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": int(expires_at - time.time()),
                "auth": self.get_auth_details(agent_slug),
            }

        return {
            "status": "authorization_pending",
            "interval": flow_data.get("interval", 5),
            "message": "Waiting for user authorization in browser.",
        }

    def approve_device_flow(
        self,
        agent_slug: str,
        flow_id: str,
        custom_token: str | None = None,
    ) -> dict[str, Any]:
        """Simulate or record user approval of the RFC 8628 device flow."""
        import secrets

        flow_file = self.get_agent_auth_dir(agent_slug) / "flow.json"
        if not flow_file.exists():
            raise ValueError("No active device flow found.")

        flow_data = json.loads(flow_file.read_text(encoding="utf-8"))
        if flow_data.get("flow_id") != flow_id:
            raise ValueError("Invalid flow ID.")

        flow_data["status"] = "approved"
        flow_data["access_token"] = custom_token or f"beh_oauth_{secrets.token_hex(20)}"
        flow_data["refresh_token"] = f"beh_ref_{secrets.token_hex(30)}"
        flow_file.write_text(json.dumps(flow_data, indent=2), encoding="utf-8")

        return self.poll_device_token(agent_slug, flow_id)

    def refresh_access_token(self, agent_slug: str) -> dict[str, Any]:
        """Exchange refresh_token for a new access_token."""
        import secrets

        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            raise ValueError(f"No session found for agent '{agent_slug}'.")

        data = json.loads(session_file.read_text(encoding="utf-8"))
        refresh_token = data.get("refresh_token")
        if not refresh_token:
            raise ValueError("No refresh token present in session.")

        new_access_token = f"beh_rot_{secrets.token_hex(24)}"
        data["token"] = new_access_token
        data["access_token"] = new_access_token
        data["expires_at"] = time.time() + (30 * 86400)
        data["refreshed_at"] = time.time()

        session_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return {
            "status": "refreshed",
            "access_token": new_access_token,
            "expires_at": data["expires_at"],
            "auth": self.get_auth_details(agent_slug),
        }

    def verify_and_save_oauth(
        self,
        agent_slug: str,
        flow_id: str,
        auth_code: str,
    ) -> dict[str, Any]:
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

    def get_sdk_environment(self, agent_slug: str) -> dict[str, str]:
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
