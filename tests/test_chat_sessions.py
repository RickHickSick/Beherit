"""Tests for Chat Session Management (BEH-051: deletion and renaming)."""

from pathlib import Path
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, db_mgr, repo
from beherit.main import app

client = TestClient(app)


def test_chat_session_lifecycle_and_management(tmp_path: Path, monkeypatch):
    """Verify BEH-051: create, list, rename (PATCH), and delete (DELETE) chat sessions."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "session-test-agent"
    create_agent = client.post(
        "/api/agents",
        json={
            "name": "Session Test Agent",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Jesteś asystentem testowym.",
        },
    )
    assert create_agent.status_code == 201

    # 1. Create a session with a title
    create_sess = client.post(
        f"/api/agents/{slug}/chats",
        json={"title": "Początkowy Tytuł"},
    )
    assert create_sess.status_code == 201
    sid = create_sess.json()["session_id"]
    assert sid.startswith(f"sess_{slug}_")

    # 2. Add message directly to SQLite
    db_mgr.add_chat_message(
        session_id=sid,
        agent_slug=slug,
        role="user",
        content="Cześć agencie!",
    )
    db_mgr.add_chat_message(
        session_id=sid,
        agent_slug=slug,
        role="assistant",
        content="Dzień dobry!",
        thoughts=["Inicjalizacja wątku"],
    )

    # 3. Verify messages are retrieved
    msgs_resp = client.get(f"/api/agents/{slug}/chats/{sid}")
    assert msgs_resp.status_code == 200
    messages = msgs_resp.json()["messages"]
    assert len(messages) == 2
    assert messages[0]["content"] == "Cześć agencie!"
    assert messages[1]["thoughts"] == ["Inicjalizacja wątku"]

    # 4. Verify session in list
    list_resp = client.get(f"/api/agents/{slug}/chats")
    assert list_resp.status_code == 200
    sessions = list_resp.json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["id"] == sid
    assert sessions[0]["title"] == "Początkowy Tytuł"

    # 5. Rename session via PATCH
    patch_resp = client.patch(
        f"/api/agents/{slug}/chats/{sid}",
        json={"title": "Nowy Lepszy Tytuł"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["status"] == "updated"
    assert patch_resp.json()["title"] == "Nowy Lepszy Tytuł"

    # Verify updated title in list
    list_after_patch = client.get(f"/api/agents/{slug}/chats")
    assert list_after_patch.json()["sessions"][0]["title"] == "Nowy Lepszy Tytuł"

    # 6. Verify error cases for PATCH
    # Non-existent session
    err_404 = client.patch(
        f"/api/agents/{slug}/chats/non_existent_session",
        json={"title": "Nowy"},
    )
    assert err_404.status_code == 404

    # Non-existent agent
    err_agent_404 = client.patch(
        "/api/agents/unknown-agent/chats/sess_123",
        json={"title": "Nowy"},
    )
    assert err_agent_404.status_code == 404

    # Empty title
    err_empty = client.patch(
        f"/api/agents/{slug}/chats/{sid}",
        json={"title": "   "},
    )
    assert err_empty.status_code == 422

    # 7. Delete session via DELETE
    del_resp = client.delete(f"/api/agents/{slug}/chats/{sid}")
    assert del_resp.status_code == 200
    assert del_resp.json()["status"] == "deleted"
    assert del_resp.json()["success"] is True

    # Verify session is gone from list
    list_after_del = client.get(f"/api/agents/{slug}/chats")
    assert len(list_after_del.json()["sessions"]) == 0

    # Verify messages are gone
    msgs_after_del = client.get(f"/api/agents/{slug}/chats/{sid}")
    assert len(msgs_after_del.json()["messages"]) == 0
