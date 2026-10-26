"""Autonomous Task Scheduler with APScheduler and Multi-Channel Dispatch."""

import datetime
import logging
from pathlib import Path
import time
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from beherit.core.database import DatabaseManager
from beherit.core.reporter import ReportDispatcher
from beherit.core.repository import AgentRepository
from beherit.engines import EngineRegistry
from beherit.models.agent import AgentModel

logger = logging.getLogger(__name__)


class AgentScheduler:
    """Orchestrates periodic background agent runs and reports outcomes."""

    def __init__(
        self,
        repository: AgentRepository | None = None,
        db_manager: DatabaseManager | None = None,
        engine_registry: EngineRegistry | None = None,
    ):
        self.repo = repository or AgentRepository()
        self.db = db_manager or DatabaseManager()
        self.engine_registry = engine_registry or EngineRegistry()
        self.reporter = ReportDispatcher(db_manager=self.db)
        self.scheduler = AsyncIOScheduler()
        self._running = False

    def start(self) -> None:
        """Start the background scheduler."""
        if not self._running:
            self.scheduler.start()
            self._running = True
            self.sync_all_jobs()

    def shutdown(self) -> None:
        """Stop the scheduler."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False

    def sync_all_jobs(self) -> None:
        """Synchronize all agent cron schedules from the repository."""
        self.scheduler.remove_all_jobs()
        agents = self.repo.list_agents()
        for agent in agents:
            if agent.schedule.enabled:
                self.register_agent_cron(agent)

    def register_agent_cron(self, agent: AgentModel) -> bool:
        """Register agent cron triggers (supports single cron or multi-cron jobs - BEH-045)."""
        registered_any = False

        # 1. Multi-Cron jobs list (BEH-045)
        if agent.schedule.jobs:
            for job in agent.schedule.jobs:
                if not job.enabled or not job.cron:
                    continue
                try:
                    trigger = CronTrigger.from_crontab(job.cron)
                    self.scheduler.add_job(
                        func=self.execute_agent_job,
                        trigger=trigger,
                        args=[agent.slug, job.prompt, job.destination_file],
                        id=f"job_{agent.slug}_{job.id}",
                        replace_existing=True,
                        name=f"Cron: {agent.name} - {job.name}",
                    )
                    registered_any = True
                except Exception as e:
                    logger.warning(f"Failed to register cron job {job.id} for {agent.slug}: {e}")

        # 2. Top-level single cron if no jobs or in addition
        if not registered_any and agent.schedule.cron:
            try:
                trigger = CronTrigger.from_crontab(agent.schedule.cron)
                self.scheduler.add_job(
                    func=self.execute_agent_job,
                    trigger=trigger,
                    args=[agent.slug, agent.schedule.prompt],
                    id=f"job_{agent.slug}",
                    replace_existing=True,
                    name=f"Cron for {agent.name}",
                )
                registered_any = True
            except Exception as e:
                logger.warning(f"Failed to register default cron for {agent.slug}: {e}")

        return registered_any

    async def execute_agent_job(
        self,
        agent_slug: str,
        custom_prompt: str | None = None,
        destination_file: str | None = None,
        file_mode: str = "overwrite",
        save_report: bool = True,
        callback_url: str | None = None,
        job_source: str = "scheduler",
    ) -> dict[str, Any]:
        """Execute a scheduled autonomous turn or webhook run for an agent (BEH-048)."""
        agent = self.repo.load_agent(agent_slug)
        if not agent:
            return {"status": "failed", "error": f"Agent {agent_slug} not found"}

        start_time = time.time()
        prompt = custom_prompt or agent.schedule.prompt or "Wykonaj zaplanowany audyt i wygeneruj raport ze swoich zadań."

        output_tokens: list[str] = []
        error_msg: str | None = None
        status = "success"

        engine = self.engine_registry.get_engine(agent.engine)
        try:
            async for chunk in engine.stream_chat(agent, prompt):
                event = chunk.get("event")
                data = chunk.get("data")
                if event == "token":
                    output_tokens.append(data if isinstance(data, str) else str(data))
                elif event == "error":
                    status = "failed"
                    error_msg = str(data)
                    break

        except Exception as e:
            status = "failed"
            error_msg = str(e)

        duration = time.time() - start_time
        full_output = "".join(output_tokens).strip() or (error_msg or "Brak danych wyjściowych.")

        # Save to destination file if specified (BEH-048)
        saved_destination = None
        if destination_file:
            try:
                agent_dir = Path(agent.file_path).parent if agent.file_path else (self.repo.base_dir / agent_slug)
                dest_path = Path(destination_file)
                if not dest_path.is_absolute():
                    candidate_vault = self.repo.base_dir / destination_file
                    if candidate_vault.parent.exists():
                        dest_path = candidate_vault
                    else:
                        dest_path = agent_dir / destination_file

                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if file_mode == "append" and dest_path.exists():
                    existing = dest_path.read_text(encoding="utf-8", errors="replace")
                    dest_path.write_text(existing + "\n\n" + full_output, encoding="utf-8")
                else:
                    dest_path.write_text(full_output, encoding="utf-8")
                saved_destination = str(dest_path.resolve())
            except Exception as e:
                logger.warning(f"Failed to write destination_file {destination_file}: {e}")

        # Multi-channel reporting dispatch
        reporting_results: dict[str, Any] = {}
        if save_report:
            reporting_results = await self.reporter.dispatch(
                agent=agent,
                status=status,
                duration_seconds=duration,
                output_text=full_output,
                error_message=error_msg,
            )

        # Notify outbound callback webhook if specified (e.g. n8n webhook)
        callback_sent = False
        if callback_url:
            try:
                import httpx
                cb_payload = {
                    "status": status,
                    "agent_slug": agent_slug,
                    "duration_seconds": round(duration, 2),
                    "output": full_output,
                    "error": error_msg,
                    "saved_destination": saved_destination,
                    "report_file": reporting_results.get("markdown_path"),
                    "job_source": job_source,
                    "timestamp": datetime.datetime.now().isoformat(),
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    cb_resp = await client.post(callback_url, json=cb_payload)
                    callback_sent = (200 <= cb_resp.status_code < 300)
            except Exception as e:
                logger.warning(f"Error dispatching callback_url {callback_url}: {e}")

        return {
            "status": status,
            "agent_slug": agent_slug,
            "duration": round(duration, 2),
            "output": full_output,
            "error": error_msg,
            "saved_destination": saved_destination,
            "report_path": reporting_results.get("markdown_path") if save_report else None,
            "reporting": reporting_results,
            "callback_sent": callback_sent,
            "summary": full_output[:300],
        }
