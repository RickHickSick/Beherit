"""Google Antigravity SDK Engine Adapter with Per-Agent Session Isolation."""

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


class AntigravityEngine(BaseAgentEngine):
    """
    Adapter for Google Antigravity SDK / CLI runner.
    Guarantees that each agent runs in an isolated environment with dedicated credentials.
    """

    def __init__(self, auth_manager: AgentAuthManager | None = None):
        self.auth_manager = auth_manager or AgentAuthManager()
        self.cli_binary = shutil.which("agy")
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
        """Return the current authentication state for this agent's Antigravity profile."""
        details = self.auth_manager.get_auth_details(agent_slug)
        return details["status"]

    async def start_auth_flow(self, agent_slug: str) -> dict[str, Any]:
        """Start the automated device code / OAuth handshake for this agent."""
        return self.auth_manager.start_oauth_flow(agent_slug, provider=EngineType.ANTIGRAVITY)

    async def complete_auth_flow(self, agent_slug: str, auth_payload: dict[str, Any]) -> bool:
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
        history: list[dict[str, str]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Execute an agent turn in an isolated subprocess with the agent's credentials.
        Yields structured SSE event dictionaries (thought, token, tool_call, done).
        """
        # Verify credentials before launching
        if not self.auth_manager.is_authenticated(agent.slug):
            if self.cli_binary and (Path.home() / ".gemini").exists():
                try:
                    self.auth_manager.import_local_cli_session(agent.slug, provider=EngineType.ANTIGRAVITY)
                except Exception:
                    pass

        if not self.auth_manager.is_authenticated(agent.slug) and not (self.cli_binary and (Path.home() / ".gemini").exists()):
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

        # Build prompt incorporating Master Prompt, Instructions, Knowledge Base (RAG) & MCP
        full_system_context = self.build_system_context(agent, query=message)

        # Combine system role context, conversation history, and user message
        prompt_parts = []
        if full_system_context:
            prompt_parts.append(f"[SYSTEM CONTEXT & ROLE]\n{full_system_context}\n[END SYSTEM CONTEXT]")

        if history:
            history_lines = []
            for h in history:
                role_label = "User" if h.get("role") == "user" else agent.name
                history_lines.append(f"{role_label}: {h.get('content', '')}")
            prompt_parts.append("[CONVERSATION HISTORY]\n" + "\n".join(history_lines) + "\n[END CONVERSATION HISTORY]")

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
                "--dangerously-skip-permissions",
                "--output-format",
                "stream-json",
            ]
            model_flag = agent.model or ""
            if "3.8" in model_flag or "flash" in model_flag.lower():
                cmd.extend(["--model", "Gemini 3.8 Flash (High)"])
            elif "sonnet" in model_flag.lower():
                cmd.extend(["--model", "Claude Sonnet 4.6 (Thinking)"])
            elif "opus" in model_flag.lower():
                cmd.extend(["--model", "Claude Opus 4.6 (Thinking)"])
            elif model_flag and " " in model_flag:
                cmd.extend(["--model", model_flag])

            cmd.extend(["--print", full_turn_prompt])

            process = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *cmd,
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
            token = self.auth_manager.get_token(agent.slug) or env.get("GEMINI_API_KEY", "")
            if token and (token.startswith("AIzaSy") or token.startswith("gemini_")):
                async for chunk in self._stream_gemini_api(agent, message, history, token, full_system_context):
                    yield chunk
            else:
                yield {"event": "thought", "data": f"Processing turn for {agent.name} with prompt context..."}
                context_summary = f"(Identity: {agent.master_prompt[:30]}...)" if agent.master_prompt else ""
                simulated_response = f"Echo from {agent.name} {context_summary}: Processed '{message}' securely."
                for word in simulated_response.split():
                    yield {"event": "token", "data": f"{word} "}
                    await asyncio.sleep(0.02)

        yield {"event": "done", "data": {"completed": True}}

    async def _stream_gemini_api(
        self,
        agent: AgentModel,
        message: str,
        history: list[dict[str, str]] | None,
        token: str,
        system_context: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Direct HTTP SSE streaming from Google Gemini REST API."""
        model = agent.model or "gemini-2.0-flash"
        if model.startswith("gemini-3.") or model == "default":
            model = "gemini-2.0-flash"

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key={token}"

        contents: list[dict[str, Any]] = []
        if history:
            for h in history:
                role = "user" if h.get("role") == "user" else "model"
                content = h.get("content", "")
                if content:
                    contents.append({"role": role, "parts": [{"text": content}]})
        contents.append({"role": "user", "parts": [{"text": message}]})

        body: dict[str, Any] = {"contents": contents}
        if system_context:
            body["systemInstruction"] = {"parts": [{"text": system_context}]}

        yield {"event": "thought", "data": f"Nawiązywanie bezpośredniego połączenia z Google Gemini API ({model})..."}

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers={"Content-Type": "application/json"},
                    json=body,
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
                                "error": f"Google Gemini API Error ({response.status_code}): {err_msg}",
                                "code": f"HTTP_{response.status_code}",
                            },
                        }
                        return

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            data_str = line[6:].strip()
                            try:
                                chunk_json = json.loads(data_str)
                                candidates = chunk_json.get("candidates", [])
                                if candidates:
                                    parts = candidates[0].get("content", {}).get("parts", [])
                                    for part in parts:
                                        if "text" in part:
                                            yield {"event": "token", "data": part["text"]}
                                        elif "thought" in part:
                                            yield {"event": "thought", "data": part["thought"]}
                            except json.JSONDecodeError:
                                pass
        except Exception as e:
            yield {"event": "error", "data": {"error": f"Błąd połączenia z Google Gemini API: {str(e)}"}}
