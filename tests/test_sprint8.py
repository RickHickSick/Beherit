"""Unit tests for Sprint 8: Auth Guard, MCP Configuration, and Knowledge Base Ingestion."""

import json
from pathlib import Path
from fastapi.testclient import TestClient

from beherit.api.routes import repo, auth_mgr
from beherit.config import settings
from beherit.engines.base import BaseAgentEngine
from beherit.main import app
from beherit.models.agent import MCPServerConfig

client = TestClient(app)


def test_auth_guard_middleware_and_endpoints(monkeypatch):
    """Verify BEH-022: Optional access token protection middleware and verify endpoint."""
    # 1. Without BEHERIT_ACCESS_TOKEN, everything is open
    monkeypatch.setattr(settings, "BEHERIT_ACCESS_TOKEN", None)
    status_resp = client.get("/api/auth/status")
    assert status_resp.status_code == 200
    assert status_resp.json()["auth_required"] is False

    open_resp = client.get("/api/agents")
    assert open_resp.status_code == 200

    # 2. Set BEHERIT_ACCESS_TOKEN
    test_pin = "secure-pin-4321"
    monkeypatch.setattr(settings, "BEHERIT_ACCESS_TOKEN", test_pin)

    # Status shows auth is required
    status_resp2 = client.get("/api/auth/status")
    assert status_resp2.status_code == 200
    assert status_resp2.json()["auth_required"] is True

    # Public paths are accessible without credentials
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200

    # Verification endpoint works
    verify_bad = client.post("/api/auth/verify", json={"token": "wrong-pin"})
    assert verify_bad.status_code == 200
    assert verify_bad.json()["valid"] is False

    verify_good = client.post("/api/auth/verify", json={"token": test_pin})
    assert verify_good.status_code == 200
    assert verify_good.json()["valid"] is True

    # Protected API endpoints are blocked with 401 if token is missing or wrong
    blocked_resp = client.get("/api/agents")
    assert blocked_resp.status_code == 401

    wrong_tok_resp = client.get("/api/agents", headers={"X-Beherit-Token": "bad-token"})
    assert wrong_tok_resp.status_code == 401

    # Passing valid token in X-Beherit-Token header succeeds
    ok_custom = client.get("/api/agents", headers={"X-Beherit-Token": test_pin})
    assert ok_custom.status_code == 200

    # Passing valid token in Authorization Bearer succeeds
    ok_bearer = client.get("/api/agents", headers={"Authorization": f"Bearer {test_pin}"})
    assert ok_bearer.status_code == 200


def test_mcp_servers_configuration(tmp_path: Path, monkeypatch):
    """Verify BEH-023: MCP server configuration per agent and mcp.json generation."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "BEHERIT_ACCESS_TOKEN", None)

    # 1. Create agent with MCP servers
    payload = {
        "name": "Data Analyst",
        "slug": "data-analyst",
        "master_prompt": "You query databases using MCP.",
        "mcp_servers": {
            "postgres": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/db"],
                "env": {"DEBUG": "true"},
            }
        },
    }
    resp = client.post("/api/agents", json=payload)
    assert resp.status_code == 201

    # 2. Check that mcp.json was generated in the agent workspace
    agent_dir = tmp_path / "data-analyst"
    mcp_file = agent_dir / "mcp.json"
    assert mcp_file.is_file()
    mcp_json = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "mcpServers" in mcp_json
    assert "postgres" in mcp_json["mcpServers"]
    assert mcp_json["mcpServers"]["postgres"]["command"] == "npx"

    # 3. Check BaseAgentEngine.build_system_context includes MCP server description
    loaded_agent = repo.load_agent("data-analyst")
    assert loaded_agent is not None
    assert "postgres" in loaded_agent.mcp_servers
    sys_ctx = BaseAgentEngine.build_system_context(loaded_agent)
    assert "Model Context Protocol" in sys_ctx
    assert "postgres" in sys_ctx


def test_knowledge_base_crud_and_context_injection(tmp_path: Path, monkeypatch):
    """Verify BEH-024: Knowledge base documents CRUD and automatic context injection."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)
    monkeypatch.setattr(settings, "BEHERIT_ACCESS_TOKEN", None)

    # 1. Create agent
    client.post(
        "/api/agents",
        json={"name": "Knowledge Guru", "slug": "guru", "master_prompt": "You answer from reference docs."},
    )

    # 2. Save document to knowledge base
    doc_payload = {
        "filename": "pricing_table.md",
        "content": "# Cennik 2026\nPlan Basic: 10 EUR\nPlan Pro: 25 EUR",
    }
    save_resp = client.post("/api/agents/guru/knowledge", json=doc_payload)
    assert save_resp.status_code == 200
    assert save_resp.json()["filename"] == "pricing_table.md"

    # 3. List knowledge documents
    list_resp = client.get("/api/agents/guru/knowledge")
    assert list_resp.status_code == 200
    files = list_resp.json()["files"]
    assert len(files) == 1
    assert files[0]["filename"] == "pricing_table.md"

    # 4. Read document content
    read_resp = client.get("/api/agents/guru/knowledge/pricing_table.md")
    assert read_resp.status_code == 200
    assert "Plan Pro: 25 EUR" in read_resp.json()["content"]

    # 5. Verify system context injection
    agent = repo.load_agent("guru")
    sys_context = BaseAgentEngine.build_system_context(agent)
    assert "Baza Wiedzy Agenta" in sys_context
    assert "pricing_table.md" in sys_context
    assert "Plan Basic: 10 EUR" in sys_context

    # 6. Delete knowledge document
    del_resp = client.delete("/api/agents/guru/knowledge/pricing_table.md")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # 7. Check 404 after deletion
    del_check = client.get("/api/agents/guru/knowledge/pricing_table.md")
    assert del_check.status_code == 404
