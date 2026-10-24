"""Test Agent Model, Repository and Auth Isolation."""

from pathlib import Path

import pytest

from beherit.core.auth_manager import AgentAuthManager
from beherit.core.repository import AgentRepository
from beherit.models.agent import AgentModel, AuthStatus, EngineType


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


def test_obsidian_extra_metadata_preservation(tmp_path: Path):
    """Verify that Obsidian-specific frontmatter tags and properties are not wiped on save."""
    repo = AgentRepository(base_dir=tmp_path)
    
    agent_dir = tmp_path / "obsidian-analyst"
    agent_dir.mkdir(parents=True)
    raw_md = """---
name: "Obsidian Analyst"
slug: "obsidian-analyst"
tags: [data, obsidian, homelab]
aliases: ["OA", "Data Bot"]
custom_metric: 99.4
engine: "antigravity"
model: "gemini-3.8-flash"
---
# Master Prompt
Analyzes notes.
"""
    (agent_dir / "agent.md").write_text(raw_md, encoding="utf-8")

    # Load agent
    loaded = repo.load_agent("obsidian-analyst")
    assert loaded is not None
    assert loaded.tags == ["data", "obsidian", "homelab"]
    assert loaded.aliases == ["OA", "Data Bot"]
    assert loaded.custom_metric == 99.4

    # Save agent again (simulating web edit or auto-save)
    loaded.master_prompt = "Updated master prompt."
    repo.save_agent(loaded)

    # Reload from disk and verify extra fields survived
    reloaded = repo.load_agent("obsidian-analyst")
    assert reloaded is not None
    assert reloaded.master_prompt == "Updated master prompt."
    assert reloaded.tags == ["data", "obsidian", "homelab"]
    assert reloaded.aliases == ["OA", "Data Bot"]
    assert reloaded.custom_metric == 99.4


@pytest.mark.asyncio
async def test_antigravity_prompt_injection(tmp_path: Path):
    """Verify BEH-011: stream_chat incorporates system prompt & instructions."""
    from beherit.engines.antigravity import AntigravityEngine
    
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("bot", "test_token")
    
    agent = AgentModel(
        name="Prompt Test Bot",
        slug="bot",
        master_prompt="You are a strict code reviewer.",
        instructions="Never approve untested code.",
    )
    
    engine = AntigravityEngine(auth_manager=auth_mgr)
    engine.cli_binary = None  # Test in pure isolated simulator mode
    
    events = []
    async for chunk in engine.stream_chat(agent, "Review this PR", history=[{"role": "user", "content": "Hi"}]):
        events.append(chunk)

    # Check thoughts and tokens generated
    token_texts = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Echo from Prompt Test Bot" in token_texts
    assert "Identity: You are a strict code reviewer" in token_texts
