"""Tool Execution Engine and Capabilities Guard (Sandbox & Unrestricted Access)."""

import asyncio
import os
import re
from pathlib import Path
from typing import Any

from beherit.config import settings
from beherit.models.agent import AgentModel, TerminalMode


class CapabilitiesGuard:
    """Enforces fine-grained permission controls based on agent capabilities."""

    def __init__(self, default_workspace: Path | None = None):
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
    ) -> dict[str, Any]:
        """
        Execute a bash command adhering to agent's terminal_mode:
        - DISABLED: denied immediately.
        - RESTRICTED: hardened sandbox within agent directory, blocking parent traversals and host escapes.
        - UNRESTRICTED: unrestricted execution.
        """
        capabilities = agent.capabilities
        if not capabilities.terminal or capabilities.terminal_mode == TerminalMode.DISABLED:
            return {
                "success": False,
                "error": "Terminal access is disabled for this agent in its capabilities.",
                "mode": TerminalMode.DISABLED.value,
            }

        workspace = self.get_agent_workspace(agent).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        # Restricted mode safety assertions (BEH-017 Hardening)
        if capabilities.terminal_mode == TerminalMode.RESTRICTED:
            # 1. Deny parent directory traversal (..)
            if re.search(r'(^|[\s/])\.\.($|[\s/])', command):
                return {
                    "success": False,
                    "error": "Restricted mode: Parent directory traversal ('..') is strictly forbidden.",
                    "mode": TerminalMode.RESTRICTED.value,
                }

            # 2. Deny directory changes to root or system paths
            if re.search(r'\bcd\s+([/~]|\$)', command):
                return {
                    "success": False,
                    "error": "Restricted mode: Root, home or variable navigation ('cd /') is forbidden.",
                    "mode": TerminalMode.RESTRICTED.value,
                }

            # 3. Block sensitive host system directories
            sensitive_roots = ["/etc", "/root", "/var", "/boot", "/proc", "/sys", "/dev", "/usr"]
            for sroot in sensitive_roots:
                if re.search(rf'(^|[\s="\'`]){re.escape(sroot)}($|[\s/"\'`])', command):
                    return {
                        "success": False,
                        "error": f"Restricted mode: Access to host directory '{sroot}' is forbidden.",
                        "mode": TerminalMode.RESTRICTED.value,
                    }

            # 4. Block destructive system commands
            forbidden_cmds = ["rm -rf /", ":(){ :|:& };:", "mkfs", "dd if="]
            for fcmd in forbidden_cmds:
                if fcmd in command:
                    return {
                        "success": False,
                        "error": "Restricted mode: Dangerous command pattern rejected.",
                        "mode": TerminalMode.RESTRICTED.value,
                    }

        # Prepare isolated execution environment
        sub_env = os.environ.copy()
        sub_env["HOME"] = str(workspace)
        sub_env["PWD"] = str(workspace)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
                env=sub_env,
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
                "cwd": str(workspace),
            }

        except TimeoutError:
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

    def read_file(self, agent: AgentModel, relative_or_abs_path: str) -> dict[str, Any]:
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

    def write_file(self, agent: AgentModel, relative_or_abs_path: str, content: str) -> dict[str, Any]:
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

    def list_dir(self, agent: AgentModel, relative_or_abs_path: str = ".") -> dict[str, Any]:
        """List files and folders in directory adhering to read capabilities."""
        if not agent.capabilities.file_read:
            return {"success": False, "error": "File read capability is disabled."}

        workspace = self.get_agent_workspace(agent)
        target = Path(relative_or_abs_path)

        if not target.is_absolute():
            target = workspace / target

        if agent.capabilities.terminal_mode == TerminalMode.RESTRICTED:
            try:
                target.resolve().relative_to(workspace.resolve())
            except ValueError:
                return {
                    "success": False,
                    "error": "Restricted mode: Cannot list files outside agent workspace.",
                }

        if not target.exists() or not target.is_dir():
            return {"success": False, "error": f"Directory not found: {target}"}

        try:
            items = []
            for item in sorted(target.iterdir()):
                items.append({
                    "name": item.name,
                    "is_dir": item.is_dir(),
                    "size": item.stat().st_size if item.is_file() else None,
                })
            return {"success": True, "path": str(target.resolve()), "items": items}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def dispatch_tool(
        self,
        agent: AgentModel,
        tool_name: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Unified dispatcher for agent tool calls."""
        args = args or {}
        if tool_name == "execute_bash":
            command = args.get("command", "")
            return await self.execute_bash(agent, command, timeout=args.get("timeout", 60))
        elif tool_name == "read_file":
            path = args.get("path") or args.get("relative_or_abs_path", "")
            return self.read_file(agent, path)
        elif tool_name == "write_file":
            path = args.get("path") or args.get("relative_or_abs_path", "")
            content = args.get("content", "")
            return self.write_file(agent, path, content)
        elif tool_name == "list_dir":
            path = args.get("path", ".")
            return self.list_dir(agent, path)
        else:
            return {
                "success": False,
                "error": f"Unknown tool '{tool_name}'. Available: execute_bash, read_file, write_file, list_dir",
            }
