"""Unit tests for Autonomous Goal Loop & Scratchpad (todo.md - BEH-063)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, repo
from beherit.core.goal_runner import AutonomousGoalRunner
from beherit.core.repository import AgentRepository
from beherit.core.todo import AgentTodoManager
from beherit.core.tools import CapabilitiesGuard
from beherit.engines.base import BaseAgentEngine
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def test_agent(tmp_path: Path):
    """Setup a test agent with temporary directory workspace."""
    repo_inst = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Agent Planista",
        slug="planista",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś inżynierem AI.",
    )
    repo_inst.save_agent(agent)
    return agent, tmp_path


def test_todo_manager_crud(test_agent):
    """Verify AgentTodoManager creates, updates, and summarizes tasks in todo.md."""
    agent, tmp_path = test_agent
    mgr = AgentTodoManager(base_dir=tmp_path)

    # 1. Add tasks
    res1 = mgr.add_item(agent, "Zbadaj strukturę bazy danych", status="todo")
    assert res1["success"] is True
    assert res1["item"]["id"] == 1
    assert res1["item"]["status"] == "todo"

    res2 = mgr.add_item(agent, "Przygotuj migrację SQL", status="todo")
    assert res2["success"] is True
    assert res2["item"]["id"] == 2

    # 2. Read tasks
    items = mgr.read_todos(agent)
    assert len(items) == 2
    assert items[0]["text"] == "Zbadaj strukturę bazy danych"

    # 3. Update task status
    upd1 = mgr.update_item(agent, item_id=1, status="in_progress")
    assert upd1["success"] is True
    assert upd1["item"]["status"] == "in_progress"

    summary1 = mgr.get_summary(agent)
    assert summary1["total"] == 2
    assert summary1["in_progress"] == 1
    assert summary1["all_done"] is False
    assert summary1["progress_percent"] == 0.0

    # 4. Mark done
    upd2 = mgr.update_item(agent, item_id=1, status="done")
    assert upd2["item"]["status"] == "done"
    upd3 = mgr.update_item(agent, item_id=2, status="done")
    assert upd3["item"]["status"] == "done"

    summary2 = mgr.get_summary(agent)
    assert summary2["done"] == 2
    assert summary2["all_done"] is True
    assert summary2["progress_percent"] == 100.0

    # 5. Clear
    clr = mgr.clear_todos(agent)
    assert clr["success"] is True
    assert len(mgr.read_todos(agent)) == 0


@pytest.mark.asyncio
async def test_capabilities_guard_manage_todo(test_agent):
    """Verify tool dispatcher supports manage_todo action."""
    agent, tmp_path = test_agent
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    # Add task via dispatch_tool
    add_res = await guard.dispatch_tool(
        agent,
        "manage_todo",
        {"action": "add", "text": "Konfiguracja serwera Nginx"}
    )
    assert add_res["success"] is True
    assert add_res["item"]["id"] == 1

    # List tasks
    list_res = await guard.dispatch_tool(agent, "manage_todo", {"action": "list"})
    assert list_res["success"] is True
    assert len(list_res["todos"]) == 1

    # Update task
    upd_res = await guard.dispatch_tool(
        agent,
        "manage_todo",
        {"action": "update", "item_id": 1, "status": "done"}
    )
    assert upd_res["success"] is True
    assert upd_res["item"]["status"] == "done"


def test_system_context_injects_todo(test_agent):
    """Verify BaseAgentEngine.build_system_context includes active todo.md."""
    agent, tmp_path = test_agent
    with patch("beherit.core.todo.AgentTodoManager.read_todos", return_value=[{"id": 1, "text": "Zadanie A do wykonania", "status": "todo"}]), \
         patch("beherit.core.todo.AgentTodoManager.read_raw", return_value="- [ ] Zadanie A do wykonania <!-- id: 1 -->"):
        context = BaseAgentEngine.build_system_context(agent)
        assert "## Aktywny Plan Działań i Scratchpad (todo.md)" in context
        assert "Zadanie A do wykonania" in context


def test_todo_rest_api_endpoints(test_agent, monkeypatch):
    """Verify GET and PUT /api/agents/{slug}/todo endpoints."""
    agent, tmp_path = test_agent
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Add item via PUT
    res_put = client.put(
        f"/api/agents/{agent.slug}/todo",
        json={"action": "add", "text": "Test zadania przez API REST", "status": "todo"}
    )
    assert res_put.status_code == 200
    data_put = res_put.json()
    assert len(data_put["todos"]) == 1
    assert data_put["todos"][0]["text"] == "Test zadania przez API REST"

    # 2. Get todo state
    res_get = client.get(f"/api/agents/{agent.slug}/todo")
    assert res_get.status_code == 200
    data_get = res_get.json()
    assert data_get["summary"]["total"] == 1
    assert data_get["todos"][0]["status"] == "todo"

    # 3. Update status to done
    res_upd = client.put(
        f"/api/agents/{agent.slug}/todo",
        json={"action": "update", "item_id": 1, "status": "done"}
    )
    assert res_upd.status_code == 200
    data_upd = res_upd.json()
    assert data_upd["summary"]["all_done"] is True


@pytest.mark.asyncio
async def test_autonomous_goal_runner_flow(test_agent):
    """Verify AutonomousGoalRunner executes loop, records events, and halts at goal completion."""
    agent, tmp_path = test_agent
    runner = AutonomousGoalRunner(base_dir=tmp_path)

    # Simulated engine chunks
    async def mock_stream_chat(ag, prompt, hist=None):
        # When prompted in step 1, add tasks to todo.md
        if "TRYB AUTONOMICZNEGO CELU" in prompt:
            runner.todo_mgr.add_item(ag, "Krok 1: Inicjalizacja", status="done")
            runner.todo_mgr.add_item(ag, "Krok 2: Weryfikacja", status="done")
        yield {"event": "token", "data": "Plan zrealizowany."}

    mock_engine = AsyncMock()
    mock_engine.stream_chat = mock_stream_chat

    with patch.object(runner.engine_registry, "get_engine", return_value=mock_engine):
        events = []
        async for evt in runner.run_goal(agent, goal="Zoptymalizuj wydajność bazy danych", max_steps=5):
            events.append(evt)

        event_names = [e.get("event") for e in events]
        assert "goal_started" in event_names
        assert "step_started" in event_names
        assert "step_finished" in event_names
        assert "goal_completed" in event_names

        # Verify final report was created in workspace
        reports_dir = tmp_path / agent.slug / "Notatki" / "Raporty"
        report_files = list(reports_dir.glob("Raport_Celu_*.md"))
        assert len(report_files) == 1
        content = report_files[0].read_text()
        assert "Raport Realizacji Celu: Zoptymalizuj wydajność bazy danych" in content
