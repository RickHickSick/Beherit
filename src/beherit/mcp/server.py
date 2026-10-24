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
                        "version": "0.2.0"
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
