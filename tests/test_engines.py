"""Tests for Engine Registry and SDK Engine Adapters (Claude Code & Codex)."""

import pytest
from pathlib import Path
from beherit.core.auth_manager import AgentAuthManager
from beherit.engines import EngineRegistry, AntigravityEngine, ClaudeCodeEngine, OpenAICodexEngine
from beherit.models.agent import AgentModel, EngineType, AuthStatus


def test_engine_registry_resolution(tmp_path: Path):
    """Verify BEH-018: EngineRegistry correctly instantiates and provides adapters."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    registry = EngineRegistry(auth_manager=auth_mgr)

    engine_ag = registry.get_engine(EngineType.ANTIGRAVITY)
    assert isinstance(engine_ag, AntigravityEngine)

    engine_claude = registry.get_engine(EngineType.CLAUDE_CODE)
    assert isinstance(engine_claude, ClaudeCodeEngine)

    engine_codex = registry.get_engine(EngineType.CODEX)
    assert isinstance(engine_codex, OpenAICodexEngine)


@pytest.mark.asyncio
async def test_claude_engine_stream(tmp_path: Path):
    """Verify ClaudeCodeEngine stream_chat fallback execution."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("claude-bot", "dummy_claude_token", provider=EngineType.CLAUDE_CODE)

    agent = AgentModel(
        name="Claude Bot",
        slug="claude-bot",
        engine=EngineType.CLAUDE_CODE,
        master_prompt="You are a Claude Assistant.",
    )

    claude_engine = ClaudeCodeEngine(auth_manager=auth_mgr)
    claude_engine.cli_binary = None  # test simulator mode

    events = []
    async for chunk in claude_engine.stream_chat(agent, "Hello Claude"):
        events.append(chunk)

    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Echo from Claude Bot [Claude Engine]" in token_text


@pytest.mark.asyncio
async def test_codex_engine_stream(tmp_path: Path):
    """Verify OpenAICodexEngine stream_chat fallback execution."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("codex-bot", "dummy_codex_token", provider=EngineType.CODEX)

    agent = AgentModel(
        name="Codex Bot",
        slug="codex-bot",
        engine=EngineType.CODEX,
        master_prompt="You are an OpenAI Codex Assistant.",
    )

    codex_engine = OpenAICodexEngine(auth_manager=auth_mgr)
    codex_engine.cli_binary = None  # test simulator mode

    events = []
    async for chunk in codex_engine.stream_chat(agent, "Hello Codex"):
        events.append(chunk)

    token_text = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Echo from Codex Bot [Codex Engine]" in token_text
