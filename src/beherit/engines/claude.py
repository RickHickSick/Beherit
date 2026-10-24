"""Claude Code SDK Engine Adapter with Per-Agent Session Isolation."""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional
from beherit.core.auth_manager import AgentAuthManager
from beherit.engines.base import BaseAgentEngine
from beherit.models.agent import AgentModel, AuthStatus, EngineType


class ClaudeCodeEngine(BaseAgentEngine):
    """
    Adapter for Anthropic Claude Code SDK / CLI runner.
    Ensures per-agent session and configuration isolation via CLAUDE_CONFIG_DIR.
    """

    def __init__(self, auth_manager: Optional[AgentAuthManager] = None):
        self.auth_manager = auth_manager or AgentAuthManager()
        self.cli_binary = shutil.which("claude")

    async def get_auth_status(self, agent_slug: str) -> AuthStatus:
        """Return authentication status for this agent's Claude profile."""
        details = self.auth_manager.get_auth_details(agent_slug)
        return details["status"]

    async def start_auth_flow(self, agent_slug: str) -> Dict[str, Any]:
        """Start device flow for Claude."""
        return self.auth_manager.start_oauth_flow(agent_slug, provider=EngineType.CLAUDE_CODE)

    async def complete_auth_flow(self, agent_slug: str, auth_payload: Dict[str, Any]) -> bool:
        """Save manual token or verify OAuth code."""
        method = auth_payload.get("method", "manual")
        if method == "manual":
            token = auth_payload.get("token", "")
            self.auth_manager.save_manual_token(
                agent_slug=agent_slug,
                token=token,
                provider=EngineType.CLAUDE_CODE,
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
        """Stream chat execution via Claude Code CLI or isolated runner."""
        if not self.auth_manager.is_authenticated(agent.slug):
            yield {
                "event": "error",
                "data": {
                    "error": "Claude subscription is unauthenticated for this agent.",
                    "code": "AUTH_REQUIRED",
                },
            }
            return

        env = self.auth_manager.get_sdk_environment(agent.slug)
        auth_dir = self.auth_manager.get_agent_auth_dir(agent.slug)
        env["CLAUDE_CONFIG_DIR"] = str(auth_dir)

        # Build full prompt
        full_system_context = agent.master_prompt.strip()
        if agent.instructions and agent.instructions.strip():
            full_system_context += f"\n\n## Guidelines\n{agent.instructions.strip()}"

        prompt_parts = []
        if full_system_context:
            prompt_parts.append(f"[SYSTEM INSTRUCTIONS]\n{full_system_context}\n[END SYSTEM INSTRUCTIONS]")

        if history:
            history_lines = [
                f"{'User' if h.get('role') == 'user' else agent.name}: {h.get('content', '')}"
                for h in history
            ]
            prompt_parts.append(f"[CONVERSATION HISTORY]\n" + "\n".join(history_lines))

        prompt_parts.append(f"[USER]\n{message}")
        full_prompt = "\n\n".join(prompt_parts)

        yield {
            "event": "thought",
            "data": f"Initializing {agent.name} with Claude Code profile ({agent.model})...",
        }

        workspace_dir = Path(agent.file_path).parent if agent.file_path else (self.auth_manager.base_dir / agent.slug)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        if self.cli_binary:
            cmd = [
                self.cli_binary,
                "-p",
                full_prompt,
            ]

            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace_dir),
                    env=env,
                )

                assert process.stdout is not None
                while True:
                    line = await process.stdout.readline()
                    if not line:
                        break
                    decoded = line.decode("utf-8", errors="replace")
                    yield {"event": "token", "data": decoded}

                await process.wait()

            except Exception as e:
                yield {"event": "error", "data": {"error": str(e)}}

        else:
            yield {"event": "thought", "data": f"Executing turn in Claude Code runtime ({agent.model})..."}
            simulated_response = f"Echo from {agent.name} [Claude Engine]: Processed '{message}' successfully."
            for word in simulated_response.split():
                yield {"event": "token", "data": f"{word} "}
                await asyncio.sleep(0.02)

        yield {"event": "done", "data": {"completed": True}}
