"""Tool Execution Engine and Capabilities Guard (Sandbox & Unrestricted Access)."""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional
from beherit.config import settings
from beherit.models.agent import AgentModel, TerminalMode


class CapabilitiesGuard:
    """Enforces fine-grained permission controls based on agent capabilities."""

    def __init__(self, default_workspace: Optional[Path] = None):
        self.default_workspace = default_workspace or settings.agents_dir

    def get_agent_workspace(self, agent: AgentModel) -> Path:
        """Return the dedicated workspace folder for an agent."""
        if agent.file_path:
            return Path(agent.file_path).parent
        return self.default_workspace / agent.slug

    async def execute_bash(
        self,
        agent: AgentModel,
        command: str,
        timeout: int = 60,
    ) -> Dict[str, Any]:
        """
        Execute a bash command adhering to agent's terminal_mode:
        - DISABLED: denied immediately.
        - RESTRICTED: executed strictly within agent's directory with path escapes blocked.
        - UNRESTRICTED: full freedom, unrestricted host execution without directory limits.
        """
        capabilities = agent.capabilities
        if not capabilities.terminal or capabilities.terminal_mode == TerminalMode.DISABLED:
            return {
                "success": False,
                "error": "Terminal access is disabled for this agent in its capabilities.",
                "mode": TerminalMode.DISABLED.value,
            }

        workspace = self.get_agent_workspace(agent)
        workspace.mkdir(parents=True, exist_ok=True)

        # Restricted mode safety assertions
        if capabilities.terminal_mode == TerminalMode.RESTRICTED:
            # Block commands trying to navigate outside
            forbidden_patterns = ["../", "cd /", "rm -rf /", ":(){ :|:& };:"]
            for pattern in forbidden_patterns:
                if pattern in command:
                    return {
                        "success": False,
                        "error": f"Command rejected: '{pattern}' violates restricted sandbox policy.",
                        "mode": TerminalMode.RESTRICTED.value,
                    }
            cwd = workspace
        else:
            # UNRESTRICTED mode: full freedom
            cwd = workspace

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )

            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            return {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "mode": capabilities.terminal_mode.value,
                "cwd": str(cwd),
            }

        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return {
                "success": False,
                "error": f"Command timed out after {timeout} seconds.",
                "mode": capabilities.terminal_mode.value,
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "mode": capabilities.terminal_mode.value,
            }

    def read_file(self, agent: AgentModel, relative_or_abs_path: str) -> Dict[str, Any]:
        """Read a file adhering to read capabilities."""
        if not agent.capabilities.file_read:
            return {"success": False, "error": "File read capability is disabled."}

        workspace = self.get_agent_workspace(agent)
        target = Path(relative_or_abs_path)

        if not target.is_absolute():
            target = workspace / target

        # In restricted mode, check containment
        if agent.capabilities.terminal_mode == TerminalMode.RESTRICTED:
            try:
                target.resolve().relative_to(workspace.resolve())
            except ValueError:
                return {
                    "success": False,
                    "error": "Restricted mode: Cannot read files outside agent workspace.",
                }

        if not target.exists() or not target.is_file():
            return {"success": False, "error": f"File not found: {target}"}

        try:
            content = target.read_text(encoding="utf-8")
            return {"success": True, "content": content, "path": str(target.resolve())}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def write_file(self, agent: AgentModel, relative_or_abs_path: str, content: str) -> Dict[str, Any]:
        """Write content to a file adhering to write capabilities."""
        if not agent.capabilities.file_write:
            return {"success": False, "error": "File write capability is disabled."}

        workspace = self.get_agent_workspace(agent)
        target = Path(relative_or_abs_path)

        if not target.is_absolute():
            target = workspace / target

        # In restricted mode, check containment
        if agent.capabilities.terminal_mode == TerminalMode.RESTRICTED:
            try:
                target.resolve().relative_to(workspace.resolve())
            except ValueError:
                return {
                    "success": False,
                    "error": "Restricted mode: Cannot write files outside agent workspace.",
                }

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return {"success": True, "path": str(target.resolve())}
        except Exception as e:
            return {"success": False, "error": str(e)}
