"""Embedded Model Context Protocol (MCP) Stdio Server for Beherit CapabilitiesGuard."""

import asyncio
import json
import sys
from typing import Any

from beherit.core.repository import AgentRepository
from beherit.core.tools import CapabilitiesGuard
from beherit.models.agent import AgentModel, TerminalMode


class MCPServer:
    """Line-delimited JSON-RPC 2.0 MCP Stdio Server exposing CapabilitiesGuard."""

    def __init__(self, agent_slug: str, repository: AgentRepository | None = None):
        self.agent_slug = agent_slug
        self.repo = repository or AgentRepository()
        self.guard = CapabilitiesGuard()
        self.agent: AgentModel | None = self.repo.load_agent(agent_slug)

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP tool definitions allowed by agent capabilities."""
        if not self.agent:
            return []

        tools = []
        caps = self.agent.capabilities

        if caps.terminal and caps.terminal_mode != TerminalMode.DISABLED:
            tools.append({
                "name": "execute_bash",
                "description": f"Execute a shell command inside the agent's isolated workspace sandbox. Mode: {caps.terminal_mode.value}.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command line string to run"}
                    },
                    "required": ["command"]
                }
            })

        if caps.file_read:
            tools.append({
                "name": "read_file",
                "description": "Read file contents from the agent workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to file in workspace"}
                    },
                    "required": ["path"]
                }
            })
            tools.append({
                "name": "list_dir",
                "description": "List files and directories in the agent workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path (default: '.')"}
                    }
                }
            })

        if caps.file_write:
            tools.append({
                "name": "write_file",
                "description": "Write or overwrite a file inside the agent workspace.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative file path to write"},
                        "content": {"type": "string", "description": "Text content to write"}
                    },
                    "required": ["path", "content"]
                }
            })

        # Multi-Agent Delegation Tool (BEH-060)
        tools.append({
            "name": "call_agent",
            "description": "Invoke another specialized AI agent in Beherit to delegate a subtask, consult domain expertise, or coordinate workflows.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent_slug": {
                        "type": "string",
                        "description": "The unique slug of the target agent to call (e.g. 'analityk-finansowy', 'devops-guard')"
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The specific question, problem, or delegation instruction for the subagent"
                    },
                    "context": {
                        "type": "object",
                        "description": "Optional dictionary payload / data context to pass to the subagent"
                    }
                },
                "required": ["agent_slug", "prompt"]
            }
        })

        # Long-term Memory tools (BEH-062)
        tools.append({
            "name": "save_memory",
            "description": "Save an important fact, user preference, system configuration, or rule to long-term memory (memory.md).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The specific fact or piece of information to remember"},
                    "category": {"type": "string", "description": "Category (e.g. 'Preferencje', 'Infrastruktura', 'Reguły')"}
                },
                "required": ["fact"]
            }
        })
        tools.append({
            "name": "recall_memory",
            "description": "Read all stored long-term memory facts from memory.md.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        })
        tools.append({
            "name": "forget_memory",
            "description": "Remove or forget obsolete facts from long-term memory matching a search string.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Keyword or text snippet to remove from memory"}
                },
                "required": ["pattern"]
            }
        })

        # Task scratchpad and planning tools (BEH-063)
        tools.append({
            "name": "manage_todo",
            "description": "Zarządzanie dynamicznym planem zadań i scratchpadem todo.md w trybie autonomicznym. Umożliwia dodawanie (action='add', text='...'), aktualizację statusu (action='update', item_id=N, status='done'|'in_progress'|'todo'|'cancelled'), listowanie (action='list') oraz czyszczenie zadań (action='clear').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Akcja: 'list', 'add', 'update', 'clear'"
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
        tools.append({
            "name": "plane_list_issues",
            "description": "Pobiera listę zadań z tablicy projektu Plane (np. 'todo', 'in_progress', 'done', 'backlog').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": "Filtruj zadania po statusie (np. 'todo', 'in_progress', 'done', 'backlog')"},
                    "limit": {"type": "integer", "description": "Maksymalna liczba zadań do pobrania (domyślnie 20)"}
                }
            }
        })
        tools.append({
            "name": "plane_create_issue",
            "description": "Tworzy nowe zadanie, zgłoszenie usterki lub cel na tablicy Plane w projekcie Beherit.",
            "inputSchema": {
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
        tools.append({
            "name": "plane_update_state",
            "description": "Aktualizuje status istniejącego zadania na tablicy Plane (np. przenosi do 'done', 'in_progress', 'cancelled').",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "issue_id": {"type": "string", "description": "UUID zadania lub numer sekwencyjny (np. 65 lub 'e428cd83-...')"},
                    "state": {"type": "string", "description": "Nowy status zadania: 'todo', 'in_progress', 'done', 'cancelled', 'backlog'"}
                },
                "required": ["issue_id", "state"]
            }
        })

        # Dynamic agent skills from skills/ directory (BEH-066)
        tools.append({
            "name": "list_skills",
            "description": "Zwraca listę wszystkich zarejestrowanych niestandardowych umiejętności i skryptów w katalogu skills/ agenta.",
            "inputSchema": {
                "type": "object",
                "properties": {}
            }
        })
        tools.append({
            "name": "run_skill",
            "description": "Uruchamia skrypt umiejętności (Python/Bash) z katalogu skills/ agenta przekazując opcjonalne argumenty.",
            "inputSchema": {
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
            smgr = SkillManager()
            for skill in smgr.list_skills(self.agent):
                tools.append({
                    "name": f"skill_{skill.name}",
                    "description": f"Dedykowana umiejętność ({skill.skill_type}): {skill.description}",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "args": {"type": "object", "description": "Parametry wejściowe dla skryptu"}
                        }
                    }
                })
        except Exception:
            pass

        return tools

    async def handle_request(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle a single JSON-RPC request message."""
        req_id = message.get("id")
        method = message.get("method")
        params = message.get("params", {})

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": f"beherit-{self.agent_slug}-mcp",
                        "version": "0.3.2"
                    }
                }
            }

        elif method == "notifications/initialized":
            return None

        elif method == "ping":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {}
            }

        elif method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "tools": self.get_tool_definitions()
                }
            }

        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            if not self.agent:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": f"Agent '{self.agent_slug}' not found."}
                }

            result = await self.guard.dispatch_tool(self.agent, tool_name, tool_args)
            success = result.get("success", False)
            content_text = json.dumps(result, indent=2, ensure_ascii=False) if isinstance(result, dict) else str(result)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": content_text
                        }
                    ],
                    "isError": not success
                }
            }

        else:
            if req_id is not None:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method '{method}' not found."
                    }
                }
            return None

    async def run_stdio(self):
        """Asynchronously read JSON-RPC lines from stdin and output to stdout."""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except json.JSONDecodeError as err:
                resp = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {err}"}
                }
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
                continue

            resp = await self.handle_request(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()

    def run(self):
        """Synchronous entry point."""
        asyncio.run(self.run_stdio())
