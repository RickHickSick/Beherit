"""Unit tests for Agent Long-term Memory (memory.md - BEH-062)."""

from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, repo
from beherit.core.memory import AgentMemoryManager
from beherit.core.repository import AgentRepository
from beherit.core.tools import CapabilitiesGuard
from beherit.engines.base import BaseAgentEngine
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def memory_agent(tmp_path: Path):
    """Setup a test agent with memory manager in a temporary directory."""
    repo_inst = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Agent z Pamięcią",
        slug="pamiec-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś asystentem.",
    )
    repo_inst.save_agent(agent)
    return agent, tmp_path


def test_save_and_read_memory(memory_agent):
    """Verify saving facts creates memory.md and formats entries with timestamp."""
    agent, tmp_path = memory_agent
    mem_mgr = AgentMemoryManager(base_dir=tmp_path)

    res1 = mem_mgr.save_fact(agent, "Użytkownik preferuje zwięzłe tabele Markdown.", category="Preferencje")
    assert res1["success"] is True
    assert "Preferencje" in res1["category"]

    res2 = mem_mgr.save_fact(agent, "Serwer produkcyjny działa na Raspberry Pi 5.", category="Infrastruktura")
    assert res2["success"] is True

    content = mem_mgr.read_memory(agent)
    assert "# 🧠 Pamięć Długoterminowa" in content
    assert "Użytkownik preferuje zwięzłe tabele" in content
    assert "Raspberry Pi 5" in content


def test_forget_memory_fact(memory_agent):
    """Verify removing a fact matching a pattern from memory.md."""
    agent, tmp_path = memory_agent
    mem_mgr = AgentMemoryManager(base_dir=tmp_path)

    mem_mgr.save_fact(agent, "Stary token API to 123456", category="Sekrety")
    mem_mgr.save_fact(agent, "Nowy token API to abcdef", category="Sekrety")

    forget_res = mem_mgr.forget_fact(agent, "123456")
    assert forget_res["success"] is True
    assert forget_res["removed_count"] == 1

    content = mem_mgr.read_memory(agent)
    assert "123456" not in content
    assert "abcdef" in content


@pytest.mark.asyncio
async def test_dispatch_tool_memory_methods(memory_agent):
    """Verify CapabilitiesGuard tool dispatcher handles save_memory, recall_memory, and forget_memory."""
    agent, tmp_path = memory_agent
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    save_res = await guard.dispatch_tool(
        agent=agent,
        tool_name="save_memory",
        args={"fact": "Klient ma NIP 1234567890", "category": "Klienci"},
    )
    assert save_res["success"] is True

    recall_res = await guard.dispatch_tool(
        agent=agent,
        tool_name="recall_memory",
    )
    assert recall_res["success"] is True
    assert "1234567890" in recall_res["memory"]

    forget_res = await guard.dispatch_tool(
        agent=agent,
        tool_name="forget_memory",
        args={"pattern": "1234567890"},
    )
    assert forget_res["success"] is True
    assert forget_res["removed_count"] == 1


def test_build_system_context_injects_memory(memory_agent):
    """Verify BaseAgentEngine injects memory.md into system context."""
    agent, tmp_path = memory_agent
    mem_mgr = AgentMemoryManager(base_dir=tmp_path)
    mem_mgr.save_fact(agent, "Użytkownik ma na imię Łukasz", category="Profil")

    from unittest.mock import patch
    with patch("beherit.core.memory.AgentMemoryManager.read_memory", return_value="- [2026-10-31] **[Profil]**: Użytkownik ma na imię Łukasz"):
        ctx = BaseAgentEngine.build_system_context(agent)
        assert "## Pamięć Długoterminowa i Profil Użytkownika" in ctx
        assert "Użytkownik ma na imię Łukasz" in ctx


def test_memory_rest_api_endpoints(memory_agent, monkeypatch):
    """Verify GET and PUT /api/agents/{slug}/memory REST endpoints."""
    agent, tmp_path = memory_agent
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Update memory via PUT
    put_resp = client.put(
        f"/api/agents/{agent.slug}/memory",
        json={"content": "# Pamięć\n- [Fakt]: Używamy Tailwind CSS i Alpine.js."},
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["status"] == "updated"

    # 2. Retrieve memory via GET
    get_resp = client.get(f"/api/agents/{agent.slug}/memory")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["slug"] == agent.slug
    assert "Tailwind CSS" in data["content"]
    assert data["exists"] is True
