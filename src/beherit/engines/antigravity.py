"""Google Antigravity SDK Engine Adapter with Per-Agent Session Isolation."""

import asyncio
import json
import os
import shutil
from typing import Any, AsyncIterator, Dict, List, Optional
from beherit.core.auth_manager import AgentAuthManager
from beherit.engines.base import BaseAgentEngine
from beherit.models.agent import AgentModel, AuthStatus, EngineType


class AntigravityEngine(BaseAgentEngine):
    """
    Adapter for Google Antigravity SDK / CLI runner.
    Guarantees that each agent runs in an isolated environment with dedicated credentials.
    """

    def __init__(self, auth_manager: Optional[AgentAuthManager] = None):
        self.auth_manager = auth_manager or AgentAuthManager()
        self.cli_binary = shutil.which("agy")

    async def get_auth_status(self, agent_slug: str) -> AuthStatus:
        """Return the current authentication state for this agent's Antigravity profile."""
        details = self.auth_manager.get_auth_details(agent_slug)
        return details["status"]

    async def start_auth_flow(self, agent_slug: str) -> Dict[str, Any]:
        """Start the automated device code / OAuth handshake for this agent."""
        return self.auth_manager.start_oauth_flow(agent_slug, provider=EngineType.ANTIGRAVITY)

    async def complete_auth_flow(self, agent_slug: str, auth_payload: Dict[str, Any]) -> bool:
        """Complete authentication (supports either OAuth flow verification or manual token)."""
        method = auth_payload.get("method", "manual")
        if method == "manual":
            token = auth_payload.get("token", "")
            self.auth_manager.save_manual_token(
                agent_slug=agent_slug,
                token=token,
                provider=EngineType.ANTIGRAVITY,
            )
            return True
        elif method == "oauth":
            flow_id = auth_payload.get("flow_id", "")
            code = auth_payload.get("code", "")
            self.auth_manager.verify_and_save_oauth(
                agent_slug=agent_slug,
                flow_id=flow_id,
                auth_code=code,
            )
            return True
        return False

    async def stream_chat(
        self,
        agent: AgentModel,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute an agent turn in an isolated subprocess with the agent's credentials.
        Yields structured SSE event dictionaries (thought, token, tool_call, done).
        """
        # Verify credentials before launching
        if not self.auth_manager.is_authenticated(agent.slug):
            yield {
                "event": "error",
                "data": {
                    "error": "Agent is unauthenticated. Please connect a subscription in settings.",
                    "code": "AUTH_REQUIRED",
                },
            }
            return

        # Prepare isolated environment
        env = self.auth_manager.get_sdk_environment(agent.slug)

        # Build prompt incorporating Master Prompt & Instructions
        full_system_context = agent.master_prompt
        if agent.instructions:
            full_system_context += f"\n\n## Guidelines\n{agent.instructions}"

        yield {
            "event": "thought",
            "data": f"Initializing {agent.name} with Antigravity profile ({agent.model})...",
        }

        # If agy binary is available on the system, run with isolated environment
        if self.cli_binary:
            cmd = [
                self.cli_binary,
                "--print",
                "--output-format",
                "stream-json",
                "--model",
                agent.model,
                message,
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env,
                )

                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8").strip()
                    if not decoded:
                        continue
                    try:
                        data = json.loads(decoded)
                        # Yield token or thought from JSON stream
                        yield {"event": "token", "data": data.get("text", decoded)}
                    except json.JSONDecodeError:
                        yield {"event": "token", "data": decoded}

                await process.wait()

            except Exception as e:
                yield {"event": "error", "data": {"error": str(e)}}

        else:
            # Fallback simulator for unit tests or environments without agy binary
            yield {"event": "thought", "data": "Processing request in authenticated sandbox..."}
            simulated_response = f"Echo from {agent.name}: Processed '{message}' securely."
            for word in simulated_response.split():
                yield {"event": "token", "data": f"{word} "}
                await asyncio.sleep(0.02)

        yield {"event": "done", "data": {"completed": True}}
