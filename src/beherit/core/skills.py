"""Dynamic Skill Loader and Runner (skills/ - BEH-066)."""

import ast
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from beherit.config import settings
from beherit.models.agent import AgentModel


@dataclass
class SkillInfo:
    """Represents a dynamically discovered agent skill."""
    name: str
    description: str
    skill_type: str  # "python" | "bash"
    file_path: Path
    filename: str


class SkillManager:
    """Manages dynamic skill discovery, code generation, and execution."""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = base_dir or settings.agents_dir

    def get_agent_workspace(self, agent: AgentModel) -> Path:
        """Return the dedicated workspace folder for an agent."""
        if agent.file_path:
            return Path(agent.file_path).parent
        return self.base_dir / agent.slug

    def get_skills_dir(self, agent: AgentModel) -> Path:
        """Return the skills directory in agent workspace."""
        skills_dir = self.get_agent_workspace(agent) / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    def list_skills(self, agent: AgentModel) -> list[SkillInfo]:
        """Scan skills/ folder and discover Python (.py) and Bash (.sh) skills."""
        skills_dir = self.get_skills_dir(agent)
        if not skills_dir.exists():
            return []

        skills: list[SkillInfo] = []

        for p in sorted(skills_dir.iterdir()):
            if p.is_file():
                ext = p.suffix.lower()
                name = p.stem.replace("-", "_").lower()

                if ext == ".py":
                    description = self._extract_python_docstring(p)
                    skills.append(SkillInfo(
                        name=name,
                        description=description,
                        skill_type="python",
                        file_path=p,
                        filename=p.name,
                    ))
                elif ext == ".sh":
                    description = self._extract_bash_description(p)
                    skills.append(SkillInfo(
                        name=name,
                        description=description,
                        skill_type="bash",
                        file_path=p,
                        filename=p.name,
                    ))
            elif p.is_dir():
                # Directory skill (e.g. skills/my_skill/SKILL.md or main.py)
                skill_md = p / "SKILL.md"
                main_py = p / "main.py"
                main_sh = p / "main.sh"
                script_path = main_py if main_py.exists() else (main_sh if main_sh.exists() else None)
                if script_path:
                    name = p.name.replace("-", "_").lower()
                    desc = self._extract_markdown_description(skill_md) if skill_md.exists() else (
                        self._extract_python_docstring(script_path) if script_path == main_py else self._extract_bash_description(script_path)
                    )
                    skills.append(SkillInfo(
                        name=name,
                        description=desc,
                        skill_type="python" if script_path == main_py else "bash",
                        file_path=script_path,
                        filename=f"{p.name}/{script_path.name}",
                    ))

        return skills

    def _extract_python_docstring(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
            doc = ast.get_docstring(tree)
            if doc:
                return doc.strip().split("\n")[0]
        except Exception:
            pass
        return "Niestandardowy skrypt Pythona w katalogu skills/"

    def _extract_bash_description(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("# Description:") or line.startswith("# Opis:"):
                    return line.split(":", 1)[1].strip()
                elif line.startswith("#") and not line.startswith("#!"):
                    text = line.lstrip("# ").strip()
                    if text:
                        return text
        except Exception:
            pass
        return "Niestandardowy skrypt powłoki Bash w katalogu skills/"

    def _extract_markdown_description(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"description:\s*(.+)", content, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            for line in content.splitlines():
                if line.startswith("# "):
                    return line.lstrip("# ").strip()
        except Exception:
            pass
        return "Moduł umiejętności z plikiem SKILL.md"

    async def execute_skill(
        self,
        agent: AgentModel,
        skill_name: str,
        args: Any = None,
        timeout: int = 60,
    ) -> dict[str, Any]:
        """Execute a discovered skill script in the agent workspace."""
        normalized_target = skill_name.lower().replace("-", "_").removeprefix("skill_")
        skills = self.list_skills(agent)
        skill = next((s for s in skills if s.name == normalized_target), None)

        if not skill:
            available = [s.name for s in skills]
            return {
                "success": False,
                "error": f"Skill '{skill_name}' not found. Available skills: {available}",
            }

        workspace = self.get_agent_workspace(agent)
        cmd: list[str] = []

        if skill.skill_type == "python":
            cmd = ["python3", str(skill.file_path)]
        else:
            cmd = ["bash", str(skill.file_path)]

        input_data = None
        if args is not None:
            if isinstance(args, (dict, list)):
                input_data = json.dumps(args, ensure_ascii=False)
                cmd.append(input_data)
            else:
                cmd.append(str(args))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if input_data else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=input_data.encode() if input_data else None),
                timeout=timeout,
            )
            stdout = stdout_bytes.decode(errors="replace").strip()
            stderr = stderr_bytes.decode(errors="replace").strip()
            code = proc.returncode

            # Attempt parsing structured json output if present
            parsed_json = None
            if stdout.startswith("{") and stdout.endswith("}"):
                try:
                    parsed_json = json.loads(stdout)
                except Exception:
                    pass

            return {
                "success": code == 0,
                "skill": skill.name,
                "skill_type": skill.skill_type,
                "file": skill.filename,
                "exit_code": code,
                "stdout": stdout,
                "stderr": stderr,
                "data": parsed_json,
            }
        except asyncio.TimeoutError:
            return {
                "success": False,
                "skill": skill.name,
                "error": f"Skill execution timed out after {timeout} seconds.",
            }
        except Exception as e:
            return {
                "success": False,
                "skill": skill.name,
                "error": f"Failed to execute skill: {e}",
            }
