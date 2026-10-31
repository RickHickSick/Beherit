"""Unit tests for Multi-Agent Orchestration and call_agent Delegation (BEH-060)."""

from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch

from beherit.core.repository import AgentRepository
from beherit.core.tools import CapabilitiesGuard
from beherit.mcp.server import MCPServer
from beherit.models.agent import AgentCapabilities, AgentModel, EngineType


@pytest.fixture
def test_agents(tmp_path: Path):
    """Setup two mock agents in a temporary repository directory."""
    repo = AgentRepository(base_dir=tmp_path)

    agent_a = AgentModel(
        name="Koordynator Roju",
        slug="koordynator",
        description="Główny orchestrator zadań",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        capabilities=AgentCapabilities(terminal=True, file_read=True, file_write=True),
        master_prompt="Jesteś głównym koordynatorem.",
    )
    repo.save_agent(agent_a)

    agent_b = AgentModel(
        name="Ekspert Finansowy",
        slug="analityk-rynkow",
        description="Analiza danych rynkowych i finansowych",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        capabilities=AgentCapabilities(terminal=False, file_read=True),
        master_prompt="Jesteś ekspertem rynkowym.",
    )
    repo.save_agent(agent_b)

    return repo, agent_a, agent_b


@pytest.mark.asyncio
async def test_call_agent_successful_delegation(test_agents, tmp_path):
    """Test that an agent can invoke a peer subagent and receive its output."""
    repo, agent_a, agent_b = test_agents
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    async def mock_stream_chat(agent, message, history=None):
        yield {"event": "thought", "data": "Thinking..."}
        yield {"event": "token", "data": f"Raport dla: {message[:15]} - Wynik: 4200 PLN."}
        yield {"event": "done", "data": {"completed": True}}

    with patch("beherit.engines.antigravity.AntigravityEngine.stream_chat", side_effect=mock_stream_chat):
        result = await guard.call_agent(
            caller_agent=agent_a,
            target_slug="analityk-rynkow",
            prompt="Sprawdź marżę za październik",
            context={"kwota": 4200},
        )

        assert result["success"] is True
        assert result["caller_agent"] == "koordynator"
        assert result["target_agent"] == "analityk-rynkow"
        assert "4200 PLN" in result["output"]
        assert result["call_stack"] == ["koordynator", "analityk-rynkow"]


@pytest.mark.asyncio
async def test_call_agent_circular_delegation_rejection(test_agents, tmp_path):
    """Test that circular agent calls are caught and rejected immediately."""
    repo, agent_a, agent_b = test_agents
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    # Simulate existing call stack where target_slug is already present
    call_stack = ["koordynator", "analityk-rynkow"]
    result = await guard.call_agent(
        caller_agent=agent_b,
        target_slug="koordynator",
        prompt="Powrót do koordynatora",
        call_stack=call_stack,
    )

    assert result["success"] is False
    assert "Circular agent delegation rejected" in result["error"]


@pytest.mark.asyncio
async def test_call_agent_depth_limit(test_agents, tmp_path):
    """Test that excessive recursion depth (>= 5) is rejected."""
    repo, agent_a, agent_b = test_agents
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    deep_stack = ["agent1", "agent2", "agent3", "agent4", "agent5"]
    result = await guard.call_agent(
        caller_agent=agent_a,
        target_slug="analityk-rynkow",
        prompt="Zbyt głębokie zagnieżdżenie",
        call_stack=deep_stack,
    )

    assert result["success"] is False
    assert "Maximum subagent delegation depth exceeded" in result["error"]


@pytest.mark.asyncio
async def test_call_agent_unknown_target(test_agents, tmp_path):
    """Test error handling when the requested subagent does not exist."""
    repo, agent_a, agent_b = test_agents
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    result = await guard.call_agent(
        caller_agent=agent_a,
        target_slug="nieistniejacy-agent",
        prompt="Halo?",
    )

    assert result["success"] is False
    assert "nieistniejacy-agent" in result["error"]


@pytest.mark.asyncio
async def test_dispatch_tool_call_agent(test_agents, tmp_path):
    """Test that dispatch_tool handles call_agent seamlessly."""
    repo, agent_a, agent_b = test_agents
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    async def mock_stream_chat(agent, message, history=None):
        yield {"event": "token", "data": "Odpowiedź subagenta."}

    with patch("beherit.engines.antigravity.AntigravityEngine.stream_chat", side_effect=mock_stream_chat):
        result = await guard.dispatch_tool(
            agent=agent_a,
            tool_name="call_agent",
            args={"agent_slug": "analityk-rynkow", "prompt": "Cześć"},
        )
        assert result["success"] is True
        assert result["output"] == "Odpowiedź subagenta."


def test_mcp_server_includes_call_agent_tool(test_agents):
    """Test that the embedded MCP server exports call_agent in its tool definitions."""
    repo, agent_a, agent_b = test_agents
    server = MCPServer(agent_slug="koordynator", repository=repo)

    tools = server.get_tool_definitions()
    tool_names = [t["name"] for t in tools]

    assert "call_agent" in tool_names
    call_tool = next(t for t in tools if t["name"] == "call_agent")
    assert "agent_slug" in call_tool["inputSchema"]["properties"]
    assert "prompt" in call_tool["inputSchema"]["properties"]


def test_build_system_context_injects_peer_agents(test_agents):
    """Test that BaseAgentEngine injects available peer agents into system context."""
    repo, agent_a, agent_b = test_agents

    from beherit.engines.base import BaseAgentEngine

    with patch("beherit.core.repository.AgentRepository.list_agents", return_value=[agent_a, agent_b]):
        ctx = BaseAgentEngine.build_system_context(agent_a)
        assert "## Dostępni Agenci do Współpracy (Narzędzie call_agent)" in ctx
        assert "Ekspert Finansowy" in ctx
        assert "analityk-rynkow" in ctx
