"""REST API Routes for Agent CRUD, Per-Agent Auth, and Streaming Chat."""

import asyncio
import hmac
import json
import re
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from beherit.config import settings
from beherit.core.auth_manager import AgentAuthManager
from beherit.core.database import DatabaseManager
from beherit.core.repository import AgentRepository
from beherit.core.scheduler import AgentScheduler
from beherit.core.tools import CapabilitiesGuard
from beherit.engines import EngineRegistry
from beherit.models.agent import (
    AgentCapabilities,
    AgentModel,
    AgentSchedule,
    EngineType,
    MCPServerConfig,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])
knowledge_router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
history_router = APIRouter(prefix="/api/history", tags=["history"])
reports_router = APIRouter(prefix="/api/reports", tags=["reports"])
auth_guard_router = APIRouter(prefix="/api/auth", tags=["auth_guard"])

repo = AgentRepository()
auth_mgr = AgentAuthManager()
db_mgr = DatabaseManager()
engine_registry = EngineRegistry(auth_manager=auth_mgr)
scheduler = AgentScheduler(repository=repo, db_manager=db_mgr, engine_registry=engine_registry)
active_chat_tasks: dict[str, asyncio.Task] = {}


# Request schemas
class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str = Field(default="")
    engine: EngineType = Field(default=EngineType.ANTIGRAVITY)
    model: str = Field(default="gemini-3.8-flash")
    master_prompt: str = Field(default="You are an autonomous AI assistant.")
    instructions: str | None = Field(default="")
    capabilities: AgentCapabilities | None = None
    schedule: AgentSchedule | None = None
    mcp_servers: dict[str, MCPServerConfig] | None = None
    custom_target_dir: str | None = Field(
        default=None,
        description="Optional custom path (e.g. specific Obsidian folder) to save this agent",
    )
    auth_type: str | None = Field(default="api", description="Key type: 'api' or 'sdk'")
    auth_token: str | None = Field(default=None, description="Initial API key or SDK session token")
    import_local_auth: bool = Field(default=False, description="Automatically import local host CLI session")


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    engine: EngineType | None = None
    model: str | None = None
    master_prompt: str | None = None
    instructions: str | None = None
    capabilities: AgentCapabilities | None = None
    schedule: AgentSchedule | None = None
    mcp_servers: dict[str, MCPServerConfig] | None = None


class SaveKnowledgeRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=100)
    content: str = Field(default="")


class AddKnowledgeLinkRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=1000)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default="")
    category: str | None = Field(default=None)


class FetchUrlMetadataRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=1000)


class AgentRunRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Task prompt or instruction for the agent")
    context: dict[str, Any] | list[Any] | str | None = Field(default=None, description="Optional extra data or payload from n8n")
    destination_file: str | None = Field(default=None, description="Optional destination path to save output (e.g. in Obsidian vault)")
    file_mode: str = Field(default="overwrite", description="'overwrite' or 'append'")
    save_report: bool = Field(default=True, description="Whether to save to reports/ and history")
    callback_url: str | None = Field(default=None, description="Optional outbound webhook URL (e.g. n8n webhook) to notify upon completion")
    async_execution: bool = Field(default=False, description="Run asynchronously in background")


