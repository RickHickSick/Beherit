"""Tests for Direct HTTP API Streaming (Anthropic, Gemini, OpenAI)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from beherit.core.auth_manager import AgentAuthManager
from beherit.engines.antigravity import AntigravityEngine
from beherit.engines.claude import ClaudeCodeEngine
from beherit.engines.codex import OpenAICodexEngine
from beherit.models.agent import AgentModel, EngineType


class MockAsyncResponse:
    def __init__(self, status_code: int, sse_lines: list[str]):
        self.status_code = status_code
        self.sse_lines = sse_lines

    async def aiter_lines(self):
        for line in self.sse_lines:
            yield line

    async def aread(self):
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_direct_gemini_api_stream(tmp_path: Path):
    """Verify AntigravityEngine directly streams from Gemini API with AIzaSy key."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("gemini-agent", "AIzaSy_test_valid_key", provider=EngineType.ANTIGRAVITY)

    agent = AgentModel(
        name="Gemini Agent",
        slug="gemini-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-2.0-flash",
    )

    engine = AntigravityEngine(auth_manager=auth_mgr)
    engine.cli_binary = None

    sse_data = [
        'data: {"candidates": [{"content": {"parts": [{"text": "Witaj! "}]}}]}',
        'data: {"candidates": [{"content": {"parts": [{"text": "Jestem modelem Gemini."}]}}]}',
    ]

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=MockAsyncResponse(200, sse_data))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        events = []
        async for chunk in engine.stream_chat(agent, "Cześć"):
            events.append(chunk)

    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Witaj! Jestem modelem Gemini." in tokens


@pytest.mark.asyncio
async def test_direct_claude_api_stream(tmp_path: Path):
    """Verify ClaudeCodeEngine directly streams from Anthropic Messages API with sk-ant key."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("claude-agent", "sk-ant-api03-test-valid-key", provider=EngineType.CLAUDE_CODE)

    agent = AgentModel(
        name="Claude Agent",
        slug="claude-agent",
        engine=EngineType.CLAUDE_CODE,
        model="claude-3-5-sonnet",
    )

    engine = ClaudeCodeEngine(auth_manager=auth_mgr)
    engine.cli_binary = None

    sse_data = [
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Cześć, "}}',
        'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "tutaj Claude!"}}',
        'data: [DONE]',
    ]

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=MockAsyncResponse(200, sse_data))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        events = []
        async for chunk in engine.stream_chat(agent, "Hej"):
            events.append(chunk)

    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Cześć, tutaj Claude!" in tokens


@pytest.mark.asyncio
async def test_direct_openai_api_stream(tmp_path: Path):
    """Verify OpenAICodexEngine directly streams from OpenAI API with sk- key."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    auth_mgr.save_manual_token("openai-agent", "sk-proj-test1234567890abcdefgh", provider=EngineType.CODEX)

    agent = AgentModel(
        name="OpenAI Agent",
        slug="openai-agent",
        engine=EngineType.CODEX,
        model="gpt-4o-mini",
    )

    engine = OpenAICodexEngine(auth_manager=auth_mgr)
    engine.cli_binary = None

    sse_data = [
        'data: {"choices": [{"delta": {"content": "Odpowiedź "}}]}',
        'data: {"choices": [{"delta": {"content": "z OpenAI."}}]}',
        'data: [DONE]',
    ]

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=MockAsyncResponse(200, sse_data))

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        events = []
        async for chunk in engine.stream_chat(agent, "Pytanie"):
            events.append(chunk)

    tokens = "".join(e["data"] for e in events if e["event"] == "token")
    assert "Odpowiedź z OpenAI." in tokens
