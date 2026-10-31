"""Tests for REST API endpoints and streaming chat guards."""

from pathlib import Path

from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, repo
from beherit.main import app

client = TestClient(app)


def test_health_endpoint():
    """Verify health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


def test_root_index_html():
    """Verify that root index serves the web UI including the comprehensive user manual."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Beherit" in response.text
    assert "Instrukcja Obsługi i Przewodnik Beherit" in response.text
    assert "Jak Podłączyć Agenta" in response.text
    assert "Słownik Funkcji" in response.text
    assert "Szybki Start" in response.text


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


def test_chat_sessions_crud_and_persistence(tmp_path: Path, monkeypatch):
    """Verify BEH-015: Multi-turn chat sessions and SQLite message history."""
    from beherit.api.routes import db_mgr

    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)
    monkeypatch.setattr(db_mgr, "db_path", tmp_path / "test.db")
    db_mgr._init_db()

    # Create agent
    client.post(
        "/api/agents",
        json={
            "name": "Chat Bot",
            "slug": "chat-bot",
            "engine": "antigravity",
            "master_prompt": "You are a friendly bot.",
        },
    )
    auth_mgr.save_manual_token("chat-bot", "test_tok")

    # 1. Create session
    create_sess = client.post("/api/agents/chat-bot/chats", json={"title": "Session 1"})
    assert create_sess.status_code == 201
    sid = create_sess.json()["session_id"]
    assert sid.startswith("sess_")

    # 2. List sessions
    list_sess = client.get("/api/agents/chat-bot/chats")
    assert list_sess.status_code == 200
    assert len(list_sess.json()["sessions"]) == 1

    # 3. Stream chat message with session_id
    chat_resp = client.post(
        "/api/agents/chat-bot/chat",
        json={"message": "First message", "session_id": sid},
    )
    assert chat_resp.status_code == 200

    # 4. Verify message recorded in SQLite
    msgs_resp = client.get(f"/api/agents/chat-bot/chats/{sid}")
    assert msgs_resp.status_code == 200
    messages = msgs_resp.json()["messages"]
    assert len(messages) >= 1
    assert any(m["role"] == "user" and m["content"] == "First message" for m in messages)

    # 5. Delete session
    del_resp = client.delete(f"/api/agents/chat-bot/chats/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["success"] is True


def test_execute_tool_api(tmp_path: Path, monkeypatch):
    """Verify BEH-016: Tool execution endpoint."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)

    client.post(
        "/api/agents",
        json={
            "name": "Tool Guy",
            "slug": "tool-guy",
            "master_prompt": "Runs tools.",
        },
    )

    tool_resp = client.post(
        "/api/agents/tool-guy/tools/execute",
        json={"tool": "list_dir", "args": {"path": "."}},
    )
    assert tool_resp.status_code == 200
    assert tool_resp.json()["status"] == "success"
    assert "items" in tool_resp.json()["result"]


def test_update_and_delete_agent(tmp_path: Path, monkeypatch):
    """Verify BEH-019: Agent update and deletion endpoints."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Create agent
    client.post(
        "/api/agents",
        json={
            "name": "Old Name",
            "slug": "update-test",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Initial prompt",
        },
    )

    # 2. Update agent
    update_resp = client.put(
        "/api/agents/update-test",
        json={
            "name": "New Name",
            "description": "Updated description",
            "engine": "codex",
            "model": "gpt-4o",
            "capabilities": {
                "terminal": True,
                "terminal_mode": "unrestricted",
            },
            "schedule": {
                "enabled": True,
                "cron": "0 12 * * *",
                "prompt": "Run daily report",
            },
        },
    )
    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["name"] == "New Name"
    assert data["engine"] == "codex"
    assert data["model"] == "gpt-4o"
    assert data["capabilities"]["terminal_mode"] == "unrestricted"
    assert data["schedule"]["enabled"] is True
    assert data["schedule"]["cron"] == "0 12 * * *"

    # 3. Delete agent
    del_resp = client.delete("/api/agents/update-test")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"

    # 4. Verify 404 after deletion
    get_resp = client.get("/api/agents/update-test")
    assert get_resp.status_code == 404


def test_reports_view_api(tmp_path: Path):
    """Verify BEH-020: Secure markdown report viewer endpoint."""
    report_file = tmp_path / "sample_report.md"
    report_file.write_text("# Test Report\n\nAll tasks completed successfully.", encoding="utf-8")

    # 1. View valid report
    resp = client.get(f"/api/reports/view?path={report_file}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["filename"] == "sample_report.md"
    assert "# Test Report" in data["content"]

    # 2. View non-existent file
    resp404 = client.get(f"/api/reports/view?path={tmp_path / 'ghost.md'}")
    assert resp404.status_code == 404

    # 3. View invalid extension
    txt_file = tmp_path / "something.py"
    txt_file.write_text("print('hello')", encoding="utf-8")
    resp_bad = client.get(f"/api/reports/view?path={txt_file}")
    assert resp_bad.status_code == 400

    # 4. Security check on forbidden path prefix
    resp_sec = client.get("/api/reports/view?path=/etc/shadow.md")
    assert resp_sec.status_code in (403, 404)


def test_logo_and_brand_assets_endpoints():
    """Verify BEH-LOGO: Brand icon, favicon, and manifest endpoints for UI and Homelab dashboards."""
    # 1. Favicon endpoint
    fav_resp = client.get("/favicon.ico")
    assert fav_resp.status_code == 200
    assert "image" in fav_resp.headers.get("content-type", "")

    # 2. Logo / icon endpoint
    logo_resp = client.get("/logo.png")
    assert logo_resp.status_code == 200
    assert logo_resp.headers.get("content-type") == "image/png"

    icon_resp = client.get("/icon.png")
    assert icon_resp.status_code == 200
    assert icon_resp.headers.get("content-type") == "image/png"

    # 3. PWA manifest endpoint
    manifest_resp = client.get("/manifest.json")
    assert manifest_resp.status_code == 200
    assert "manifest" in manifest_resp.headers.get("content-type", "")
    manifest_data = manifest_resp.json()
    assert "Beherit" in manifest_data["name"]
    assert len(manifest_data["icons"]) > 0

    # 4. Web UI contains brand references
    index_resp = client.get("/")
    assert index_resp.status_code == 200
    assert "/static/icon.png" in index_resp.text


def test_create_agent_with_initial_key(tmp_path: Path, monkeypatch):
    """Verify creating an agent with initial API or SDK key automatically connects it."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Create agent with direct API key
    res_api = client.post(
        "/api/agents",
        json={
            "name": "Market Analyst",
            "slug": "market-analyst",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Analyze stocks.",
            "auth_type": "api",
            "auth_token": "AIzaSyTestApiKey1234567890abcdef",
        },
    )
    assert res_api.status_code == 201
    agent_data = res_api.json()["agent"]
    assert agent_data["auth"]["status"] == "authenticated"

    status_resp = client.get("/api/agents/market-analyst/auth/status")
    assert status_resp.status_code == 200
    details = status_resp.json()
    assert details["authenticated"] is True
    assert details["auth_type"] == "api"
    assert "AIzaSy" in details["masked_token"]

    # 2. Create agent with SDK token
    res_sdk = client.post(
        "/api/agents",
        json={
            "name": "Claude Agent",
            "slug": "claude-agent",
            "engine": "claude-code",
            "model": "claude-3-7-sonnet-20250219",
            "master_prompt": "Code helper.",
            "auth_type": "sdk",
            "auth_token": "sk-ant-sid01-test-sdk-session-token-9999",
        },
    )
    assert res_sdk.status_code == 201
    sdk_agent_data = res_sdk.json()["agent"]
    assert sdk_agent_data["auth"]["status"] == "authenticated"

    sdk_status = client.get("/api/agents/claude-agent/auth/status").json()
    assert sdk_status["authenticated"] is True
    assert sdk_status["auth_type"] == "sdk"


def test_global_local_detect_endpoint():
    """Verify global local CLI credentials detection without requiring agent slug."""
    resp = client.get("/api/agents/auth/local-detect")
    assert resp.status_code == 200
    data = resp.json()
    assert "antigravity" in data
    assert "claude_code" in data
    assert "claude-code" in data
    assert "codex" in data


def test_create_agent_with_import_local_auth(tmp_path: Path, monkeypatch):
    """Verify creating an agent with import_local_auth automatically attempts server import."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # Mock import_local_cli_session
    called = {}

    def mock_import(agent_slug, provider):
        called["slug"] = agent_slug
        called["provider"] = provider
        auth_mgr.save_manual_token(agent_slug, "imported_host_token", provider=provider)

    monkeypatch.setattr(auth_mgr, "import_local_cli_session", mock_import)

    res = client.post(
        "/api/agents",
        json={
            "name": "Auto Host Agent",
            "slug": "auto-host-agent",
            "engine": "antigravity",
            "import_local_auth": True,
        },
    )
    assert res.status_code == 201
    assert called.get("slug") == "auto-host-agent"
    status_resp = client.get("/api/agents/auto-host-agent/auth/status").json()
    assert status_resp["authenticated"] is True




