"""Tests for Agent Folders, Team Swarm Organization, and Moving Agents."""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path

from beherit.main import app
from beherit.core.repository import AgentRepository


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    test_repo = AgentRepository(base_dir=tmp_path / "agents")
    import beherit.api.routes as routes
    monkeypatch.setattr(routes, "repo", test_repo)
    return TestClient(app)


def test_folders_lifecycle_and_subagent_propagation(client: TestClient):
    # 1. Create a parent coordinator agent in folder "Finanse"
    res1 = client.post(
        "/api/agents",
        json={
            "name": "Dyrektor Finansowy",
            "slug": "dyrektor-finansowy",
            "folder": "Finanse",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    assert res1.status_code == 201, res1.text
    assert res1.json()["agent"]["folder"] == "Finanse"

    # 2. Create subagent under dyrektor-finansowy without explicit folder -> should inherit "Finanse"
    res_sub = client.post(
        "/api/agents/dyrektor-finansowy/subagents",
        json={
            "name": "Audytor Faktur",
            "slug": "audytor-faktur",
            "master_prompt": "Weryfikuj faktury i koszty.",
        },
    )
    assert res_sub.status_code == 201, res_sub.text
    assert res_sub.json()["subagent"]["folder"] == "Finanse"

    # 3. Create another independent agent without folder
    res2 = client.post(
        "/api/agents",
        json={
            "name": "DevOps Bot",
            "slug": "devops-bot",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    assert res2.status_code == 201
    assert res2.json()["agent"]["folder"] is None

    # 4. Check GET /api/agents/folders/list
    res_folders = client.get("/api/agents/folders/list")
    assert res_folders.status_code == 200
    data = res_folders.json()
    assert data["total_agents"] == 3
    assert data["unassigned_count"] == 1
    assert len(data["folders"]) == 1
    assert data["folders"][0]["name"] == "Finanse"
    assert data["folders"][0]["count"] == 2  # dyrektor + audytor

    # 5. Move Dyrektor Finansowy to "Zarząd" WITH move_subagents=True
    res_move = client.patch(
        "/api/agents/dyrektor-finansowy/folder",
        json={"folder": "Zarząd", "move_subagents": True},
    )
    assert res_move.status_code == 200
    move_data = res_move.json()
    assert move_data["status"] == "moved"
    assert move_data["folder"] == "Zarząd"
    assert "audytor-faktur" in move_data["affected_subagents"]

    # Verify both parent and subagent now have folder "Zarząd"
    parent_check = client.get("/api/agents/dyrektor-finansowy").json()
    sub_check = client.get("/api/agents/audytor-faktur").json()
    assert parent_check["folder"] == "Zarząd"
    assert sub_check["folder"] == "Zarząd"

    # 6. Rename folder "Zarząd" -> "Zarząd & Strategia"
    res_rename = client.post(
        "/api/agents/folders/rename",
        json={"old_name": "Zarząd", "new_name": "Zarząd & Strategia"},
    )
    assert res_rename.status_code == 200
    assert res_rename.json()["updated_count"] == 2

    # Check list again
    res_folders2 = client.get("/api/agents/folders/list")
    folders2 = {f["name"]: f["count"] for f in res_folders2.json()["folders"]}
    assert "Zarząd & Strategia" in folders2
    assert folders2["Zarząd & Strategia"] == 2

    # 7. Move agent to None (unassigned)
    res_unassign = client.patch(
        "/api/agents/dyrektor-finansowy/folder",
        json={"folder": None, "move_subagents": False},
    )
    assert res_unassign.status_code == 200
    parent_unassigned = client.get("/api/agents/dyrektor-finansowy").json()
    assert parent_unassigned["folder"] is None
    # Subagent should still be in "Zarząd & Strategia" because move_subagents was False
    sub_still = client.get("/api/agents/audytor-faktur").json()
    assert sub_still["folder"] == "Zarząd & Strategia"


def test_folder_update_and_errors(client: TestClient):
    # 404 on moving non-existent agent
    res_404 = client.patch(
        "/api/agents/nie-istnieje/folder",
        json={"folder": "Test", "move_subagents": True},
    )
    assert res_404.status_code == 404

    # Create agent and update via PUT /api/agents/{slug}
    client.post(
        "/api/agents",
        json={
            "name": "Marketing Specialist",
            "slug": "marketing-spec",
            "folder": "Marketing",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    update_res = client.put(
        "/api/agents/marketing-spec",
        json={
            "name": "Marketing Lead",
            "folder": "Growth Team",
        },
    )
    assert update_res.status_code == 200
    get_res = client.get("/api/agents/marketing-spec")
    assert get_res.status_code == 200
    assert get_res.json()["folder"] == "Growth Team"