async def scrape_link_metadata(url: str) -> dict[str, str]:
    """Fetch webpage title, description and category from URL or YouTube oEmbed."""
    import html
    from urllib.parse import urlparse
    import httpx

    url = url.strip()
    is_yt = "youtube.com" in url or "youtu.be" in url
    default_cat = "Wideo YouTube" if is_yt else "Strona WWW / Dokumentacja"

    if is_yt:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get(oembed_url)
                if res.status_code == 200:
                    data = res.json()
                    title = data.get("title", "").strip()
                    author = data.get("author_name", "").strip()
                    desc = f"Kanał YouTube: {author}" if author else ""
                    return {
                        "title": title or "Wideo YouTube",
                        "description": desc,
                        "category": "Wideo YouTube",
                    }
        except Exception:
            pass

    # Generic webpage title scraping
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True, headers=headers) as client:
            res = await client.get(url)
            if res.status_code == 200:
                html_text = res.text
                title = ""
                # 1. OpenGraph og:title
                og_match = re.search(r'<meta\s+property=["\']og:title["\']\s+content=["\'](.*?)["\']', html_text, re.IGNORECASE)
                if not og_match:
                    og_match = re.search(r'<meta\s+content=["\'](.*?)["\']\s+property=["\']og:title["\']', html_text, re.IGNORECASE)
                if og_match:
                    title = og_match.group(1).strip()

                # 2. HTML <title>
                if not title:
                    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.IGNORECASE | re.DOTALL)
                    if title_match:
                        title = re.sub(r"\s+", " ", title_match.group(1)).strip()

                # Description
                desc = ""
                desc_match = re.search(r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']', html_text, re.IGNORECASE)
                if not desc_match:
                    desc_match = re.search(r'<meta\s+property=["\']og:description["\']\s+content=["\'](.*?)["\']', html_text, re.IGNORECASE)
                if desc_match:
                    desc = desc_match.group(1).strip()

                if title:
                    title = html.unescape(title)[:200].strip()
                    desc = html.unescape(desc)[:500].strip() if desc else ""
                    return {
                        "title": title,
                        "description": desc,
                        "category": default_cat,
                    }
    except Exception:
        pass

    try:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        fallback_title = (parsed.netloc + path).strip("/")
        return {
            "title": fallback_title or url,
            "description": "",
            "category": default_cat,
        }
    except Exception:
        return {
            "title": url,
            "description": "",
            "category": default_cat,
        }


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=20)


class VerifyTokenRequest(BaseModel):
    token: str


class ManualTokenRequest(BaseModel):
    token: str = Field(..., min_length=1)
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)
    auth_type: str | None = Field(default="api", description="Key type: 'api' or 'sdk'")


class OAuthStartRequest(BaseModel):
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)


class OAuthVerifyRequest(BaseModel):
    flow_id: str = Field(..., min_length=1)
    auth_code: str = Field(..., min_length=1)


class DevicePollRequest(BaseModel):
    flow_id: str = Field(..., min_length=1)


class ImportLocalAuthRequest(BaseModel):
    provider: EngineType = Field(default=EngineType.CLAUDE_CODE)


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str | None = Field(default=None, description="Optional ID of active chat session")
    history: list[dict[str, str]] | None = Field(default_factory=list)


class CreateChatSessionRequest(BaseModel):
    title: str | None = Field(default=None, description="Optional chat session title")


class ExecuteToolRequest(BaseModel):
    tool: str = Field(..., description="Tool name: execute_bash, read_file, write_file, list_dir")
    args: dict[str, Any] = Field(default_factory=dict)


# --- Agent CRUD Endpoints ---


@router.get("", response_model=dict[str, list[dict[str, Any]]])
async def list_agents():
    """List all agents registered in the storage directory."""
    agents = repo.list_agents()
    return {"agents": [a.model_dump(mode="json") for a in agents]}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent(payload: CreateAgentRequest):
    """Create a new agent as a Markdown directory structure."""
    if repo.load_agent(payload.slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent with slug '{payload.slug}' already exists.",
        )

    agent_model = AgentModel(
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        engine=payload.engine,
        model=payload.model,
        master_prompt=payload.master_prompt,
        instructions=payload.instructions,
        capabilities=payload.capabilities or AgentCapabilities(),
        schedule=payload.schedule or AgentSchedule(),
        mcp_servers=payload.mcp_servers or {},
    )

    target_dir = Path(payload.custom_target_dir) if payload.custom_target_dir else None
    saved_path = repo.save_agent(agent_model, target_dir=target_dir)

    # Automatically connect agent via SDK token, API key, or host session import
    if payload.import_local_auth:
        try:
            auth_mgr.import_local_cli_session(agent_model.slug, provider=agent_model.engine)
        except Exception:
            pass
    elif payload.auth_token and payload.auth_token.strip():
        auth_mgr.save_manual_token(
            agent_slug=agent_model.slug,
            token=payload.auth_token.strip(),
            provider=agent_model.engine,
            extra_metadata={"auth_type": payload.auth_type or "api"},
        )

    scheduler.sync_all_jobs()

    return {
        "status": "created",
        "slug": agent_model.slug,
        "path": str(saved_path),
        "agent": repo.load_agent(agent_model.slug).model_dump(mode="json"),
    }


