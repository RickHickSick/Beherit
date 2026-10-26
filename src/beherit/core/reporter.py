"""Multi-Channel Reporting Dispatcher (SQLite, Markdown, Telegram, Multimodal)."""

import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from beherit.config import settings
from beherit.core.database import DatabaseManager
from beherit.models.agent import AgentModel


class ReportDispatcher:
    """Dispatches job outcomes to SQL, Markdown reports, and Telegram alerts with multimodal artifact support (BEH-047)."""

    def __init__(self, db_manager: DatabaseManager | None = None):
        self.db = db_manager or DatabaseManager()

    def discover_artifacts(
        self,
        agent: AgentModel,
        start_time: float | None = None,
        explicit_artifacts: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Auto-detect generated artifacts in agent's workspace or artifact directories."""
        discovered: list[dict[str, Any]] = list(explicit_artifacts or [])
        known_paths = {a.get("path") for a in discovered if a.get("path")}

        if not agent.file_path:
            return discovered

        agent_dir = Path(agent.file_path).parent
        candidate_dirs = [agent_dir / "artifacts", agent_dir / "output", agent_dir / "media"]

        cutoff_time = (start_time - 5.0) if start_time else (datetime.datetime.now().timestamp() - 600.0)

        for c_dir in candidate_dirs:
            if not c_dir.is_dir():
                continue
            for file_path in c_dir.glob("**/*"):
                if not file_path.is_file() or file_path.name.startswith("."):
                    continue
                resolved = str(file_path.resolve())
                if resolved in known_paths:
                    continue

                try:
                    mtime = file_path.stat().st_mtime
                    if mtime >= cutoff_time:
                        ext = file_path.suffix.lower()
                        media_type = "file"
                        if ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"]:
                            media_type = "image"
                        elif ext in [".mp4", ".webm", ".mkv", ".mov"]:
                            media_type = "video"
                        elif ext in [".mp3", ".wav", ".ogg"]:
                            media_type = "audio"
                        elif ext in [".pdf", ".csv", ".json", ".tsv", ".html"]:
                            media_type = "document"

                        discovered.append({
                            "name": file_path.name,
                            "path": resolved,
                            "type": media_type,
                            "size_bytes": file_path.stat().st_size,
                            "url": f"/api/reports/artifact?path={quote(resolved)}",
                            "created_at": datetime.datetime.fromtimestamp(mtime).isoformat(),
                        })
                        known_paths.add(resolved)
                except Exception:
                    continue

        return discovered

    async def dispatch(
        self,
        agent: AgentModel,
        status: str,
        duration_seconds: float,
        output_text: str,
        error_message: str | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        terminal_logs: list[dict[str, Any]] | None = None,
        start_time: float | None = None,
    ) -> dict[str, Any]:
        """Dispatch reporting according to agent.schedule.reporting settings with multimodal support."""
        reporting = agent.schedule.reporting
        all_artifacts = self.discover_artifacts(agent, start_time=start_time, explicit_artifacts=artifacts)

        results: dict[str, Any] = {
            "sql_saved": False,
            "markdown_path": None,
            "telegram_sent": False,
            "artifacts": all_artifacts,
        }

        # 1. Markdown Report Generation (with Multimodal Artifacts & Bash Logs - BEH-047)
        report_path = None
        if reporting.markdown_report and agent.file_path:
            agent_dir = Path(agent.file_path).parent
            reports_dir = agent_dir / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)

            timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            report_file = reports_dir / f"report_{timestamp_str}.md"

            content_lines = [
                f"# Raport Autonomiczny Agenta: {agent.name}",
                "",
                f"* **Data wykonania:** {datetime.datetime.now().isoformat()}",
                f"* **Status:** {'✅ Sukces' if status == 'success' else '❌ Błąd'}",
                f"* **Czas wykonania:** {duration_seconds:.2f} s",
                f"* **Model silnika:** `{agent.model}`",
                "",
                "---",
                "",
            ]

            # Terminal / Bash actions log
            if terminal_logs:
                content_lines.append("## 💻 Akcje i Polecenia Terminala (Bash)")
                for t_log in terminal_logs:
                    cmd = t_log.get("command", "")
                    out = t_log.get("output", "")
                    code = t_log.get("exit_code", 0)
                    content_lines.append(f"```bash\n$ {cmd}\n# Exit code: {code}\n{out}\n```")
                content_lines.append("")

            # Main Summary
            content_lines.append("## Wyniki / Podsumowanie Zadań")
            content_lines.append(output_text.strip() or "Brak danych wyjściowych.")
            content_lines.append("")

            # Multimodal Artifacts Section
            if all_artifacts:
                content_lines.append("## 🎨 Wygenerowane Multimedia i Artefakty")
                for art in all_artifacts:
                    art_name = art.get("name", "Plik")
                    art_type = art.get("type", "file")
                    art_url = art.get("url", f"/api/reports/artifact?path={quote(art.get('path', ''))}")
                    size_kb = round(art.get("size_bytes", 0) / 1024, 1)

                    if art_type == "image":
                        content_lines.append(f"### 🖼️ Obraz: {art_name}")
                        content_lines.append(f"![{art_name}]({art_url})\n*Rozmiar: {size_kb} KB*\n")
                    elif art_type == "video":
                        content_lines.append(f"### 🎥 Wideo: [{art_name}]({art_url}) ({size_kb} KB)\n")
                    elif art_type == "document":
                        content_lines.append(f"- 📊 **Dokument / Dane:** [{art_name}]({art_url}) ({size_kb} KB)")
                    else:
                        content_lines.append(f"- 📦 **Artefakt:** [{art_name}]({art_url}) ({size_kb} KB)")
                content_lines.append("")

            if error_message:
                content_lines.append(f"## ⚠️ Błędy\n```text\n{error_message}\n```\n")

            full_content = "\n".join(content_lines)
            report_file.write_text(full_content, encoding="utf-8")
            report_path = str(report_file.resolve())
            results["markdown_path"] = report_path

        # 2. SQLite Database Record with Artifacts
        if reporting.sql_history:
            job_id = self.db.record_job(
                agent_slug=agent.slug,
                status=status,
                duration_seconds=duration_seconds,
                summary=output_text[:500] if output_text else None,
                error_message=error_message,
                report_path=report_path,
                artifacts=all_artifacts,
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
