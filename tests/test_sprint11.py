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


def test_dynamic_models_available_endpoint():
    """Test dynamic models listing endpoint GET /api/agents/models/available (BEH-043)."""
    resp_gemini = client.get("/api/agents/models/available?engine=antigravity")
    assert resp_gemini.status_code == 200
    data_g = resp_gemini.json()
    assert data_g["engine"] == "antigravity"
    assert len(data_g["models"]) > 0
    assert any("gemini" in m["id"] for m in data_g["models"])

    resp_claude = client.get("/api/agents/models/available?engine=claude-code")
    assert resp_claude.status_code == 200
    data_c = resp_claude.json()
    assert any("claude" in m["id"] for m in data_c["models"])

    resp_codex = client.get("/api/agents/models/available?engine=codex")
    assert resp_codex.status_code == 200
    data_o = resp_codex.json()
    assert any("gpt" in m["id"] or "o" in m["id"] for m in data_o["models"])


def test_mcp_servers_configuration(tmp_path: Path, monkeypatch):
    """Test creating and updating agent with MCP servers (BEH-044)."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "mcp-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "MCP Tester",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "mcp_servers": {
                "brave-search": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-brave-search"],
                    "env": {"BRAVE_API_KEY": "test_key"},
                },
                "postgres": {
                    "command": "npx",
                    "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/test"],
                },
            },
        },
    )
    assert create_resp.status_code == 201
    agent = repo.load_agent(slug)
    assert agent is not None
    assert "brave-search" in agent.mcp_servers
    assert "postgres" in agent.mcp_servers
    assert agent.mcp_servers["brave-search"].command == "npx"


def test_multimodal_reports_and_artifacts(tmp_path: Path, monkeypatch):
    """Test multimodal artifacts discovery, report generation, and streaming (BEH-047)."""
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    slug = "multimodal-agent"
    create_resp = client.post(
        "/api/agents",
        json={
            "name": "Multimodal Agent",
            "slug": slug,
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
        },
    )
    assert create_resp.status_code == 201

    agent = repo.load_agent(slug)
    agent_dir = Path(agent.file_path).parent

    # Create dummy artifact file (e.g. generated image and video)
    art_dir = agent_dir / "artifacts"
    art_dir.mkdir(parents=True, exist_ok=True)
    sample_img = art_dir / "chart.png"
    sample_img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRtest")

    # Run agent turn which produces report
    run_resp = client.post(
        f"/api/agents/{slug}/run",
        json={
            "prompt": "Wygeneruj wykres i zapisz w artifacts/chart.png",
            "save_report": True,
        },
    )
    assert run_resp.status_code == 200
    run_data = run_resp.json()
    assert run_data["status"] == "success"
    assert "artifacts" in run_data
    assert any(a["name"] == "chart.png" for a in run_data["artifacts"])

    # Verify report file contains artifact reference
    report_path = run_data["report_path"]
    assert report_path is not None
    report_text = Path(report_path).read_text(encoding="utf-8")
    assert "Wygenerowane Multimedia i Artefakty" in report_text
    assert "chart.png" in report_text

    # Verify streaming artifact via /api/reports/artifact
    stream_resp = client.get(f"/api/reports/artifact?path={sample_img.resolve()}")
    assert stream_resp.status_code == 200
    assert stream_resp.headers["content-type"] == "image/png"
    assert len(stream_resp.content) > 0

    # Test forbidden path
    forbidden_resp = client.get("/api/reports/artifact?path=/etc/passwd")
    assert forbidden_resp.status_code == 403
