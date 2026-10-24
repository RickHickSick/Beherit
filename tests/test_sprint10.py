"""Tests for Sprint 10: Multi-Provider SDK Auth, Real Google OAuth Device Flow, Claude Import, and Auth Healthcheck (BEH-030 to BEH-034)."""

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from beherit.core.auth_manager import AgentAuthManager
from beherit.main import app
from beherit.models.agent import AuthStatus, EngineType


def test_beh030_multi_provider_flow_metadata(tmp_path: Path):
    """Test BEH-030: Multi-Provider flow initiation produces correct provider metadata."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "multi-agent"

    # 1. Antigravity flow
    flow_ag = mgr.start_oauth_flow(slug, provider=EngineType.ANTIGRAVITY)
    assert flow_ag["provider"] == EngineType.ANTIGRAVITY
    assert "user_code" in flow_ag
    assert "google.com" in flow_ag["verification_uri"]
    assert "oauth_configured" in flow_ag

    # 2. Claude Code flow
    flow_cl = mgr.start_oauth_flow(slug, provider=EngineType.CLAUDE_CODE)
    assert flow_cl["provider"] == EngineType.CLAUDE_CODE
    assert "anthropic.com" in flow_cl["verification_uri"]
    assert flow_cl["oauth_configured"] is False
    assert "Device Flow" in flow_cl["warning"]

    # 3. OpenAI Codex flow
    flow_cx = mgr.start_oauth_flow(slug, provider=EngineType.CODEX)
    assert flow_cx["provider"] == EngineType.CODEX
    assert "openai.com" in flow_cx["verification_uri"]
    assert flow_cx["oauth_configured"] is False


def test_beh032_local_cli_import(tmp_path: Path, monkeypatch):
    """Test BEH-032: Importing host credentials into isolated agent environment."""
    fake_home = tmp_path / "fake_home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    fake_creds_file = fake_claude / ".credentials.json"
    fake_creds_file.write_text(
        json.dumps({
            "claudeAiOauth": {
                "accessToken": "sk-ant-oauth-test-token-1234567890",
                "refreshToken": "ref-claude-test-987654321",
                "subscriptionType": "Pro",
                "expiresAt": time.time() + 3600,
            }
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    agents_dir = tmp_path / "agents"
    mgr = AgentAuthManager(base_data_dir=agents_dir)
    slug = "imported-agent"

    # Detect
    detected = mgr.detect_local_cli_credentials()
    assert detected["claude_code"]["available"] is True
    assert detected["claude_code"]["tier"] == "Pro"

    # Import
    imported = mgr.import_local_cli_session(slug, provider=EngineType.CLAUDE_CODE)
    assert imported["status"] == AuthStatus.AUTHENTICATED
    assert imported["auth_method"] == "cli_import"
    assert mgr.is_authenticated(slug)

    # Check isolated .credentials.json in agent's auth folder
    isolated_creds = mgr.get_agent_auth_dir(slug) / ".credentials.json"
    assert isolated_creds.exists()
    assert "sk-ant-oauth" in isolated_creds.read_text(encoding="utf-8")


def test_beh033_auth_healthcheck_connection(tmp_path: Path):
    """Test BEH-033: Validating and testing connection status (Auth Healthcheck)."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "healthcheck-agent"

    # Unauthenticated
    unauth = mgr.test_agent_connection(slug)
    assert unauth["valid"] is False
    assert "nie jest jeszcze uwierzytelniony" in unauth["error"]

    # Valid Gemini key
    mgr.save_manual_token(slug, token="AIzaSyDummyGeminiKey1234567890abcdef", provider=EngineType.ANTIGRAVITY)
    valid_ag = mgr.test_agent_connection(slug)
    assert valid_ag["valid"] is True
    assert "prawidłowo sformatowane" in valid_ag["message"]

    # Invalid token format for Claude
    mgr.save_manual_token(slug, token="short", provider=EngineType.CLAUDE_CODE)
    invalid_cl = mgr.test_agent_connection(slug)
    assert invalid_cl["valid"] is False
    assert "nieprawidłowy" in invalid_cl["error"]

    # Valid Claude key
    mgr.save_manual_token(slug, token="sk-ant-api03-valid-test-key-123456789012345", provider=EngineType.CLAUDE_CODE)
    valid_cl = mgr.test_agent_connection(slug)
    assert valid_cl["valid"] is True


def test_api_routes_sprint10(tmp_path: Path, monkeypatch):
    """Test API endpoints for Sprint 10: local-detect, import-local, and auth test."""
    agents_dir = tmp_path / "agents"
    fake_home = tmp_path / "fake_home"
    fake_claude = fake_home / ".claude"
    fake_claude.mkdir(parents=True)
    (fake_claude / ".credentials.json").write_text(
        json.dumps({
            "claudeAiOauth": {
                "accessToken": "sk-ant-oauth-token-for-api-test-123456",
                "subscriptionType": "Team",
            }
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    from beherit.api import routes
    routes.repo.base_dir = agents_dir
    routes.auth_mgr = AgentAuthManager(base_data_dir=agents_dir)

    client = TestClient(app)

    # 1. Create agent
    slug = "s10-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "Sprint 10 Agent",
            "slug": slug,
            "engine": "claude-code",
            "model": "claude-3-7-sonnet",
        },
    )
    assert create_resp.status_code in (201, 409)

    # 2. GET /local-detect
    detect_resp = client.get(f"/api/agents/{slug}/auth/local-detect")
    assert detect_resp.status_code == 200
    assert detect_resp.json()["claude_code"]["available"] is True

    # 3. POST /import-local
    import_resp = client.post(
        f"/api/agents/{slug}/auth/import-local",
        json={"provider": "claude-code"},
    )
    assert import_resp.status_code == 200
    assert import_resp.json()["status"] == "success"

    # 4. POST /auth/test
    test_resp = client.post(f"/api/agents/{slug}/auth/test")
    assert test_resp.status_code == 200
    data = test_resp.json()
    assert data["valid"] is True
    assert "Claude Code" in data["message"]
