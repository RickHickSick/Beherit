"""Tests for REST API endpoints and streaming chat guards."""

from pathlib import Path
from fastapi.testclient import TestClient
from beherit.api.routes import repo, auth_mgr
from beherit.main import app

client = TestClient(app)


def test_health_endpoint():
    """Verify health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_agent_creation_and_auth_flow(tmp_path: Path, monkeypatch):
    """Test full agent creation, manual auth, and disconnect flow."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Create agent
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "DevOps Guard",
            "slug": "devops-guard",
            "description": "Monitors deployment logs",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Audit all logs for errors.",
        },
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["slug"] == "devops-guard"

    # 2. Check initial auth status (unauthenticated)
    status_resp = client.get("/api/agents/devops-guard/auth/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["authenticated"] is False

    # 3. Paste manual token
    auth_resp = client.post(
        "/api/agents/devops-guard/auth/manual",
        json={"token": "secret_sdk_token_999", "provider": "antigravity"},
    )
    assert auth_resp.status_code == 200
    assert auth_resp.json()["auth"]["authenticated"] is True

    # 4. Check updated auth status
    status_resp2 = client.get("/api/agents/devops-guard/auth/status")
    assert status_resp2.json()["authenticated"] is True

    # 5. Disconnect
    disc_resp = client.post("/api/agents/devops-guard/auth/disconnect")
    assert disc_resp.status_code == 200
    assert disc_resp.json()["auth"]["authenticated"] is False
