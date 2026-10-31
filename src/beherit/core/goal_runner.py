"""Autonomous Multi-step Goal Loop and Task Planner (BEH-063)."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

from beherit.core.todo import AgentTodoManager
from beherit.engines import EngineRegistry
from beherit.models.agent import AgentModel


class AutonomousGoalRunner:
    """Orchestrates an agent through a multi-step goal decomposition and execution loop."""

    def __init__(self, base_dir: Path | None = None):
        self.todo_mgr = AgentTodoManager(base_dir=base_dir)
        self.engine_registry = EngineRegistry()

    async def run_goal(
        self,
        agent: AgentModel,
        goal: str,
        max_steps: int = 10,
        auto_decompose: bool = True,
        auto_save_report: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute an autonomous goal loop with scratchpad management,
        yielding real-time execution events.
        """
        engine = self.engine_registry.get_engine(agent.engine)

        yield {
            "event": "goal_started",
            "agent": agent.slug,
            "goal": goal,
            "max_steps": max_steps,
            "timestamp": datetime.now().isoformat(),
        }

        # Reset or initialize todo.md for a fresh goal run if auto_decompose requested
        if auto_decompose:
            self.todo_mgr.clear_todos(agent)

        step = 1
        goal_completed = False

        while step <= max_steps and not goal_completed:
            todos = self.todo_mgr.read_todos(agent)
            summary = self.todo_mgr.get_summary(agent)

            yield {
                "event": "step_started",
                "step": step,
                "max_steps": max_steps,
                "summary": summary,
                "timestamp": datetime.now().isoformat(),
            }

            if step == 1 and auto_decompose and summary["total"] == 0:
                # Step 1: Decompose goal into steps and start step 1
                prompt = (
                    f"### TRYB AUTONOMICZNEGO CELU (Autonomous Goal Execution Loop)\n"
                    f"Twój nadrzędny cel inżynieryjny:\n"
                    f"**\"{goal}\"**\n\n"
                    f"Twoje wytyczne dla tego etapu:\n"
                    f"1. Rozbij powyższy cel na sekwencję logicznych, weryfikowalnych kroków za pomocą narzędzia `manage_todo(action='add', text=...)`.\n"
                    f"2. Oznacz pierwszy krok jako 'in_progress' (`manage_todo(action='update', item_id=1, status='in_progress')`).\n"
                    f"3. Przystąp bezpośrednio do realizacji pierwszego kroku przy użyciu dostępnych narzędzi (bash, read_file, write_file, mcp, call_agent itp.)."
                )
            else:
                # Check if all tasks are already done
                if summary["all_done"] and summary["total"] > 0:
                    goal_completed = True
                    break

                next_task = next((i for i in todos if i["status"] == "in_progress"), None) or next((i for i in todos if i["status"] == "todo"), None)
                next_desc = f"#{next_task['id']}: {next_task['text']}" if next_task else "Dokończenie pozostałych zadań"
                next_id = next_task["id"] if next_task else 1
                raw_todo = self.todo_mgr.read_raw(agent)

                prompt = (
                    f"### KONTROLA AUTONOMICZNEJ PĘTLI CELÓW (Krok {step}/{max_steps})\n"
                    f"Realizowany cel: **\"{goal}\"**\n\n"
                    f"Bieżący stan Twojego planu działań (todo.md):\n"
                    f"{raw_todo}\n\n"
                    f"Zadanie do zrealizowania w tym kroku: **{next_desc}**.\n"
                    f"1. Wykonaj niezbędne operacje narzędziowe, aby zrealizować ten krok.\n"
                    f"2. Zweryfikuj poprawność rezultatu.\n"
                    f"3. Zaktualizuj stan zadania za pomocą `manage_todo(action='update', item_id={next_id}, status='done')`.\n"
                    f"Jeśli wszystkie zaplanowane kroki zostały ukończone, potwierdź to w podsumowaniu."
                )

            # Stream execution turn from engine
            turn_output_parts: list[str] = []
            try:
                async for chunk in engine.stream_chat(agent, prompt):
                    evt = chunk.get("event")
                    data = chunk.get("data")
                    if evt == "token":
                        turn_output_parts.append(data if isinstance(data, str) else str(data))
                    yield chunk
            except Exception as e:
                yield {
                    "event": "error",
                    "step": step,
                    "error": f"Błąd wykonania kroku {step}: {e}",
                }
                break

            turn_text = "".join(turn_output_parts).strip()

            # Refresh todos after tool executions during turn
            updated_todos = self.todo_mgr.read_todos(agent)
            updated_summary = self.todo_mgr.get_summary(agent)

            yield {
                "event": "step_finished",
                "step": step,
                "todos": updated_todos,
                "summary": updated_summary,
                "turn_output": turn_text,
                "timestamp": datetime.now().isoformat(),
            }

            if updated_summary["all_done"] and updated_summary["total"] > 0:
                goal_completed = True
                break

            step += 1

        # Post-loop finalization
        final_summary = self.todo_mgr.get_summary(agent)
        final_todos = self.todo_mgr.read_todos(agent)

        if goal_completed or (final_summary["all_done"] and final_summary["total"] > 0):
            # Generate final synthesis
            final_prompt = (
                f"Wszystkie zaplanowane kroki celu **\"{goal}\"** zostały pomyślnie ukończone!\n\n"
                f"Wygeneruj całościowe, zwięzłe podsumowanie wykonanych prac i osiągniętych rezultatów."
            )
            final_report_parts: list[str] = []
            try:
                async for chunk in engine.stream_chat(agent, final_prompt):
                    evt = chunk.get("event")
                    data = chunk.get("data")
                    if evt == "token":
                        final_report_parts.append(data if isinstance(data, str) else str(data))
                    yield chunk
            except Exception:
                pass

            report_text = "".join(final_report_parts).strip()
            saved_report_path = None

            if auto_save_report and report_text:
                try:
                    workspace = self.todo_mgr.get_agent_workspace(agent)
                    reports_dir = workspace / "Notatki" / "Raporty"
                    reports_dir.mkdir(parents=True, exist_ok=True)
                    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
                    report_file = reports_dir / f"Raport_Celu_{date_str}.md"
                    content = (
                        f"# Raport Realizacji Celu: {goal}\n\n"
                        f"- **Agent**: `{agent.slug}`\n"
                        f"- **Data**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"- **Liczba zrealizowanych kroków**: {final_summary['done']}/{final_summary['total']}\n\n"
                        f"## Podsumowanie i Rezultaty\n\n{report_text}\n\n"
                        f"## Zrealizowany Plan Działań (todo.md)\n\n{self.todo_mgr.read_raw(agent)}\n"
                    )
                    report_file.write_text(content, encoding="utf-8")
                    saved_report_path = str(report_file)
                except Exception:
                    pass

            yield {
                "event": "goal_completed",
                "goal": goal,
                "status": "success",
                "total_steps": step,
                "summary": final_summary,
                "todos": final_todos,
                "report_file": saved_report_path,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            yield {
                "event": "goal_limit_reached",
                "goal": goal,
                "status": "limit_reached",
                "steps_executed": step - 1,
                "max_steps": max_steps,
                "summary": final_summary,
                "todos": final_todos,
                "message": f"Osiągnięto limit kroków ({max_steps}) autonomicznej pętli celu. Plan nie został w pełni ukończony.",
                "timestamp": datetime.now().isoformat(),
            }
