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
        full_system_context = agent.master_prompt.strip()
        if agent.instructions and agent.instructions.strip():
            full_system_context += f"\n\n## Operational Guidelines\n{agent.instructions.strip()}"

        # Combine system role context, conversation history, and user message
        prompt_parts = []
        if full_system_context:
            prompt_parts.append(f"[SYSTEM CONTEXT & ROLE]\n{full_system_context}\n[END SYSTEM CONTEXT]")

        if history:
            history_lines = []
            for h in history:
                role_label = "User" if h.get("role") == "user" else agent.name
                history_lines.append(f"{role_label}: {h.get('content', '')}")
            prompt_parts.append(f"[CONVERSATION HISTORY]\n" + "\n".join(history_lines) + "\n[END CONVERSATION HISTORY]")

        prompt_parts.append(f"[CURRENT USER REQUEST]\n{message}")
        full_turn_prompt = "\n\n".join(prompt_parts)

        yield {
            "event": "thought",
            "data": f"Initializing {agent.name} with Antigravity profile ({agent.model})...",
        }

        # Resolve agent workspace directory
        workspace_dir = Path(agent.file_path).parent if agent.file_path else (self.auth_manager.base_dir / agent.slug)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # If agy binary is available on the system, run with isolated environment
        if self.cli_binary:
            cmd = [
                self.cli_binary,
                "--output-format",
                "stream-json",
                "--model",
                agent.model,
                "--print",
                full_turn_prompt,
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
                    decoded = line.decode("utf-8").strip()
                    if not decoded:
                        continue
                    try:
                        data = json.loads(decoded)
                        event_type = data.get("event")

                        # Handle agy stream-json step events
                        if event_type == "step_update":
                            step_data = data.get("step_update", {})
                            text_delta = step_data.get("text_delta")
                            if text_delta:
                                yield {"event": "token", "data": text_delta}
                        elif event_type == "result":
                            pass
                        elif event_type == "thought":
                            yield {"event": "thought", "data": data.get("data", decoded)}
                        elif event_type == "error":
                            yield {"event": "error", "data": data.get("error", decoded)}
                        else:
                            text = data.get("text") or data.get("delta")
                            if text:
                                yield {"event": "token", "data": text}
                    except json.JSONDecodeError:
                        yield {"event": "token", "data": decoded}

                await process.wait()
                if process.returncode != 0 and process.stderr:
                    err_bytes = await process.stderr.read()
                    err_text = err_bytes.decode("utf-8", errors="replace").strip()
                    if err_text:
                        yield {"event": "error", "data": {"error": err_text}}

            except Exception as e:
                yield {"event": "error", "data": {"error": str(e)}}

        else:
            # Fallback simulator for unit tests or environments without agy binary
            yield {"event": "thought", "data": f"Processing turn for {agent.name} with prompt context..."}
            context_summary = f"(Identity: {agent.master_prompt[:30]}...)" if agent.master_prompt else ""
            simulated_response = f"Echo from {agent.name} {context_summary}: Processed '{message}' securely."
            for word in simulated_response.split():
                yield {"event": "token", "data": f"{word} "}
                await asyncio.sleep(0.02)

        yield {"event": "done", "data": {"completed": True}}
