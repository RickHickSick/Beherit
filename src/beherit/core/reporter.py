"""Multi-Channel Reporting Dispatcher (SQLite, Markdown, Telegram)."""

import datetime
from pathlib import Path
from typing import Any

import httpx

from beherit.config import settings
from beherit.core.database import DatabaseManager
from beherit.models.agent import AgentModel


class ReportDispatcher:
    """Dispatches job outcomes to SQL, Markdown reports, and Telegram alerts."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager()

    async def dispatch(
        self,
        agent: AgentModel,
        status: str,
        duration_seconds: float,
        output_text: str,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Dispatch reporting according to agent.schedule.reporting settings."""
        reporting = agent.schedule.reporting
        results: dict[str, Any] = {
            "sql_saved": False,
            "markdown_path": None,
            "telegram_sent": False,
        }

        # 1. Markdown Report Generation (e.g. in Obsidian folder)
        report_path = None
        if reporting.markdown_report and agent.file_path:
            agent_dir = Path(agent.file_path).parent
            reports_dir = agent_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)

            timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            report_file = reports_dir / f"report_{timestamp_str}.md"

            content = f"""# Raport Autonomiczny Agenta: {agent.name}

* **Data wykonania:** {datetime.datetime.now().isoformat()}
* **Status:** {'✅ Sukces' if status == 'success' else '❌ Błąd'}
* **Czas wykonania:** {duration_seconds:.2f} s
* **Model silnika:** `{agent.model}`

---

## Wyniki / Podsumowanie Zadań
{output_text}
"""
            if error_message:
                content += f"\n\n## ⚠️ Błędy\n```text\n{error_message}\n```\n"

            report_file.write_text(content, encoding="utf-8")
            report_path = str(report_file.resolve())
            results["markdown_path"] = report_path

        # 2. SQLite Database Record
        if reporting.sql_history:
            job_id = self.db.record_job(
                agent_slug=agent.slug,
                status=status,
                duration_seconds=duration_seconds,
                summary=output_text[:500] if output_text else None,
                error_message=error_message,
                report_path=report_path,
            )
            results["sql_saved"] = True
            results["job_id"] = job_id

        # 3. Telegram Alert
        if reporting.telegram_alert:
            should_send = True
            if reporting.telegram_on_error_only and status == "success":
                should_send = False

            if should_send and settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID:
                try:
                    await self._send_telegram(
                        token=settings.TELEGRAM_BOT_TOKEN,
                        chat_id=settings.TELEGRAM_CHAT_ID,
                        agent_name=agent.name,
                        status=status,
                        output_text=output_text,
                        error=error_message,
                    )
                    results["telegram_sent"] = True
                except Exception as e:
                    results["telegram_error"] = str(e)

        return results

    async def _send_telegram(
        self,
        token: str,
        chat_id: str,
        agent_name: str,
        status: str,
        output_text: str,
        error: str | None = None,
    ) -> None:
        """Send formatted message via Telegram Bot API."""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        icon = "✅" if status == "success" else "🚨"
        text = f"{icon} *Beherit Agent:* `{agent_name}`\n*Status:* {status}\n\n"
        if error:
            text += f"⚠️ *Błąd:* `{error}`\n\n"
        text += f"📝 *Podsumowanie:* {output_text[:400]}"

        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})
