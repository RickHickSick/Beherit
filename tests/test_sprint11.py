"""Tests for Sprint 11 (BEH-045 Multi-Cron, BEH-046 Split Run, BEH-048 Webhook API, BEH-049 n8n Integration)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, db_mgr, repo, scheduler
from beherit.main import app
from beherit.models.agent import AgentModel, AgentSchedule, ScheduledJob

client = TestClient(app)


def test_agent_run_webhook_endpoint(tmp_path: Path, monkeypatch):
    """Test POST /api/agents/{slug}/run universal webhook for external callers (e.g. n8n - BEH-048)."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "n8n-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "n8n Integration Agent",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "Jesteś asystentem analitycznym.",
        },
    )
    assert create_resp.status_code == 201

    dest_file = tmp_path / "Output" / "result.md"

    # 1. Test synchronous run with context and destination_file
    run_resp = client.post(
        f"/api/agents/{slug}/run",
        json={
            "prompt": "Przeanalizuj zgłoszenie",
            "context": {"user_id": 1234, "issue": "Problem z logowaniem"},
            "destination_file": str(dest_file),
            "file_mode": "overwrite",
            "save_report": True,
        },
    )
    assert run_resp.status_code == 200
    data = run_resp.json()
    assert data["status"] in ["success", "failed"]
    assert data["agent_slug"] == slug
    assert "output" in data
    assert dest_file.exists()
    assert len(dest_file.read_text(encoding="utf-8")) > 0

    # 2. Test async run
    async_resp = client.post(
        f"/api/agents/{slug}/run",
        json={
            "prompt": "Zadanie w tle",
            "async_execution": True,
        },
    )
    assert async_resp.status_code == 200
    assert async_resp.json()["async"] is True


def test_multi_cron_scheduling(tmp_path: Path, monkeypatch):
    """Test Multi-Cron configuration and execution by job_id (BEH-045, BEH-046)."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "multi-cron-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "Multi-Cron Agent",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "schedule": {
                "enabled": True,
                "cron": "0 8 * * *",
                "prompt": "Domyślny poranny audyt",
                "jobs": [
                    {
                        "id": "job_morning",
                        "name": "Poranny raport",
                        "cron": "0 8 * * *",
                        "prompt": "Wykonaj poranny raport rynkowy",
                        "enabled": True,
                    },
                    {
                        "id": "job_evening",
                        "name": "Wieczorne podsumowanie",
                        "cron": "0 20 * * *",
                        "prompt": "Podsumuj dzień i wyślij alerty",
                        "enabled": True,
                    },
                ],
            },
        },
    )
    assert create_resp.status_code == 201
    agent = repo.load_agent(slug)
    assert len(agent.schedule.jobs) == 2
    assert agent.schedule.jobs[0].id == "job_morning"

    # Test trigger specific job by job_id (BEH-046)
    run_job_resp = client.post(f"/api/agents/{slug}/run-now?job_id=job_evening")
    assert run_job_resp.status_code == 200
    assert run_job_resp.json()["agent_slug"] == slug
