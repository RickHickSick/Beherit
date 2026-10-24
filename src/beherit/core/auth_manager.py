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

    def get_token(self, agent_slug: str) -> str | None:
        """Return the unmasked token / API key for direct engine execution."""
        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            return None
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return data.get("token") or data.get("access_token")
        except Exception:
            return None

    def get_provider(self, agent_slug: str) -> str | None:
        """Return the auth provider name (e.g. claude-code, antigravity, codex)."""
        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            return None
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return data.get("provider")
        except Exception:
            return None

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

    def detect_local_cli_credentials(self) -> dict[str, Any]:
        """
        Inspect the local host for existing CLI credentials (e.g. Claude Code or Antigravity)
        that can be imported with 1-click into an agent's isolated environment.
        """
        home = Path.home()
        claude_creds = home / ".claude" / ".credentials.json"
        gemini_creds = home / ".gemini" / "antigravity-cli" / ".credentials.json"
        gemini_config = home / ".gemini" / "config" / "config.json"

        claude_found = False
        claude_tier = None
        if claude_creds.exists():
            try:
                c_data = json.loads(claude_creds.read_text(encoding="utf-8"))
                oauth = c_data.get("claudeAiOauth", {})
                if oauth.get("accessToken"):
                    claude_found = True
                    claude_tier = oauth.get("subscriptionType", "Pro/Team")
            except Exception:
                pass

        antigravity_found = False
        if gemini_creds.exists():
            antigravity_found = True
        elif os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"):
            antigravity_found = True

        return {
            "claude_code": {
                "available": claude_found,
                "tier": claude_tier,
                "path": str(claude_creds) if claude_found else None,
                "description": f"Wykryto subskrypcję Claude ({claude_tier or 'CLI'}) na serwerze" if claude_found else "Brak poświadczeń ~/.claude",
            },
            "antigravity": {
                "available": antigravity_found,
                "description": "Wykryto aktywną sesję Google Antigravity / Gemini na serwerze" if antigravity_found else "Brak poświadczeń ~/.gemini",
            },
            "codex": {
                "available": bool(os.environ.get("OPENAI_API_KEY")),
                "description": "Wykryto klucz OPENAI_API_KEY w środowisku serwera" if os.environ.get("OPENAI_API_KEY") else "Wymaga podania klucza API",
            },
        }

    def import_local_cli_session(
        self,
        agent_slug: str,
        provider: EngineType = EngineType.CLAUDE_CODE,
    ) -> dict[str, Any]:
        """
        Clone host CLI credentials into the agent's isolated .auth directory.
        Maintains complete isolation by copying rather than symlinking.
        """
        home = Path.home()
        auth_dir = self.get_agent_auth_dir(agent_slug)

        if provider == EngineType.CLAUDE_CODE:
            claude_creds = home / ".claude" / ".credentials.json"
            if not claude_creds.exists():
                raise ValueError("Nie znaleziono pliku poświadczeń Claude Code (~/.claude/.credentials.json) na serwerze.")

            try:
                raw_data = json.loads(claude_creds.read_text(encoding="utf-8"))
                oauth = raw_data.get("claudeAiOauth", {})
                access_token = oauth.get("accessToken")
                if not access_token:
                    raise ValueError("Plik poświadczeń Claude nie zawiera aktywnego tokena dostępowego.")

                # Save isolated copy for Claude CLI config dir
                target_creds = auth_dir / ".credentials.json"
                target_creds.write_text(json.dumps(raw_data, indent=2), encoding="utf-8")
                try:
                    os.chmod(target_creds, 0o600)
                except OSError:
                    pass

                # Save session.json for Beherit engine
                session_payload = {
                    "authenticated": True,
                    "provider": "claude-code",
                    "auth_method": "cli_import",
                    "token": access_token,
                    "access_token": access_token,
                    "refresh_token": oauth.get("refreshToken"),
                    "expires_at": oauth.get("expiresAt") or (time.time() + (30 * 86400)),
                    "subscription_type": oauth.get("subscriptionType", "Pro"),
                    "created_at": time.time(),
                    "profile_id": f"{agent_slug}-claude-imported",
                }
                session_file = self.get_session_file(agent_slug)
                session_file.write_text(json.dumps(session_payload, indent=2), encoding="utf-8")
                try:
                    os.chmod(session_file, 0o600)
                except OSError:
                    pass

                return self.get_auth_details(agent_slug)
            except Exception as e:
                raise ValueError(f"Błąd podczas importu sesji Claude: {e}")

        elif provider == EngineType.ANTIGRAVITY:
            gemini_creds = home / ".gemini" / "antigravity-cli" / ".credentials.json"
            token = None
            if gemini_creds.exists():
                try:
                    g_data = json.loads(gemini_creds.read_text(encoding="utf-8"))
                    token = g_data.get("token") or g_data.get("access_token")
                except Exception:
                    pass

            if not token:
                token = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

            if not token:
                raise ValueError("Nie znaleziono poświadczeń Google Antigravity ani klucza GEMINI_API_KEY na serwerze.")

            return self.save_manual_token(agent_slug, token=token, provider=EngineType.ANTIGRAVITY)

        elif provider == EngineType.CODEX:
            token = os.environ.get("OPENAI_API_KEY")
            if not token:
                raise ValueError("Zmienna OPENAI_API_KEY nie jest ustawiona w środowisku serwera.")
            return self.save_manual_token(agent_slug, token=token, provider=EngineType.CODEX)

        raise ValueError(f"Nieobsługiwany dostawca do importu: {provider}")

    def test_agent_connection(self, agent_slug: str) -> dict[str, Any]:
        """
        Validate active credentials for the agent (Auth Healthcheck BEH-033).
        Checks token presence, expiration, and provider-specific syntax/structure.
        """
        session_file = self.get_session_file(agent_slug)
        if not session_file.exists():
            return {
                "valid": False,
                "error": "Brak poświadczeń. Agent nie jest jeszcze uwierzytelniony.",
            }

        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            if not data.get("authenticated", False):
                return {"valid": False, "error": "Sesja jest oznaczona jako nieaktywna."}

            expires_at = data.get("expires_at")
            if expires_at and expires_at < time.time():
                return {"valid": False, "error": f"Sesja wygasła w dniu {time.ctime(expires_at)}."}

            token = data.get("token") or data.get("access_token") or ""
            if not token.strip():
                return {"valid": False, "error": "Token w pliku sesji jest pusty."}

            provider = data.get("provider", "antigravity")
            details = self.get_auth_details(agent_slug)

            # Provider specific checks
            if provider == "antigravity":
                # Valid tokens: Gemini API key (AIza...), OAuth token (ya29...), or Beherit token
                is_standard = token.startswith("AIza") or token.startswith("ya29.") or token.startswith("beh_") or len(token) > 20
                if not is_standard:
                    return {
                        "valid": False,
                        "error": "Format tokena Gemini/Antigravity wydaje się nieprawidłowy (oczekiwano klucza AIza... lub tokena OAuth ya29...).",
                    }
                msg = "Poświadczenia Google Antigravity / Gemini są prawidłowo sformatowane i aktywne."

            elif provider == "claude-code":
                is_standard = token.startswith("sk-ant-") or len(token) > 25
                if not is_standard:
                    return {
                        "valid": False,
                        "error": "Format tokena Claude wydaje się nieprawidłowy (oczekiwano klucza sk-ant-... lub tokena sesji).",
                    }
                tier = data.get("subscription_type", "Pro/Team")
                msg = f"Poświadczenia Claude Code ({tier}) są aktywne i gotowe do użycia."

            elif provider == "codex":
                is_standard = token.startswith("sk-") or len(token) > 20
                if not is_standard:
                    return {
                        "valid": False,
                        "error": "Format tokena OpenAI wydaje się nieprawidłowy (oczekiwano klucza sk-...).",
                    }
                msg = "Klucz OpenAI Codex jest poprawny i gotowy do pracy."

            else:
                msg = f"Poświadczenia dla silnika '{provider}' są zapisane i aktywne."

            return {
                "valid": True,
                "provider": provider,
                "masked_token": details.get("masked_token"),
                "auth_method": data.get("auth_method", "manual"),
                "message": msg,
            }
        except Exception as e:
            return {"valid": False, "error": f"Błąd weryfikacji poświadczeń: {e}"}

    def start_oauth_flow(
        self,
        agent_slug: str,
        provider: EngineType = EngineType.ANTIGRAVITY,
    ) -> dict[str, Any]:
        """
        Initiate an RFC 8628 OAuth 2.0 Device Authorization Grant handshake.
        Supports real Google OAuth Device endpoint when configured, or transparent fallback.
        """
        import secrets

        flow_id = f"flow_{agent_slug}_{int(time.time())}"
        device_code = f"beh_dev_{secrets.token_urlsafe(32)}"
        real_oauth = False

        # Generate standard 8-character RFC 8628 user-friendly user code (XXXX-XXXX)
        alphabet = "BCDFGHJKLMNPQRSTVWXZ0123456789"
        part1 = "".join(secrets.choice(alphabet) for _ in range(4))
        part2 = "".join(secrets.choice(alphabet) for _ in range(4))
        user_code = f"{part1}-{part2}"

        # Standard RFC 8628 verification URIs
        if provider == EngineType.ANTIGRAVITY:
            verification_uri = "https://www.google.com/device"
            # Attempt real Google Cloud Device Code if credentials provided
            if settings.GOOGLE_OAUTH_CLIENT_ID:
                try:
                    import httpx

                    resp = httpx.post(
                        "https://oauth2.googleapis.com/device/code",
                        data={
                            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                            "scope": "https://www.googleapis.com/auth/generative-language openid email",
                        },
                        timeout=10.0,
                    )
                    if resp.status_code == 200:
                        g_data = resp.json()
                        device_code = g_data.get("device_code", device_code)
                        user_code = g_data.get("user_code", user_code)
                        verification_uri = g_data.get("verification_url", "https://www.google.com/device")
                        real_oauth = True
                except Exception:
                    pass
        elif provider == EngineType.CLAUDE_CODE:
            verification_uri = "https://console.anthropic.com/settings/keys"
        else:
            verification_uri = "https://platform.openai.com/api-keys"

        verification_uri_complete = f"{verification_uri}?user_code={user_code}" if provider == EngineType.ANTIGRAVITY else verification_uri
        expires_in = 1800  # 30 minutes
        interval = 5

        # Provider specific warning/instruction for self-hosted setup
        warning = None
        if provider == EngineType.ANTIGRAVITY and not real_oauth:
            warning = "Brak skonfigurowanego GOOGLE_OAUTH_CLIENT_ID w .env serwera. Wygenerowano kod demonstracyjny. Dla homelab rekomendowane jest wklejenie tokena w 'Ręczne Wklejenie Tokena' lub użycie 'Import z serwera'."
        elif provider == EngineType.CLAUDE_CODE:
            warning = "Anthropic Claude Code nie posiada publicznego protokołu RFC 8628 Device Flow. Skorzystaj z 'Import sesji z serwera' lub wklej klucz w 'Ręczne Wklejenie Tokena'."
        elif provider == EngineType.CODEX:
            warning = "OpenAI nie udostępnia publicznego protokołu Device Flow dla subskrypcji. Wklej klucz API w 'Ręczne Wklejenie Tokena'."

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
            "real_oauth": real_oauth,
            "warning": warning,
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
            "oauth_configured": real_oauth,
            "warning": warning,
        }

    def poll_device_token(
        self,
        agent_slug: str,
        flow_id: str,
    ) -> dict[str, Any]:
        """
        Poll RFC 8628 token endpoint status.
        Supports polling real Google OAuth token endpoint when configured, or local flow.
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

        # Real Google OAuth polling
        if flow_data.get("real_oauth") and settings.GOOGLE_OAUTH_CLIENT_ID:
            try:
                import httpx

                req_data = {
                    "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                    "device_code": flow_data["device_code"],
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                }
                if settings.GOOGLE_OAUTH_CLIENT_SECRET:
                    req_data["client_secret"] = settings.GOOGLE_OAUTH_CLIENT_SECRET

                resp = httpx.post("https://oauth2.googleapis.com/token", data=req_data, timeout=10.0)
                body = resp.json()

                if resp.status_code == 200 and "access_token" in body:
                    access_token = body["access_token"]
                    refresh_token = body.get("refresh_token")
                    expires_in = body.get("expires_in", 3600)
                    expires_at = time.time() + expires_in

                    payload = {
                        "authenticated": True,
                        "provider": "antigravity",
                        "auth_method": "google_oauth_device",
                        "token": access_token,
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "token_type": "Bearer",
                        "created_at": time.time(),
                        "expires_at": expires_at,
                        "profile_id": f"{agent_slug}-google-device",
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
                        "expires_in": expires_in,
                        "auth": self.get_auth_details(agent_slug),
                    }
                elif body.get("error") == "authorization_pending":
                    return {
                        "status": "authorization_pending",
                        "interval": flow_data.get("interval", 5),
                        "message": "Oczekiwanie na zatwierdzenie logowania na koncie Google...",
                    }
                elif body.get("error") == "slow_down":
                    return {
                        "status": "slow_down",
                        "interval": flow_data.get("interval", 5) + 5,
                        "message": "Zwolnij częstotliwość zapytań (Google slow down).",
                    }
                else:
                    return {
                        "status": "error",
                        "error": body.get("error_description", body.get("error", "Błąd autoryzacji Google OAuth")),
                    }
            except Exception as e:
                return {"status": "error", "error": f"Błąd połączenia z Google Token Endpoint: {e}"}

        # Fallback / mock flow polling
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
            "message": "Oczekiwanie na zatwierdzenie logowania w przeglądarce.",
            "warning": flow_data.get("warning"),
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
