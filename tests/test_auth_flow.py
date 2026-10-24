"""Tests for Per-Agent SDK Authentication Isolation (Manual & OAuth flows)."""

from pathlib import Path

from beherit.core.auth_manager import AgentAuthManager
from beherit.models.agent import AuthStatus, EngineType


def test_manual_token_injection(tmp_path: Path):
    """Verify saving, masking, and loading manually pasted SDK tokens."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "analyst-agent"

    # Initially unauthenticated
    details = mgr.get_auth_details(slug)
    assert details["status"] == AuthStatus.UNAUTHENTICATED
    assert not details["authenticated"]

    # Paste manual token
    token_str = "ya29.a0AfH6SMB_secret_antigravity_token_123456789"
    saved = mgr.save_manual_token(slug, token=token_str, provider=EngineType.ANTIGRAVITY)

    assert saved["status"] == AuthStatus.AUTHENTICATED
    assert saved["authenticated"] is True
    assert saved["auth_method"] == "manual"
    assert saved["masked_token"].startswith("ya29.a...")
    assert token_str not in saved["masked_token"]  # Secret is properly masked

    # Check environment variables generated for subprocess
    env = mgr.get_sdk_environment(slug)
    assert env["BEHERIT_AGENT_SLUG"] == slug
    assert env["ANTIGRAVITY_SESSION_TOKEN"] == token_str
    assert str(tmp_path / slug / ".auth") == env["BEHERIT_AGENT_AUTH_DIR"]


def test_oauth_automated_flow(tmp_path: Path):
    """Verify OAuth start and verify handshake."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "coder-agent"

    # Start flow
    flow = mgr.start_oauth_flow(slug, provider=EngineType.ANTIGRAVITY)
    assert "flow_id" in flow
    assert "user_code" in flow
    assert "verification_url" in flow

    # Complete flow with auth code
    auth_code = "oauth_code_xyz987"
    completed = mgr.verify_and_save_oauth(slug, flow_id=flow["flow_id"], auth_code=auth_code)

    assert completed["status"] == AuthStatus.AUTHENTICATED
    assert completed["auth_method"] == "oauth_automated"


def test_multi_agent_isolation(tmp_path: Path):
    """Verify that multiple agents have completely independent, non-overlapping auth contexts."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    agent_a = "agent-alpha"
    agent_b = "agent-beta"

    mgr.save_manual_token(agent_a, token="token_alpha_111", provider=EngineType.ANTIGRAVITY)
    mgr.save_manual_token(agent_b, token="token_beta_222", provider=EngineType.CLAUDE_CODE)

    env_a = mgr.get_sdk_environment(agent_a)
    env_b = mgr.get_sdk_environment(agent_b)

    assert env_a["ANTIGRAVITY_SESSION_TOKEN"] == "token_alpha_111"
    assert env_b["ANTIGRAVITY_SESSION_TOKEN"] == "token_beta_222"
    assert env_a["BEHERIT_AGENT_AUTH_DIR"] != env_b["BEHERIT_AGENT_AUTH_DIR"]

    # Disconnecting agent A must not affect agent B
    mgr.clear_session(agent_a)
    assert not mgr.is_authenticated(agent_a)
    assert mgr.is_authenticated(agent_b)
