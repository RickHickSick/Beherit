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

        import shutil
        use_bwrap = False
        if capabilities.terminal_mode == TerminalMode.RESTRICTED and shutil.which("bwrap"):
            use_bwrap = True

        try:
            if use_bwrap:
                bwrap_cmd = self._build_bwrap_command(workspace, command)
                process = await asyncio.create_subprocess_exec(
                    *bwrap_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace),
                    env=sub_env,
                )
            else:
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

            # Detect if bwrap failed due to container permission / user namespace limitations
            if use_bwrap and process.returncode != 0 and "bwrap:" in stderr and "namespace" in stderr:
                # Soft fallback to restricted shell
                fallback_proc = await asyncio.create_subprocess_shell(
                    command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace),
                    env=sub_env,
                )
                fb_out, fb_err = await asyncio.wait_for(fallback_proc.communicate(), timeout=timeout)
                return {
                    "success": fallback_proc.returncode == 0,
                    "exit_code": fallback_proc.returncode,
                    "stdout": fb_out.decode("utf-8", errors="replace"),
                    "stderr": fb_err.decode("utf-8", errors="replace"),
                    "mode": capabilities.terminal_mode.value,
                    "sandbox": "restricted_env_fallback",
                    "cwd": str(workspace),
                }

            return {
                "success": process.returncode == 0,
                "exit_code": process.returncode,
                "stdout": stdout,
                "stderr": stderr,
                "mode": capabilities.terminal_mode.value,
                "sandbox": "bubblewrap" if use_bwrap else "restricted_env",
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

    def _build_bwrap_command(self, workspace: Path, command: str) -> list[str]:
        """Construct unprivileged bubblewrap sandbox execution command (BEH-069)."""
        import shutil

        bwrap = shutil.which("bwrap") or "/usr/bin/bwrap"
        cmd = [
            bwrap,
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
        ]
        if Path("/lib64").exists():
            cmd.extend(["--ro-bind-try", "/lib64", "/lib64"])
        if Path("/etc/resolv.conf").exists():
            cmd.extend(["--ro-bind-try", "/etc/resolv.conf", "/etc/resolv.conf"])
        if Path("/etc/ssl").exists():
            cmd.extend(["--ro-bind-try", "/etc/ssl", "/etc/ssl"])
        if Path("/etc/ca-certificates").exists():
            cmd.extend(["--ro-bind-try", "/etc/ca-certificates", "/etc/ca-certificates"])

        cmd.extend([
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--bind", str(workspace), str(workspace),
            "--chdir", str(workspace),
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--die-with-parent",
            "bash", "-c", command,
        ])
        return cmd

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

    def manage_todo(
        self,
        agent: AgentModel,
        action: str = "list",
        text: str | None = None,
        item_id: int | str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        """Manage agent scratchpad and task checklist in todo.md (BEH-063)."""
        from beherit.core.todo import AgentTodoManager

        mgr = AgentTodoManager(base_dir=self.default_workspace)
        action = (action or "list").lower()

        if action == "list":
            return {
                "success": True,
                "action": "list",
                "todos": mgr.read_todos(agent),
                "summary": mgr.get_summary(agent),
                "raw": mgr.read_raw(agent),
            }
        elif action == "add":
            if not text:
                return {"success": False, "error": "Argument 'text' is required when adding a todo task."}
            return mgr.add_item(agent, text=text, status=status or "todo")
        elif action == "update":
            if item_id is None and not text:
                return {"success": False, "error": "Argument 'item_id' or 'text' is required to update a task."}
            return mgr.update_item(agent, item_id=item_id, status=status, text=text)
        elif action == "clear":
            return mgr.clear_todos(agent)
        else:
            return {"success": False, "error": f"Unknown action '{action}'. Use: list, add, update, clear."}

    def get_tools_schema(self, agent: AgentModel, provider: str = "gemini") -> Any:
        """
        Generate tool declarations for direct HTTP API calls (BEH-061 Function Calling).
        Supports: 'gemini', 'anthropic', 'openai'.
        """
        caps = agent.capabilities
        raw_tools = []

        if caps.terminal and caps.terminal_mode != TerminalMode.DISABLED:
            raw_tools.append({
                "name": "execute_bash",
                "description": f"Execute a shell command inside the agent's isolated workspace sandbox. Mode: {caps.terminal_mode.value}.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command line string to run"}
                    },
                    "required": ["command"]
                }
            })

        if caps.file_read:
            raw_tools.append({
                "name": "read_file",
                "description": "Read text file contents from the agent workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to file in workspace"}
                    },
                    "required": ["path"]
                }
            })
            raw_tools.append({
                "name": "list_dir",
                "description": "List files and subdirectories in the agent workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path (default: '.')"}
                    }
                }
            })

        if caps.file_write:
            raw_tools.append({
                "name": "write_file",
                "description": "Write or overwrite a file inside the agent workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path to write"},
                        "content": {"type": "string", "description": "Text content to write"}
                    },
                    "required": ["path", "content"]
                }
            })

        # Multi-Agent delegation tool (BEH-060)
        raw_tools.append({
            "name": "call_agent",
            "description": "Delegate a subtask or question to another specialized AI agent in Beherit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "agent_slug": {"type": "string", "description": "Target agent slug"},
                    "prompt": {"type": "string", "description": "Task prompt or instruction"},
                    "context": {"type": "object", "description": "Optional dictionary context"}
                },
                "required": ["agent_slug", "prompt"]
            }
        })

        # Persistent Long-term Memory tools (BEH-062)
        raw_tools.append({
            "name": "save_memory",
            "description": "Save an important fact, user preference, system configuration, or rule to long-term memory (memory.md).",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The specific fact or piece of information to remember"},
                    "category": {"type": "string", "description": "Category (e.g. 'Preferencje', 'Infrastruktura', 'Reguły biznesowe')"}
                },
                "required": ["fact"]
            }
        })
        raw_tools.append({
            "name": "recall_memory",
            "description": "Read all stored long-term memory facts from memory.md.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        })
        raw_tools.append({
            "name": "forget_memory",
            "description": "Remove or forget obsolete facts from long-term memory matching a search string.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Keyword or text snippet to remove from memory"}
                },
                "required": ["pattern"]
            }
        })

        # Task scratchpad and planning tools (BEH-063)
        raw_tools.append({
            "name": "manage_todo",
            "description": "Zarządzanie dynamicznym planem zadań i scratchpadem todo.md w trybie autonomicznym. Umożliwia dodawanie (action='add', text='...'), aktualizację statusu (action='update', item_id=N, status='done'|'in_progress'|'todo'|'cancelled'), listowanie (action='list') oraz czyszczenie zadań (action='clear').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Akcja do wykonania: 'list', 'add', 'update', 'clear'"
                    },
                    "text": {
                        "type": "string",
                        "description": "Opis zadania (wymagany przy action='add', opcjonalny przy 'update')"
                    },
                    "item_id": {
                        "type": "integer",
                        "description": "Identyfikator numeryczny zadania do aktualizacji (wymagany przy action='update')"
                    },
                    "status": {
                        "type": "string",
                        "description": "Nowy status zadania: 'todo', 'in_progress', 'done', 'cancelled'"
                    }
                },
                "required": ["action"]
            }
        })

        # Plane Project Management tools (BEH-065)
        raw_tools.append({
            "name": "plane_list_issues",
            "description": "Pobiera listę zadań z tablicy projektu Plane (np. 'todo', 'in_progress', 'done', 'backlog').",
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": "Filtruj zadania po statusie (np. 'todo', 'in_progress', 'done', 'backlog')"},
                    "limit": {"type": "integer", "description": "Maksymalna liczba zadań do pobrania (domyślnie 20)"}
                }
            }
        })
        raw_tools.append({
            "name": "plane_create_issue",
            "description": "Tworzy nowe zadanie, zgłoszenie usterki lub cel na tablicy Plane w projekcie Beherit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Tytuł zadania w Plane"},
                    "description": {"type": "string", "description": "Szczegółowy opis zadania, log błędu lub kontekst"},
                    "priority": {"type": "string", "description": "Priorytet: 'urgent', 'high', 'medium', 'low', 'none'"},
                    "state": {"type": "string", "description": "Początkowy status zadania: 'todo', 'backlog', 'in_progress'"}
                },
                "required": ["name"]
            }
        })
        raw_tools.append({
            "name": "plane_update_state",
            "description": "Aktualizuje status istniejącego zadania na tablicy Plane (np. przenosi do 'done', 'in_progress', 'cancelled').",
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "UUID zadania lub numer sekwencyjny (np. 65 lub 'e428cd83-...')"},
                    "state": {"type": "string", "description": "Nowy status zadania: 'todo', 'in_progress', 'done', 'cancelled', 'backlog'"}
                },
                "required": ["issue_id", "state"]
            }
        })

        # Dynamic agent skills from skills/ directory (BEH-066)
        raw_tools.append({
            "name": "list_skills",
            "description": "Zwraca listę wszystkich zarejestrowanych niestandardowych umiejętności i skryptów w katalogu skills/ agenta.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        })
        raw_tools.append({
            "name": "run_skill",
            "description": "Uruchamia skrypt umiejętności (Python/Bash) z katalogu skills/ agenta przekazując opcjonalne argumenty.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Nazwa umiejętności (np. 'backup_db' lub 'analyze_metrics')"},
                    "args": {"type": "object", "description": "Opcjonalne argumenty lub parametry wejściowe przekazywane do skryptu"}
                },
                "required": ["skill_name"]
            }
        })

        try:
            from beherit.core.skills import SkillManager
            smgr = SkillManager(base_dir=self.default_workspace)
            for skill in smgr.list_skills(agent):
                raw_tools.append({
                    "name": f"skill_{skill.name}",
                    "description": f"Dedykowana umiejętność ({skill.skill_type}): {skill.description}",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "args": {"type": "object", "description": "Parametry wejściowe dla skryptu"}
                        }
                    }
                })
        except Exception:
            pass

        # Obsidian Wikilinks Graph RAG tool (BEH-067)
        raw_tools.append({
            "name": "query_knowledge_graph",
            "description": "Przeszukuje graf wiedzy powiązań wikilinków Obsidiana ([[...]]). Umożliwia pobranie całej sieci pojęć lub analizę połączeń (1-hop / 2-hop / backlinks) dla wybranej notatki.",
            "parameters": {
                "type": "object",
                "properties": {
                    "doc_name": {"type": "string", "description": "Opcjonalna nazwa dokumentu bazowego do analizy powiązań (np. 'architektura.md'). Przy braku zwraca strukturę całego grafu."},
                    "max_hops": {"type": "integer", "description": "Maksymalny zasięg ekspansji grafu (domyślnie 2)"}
                }
            }
        })

        # Telegram Notification tool (BEH-068)
        raw_tools.append({
            "name": "send_telegram_notification",
            "description": "Wysyła bezpośrednie powiadomienie lub alert na czat Telegram administratora/użytkownika.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Treść powiadomienia lub raportu do wysłania na Telegram"},
                    "chat_id": {"type": "string", "description": "Opcjonalny docelowy chat_id"}
                },
                "required": ["message"]
            }
        })

        if not raw_tools:
            return None

        if provider == "gemini":
            func_decls = []
            for t in raw_tools:
                props = {}
                for k, v in t["parameters"].get("properties", {}).items():
                    v_copy = dict(v)
                    if "type" in v_copy:
                        v_copy["type"] = v_copy["type"].upper()
                    props[k] = v_copy
                func_decls.append({
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": {
                        "type": "OBJECT",
                        "properties": props,
                        "required": t["parameters"].get("required", [])
                    }
                })
            return [{"functionDeclarations": func_decls}]

        elif provider == "anthropic":
            anthropic_tools = []
            for t in raw_tools:
                anthropic_tools.append({
                    "name": t["name"],
                    "description": t["description"],
                    "input_schema": t["parameters"]
                })
            return anthropic_tools

        elif provider == "openai":
            openai_tools = []
            for t in raw_tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t["description"],
                        "parameters": t["parameters"]
                    }
                })
            return openai_tools

        return raw_tools

    async def call_agent(
        self,
        caller_agent: AgentModel,
        target_slug: str,
        prompt: str,
        context: dict[str, Any] | None = None,
        call_stack: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Invoke another agent in Beherit as a subagent / delegated collaborator (BEH-060).
        Protects against infinite recursion and circular delegation.
        """
        import json

        stack = list(call_stack or [])
        if caller_agent.slug not in stack:
            stack.append(caller_agent.slug)

        if not target_slug or not target_slug.strip():
            return {
                "success": False,
                "error": "Target agent slug must not be empty.",
                "call_stack": stack,
            }

        if target_slug in stack:
            return {
                "success": False,
                "error": f"Circular agent delegation rejected: {' -> '.join(stack)} -> {target_slug}",
                "call_stack": stack,
            }

        if len(stack) >= 5:
            return {
                "success": False,
                "error": f"Maximum subagent delegation depth exceeded (limit=5): {' -> '.join(stack)}",
                "call_stack": stack,
            }

        from beherit.core.repository import AgentRepository
        from beherit.engines import EngineRegistry

        repo = AgentRepository(base_dir=self.default_workspace)
        target_agent = repo.load_agent(target_slug)
        if not target_agent:
            return {
                "success": False,
                "error": f"Target agent '{target_slug}' not found.",
                "call_stack": stack,
            }

        # Format prompt with optional context
        final_prompt = prompt.strip()
        if context:
            ctx_str = json.dumps(context, ensure_ascii=False, indent=2)
            final_prompt = f"{final_prompt}\n\n### Przekazany Kontekst (Delegation Context from {caller_agent.name}):\n```json\n{ctx_str}\n```"

        stack.append(target_slug)

        engine_registry = EngineRegistry()
        engine = engine_registry.get_engine(target_agent.engine)

        tokens: list[str] = []
        error_msg: str | None = None

        try:
            async for chunk in engine.stream_chat(target_agent, final_prompt):
                evt = chunk.get("event")
                data = chunk.get("data")
                if evt == "token":
                    tokens.append(data if isinstance(data, str) else str(data))
                elif evt == "error":
                    error_msg = str(data)
                    break
        except Exception as e:
            error_msg = str(e)

        if error_msg:
            return {
                "success": False,
                "target_agent": target_slug,
                "error": f"Error executing subagent '{target_slug}': {error_msg}",
                "call_stack": stack,
            }

        full_output = "".join(tokens).strip() or "Brak odpowiedzi od podagenta."
        return {
            "success": True,
            "caller_agent": caller_agent.slug,
            "target_agent": target_slug,
            "target_agent_name": target_agent.name,
            "output": full_output,
            "call_stack": stack,
        }

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
        elif tool_name == "call_agent":
            target_slug = args.get("agent_slug") or args.get("slug", "")
            prompt = args.get("prompt", "")
            context = args.get("context")
            call_stack = args.get("call_stack")
            return await self.call_agent(
                caller_agent=agent,
                target_slug=target_slug,
                prompt=prompt,
                context=context,
                call_stack=call_stack,
            )
        elif tool_name == "save_memory":
            fact = args.get("fact", "")
            category = args.get("category", "Ogólne")
            from beherit.core.memory import AgentMemoryManager
            mgr = AgentMemoryManager(base_dir=self.default_workspace)
            return mgr.save_fact(agent, fact, category)
        elif tool_name == "recall_memory":
            from beherit.core.memory import AgentMemoryManager
            mgr = AgentMemoryManager(base_dir=self.default_workspace)
            content = mgr.read_memory(agent)
            return {"success": True, "memory": content or "Brak zapisanych wspomnień w pliku memory.md."}
        elif tool_name == "forget_memory":
            pattern = args.get("pattern", "")
            from beherit.core.memory import AgentMemoryManager
            mgr = AgentMemoryManager(base_dir=self.default_workspace)
            return mgr.forget_fact(agent, pattern)
        elif tool_name == "manage_todo":
            action = args.get("action", "list")
            text = args.get("text")
            item_id = args.get("item_id") or args.get("id")
            status = args.get("status")
            return self.manage_todo(agent, action=action, text=text, item_id=item_id, status=status)
        elif tool_name == "plane_list_issues":
            from beherit.core.plane import PlaneClient
            client = PlaneClient()
            issues = await client.list_issues(state=args.get("state"), limit=args.get("limit", 20))
            return {"success": True, "total": len(issues), "issues": issues}
        elif tool_name == "plane_create_issue":
            from beherit.core.plane import PlaneClient
            client = PlaneClient()
            return await client.create_issue(
                name=args.get("name", ""),
                description=args.get("description"),
                priority=args.get("priority", "medium"),
                state=args.get("state"),
                module_id=args.get("module_id"),
            )
        elif tool_name == "plane_update_state":
            from beherit.core.plane import PlaneClient
            client = PlaneClient()
            issue_id = args.get("issue_id") or args.get("id") or args.get("sequence_id")
            state = args.get("state", "done")
            return await client.update_issue_state(issue_id_or_seq=issue_id, state=state)
        elif tool_name == "list_skills":
            from beherit.core.skills import SkillManager
            smgr = SkillManager(base_dir=self.default_workspace)
            skills = smgr.list_skills(agent)
            return {
                "success": True,
                "total": len(skills),
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "type": s.skill_type,
                        "file": s.filename,
                    }
                    for s in skills
                ],
            }
        elif tool_name == "run_skill":
            from beherit.core.skills import SkillManager
            smgr = SkillManager(base_dir=self.default_workspace)
            skill_name = args.get("skill_name", "")
            skill_args = args.get("args")
            timeout = args.get("timeout", 60)
            return await smgr.execute_skill(agent, skill_name, args=skill_args, timeout=timeout)
        elif tool_name.startswith("skill_"):
            from beherit.core.skills import SkillManager
            smgr = SkillManager(base_dir=self.default_workspace)
            skill_name = tool_name.removeprefix("skill_")
            skill_args = args.get("args") if "args" in args else (args if args else None)
            timeout = args.get("timeout", 60)
            return await smgr.execute_skill(agent, skill_name, args=skill_args, timeout=timeout)
        elif tool_name == "query_knowledge_graph":
            from beherit.core.rag import KnowledgeRAG
            rag = KnowledgeRAG()
            doc_name = args.get("doc_name")
            max_hops = args.get("max_hops", 2)
            if doc_name:
                backlinks = rag.get_document_backlinks(agent.slug, doc_name)
                expansion = rag.expand_graph(agent.slug, [doc_name], max_hops=max_hops)
                return {
                    "success": True,
                    "doc_name": doc_name,
                    "backlinks": backlinks,
                    "graph_expansion": expansion,
                }
            else:
                graph = rag.get_graph(agent.slug)
                return {
                    "success": True,
                    "graph": graph,
                }
        elif tool_name == "send_telegram_notification":
            from beherit.core.telegram_bot import telegram_bot_service
            msg_text = args.get("message", "")
            target_chat = args.get("chat_id")
            sent = await telegram_bot_service.send_notification(msg_text, chat_id=target_chat)
            return {
                "success": sent,
                "message": msg_text,
                "delivered": sent,
                "configured": telegram_bot_service.is_configured,
            }
        else:
            return {
                "success": False,
                "error": f"Unknown tool '{tool_name}'. Available: execute_bash, read_file, write_file, list_dir, call_agent, save_memory, recall_memory, forget_memory, manage_todo, plane_list_issues, plane_create_issue, plane_update_state, list_skills, run_skill, query_knowledge_graph, send_telegram_notification",
            }
