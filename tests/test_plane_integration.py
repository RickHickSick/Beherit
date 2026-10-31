"""Unit tests for Bidirectional Plane API Integration Tools (BEH-065)."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from beherit.core.plane import PlaneClient
from beherit.core.repository import AgentRepository
from beherit.core.tools import CapabilitiesGuard
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def plane_agent(tmp_path: Path):
    """Setup a test agent."""
    repo = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="DevOps Agent",
        slug="devops-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś inżynierem DevOps.",
    )
    repo.save_agent(agent)
    return agent, tmp_path


@pytest.mark.asyncio
async def test_plane_client_list_issues():
    """Verify PlaneClient lists issues and maps states."""
    plane = PlaneClient(api_url="http://mock-plane/api/v1", api_key="mock_key")

    mock_states = {
        "results": [
            {"id": "state-todo-1", "name": "Todo", "slug": "todo", "group": "unstarted"},
            {"id": "state-done-1", "name": "Done", "slug": "done", "group": "completed"},
        ]
    }
    mock_issues = {
        "results": [
            {
                "id": "issue-uuid-1",
                "sequence_id": 65,
                "name": "BEH-065: Dwukierunkowa integracja z Plane API",
                "priority": "high",
                "state": "state-todo-1",
                "created_at": "2026-10-31T18:00:00Z",
            }
        ]
    }

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp_states = MagicMock()
        mock_resp_states.status_code = 200
        mock_resp_states.json.return_value = mock_states

        mock_resp_issues = MagicMock()
        mock_resp_issues.status_code = 200
        mock_resp_issues.json.return_value = mock_issues

        # First call is for issues, second call is for states
        mock_get.side_effect = [mock_resp_issues, mock_resp_states]

        issues = await plane.list_issues(state="todo")
        assert len(issues) == 1
        assert issues[0]["identifier"] == "BEH-65"
        assert issues[0]["state"] == "Todo"
        assert issues[0]["priority"] == "high"


@pytest.mark.asyncio
async def test_plane_client_create_issue():
    """Verify PlaneClient creates issue and resolves state ID."""
    plane = PlaneClient(api_url="http://mock-plane/api/v1", api_key="mock_key")

    mock_states = {
        "results": [
            {"id": "state-todo-uuid", "name": "Todo", "slug": "todo", "group": "unstarted"}
        ]
    }
    mock_created = {
        "id": "new-issue-uuid",
        "sequence_id": 71,
        "name": "Wykryto brak miejsca na dysku /dev/sda1",
        "priority": "urgent",
        "state": "state-todo-uuid",
    }

    with patch("httpx.AsyncClient.get") as mock_get, \
         patch("httpx.AsyncClient.post") as mock_post:

        mock_resp_states = MagicMock()
        mock_resp_states.status_code = 200
        mock_resp_states.json.return_value = mock_states
        mock_get.return_value = mock_resp_states

        mock_resp_post = MagicMock()
        mock_resp_post.status_code = 201
        mock_resp_post.json.return_value = mock_created
        mock_post.return_value = mock_resp_post

        res = await plane.create_issue(
            name="Wykryto brak miejsca na dysku /dev/sda1",
            description="Wolne miejsce spadło poniżej 5%",
            priority="urgent",
            state="todo",
        )
        assert res["success"] is True
        assert res["identifier"] == "BEH-71"
        assert res["state_id"] == "state-todo-uuid"


@pytest.mark.asyncio
async def test_dispatch_tool_plane_methods(plane_agent):
    """Verify CapabilitiesGuard dispatches plane_create_issue and plane_list_issues tools."""
    agent, tmp_path = plane_agent
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    with patch.object(PlaneClient, "list_issues", new_callable=AsyncMock) as mock_list, \
         patch.object(PlaneClient, "create_issue", new_callable=AsyncMock) as mock_create, \
         patch.object(PlaneClient, "update_issue_state", new_callable=AsyncMock) as mock_update:

        mock_list.return_value = [{"identifier": "BEH-65", "name": "Integracja Plane", "state": "todo"}]
        mock_create.return_value = {"success": True, "identifier": "BEH-72", "name": "Alert z audytu"}
        mock_update.return_value = {"success": True, "issue_id": "issue-123", "new_state": "done"}

        # 1. Test plane_list_issues
        res_list = await guard.dispatch_tool(agent, "plane_list_issues", {"state": "todo"})
        assert res_list["success"] is True
        assert res_list["total"] == 1

        # 2. Test plane_create_issue
        res_create = await guard.dispatch_tool(
            agent,
            "plane_create_issue",
            {"name": "Alert z audytu", "description": "Crash kontenera", "priority": "high"},
        )
        assert res_create["success"] is True
        assert res_create["identifier"] == "BEH-72"

        # 3. Test plane_update_state
        res_upd = await guard.dispatch_tool(
            agent,
            "plane_update_state",
            {"issue_id": "issue-123", "state": "done"},
        )
        assert res_upd["success"] is True
        assert res_upd["new_state"] == "done"


def test_plane_rest_endpoints():
    """Verify /api/plane/issues REST endpoints."""
    with patch.object(PlaneClient, "list_issues", new_callable=AsyncMock) as mock_list, \
         patch.object(PlaneClient, "create_issue", new_callable=AsyncMock) as mock_create, \
         patch.object(PlaneClient, "update_issue_state", new_callable=AsyncMock) as mock_update:

        mock_list.return_value = [{"identifier": "BEH-65", "name": "Test Plane API"}]
        mock_create.return_value = {"success": True, "identifier": "BEH-73", "name": "Nowe zadanie"}
        mock_update.return_value = {"success": True, "issue_id": "123", "new_state": "done"}

        # 1. GET /api/plane/issues
        res_get = client.get("/api/plane/issues")
        assert res_get.status_code == 200
        assert res_get.json()["total"] == 1

        # 2. POST /api/plane/issues
        res_post = client.post(
            "/api/plane/issues",
            json={"name": "Nowe zadanie", "priority": "high", "state": "todo"},
        )
        assert res_post.status_code == 201
        assert res_post.json()["identifier"] == "BEH-73"

        # 3. PATCH /api/plane/issues/{id}/state
        res_patch = client.patch(
            "/api/plane/issues/123/state",
            json={"state": "done"},
        )
        assert res_patch.status_code == 200
        assert res_patch.json()["new_state"] == "done"
