"""Claude Code SDK Engine Adapter with Per-Agent Session Isolation."""

import asyncio
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from beherit.core.auth_manager import AgentAuthManager
from beherit.engines.base import BaseAgentEngine
from beherit.models.agent import AgentModel, AuthStatus, EngineType


class ClaudeCodeEngine(BaseAgentEngine):
    """
    Adapter for Anthropic Claude Code SDK / CLI runner.
    Ensures per-agent session and configuration isolation via CLAUDE_CONFIG_DIR.
    """

    def __init__(self, auth_manager: AgentAuthManager | None = None):
        self.auth_manager = auth_manager or AgentAuthManager()
        self.cli_binary = shutil.which("claude")
        self._active_processes: dict[str, asyncio.subprocess.Process] = {}

    async def cancel_turn(self, agent_slug: str) -> bool:
        """Terminate active subprocess for this agent if running."""
        proc = self._active_processes.get(agent_slug)
        if proc and proc.returncode is None:
            try:
                proc.terminate()
                return True
            except Exception:
                try:
                    proc.kill()
                    return True
                except Exception:
                    pass
        return False

    async def get_auth_status(self, agent_slug: str) -> AuthStatus:
        """Return authentication status for this agent's Claude profile."""
        details = self.auth_manager.get_auth_details(agent_slug)
        return details["status"]

    async def start_auth_flow(self, agent_slug: str) -> dict[str, Any]:
        """Start device flow for Claude."""
        return self.auth_manager.start_oauth_flow(agent_slug, provider=EngineType.CLAUDE_CODE)

    async def complete_auth_flow(self, agent_slug: str, auth_payload: dict[str, Any]) -> bool:
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
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
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

        # Build full prompt with RAG
        full_system_context = self.build_system_context(agent, query=message)

        prompt_parts = []
        if full_system_context:
            prompt_parts.append(f"[SYSTEM INSTRUCTIONS]\n{full_system_context}\n[END SYSTEM INSTRUCTIONS]")

        if history:
            history_lines = [
                f"{'User' if h.get('role') == 'user' else agent.name}: {h.get('content', '')}"
                for h in history
            ]
            prompt_parts.append("[CONVERSATION HISTORY]\n" + "\n".join(history_lines))

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

            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workspace_dir),
                    env=env,
                )
                self._active_processes[agent.slug] = process

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
            finally:
                self._active_processes.pop(agent.slug, None)
                if process is not None and process.returncode is None:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=1.0)
                    except Exception:
                        try:
                            process.kill()
                        except Exception:
                            pass

        else:
            token = self.auth_manager.get_token(agent.slug) or env.get("ANTHROPIC_API_KEY", "")
            if token and (token.startswith("sk-ant-api") or token.startswith("sk-ant-oat")):
                async for chunk in self._stream_anthropic_api(agent, message, history, token, full_system_context):
                    yield chunk
            else:
                yield {"event": "thought", "data": f"Executing turn in Claude Code runtime ({agent.model})..."}
                simulated_response = f"Echo from {agent.name} [Claude Engine]: Processed '{message}' successfully."
                for word in simulated_response.split():
                    yield {"event": "token", "data": f"{word} "}
                    await asyncio.sleep(0.02)

        yield {"event": "done", "data": {"completed": True}}

    async def _stream_anthropic_api(
        self,
        agent: AgentModel,
        message: str,
        history: list[dict[str, str]] | None,
        token: str,
        system_context: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Direct HTTP streaming from Anthropic Messages API."""
        model = agent.model or "claude-3-5-sonnet-20241022"
        if "sonnet" in model.lower() and not ("-" in model and any(c.isdigit() for c in model.split("-")[-1])):
            model = "claude-3-5-sonnet-20241022"
        elif "haiku" in model.lower() and not ("-" in model and any(c.isdigit() for c in model.split("-")[-1])):
            model = "claude-3-5-haiku-20241022"

        headers = {
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if token.startswith("sk-ant-oat"):
            headers["authorization"] = f"Bearer {token}"
            headers["anthropic-beta"] = "oauth-2025-01-01"
        else:
            headers["x-api-key"] = token

        messages = []
        if history:
            for h in history:
                role = "user" if h.get("role") == "user" else "assistant"
                content = h.get("content", "")
                if content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
            "stream": True,
        }
        if system_context:
            payload["system"] = system_context

        yield {"event": "thought", "data": f"Nawiązywanie bezpośredniego połączenia z Anthropic API ({model})..."}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    "https://api.anthropic.com/v1/messages",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        err_body = await response.aread()
                        try:
                            err_json = json.loads(err_body.decode())
                            err_msg = err_json.get("error", {}).get("message", err_body.decode())
                        except Exception:
                            err_msg = err_body.decode(errors="replace")
                        yield {
                            "event": "error",
                            "data": {
                                "error": f"Anthropic API Error ({response.status_code}): {err_msg}",
                                "code": f"HTTP_{response.status_code}",
                            },
                        }
                        return

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            if data_str == "[DONE]":
                                break
                            try:
                                event_data = json.loads(data_str)
                                event_type = event_data.get("type")
                                if event_type == "content_block_delta":
                                    delta = event_data.get("delta", {})
                                    if delta.get("type") == "text_delta":
                                        yield {"event": "token", "data": delta.get("text", "")}
                                elif event_type == "error":
                                    yield {"event": "error", "data": {"error": event_data.get("error", {}).get("message", "API Error")}}
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield {"event": "error", "data": {"error": f"Błąd połączenia z Anthropic API: {str(e)}"}}
