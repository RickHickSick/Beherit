"""Autonomous Task Scheduler with APScheduler and Multi-Channel Dispatch."""

import asyncio
import time
from typing import Any, Dict, List, Optional
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from beherit.core.database import DatabaseManager
from beherit.core.reporter import ReportDispatcher
from beherit.core.repository import AgentRepository
from beherit.engines import EngineRegistry
from beherit.models.agent import AgentModel


class AgentScheduler:
    """Orchestrates periodic background agent runs and reports outcomes."""

    def __init__(
        self,
        repository: Optional[AgentRepository] = None,
        db_manager: Optional[DatabaseManager] = None,
        engine_registry: Optional[EngineRegistry] = None,
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
            if agent.schedule.enabled and agent.schedule.cron:
                self.register_agent_cron(agent)

    def register_agent_cron(self, agent: AgentModel) -> bool:
        """Register a single agent cron trigger."""
        if not agent.schedule.cron:
            return False

        try:
            trigger = CronTrigger.from_crontab(agent.schedule.cron)
            self.scheduler.add_job(
                func=self.execute_agent_job,
                trigger=trigger,
                args=[agent.slug],
                id=f"job_{agent.slug}",
                replace_existing=True,
                name=f"Cron for {agent.name}",
            )
            return True
        except Exception:
            return False

    async def execute_agent_job(self, agent_slug: str) -> Dict[str, Any]:
        """Execute a scheduled autonomous turn for an agent."""
        agent = self.repo.load_agent(agent_slug)
        if not agent:
            return {"status": "failed", "error": f"Agent {agent_slug} not found"}

        start_time = time.time()
        prompt = agent.schedule.prompt or "Wykonaj zaplanowany audyt i wygeneruj raport ze swoich zadań."

        output_tokens: List[str] = []
        error_msg: Optional[str] = None
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

        # Multi-channel reporting dispatch
        reporting_results = await self.reporter.dispatch(
            agent=agent,
            status=status,
            duration_seconds=duration,
            output_text=full_output,
            error_message=error_msg,
        )

        return {
            "status": status,
            "agent_slug": agent_slug,
            "duration": duration,
            "reporting": reporting_results,
            "summary": full_output[:300],
        }
