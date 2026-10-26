"""End-to-End Integration Test: n8n Workflow <-> Beherit Agent (BEH-050).

Simulates bidirectional external orchestration:
1. Inbound trigger from n8n into Beherit agent webhook (POST /api/agents/{slug}/run).
2. Agent processes context payload and executes autonomous task.
3. Agent writes output file into destination_file (Obsidian vault).
4. System automatically registers report and multimodal artifacts in SQLite + Markdown.
5. System notifies outbound callback_url (n8n callback webhook).
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, db_mgr, repo, scheduler
from beherit.main import app

client = TestClient(app)


def test_n8n_bidirectional_e2e_flow(tmp_path: Path, monkeypatch):
    """Verify complete bidirectional n8n workflow interaction with file creation and reporting."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "n8n-e2e-agent"

    # Step 1: Create dedicated agent for n8n workflow
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "n8n Pipeline Processor",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Jesteś agentem integracyjnym przetwarzającym zamówienia z n8n.",
            "instructions": "Generuj podsumowania transakcji w formacie Markdown z tabelami.",
        },
    )
    assert create_resp.status_code == 201
    agent_data = create_resp.json()
    assert agent_data["slug"] == slug

    # Mock engine stream_chat to test flow predictably and swiftly
    mock_engine = scheduler.engine_registry.get_engine("antigravity")

    async def mock_stream_chat(agent, message, history=None):
        yield {"event": "thought", "data": "Przetwarzanie payloadu z n8n..."}
        yield {
            "event": "token",
            "data": (
                "# Raport Zamówienia n8n\n\n"
                "| ID Zamówienia | Klient | Kwota | Status |\n"
                "|---------------|--------|-------|--------|\n"
                "| ORD-9999 | Jan Kowalski | 2500 PLN | Opłacone |\n\n"
                "Pomyślnie zweryfikowano płatność w systemie n8n."
            ),
        }
        yield {"event": "done", "data": {"completed": True}}

    monkeypatch.setattr(mock_engine, "stream_chat", mock_stream_chat)

    # Step 2: Simulate n8n calling Beherit webhook with context and callback_url
    dest_file = tmp_path / "Notatki" / "Zamowienia" / "Raport_ORD-9999.md"
    n8n_input_context = {
        "n8n_workflow_id": "ECOMMorderSync01",
        "order_id": "ORD-9999",
        "customer": "Jan Kowalski",
        "amount": 2500,
        "currency": "PLN",
        "source": "n8n-webhook",
    }

    # Track callback requests
    captured_callbacks = []

    async def mock_post(url, json=None, **kwargs):
        captured_callbacks.append({"url": url, "payload": json})
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        run_resp = client.post(
            f"/api/agents/{slug}/run",
            json={
                "prompt": "Przeanalizuj i zapisz podsumowanie nowego zamówienia",
                "context": n8n_input_context,
                "destination_file": str(dest_file),
                "file_mode": "overwrite",
                "save_report": True,
                "callback_url": "http://192.168.1.60:8443/webhook/test-hermes-pipeline",
            },
        )

    # Step 3: Assert response
    assert run_resp.status_code == 200
    res_data = run_resp.json()
    assert res_data["status"] == "success"
    assert res_data["agent_slug"] == slug
    assert res_data["callback_sent"] is True

    # Step 4: Verify file creation in destination
    assert dest_file.exists()
    dest_content = dest_file.read_text(encoding="utf-8")
    assert "ORD-9999" in dest_content
    assert "Jan Kowalski" in dest_content

    # Step 5: Verify SQLite job history record
    history = db_mgr.get_agent_history(slug)
    assert len(history) >= 1
    latest_job = history[0]
    assert latest_job["status"] == "success"
    assert latest_job["report_path"] is not None
    assert Path(latest_job["report_path"]).exists()

    # Step 6: Verify outbound callback payload sent back to n8n
    assert len(captured_callbacks) == 1
    cb = captured_callbacks[0]
    assert cb["url"] == "http://192.168.1.60:8443/webhook/test-hermes-pipeline"
    cb_payload = cb["payload"]
    assert cb_payload["agent_slug"] == slug
    assert cb_payload["status"] == "success"
    assert cb_payload["saved_destination"] == str(dest_file.resolve())
    assert cb_payload["report_file"] == latest_job["report_path"]
    assert "artifacts" in cb_payload
