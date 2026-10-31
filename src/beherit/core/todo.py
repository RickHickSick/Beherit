"""Agent Scratchpad and Task Planner (todo.md - BEH-063)."""

from pathlib import Path
import re
from typing import Any

from beherit.config import settings
from beherit.models.agent import AgentModel


class AgentTodoManager:
    """Manages agent's active scratchpad and task checklist in todo.md."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or settings.agents_dir

    def get_agent_workspace(self, agent: AgentModel) -> Path:
        """Return the dedicated workspace folder for an agent."""
        if agent.file_path:
            return Path(agent.file_path).parent
        return self.base_dir / agent.slug

    def get_todo_path(self, agent: AgentModel) -> Path:
        """Return the path to agent's todo.md."""
        return self.get_agent_workspace(agent) / "todo.md"

    def read_raw(self, agent: AgentModel) -> str:
        """Read raw todo.md content if it exists."""
        todo_file = self.get_todo_path(agent)
        if not todo_file.exists():
            return ""
        try:
            return todo_file.read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def write_raw(self, agent: AgentModel, content: str) -> None:
        """Write raw markdown content to todo.md."""
        todo_file = self.get_todo_path(agent)
        todo_file.parent.mkdir(parents=True, exist_ok=True)
        todo_file.write_text(content.strip() + "\n", encoding="utf-8")

    def read_todos(self, agent: AgentModel) -> list[dict[str, Any]]:
        """
        Parse todo.md checklist into structured task items:
        - [ ] Task pending -> 'todo'
        - [/] Task in progress -> 'in_progress'
        - [x] Task done -> 'done'
        - [-] Task cancelled/skipped -> 'cancelled'
        """
        raw = self.read_raw(agent)
        if not raw:
            return []

        items: list[dict[str, Any]] = []
        item_id = 1

        pattern = re.compile(r"^[-*]\s*\[([ xX/\-])\]\s*(.*?)(?:\s*<!--\s*id:\s*(\d+)\s*-->)?$", re.MULTILINE)
        for match in pattern.finditer(raw):
            mark = match.group(1).lower()
            text = match.group(2).strip()
            explicit_id = match.group(3)

            if mark in ("x", "x"):
                status = "done"
            elif mark == "/":
                status = "in_progress"
            elif mark == "-":
                status = "cancelled"
            else:
                status = "todo"

            assigned_id = int(explicit_id) if explicit_id else item_id
            items.append({
                "id": assigned_id,
                "text": text,
                "status": status,
                "raw": match.group(0),
            })
            item_id = max(item_id + 1, assigned_id + 1)

        return items

    def write_todos(self, agent: AgentModel, items: list[dict[str, Any]]) -> str:
        """Format structured task items into markdown and save to todo.md."""
        lines = ["# 📋 Plan Działań i Scratchpad (todo.md)", ""]
        for it in items:
            status = it.get("status", "todo")
            if status == "done":
                box = "[x]"
            elif status == "in_progress":
                box = "[/]"
            elif status == "cancelled":
                box = "[-]"
            else:
                box = "[ ]"

            item_id = it.get("id")
            text = it.get("text", "").strip()
            lines.append(f"- {box} {text} <!-- id: {item_id} -->")

        content = "\n".join(lines) + "\n"
        self.write_raw(agent, content)
        return content

    def add_item(self, agent: AgentModel, text: str, status: str = "todo") -> dict[str, Any]:
        """Add a new task item to todo.md."""
        items = self.read_todos(agent)
        next_id = max([i["id"] for i in items], default=0) + 1
        new_item = {
            "id": next_id,
            "text": text.strip(),
            "status": status if status in ("todo", "in_progress", "done", "cancelled") else "todo",
        }
        items.append(new_item)
        self.write_todos(agent, items)
        return {
            "success": True,
            "action": "add",
            "item": new_item,
            "total_items": len(items),
        }

    def update_item(
        self,
        agent: AgentModel,
        item_id: int | str | None = None,
        status: str | None = None,
        text: str | None = None,
    ) -> dict[str, Any]:
        """Update an existing task in todo.md by numeric ID or matching text."""
        items = self.read_todos(agent)
        matched = False
        target_item = None

        valid_statuses = ("todo", "in_progress", "done", "cancelled")
        if status and status not in valid_statuses:
            status = "todo"

        target_id_int = None
        if item_id is not None:
            try:
                target_id_int = int(item_id)
            except ValueError:
                pass

        for it in items:
            is_match = False
            if target_id_int is not None and it["id"] == target_id_int:
                is_match = True
            elif item_id and str(item_id).lower() in it["text"].lower():
                is_match = True

            if is_match:
                if status:
                    it["status"] = status
                if text:
                    it["text"] = text.strip()
                matched = True
                target_item = it
                break

        if not matched:
            return {
                "success": False,
                "error": f"Task matching ID or title '{item_id}' not found in todo.md.",
                "available_items": items,
            }

        self.write_todos(agent, items)
        return {
            "success": True,
            "action": "update",
            "item": target_item,
            "summary": self.get_summary(agent),
        }

    def clear_todos(self, agent: AgentModel) -> dict[str, Any]:
        """Reset or clear todo.md."""
        todo_file = self.get_todo_path(agent)
        if todo_file.exists():
            todo_file.unlink()
        return {
            "success": True,
            "action": "clear",
            "message": "Scratchpad todo.md cleared.",
        }

    def get_summary(self, agent: AgentModel) -> dict[str, Any]:
        """Return counts, progress percent, and status summary."""
        items = self.read_todos(agent)
        total = len(items)
        if total == 0:
            return {
                "total": 0,
                "todo": 0,
                "in_progress": 0,
                "done": 0,
                "cancelled": 0,
                "progress_percent": 0.0,
                "all_done": False,
            }

        done_cnt = sum(1 for i in items if i["status"] == "done")
        prog_cnt = sum(1 for i in items if i["status"] == "in_progress")
        canc_cnt = sum(1 for i in items if i["status"] == "cancelled")
        todo_cnt = sum(1 for i in items if i["status"] == "todo")

        # Finished tasks count both done and explicitly cancelled/skipped
        finished = done_cnt + canc_cnt
        percent = round((finished / total) * 100, 1)

        return {
            "total": total,
            "todo": todo_cnt,
            "in_progress": prog_cnt,
            "done": done_cnt,
            "cancelled": canc_cnt,
            "progress_percent": percent,
            "all_done": (todo_cnt == 0 and prog_cnt == 0 and total > 0),
        }
