"""Tests for Autonomous Background Scheduler and Multi-Channel Reporting."""

from pathlib import Path

import pytest

from beherit.core.database import DatabaseManager
from beherit.core.reporter import ReportDispatcher
from beherit.core.repository import AgentRepository
from beherit.core.scheduler import AgentScheduler
from beherit.models.agent import AgentModel, AgentSchedule, ScheduleReporting


def test_database_manager(tmp_path: Path):
    """Verify recording and querying SQLite job history."""
    db = DatabaseManager(db_path=tmp_path / "test.db")
    slug = "analyst-agent"

    job_id = db.record_job(
        agent_slug=slug,
        status="success",
        duration_seconds=1.45,
        summary="All market tables validated with 0 anomalies.",
        report_path="/path/to/report.md",
    )
    assert job_id == 1

    history = db.get_agent_history(slug)
    assert len(history) == 1
    assert history[0]["status"] == "success"
    assert history[0]["duration_seconds"] == 1.45
    assert "0 anomalies" in history[0]["summary"]


@pytest.mark.asyncio
async def test_multi_channel_reporter(tmp_path: Path):
    """Verify Markdown report creation in agent directory and SQLite recording."""
    db = DatabaseManager(db_path=tmp_path / "test.db")
    dispatcher = ReportDispatcher(db_manager=db)

    agent_dir = tmp_path / "my-agent"
    agent_dir.mkdir(parents=True)
    agent_file = agent_dir / "agent.md"
    agent_file.write_text("# Test Agent", encoding="utf-8")

    agent = AgentModel(
        name="Reporter Agent",
        slug="my-agent",
        file_path=str(agent_file),
        schedule=AgentSchedule(
            enabled=True,
            cron="0 7 * * *",
            reporting=ScheduleReporting(sql_history=True, markdown_report=True, telegram_alert=False),
        ),
    )

    result = await dispatcher.dispatch(
        agent=agent,
        status="success",
        duration_seconds=2.1,
        output_text="Successfully inspected 1200 rows.",
    )

    assert result["sql_saved"] is True
    assert result["markdown_path"] is not None

    # Verify markdown file exists on disk
    report_file = Path(result["markdown_path"])
    assert report_file.exists()
    assert "Reporter Agent" in report_file.read_text(encoding="utf-8")
    assert "1200 rows" in report_file.read_text(encoding="utf-8")


def test_scheduler_sync_jobs(tmp_path: Path):
    """Verify scheduler registers cron jobs from repository."""
    repo = AgentRepository(base_dir=tmp_path)
    db = DatabaseManager(db_path=tmp_path / "test.db")
    scheduler = AgentScheduler(repository=repo, db_manager=db)

    agent = AgentModel(
        name="Scheduled Agent",
        slug="scheduled-agent",
        schedule=AgentSchedule(enabled=True, cron="0 8 * * *"),
    )
    repo.save_agent(agent)

    scheduler.sync_all_jobs()
    jobs = scheduler.scheduler.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "job_scheduled-agent"
