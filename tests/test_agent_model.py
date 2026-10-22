"""Test Agent Model, Repository and Auth Isolation."""

import shutil
from pathlib import Path
from beherit.core.auth_manager import AgentAuthManager
from beherit.core.repository import AgentRepository
from beherit.models.agent import AgentModel, EngineType, AuthStatus


def test_template_agent_loading():
    """Verify that the template agent parses correctly."""
    repo = AgentRepository(base_dir=Path("./data/agents"))
    agent = repo.load_agent("template-agent")
    assert agent is not None
    assert agent.name == "Template Agent"
    assert agent.slug == "template-agent"
    assert agent.engine == EngineType.ANTIGRAVITY
    assert agent.auth.status == AuthStatus.UNAUTHENTICATED


def test_auth_manager_isolation(tmp_path: Path):
    """Verify that auth manager keeps session files isolated and restricted."""
    mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "test-agent"

    assert not mgr.is_authenticated(slug)

    # Save session
    mgr.save_session(slug, {"token": "dummy_secret_session_token"})
    assert mgr.is_authenticated(slug)

    # Check env override
    env = mgr.get_sdk_environment(slug)
    assert "BEHERIT_AGENT_AUTH_DIR" in env
    assert str(tmp_path / slug / ".auth") == env["BEHERIT_AGENT_AUTH_DIR"]

    # Clear session
    mgr.clear_session(slug)
    assert not mgr.is_authenticated(slug)


def test_agent_save_and_reload(tmp_path: Path):
    """Verify bidirectional Markdown-first saving and reloading."""
    repo = AgentRepository(base_dir=tmp_path)
    new_agent = AgentModel(
        name="Security Auditor",
        slug="security-auditor",
        description="Audits log anomalies",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="You are a security auditor.",
        instructions="Analyze auth logs for brute-force patterns.",
    )

    repo.save_agent(new_agent)

    loaded = repo.load_agent("security-auditor")
    assert loaded is not None
    assert loaded.name == "Security Auditor"
    assert loaded.master_prompt == "You are a security auditor."
    assert loaded.instructions == "Analyze auth logs for brute-force patterns."