@router.get("/models/available")
async def get_available_models(engine: str = "antigravity", agent_slug: str | None = None):
    """Dynamically fetch or refresh available LLM models for the given engine/account (BEH-043)."""
    import os
    import httpx

    models: list[dict[str, str]] = []
    source = "curated"

    token = None
    if agent_slug:
        token = auth_mgr.get_token(agent_slug)

    engine_clean = engine.lower().replace("_", "-")

    if engine_clean in ["antigravity", "gemini"]:
        gemini_key = token or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if gemini_key and not gemini_key.startswith("oauth_"):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        fetched = []
                        for m in data.get("models", []):
                            m_id = m.get("name", "").replace("models/", "")
                            if "gemini" in m_id:
                                disp = m.get("displayName", m_id)
                                fetched.append({"id": m_id, "name": f"{disp} ({m_id})"})
                        if fetched:
                            models = fetched
                            source = "live_api"
            except Exception:
                pass

        if not models:
            models = [
                {"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash (Domyślny, szybki)"},
                {"id": "gemini-3.8-pro", "name": "Gemini 3.8 Pro (Złożona analiza i reasoning)"},
                {"id": "gemini-2.5-pro", "name": "Gemini 2.5 Pro (Thinking / Advanced Reasoning)"},
                {"id": "gemini-2.5-flash", "name": "Gemini 2.5 Flash (Ultra szybki)"},
                {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash"},
                {"id": "claude-3-7-sonnet", "name": "Claude 3.7 Sonnet (W sesji Antigravity)"},
            ]

    elif engine_clean in ["claude-code", "claude", "anthropic"]:
        anthropic_key = token or os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key and anthropic_key.startswith("sk-ant-"):
            try:
                url = "https://api.anthropic.com/v1/models"
                headers = {"x-api-key": anthropic_key, "anthropic-version": "2023-06-01"}
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        fetched = []
                        for m in data.get("data", []):
                            m_id = m.get("id", "")
                            disp = m.get("display_name", m_id)
                            fetched.append({"id": m_id, "name": f"{disp} ({m_id})"})
                        if fetched:
                            models = fetched
                            source = "live_api"
            except Exception:
                pass

        if not models:
            models = [
                {"id": "claude-3-7-sonnet-20250219", "name": "Claude 3.7 Sonnet (Thinking / domyślny)"},
                {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet v2"},
                {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku (Lekki i szybki)"},
                {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus (Głębokie rozumowanie)"},
            ]

    elif engine_clean in ["codex", "openai"]:
        openai_key = token or os.getenv("OPENAI_API_KEY")
        if openai_key and openai_key.startswith("sk-"):
            try:
                url = "https://api.openai.com/v1/models"
                headers = {"Authorization": f"Bearer {openai_key}"}
                async with httpx.AsyncClient(timeout=4.0) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        fetched = []
                        for m in data.get("data", []):
                            m_id = m.get("id", "")
                            if any(m_id.startswith(p) for p in ["gpt-4o", "o1", "o3", "gpt-4-turbo", "chatgpt-"]):
                                fetched.append({"id": m_id, "name": m_id})
                        if fetched:
                            fetched.sort(key=lambda x: x["id"])
                            models = fetched
                            source = "live_api"
            except Exception:
                pass

        if not models:
            models = [
                {"id": "gpt-4o", "name": "GPT-4o (Wszechstronny / multimodalny)"},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini (Szybki i ekonomiczny)"},
                {"id": "o3-mini", "name": "o3-mini (Zaawansowane wnioskowanie)"},
                {"id": "o1", "name": "o1 (Reasoning / deep thought)"},
                {"id": "o1-mini", "name": "o1-mini (Szybki reasoning)"},
                {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
            ]

    # Include local Ollama models if service is running
    ollama_models: list[dict[str, str]] = []
    try:
        ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        async with httpx.AsyncClient(timeout=1.0) as client:
            resp = await client.get(f"{ollama_host}/api/tags")
            if resp.status_code == 200:
                for om in resp.json().get("models", []):
                    m_name = om.get("name", "")
                    if m_name:
                        ollama_models.append({"id": f"ollama/{m_name}", "name": f"🦙 Ollama: {m_name}"})
    except Exception:
        pass

    return {
        "engine": engine,
        "models": models,
        "ollama_models": ollama_models,
        "source": source,
        "count": len(models),
    }


@router.get("/{slug}")
async def get_agent(slug: str):
    """Get single agent specification and status."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return agent.model_dump(mode="json")


@router.put("/{slug}")
async def update_agent(slug: str, payload: UpdateAgentRequest):
    """Update agent metadata and prompt."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    if payload.name is not None:
        agent.name = payload.name
    if payload.description is not None:
        agent.description = payload.description
    if payload.engine is not None:
        agent.engine = payload.engine
    if payload.model is not None:
        agent.model = payload.model
    if payload.master_prompt is not None:
        agent.master_prompt = payload.master_prompt
    if payload.instructions is not None:
        agent.instructions = payload.instructions
    if payload.capabilities is not None:
        agent.capabilities = payload.capabilities
    if payload.schedule is not None:
        agent.schedule = payload.schedule
    if payload.mcp_servers is not None:
        agent.mcp_servers = payload.mcp_servers

    repo.save_agent(agent)
    scheduler.sync_all_jobs()
    return repo.load_agent(slug).model_dump(mode="json")


@router.delete("/{slug}", status_code=status.HTTP_200_OK)
async def delete_agent(slug: str):
    """Delete an agent directory and clear its auth session and database records."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    auth_mgr.clear_session(slug)
    db_mgr.delete_agent_data(slug)
    deleted = repo.delete_agent(slug)
    return {"status": "deleted", "slug": slug, "success": deleted}


# --- Per-Agent Authentication Endpoints ---


@router.get("/{slug}/auth/status")
async def get_agent_auth_status(slug: str):
    """Get authentication details for this specific agent's subscription."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    return auth_mgr.get_auth_details(slug)


@router.post("/{slug}/auth/manual")
async def save_manual_token(slug: str, payload: ManualTokenRequest):
    """Manually paste session token/credentials for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    try:
        details = auth_mgr.save_manual_token(
            agent_slug=slug,
            token=payload.token,
            provider=payload.provider,
            extra_metadata={"auth_type": payload.auth_type or "api"},
        )
        return {"status": "success", "auth": details}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{slug}/auth/start")
async def start_oauth_flow(slug: str, payload: OAuthStartRequest):
    """Initiate an automated OAuth / Device Code handshake for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    flow_info = auth_mgr.start_oauth_flow(slug, provider=payload.provider)
    return flow_info


@router.post("/{slug}/auth/verify")
async def verify_oauth_code(slug: str, payload: OAuthVerifyRequest):
    """Complete automated OAuth handshake using the received auth code."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    try:
        details = auth_mgr.verify_and_save_oauth(
            agent_slug=slug,
            flow_id=payload.flow_id,
            auth_code=payload.auth_code,
        )
        return {"status": "success", "auth": details}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{slug}/auth/device-poll")
async def poll_device_auth(slug: str, payload: DevicePollRequest):
    """Poll RFC 8628 device authorization status."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    try:
        result = auth_mgr.poll_device_token(slug, payload.flow_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{slug}/auth/refresh")
async def refresh_agent_token(slug: str):
    """Refresh active access token using refresh_token."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    try:
        result = auth_mgr.refresh_access_token(slug)
        return result
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{slug}/auth/disconnect")
async def disconnect_agent_auth(slug: str):
    """Disconnect subscription credentials for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    auth_mgr.clear_session(slug)
    return {"status": "disconnected", "auth": auth_mgr.get_auth_details(slug)}


@router.get("/auth/local-detect")
async def detect_local_auth_global():
    """Detect available local CLI sessions on server without requiring agent slug."""
    return auth_mgr.detect_local_cli_credentials()


@router.get("/{slug}/auth/local-detect")
async def detect_local_auth(slug: str):
    """Detect available local CLI sessions on server (BEH-032)."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return auth_mgr.detect_local_cli_credentials()


@router.post("/{slug}/auth/import-local")
async def import_local_auth(slug: str, payload: ImportLocalAuthRequest):
    """Import local host CLI credentials into agent's isolated .auth dir (BEH-032)."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    try:
        details = auth_mgr.import_local_cli_session(slug, provider=payload.provider)
        return {"status": "success", "auth": details}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/{slug}/auth/test")
async def test_agent_auth(slug: str):
    """Validate and test agent credentials connection (BEH-033 Auth Healthcheck)."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return auth_mgr.test_agent_connection(slug)


# --- Background Scheduler & History Endpoints ---


@router.get("/{slug}/history")
async def get_agent_job_history(slug: str, limit: int = 50):
    """Retrieve job execution audit history for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return {"history": db_mgr.get_agent_history(slug, limit=limit)}


@router.post("/{slug}/run-now")
async def trigger_agent_job_now(slug: str, job_id: str | None = None, prompt: str | None = None):
    """Trigger an autonomous job run immediately on-demand and dispatch multi-channel reports (BEH-046)."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    target_prompt = prompt
    target_dest = None

    if job_id and agent.schedule.jobs:
        match_job = next((j for j in agent.schedule.jobs if j.id == job_id), None)
        if match_job:
            target_prompt = match_job.prompt or target_prompt
            target_dest = match_job.destination_file

    result = await scheduler.execute_agent_job(
        agent_slug=slug,
        custom_prompt=target_prompt,
        destination_file=target_dest,
    )
    return result


@router.post("/{slug}/run")
async def run_agent_external(slug: str, payload: AgentRunRequest):
    """Universal Webhook & REST API to trigger an agent from n8n, cURL, or external services (BEH-048)."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    # Combine prompt with optional context payload if provided
    final_prompt = payload.prompt.strip()
    if payload.context is not None:
        if isinstance(payload.context, (dict, list)):
            ctx_str = json.dumps(payload.context, ensure_ascii=False, indent=2)
        else:
            ctx_str = str(payload.context).strip()
        final_prompt = f"{final_prompt}\n\n### Dane Wejściowe (Context):\n```json\n{ctx_str}\n```"

    if payload.async_execution:
        asyncio.create_task(
            scheduler.execute_agent_job(
                agent_slug=slug,
                custom_prompt=final_prompt,
                destination_file=payload.destination_file,
                file_mode=payload.file_mode,
                save_report=payload.save_report,
                callback_url=payload.callback_url,
                job_source="webhook_n8n",
            )
        )
        return {
            "status": "accepted",
            "message": f"Zadanie dla agenta {slug} zostało zakolejkowane w tle.",
            "agent_slug": slug,
            "async": True,
        }

    result = await scheduler.execute_agent_job(
        agent_slug=slug,
        custom_prompt=final_prompt,
        destination_file=payload.destination_file,
        file_mode=payload.file_mode,
        save_report=payload.save_report,
        callback_url=payload.callback_url,
        job_source="webhook_n8n",
    )
    return result


@history_router.get("")
async def get_recent_jobs_history(limit: int = 50):
    """Retrieve global recent job execution history."""
    return {"history": db_mgr.get_recent_history(limit=limit)}


# --- Markdown Report Viewer Endpoint ---


@reports_router.get("/view")
async def view_report(path: str):
    """Safely read and return a markdown report file."""
    report_file = Path(path).resolve()
    if not report_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report file not found.")
    if not (report_file.name.endswith(".md") or report_file.name.endswith(".txt")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only markdown or text reports can be viewed.",
        )
    forbidden_prefixes = ["/etc", "/proc", "/sys", "/dev", "/root", "/var/run"]
    resolved_str = str(report_file)
    if any(resolved_str.startswith(p) for p in forbidden_prefixes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    try:
        content = report_file.read_text(encoding="utf-8")
        return {
            "path": resolved_str,
            "filename": report_file.name,
            "content": content,
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@reports_router.get("/artifact")
async def view_report_artifact(path: str):
    """Safely stream a generated media artifact, image, video, or data file (BEH-047)."""
    artifact_file = Path(path).resolve()
    if not artifact_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact file not found.")

    forbidden_prefixes = ["/etc", "/proc", "/sys", "/dev", "/root", "/var/run"]
    resolved_str = str(artifact_file)
    if any(resolved_str.startswith(p) for p in forbidden_prefixes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied.")

    import mimetypes
    from fastapi.responses import FileResponse

    mime_type, _ = mimetypes.guess_type(resolved_str)
    return FileResponse(
        path=resolved_str,
        media_type=mime_type or "application/octet-stream",
        filename=artifact_file.name,
    )


# --- System Auth Guard Endpoints ---


@auth_guard_router.get("/status")
async def get_system_auth_status():
    """Check if BEHERIT_ACCESS_TOKEN protection is enabled on the server."""
    return {
        "auth_required": bool(settings.BEHERIT_ACCESS_TOKEN),
    }


@auth_guard_router.post("/verify")
async def verify_system_token(payload: VerifyTokenRequest):
    """Verify system PIN or access token."""
    if not settings.BEHERIT_ACCESS_TOKEN:
        return {"valid": True, "auth_required": False}

    valid = hmac.compare_digest(
        payload.token.strip().encode("utf-8"),
        settings.BEHERIT_ACCESS_TOKEN.strip().encode("utf-8"),
    )
    return {"valid": valid, "auth_required": True}


# --- Agent Knowledge Base Endpoints ---


@router.get("/{slug}/knowledge")
async def list_agent_knowledge(slug: str):
    """List documents and assets stored in the agent's knowledge base directory."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    files = []
    for f in sorted(knowledge_dir.iterdir()):
        if f.is_file():
            stat = f.stat()
            ext = f.suffix.lower()
            if ext == ".pdf":
                file_type = "pdf"
            elif ext in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
                file_type = "image"
            elif f.name.startswith("link_"):
                file_type = "link"
            elif ext in {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}:
                file_type = "document"
            else:
                file_type = "other"

            files.append({
                "filename": f.name,
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
                "file_type": file_type,
            })
    return {"slug": slug, "files": files}


@router.get("/{slug}/knowledge/{filename}")
async def get_agent_knowledge_file(slug: str, filename: str):
    """Read a specific document or asset metadata from the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(filename).name
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    target_file = agent_dir / "knowledge" / safe_filename

    if not target_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")

    ext = target_file.suffix.lower()
    is_binary = ext in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}

    if is_binary:
        from beherit.core.rag import extract_file_content

        extracted = extract_file_content(target_file)
        return {
            "filename": safe_filename,
            "is_binary": True,
            "file_type": "pdf" if ext == ".pdf" else "image",
            "size_bytes": target_file.stat().st_size,
            "content": extracted,
        }

    content = target_file.read_text(encoding="utf-8", errors="replace")
    return {
        "filename": safe_filename,
        "is_binary": False,
        "file_type": "link" if safe_filename.startswith("link_") else "document",
        "size_bytes": target_file.stat().st_size,
        "content": content,
    }


@router.get("/{slug}/knowledge/{filename}/raw")
async def get_raw_knowledge_file(slug: str, filename: str):
    """Serve raw binary asset (PDF, Image) for direct inline display or download in browser."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(filename).name
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    target_file = agent_dir / "knowledge" / safe_filename

    if not target_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")

    ext = target_file.suffix.lower()
    media_types = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".md": "text/markdown; charset=utf-8",
        ".txt": "text/plain; charset=utf-8",
        ".json": "application/json",
        ".csv": "text/csv; charset=utf-8",
    }
    media_type = media_types.get(ext, "application/octet-stream")

    return FileResponse(
        path=target_file,
        media_type=media_type,
        filename=safe_filename,
        headers={"Content-Disposition": f"inline; filename=\"{safe_filename}\""},
    )


@router.post("/{slug}/knowledge/upload")
async def upload_agent_knowledge_files(slug: str, files: list[UploadFile] = File(...)):
    """Upload one or more files (PDF, images, text, markdown) to the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        safe_filename = Path(f.filename or "uploaded_file").name
        if not safe_filename or safe_filename.startswith("."):
            continue
        target_file = knowledge_dir / safe_filename
        data = await f.read()
        target_file.write_bytes(data)
        uploaded.append(safe_filename)

    # Sync RAG index
    from beherit.core.rag import KnowledgeRAG

    rag = KnowledgeRAG(db_manager=db_mgr)
    total_chunks = rag.sync_agent_knowledge(slug, knowledge_dir)

    return {
        "status": "uploaded",
        "count": len(uploaded),
        "uploaded_files": uploaded,
        "total_chunks": total_chunks,
    }


@router.post("/knowledge/fetch-metadata")
async def fetch_url_metadata_endpoint(payload: FetchUrlMetadataRequest):
    """Fetch title, description and category from URL or YouTube video."""
    return await scrape_link_metadata(payload.url)


@knowledge_router.post("/fetch-metadata")
async def fetch_url_metadata_direct_endpoint(payload: FetchUrlMetadataRequest):
    """Fetch title, description and category from URL or YouTube video."""
    return await scrape_link_metadata(payload.url)


@router.post("/{slug}/knowledge/link")
async def add_agent_knowledge_link(slug: str, payload: AddKnowledgeLinkRequest):
    """Add external reference link (Website, documentation, YouTube video) to the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    final_title = payload.title.strip() if payload.title and payload.title.strip() else ""
    final_desc = payload.description.strip() if payload.description and payload.description.strip() else ""
    final_cat = payload.category

    # If title wasn't provided, auto-scrape from URL
    if not final_title:
        meta = await scrape_link_metadata(payload.url)
        final_title = meta.get("title") or payload.url.strip()
        if not final_desc:
            final_desc = meta.get("description", "")
        if not final_cat:
            final_cat = meta.get("category")

    if not final_cat:
        is_yt = "youtube.com" in payload.url or "youtu.be" in payload.url
        final_cat = "Wideo YouTube" if is_yt else "Strona WWW / Dokumentacja"

    # Clean title to valid filename
    clean_slug = re.sub(r"[^\w\-_]", "_", final_title.strip().lower())[:40].strip("_")
    if not clean_slug:
        clean_slug = f"source_{int(time.time())}"
    filename = f"link_{clean_slug}.md"
    target_file = knowledge_dir / filename

    notes_section = final_desc if final_desc else "(Brak dodatkowych notatek)"
    md_content = (
        f"# 🔗 {final_title.strip()}\n\n"
        f"- **Kategoria:** {final_cat}\n"
        f"- **Adres URL:** {payload.url.strip()}\n"
        f"- **Data dodania:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"## Opis / Transkrypcja / Notatki Kluczowe\n"
        f"{notes_section}\n"
    )

    target_file.write_text(md_content, encoding="utf-8")

    from beherit.core.rag import KnowledgeRAG

    rag = KnowledgeRAG(db_manager=db_mgr)
    total_chunks = rag.sync_agent_knowledge(slug, knowledge_dir)

    return {
        "status": "created",
        "filename": filename,
        "title": final_title,
        "url": payload.url,
        "total_chunks": total_chunks,
    }


@router.post("/{slug}/knowledge")
async def save_agent_knowledge_file(slug: str, payload: SaveKnowledgeRequest):
    """Create or update a document in the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(payload.filename).name
    if not safe_filename or safe_filename.startswith("."):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename.")

    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    target_file = knowledge_dir / safe_filename

    target_file.write_text(payload.content, encoding="utf-8")

    # Sync RAG
    from beherit.core.rag import KnowledgeRAG

    rag = KnowledgeRAG(db_manager=db_mgr)
    total_chunks = rag.sync_agent_knowledge(slug, knowledge_dir)

    return {
        "status": "saved",
        "filename": safe_filename,
        "size_bytes": len(payload.content),
        "total_chunks": total_chunks,
    }


@router.delete("/{slug}/knowledge/{filename}")
async def delete_agent_knowledge_file(slug: str, filename: str):
    """Delete a document or asset from the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(filename).name
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    target_file = knowledge_dir / safe_filename

    if not target_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")

    target_file.unlink()

    # Sync RAG
    from beherit.core.rag import KnowledgeRAG

    rag = KnowledgeRAG(db_manager=db_mgr)
    total_chunks = rag.sync_agent_knowledge(slug, knowledge_dir)

    return {"status": "deleted", "filename": safe_filename, "total_chunks": total_chunks}


@router.post("/{slug}/knowledge/search")
async def search_agent_knowledge(slug: str, payload: KnowledgeSearchRequest):
    """Semantic Local RAG search over the agent's knowledge documents."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    from beherit.core.rag import KnowledgeRAG

    rag = KnowledgeRAG(db_manager=db_mgr)
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    knowledge_dir = agent_dir / "knowledge"
    if knowledge_dir.exists():
        rag.sync_agent_knowledge(slug, knowledge_dir)

    results = rag.search(slug, query=payload.query, top_k=payload.top_k)
    return {
        "slug": slug,
        "query": payload.query,
        "results_count": len(results),
        "results": results,
    }


# --- Chat Sessions & Persistence Endpoints ---


@router.get("/{slug}/chats")
async def list_agent_chat_sessions(slug: str, limit: int = 30):
    """List stored chat sessions for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return {"sessions": db_mgr.get_agent_sessions(slug, limit=limit)}


@router.post("/{slug}/chats", status_code=status.HTTP_201_CREATED)
async def create_agent_chat_session(slug: str, payload: CreateChatSessionRequest):
    """Create a new conversational thread for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    session_id = db_mgr.create_chat_session(slug, title=payload.title)
    return {"status": "created", "session_id": session_id}


@router.get("/{slug}/chats/{session_id}")
async def get_agent_chat_messages(slug: str, session_id: str):
    """Retrieve full message history for a chat session."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    messages = db_mgr.get_session_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@router.delete("/{slug}/chats/{session_id}")
async def delete_agent_chat_session(slug: str, session_id: str):
    """Delete a chat session and all its messages."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    deleted = db_mgr.delete_chat_session(session_id)
    return {"status": "deleted", "success": deleted}


# --- Tool Execution Endpoint ---


@router.post("/{slug}/tools/execute")
async def execute_agent_tool(slug: str, payload: ExecuteToolRequest):
    """Execute a tool in the agent workspace adhering to its capabilities."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    guard = CapabilitiesGuard()
    result = await guard.dispatch_tool(agent, payload.tool, payload.args)
    return {"status": "success" if result.get("success") else "failed", "result": result}


# --- Streaming SSE Chat Endpoint with Multi-Turn Persistence ---


@router.post("/{slug}/chat")
async def stream_agent_chat(slug: str, payload: ChatMessageRequest):
    """Stream response from agent with multi-turn persistence."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    # Resolve or create persistent session
    session_id = payload.session_id or db_mgr.create_chat_session(slug, title=payload.message[:40])

    # Fetch prior conversation history from SQLite if not passed directly
    history = payload.history or []
    if not history and session_id:
        prior_messages = db_mgr.get_session_messages(session_id)
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in prior_messages
            if m.get("role") in ("user", "assistant")
        ]

    # Persist user message to SQLite
    db_mgr.add_chat_message(session_id, slug, role="user", content=payload.message)

    async def event_generator():
        # Inform client about active session ID
        yield {
            "event": "session",
            "data": json.dumps({"session_id": session_id}),
        }

        assistant_tokens: list[str] = []
        assistant_thoughts: list[str] = []

        curr_task = asyncio.current_task()
        if curr_task:
            active_chat_tasks[slug] = curr_task

        engine = engine_registry.get_engine(agent.engine)
        try:
            async for chunk in engine.stream_chat(agent, payload.message, history):
                event = chunk.get("event", "message")
                data = chunk.get("data", "")

                if event == "token":
                    assistant_tokens.append(data if isinstance(data, str) else str(data))
                elif event == "thought":
                    assistant_thoughts.append(data if isinstance(data, str) else str(data))

                yield {
                    "event": event,
                    "data": json.dumps(data),
                }

            # Persist complete assistant answer and thoughts to DB
            full_reply = "".join(assistant_tokens).strip()
            if full_reply:
                db_mgr.add_chat_message(
                    session_id=session_id,
                    agent_slug=slug,
                    role="assistant",
                    content=full_reply,
                    thoughts=assistant_thoughts if assistant_thoughts else None,
                )
        except asyncio.CancelledError:
            full_reply = "".join(assistant_tokens).strip()
            if full_reply:
                db_mgr.add_chat_message(
                    session_id=session_id,
                    agent_slug=slug,
                    role="assistant",
                    content=full_reply + "\n\n*(Generowanie przerwane przez użytkownika)*",
                    thoughts=assistant_thoughts if assistant_thoughts else None,
                )
            raise
        finally:
            if slug in active_chat_tasks and active_chat_tasks[slug] == curr_task:
                active_chat_tasks.pop(slug, None)

    return EventSourceResponse(event_generator())


@router.post("/{slug}/chat/cancel")
async def cancel_agent_chat(slug: str):
    """Cancel currently running chat generation for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    engine = engine_registry.get_engine(agent.engine)
    proc_terminated = await engine.cancel_turn(slug)

    task_cancelled = False
    task = active_chat_tasks.get(slug)
    if task and not task.done():
        task.cancel()
        task_cancelled = True

    return {
        "status": "cancelled",
        "slug": slug,
        "process_terminated": proc_terminated,
        "task_cancelled": task_cancelled,
    }

