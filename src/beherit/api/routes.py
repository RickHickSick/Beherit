"""REST API Routes for Agent CRUD, Per-Agent Auth, and Streaming Chat."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from beherit.core.auth_manager import AgentAuthManager
from beherit.core.database import DatabaseManager
from beherit.core.repository import AgentRepository
from beherit.core.scheduler import AgentScheduler
from beherit.engines.antigravity import AntigravityEngine
from beherit.models.agent import AgentModel, EngineType

router = APIRouter(prefix="/api/agents", tags=["agents"])
history_router = APIRouter(prefix="/api/history", tags=["history"])

repo = AgentRepository()
auth_mgr = AgentAuthManager()
db_mgr = DatabaseManager()
antigravity_engine = AntigravityEngine(auth_manager=auth_mgr)
scheduler = AgentScheduler(repository=repo, db_manager=db_mgr, engine=antigravity_engine)


# Request schemas
class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str = Field(default="")
    engine: EngineType = Field(default=EngineType.ANTIGRAVITY)
    model: str = Field(default="gemini-3.8-flash")
    master_prompt: str = Field(default="You are an autonomous AI assistant.")
    instructions: Optional[str] = Field(default="")
    custom_target_dir: Optional[str] = Field(
        default=None,
        description="Optional custom path (e.g. specific Obsidian folder) to save this agent",
    )


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    model: Optional[str] = None
    master_prompt: Optional[str] = None
    instructions: Optional[str] = None


class ManualTokenRequest(BaseModel):
    token: str = Field(..., min_length=1)
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)


class OAuthStartRequest(BaseModel):
    provider: EngineType = Field(default=EngineType.ANTIGRAVITY)


class OAuthVerifyRequest(BaseModel):
    flow_id: str = Field(..., min_length=1)
    auth_code: str = Field(..., min_length=1)


class ChatMessageRequest(BaseModel):
    message: str = Field(..., min_length=1)
    history: Optional[List[Dict[str, str]]] = Field(default_factory=list)


# --- Agent CRUD Endpoints ---


@router.get("", response_model=Dict[str, List[Dict[str, Any]]])
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
    )

    target_dir = Path(payload.custom_target_dir) if payload.custom_target_dir else None
    saved_path = repo.save_agent(agent_model, target_dir=target_dir)

    return {
        "status": "created",
        "slug": agent_model.slug,
        "path": str(saved_path),
        "agent": repo.load_agent(agent_model.slug).model_dump(mode="json"),
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
    if payload.model is not None:
        agent.model = payload.model
    if payload.master_prompt is not None:
        agent.master_prompt = payload.master_prompt
    if payload.instructions is not None:
        agent.instructions = payload.instructions

    repo.save_agent(agent)
    return repo.load_agent(slug).model_dump(mode="json")


@router.delete("/{slug}", status_code=status.HTTP_200_OK)
async def delete_agent(slug: str):
    """Delete an agent directory and clear its auth session."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    auth_mgr.clear_session(slug)
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


@router.post("/{slug}/auth/disconnect")
async def disconnect_agent_auth(slug: str):
    """Disconnect subscription credentials for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    auth_mgr.clear_session(slug)
    return {"status": "disconnected", "auth": auth_mgr.get_auth_details(slug)}


# --- Background Scheduler & History Endpoints ---


@router.get("/{slug}/history")
async def get_agent_job_history(slug: str, limit: int = 50):
    """Retrieve job execution audit history for this agent."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return {"history": db_mgr.get_agent_history(slug, limit=limit)}


@router.post("/{slug}/run-now")
async def trigger_agent_job_now(slug: str):
    """Trigger an autonomous job run immediately on-demand and dispatch multi-channel reports."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    result = await scheduler.execute_agent_job(slug)
    return result


@history_router.get("")
async def get_recent_jobs_history(limit: int = 50):
    """Retrieve global recent job execution history."""
    return {"history": db_mgr.get_recent_history(limit=limit)}


# --- Streaming SSE Chat Endpoint ---


@router.post("/{slug}/chat")
async def stream_agent_chat(slug: str, payload: ChatMessageRequest):
    """Stream response from agent using its dedicated subscription session."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    async def event_generator():
        async for chunk in antigravity_engine.stream_chat(agent, payload.message, payload.history):
            yield {
                "event": chunk.get("event", "message"),
                "data": json.dumps(chunk.get("data", "")),
            }

    return EventSourceResponse(event_generator())
