"""REST API Routes for Agent CRUD, Per-Agent Auth, and Streaming Chat."""

import hmac
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, status
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
    AgentModel,
    EngineType,
    AgentCapabilities,
    AgentSchedule,
    MCPServerConfig,
)

router = APIRouter(prefix="/api/agents", tags=["agents"])
history_router = APIRouter(prefix="/api/history", tags=["history"])
reports_router = APIRouter(prefix="/api/reports", tags=["reports"])
auth_guard_router = APIRouter(prefix="/api/auth", tags=["auth_guard"])

repo = AgentRepository()
auth_mgr = AgentAuthManager()
db_mgr = DatabaseManager()
engine_registry = EngineRegistry(auth_manager=auth_mgr)
scheduler = AgentScheduler(repository=repo, db_manager=db_mgr, engine_registry=engine_registry)


# Request schemas
class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    slug: str = Field(..., min_length=2, max_length=50, pattern=r"^[a-z0-9-]+$")
    description: str = Field(default="")
    engine: EngineType = Field(default=EngineType.ANTIGRAVITY)
    model: str = Field(default="gemini-3.8-flash")
    master_prompt: str = Field(default="You are an autonomous AI assistant.")
    instructions: Optional[str] = Field(default="")
    capabilities: Optional[AgentCapabilities] = None
    schedule: Optional[AgentSchedule] = None
    mcp_servers: Optional[Dict[str, MCPServerConfig]] = None
    custom_target_dir: Optional[str] = Field(
        default=None,
        description="Optional custom path (e.g. specific Obsidian folder) to save this agent",
    )


class UpdateAgentRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    engine: Optional[EngineType] = None
    model: Optional[str] = None
    master_prompt: Optional[str] = None
    instructions: Optional[str] = None
    capabilities: Optional[AgentCapabilities] = None
    schedule: Optional[AgentSchedule] = None
    mcp_servers: Optional[Dict[str, MCPServerConfig]] = None


class SaveKnowledgeRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=100)
    content: str = Field(default="")


class VerifyTokenRequest(BaseModel):
    token: str


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
    session_id: Optional[str] = Field(default=None, description="Optional ID of active chat session")
    history: Optional[List[Dict[str, str]]] = Field(default_factory=list)


class CreateChatSessionRequest(BaseModel):
    title: Optional[str] = Field(default=None, description="Optional chat session title")


class ExecuteToolRequest(BaseModel):
    tool: str = Field(..., description="Tool name: execute_bash, read_file, write_file, list_dir")
    args: Dict[str, Any] = Field(default_factory=dict)


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
        capabilities=payload.capabilities or AgentCapabilities(),
        schedule=payload.schedule or AgentSchedule(),
        mcp_servers=payload.mcp_servers or {},
    )

    target_dir = Path(payload.custom_target_dir) if payload.custom_target_dir else None
    saved_path = repo.save_agent(agent_model, target_dir=target_dir)
    scheduler.sync_all_jobs()

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
    """List documents stored in the agent's knowledge base directory."""
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
            files.append({
                "filename": f.name,
                "size_bytes": stat.st_size,
                "updated_at": stat.st_mtime,
            })
    return {"slug": slug, "files": files}


@router.get("/{slug}/knowledge/{filename}")
async def get_agent_knowledge_file(slug: str, filename: str):
    """Read a specific document from the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(filename).name
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    target_file = agent_dir / "knowledge" / safe_filename

    if not target_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")

    content = target_file.read_text(encoding="utf-8", errors="replace")
    return {"filename": safe_filename, "content": content}


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
    return {"status": "saved", "filename": safe_filename, "size_bytes": len(payload.content)}


@router.delete("/{slug}/knowledge/{filename}")
async def delete_agent_knowledge_file(slug: str, filename: str):
    """Delete a document from the agent's knowledge base."""
    agent = repo.load_agent(slug)
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")

    safe_filename = Path(filename).name
    agent_dir = Path(agent.file_path).parent if agent.file_path else (repo.base_dir / slug)
    target_file = agent_dir / "knowledge" / safe_filename

    if not target_file.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge file not found.")

    target_file.unlink()
    return {"status": "deleted", "filename": safe_filename}


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

        assistant_tokens: List[str] = []
        assistant_thoughts: List[str] = []

        engine = engine_registry.get_engine(agent.engine)
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

    return EventSourceResponse(event_generator())
