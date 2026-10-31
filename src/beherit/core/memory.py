"""Long-term Episodic & Profile Memory Manager for Beherit Agents (BEH-062)."""

from datetime import datetime
from pathlib import Path
from typing import Any

from beherit.models.agent import AgentModel


class AgentMemoryManager:
    """Manages persistent long-term memory stored in agent's memory.md."""

    def __init__(self, base_dir: Path | None = None):
        from beherit.config import settings
        self.base_dir = base_dir or settings.agents_dir

    def get_memory_file(self, agent: AgentModel) -> Path:
        """Resolve memory.md path for given agent."""
        if agent.file_path:
            return Path(agent.file_path).parent / "memory.md"
        return self.base_dir / agent.slug / "memory.md"

    def read_memory(self, agent: AgentModel) -> str:
        """Read full memory.md content if exists."""
        mem_file = self.get_memory_file(agent)
        if mem_file.exists() and mem_file.is_file():
            return mem_file.read_text(encoding="utf-8").strip()
        return ""

    def save_fact(self, agent: AgentModel, fact: str, category: str = "Wiedza ogólna") -> dict[str, Any]:
        """Append a new fact or preference to memory.md."""
        if not fact or not fact.strip():
            return {"success": False, "error": "Fakt do zapamiętania nie może być pusty."}

        mem_file = self.get_memory_file(agent)
        mem_file.parent.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_entry = f"- [{timestamp}] **[{category}]**: {fact.strip()}\n"

        if not mem_file.exists() or mem_file.stat().st_size == 0:
            header = f"# 🧠 Pamięć Długoterminowa Agenta: {agent.name}\n\n"
            mem_file.write_text(header + new_entry, encoding="utf-8")
        else:
            with open(mem_file, "a", encoding="utf-8") as f:
                f.write(new_entry)

        return {
            "success": True,
            "fact": fact.strip(),
            "category": category,
            "timestamp": timestamp,
            "memory_path": str(mem_file),
        }

    def forget_fact(self, agent: AgentModel, pattern: str) -> dict[str, Any]:
        """Remove facts matching pattern from memory.md."""
        if not pattern or not pattern.strip():
            return {"success": False, "error": "Wzorzec do usunięcia nie może być pusty."}

        mem_file = self.get_memory_file(agent)
        if not mem_file.exists():
            return {"success": False, "error": "Brak pliku pamięci."}

        lines = mem_file.read_text(encoding="utf-8").splitlines()
        pattern_lower = pattern.lower().strip()
        removed = 0
        kept_lines = []

        for line in lines:
            if line.startswith("- [") and pattern_lower in line.lower():
                removed += 1
            else:
                kept_lines.append(line)

        mem_file.write_text("\n".join(kept_lines) + "\n", encoding="utf-8")
        return {"success": True, "removed_count": removed, "pattern": pattern}

    def set_memory_content(self, agent: AgentModel, content: str) -> bool:
        """Overwrite memory.md content directly."""
        mem_file = self.get_memory_file(agent)
        mem_file.parent.mkdir(parents=True, exist_ok=True)
        mem_file.write_text(content.strip() + "\n", encoding="utf-8")
        return True
