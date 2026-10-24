"""Comprehensive tests for Sprint 9 features (MCP Server, Local RAG, RFC 8628, Cancel Stream)."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from beherit.core.auth_manager import AgentAuthManager
from beherit.core.database import DatabaseManager
from beherit.core.rag import KnowledgeChunker, KnowledgeRAG
from beherit.core.repository import AgentRepository
from beherit.main import app
from beherit.mcp.server import MCPServer
from beherit.models.agent import (
    AgentCapabilities,
    AgentModel,
    EngineType,
    TerminalMode,
)


@pytest.fixture
def test_client():
    return TestClient(app)


@pytest.fixture
def temp_agent(tmp_path):
    repo = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Sprint 9 Test Agent",
        slug="sprint9-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="You are a sprint 9 testing agent.",
        capabilities=AgentCapabilities(
            terminal=True,
            terminal_mode=TerminalMode.RESTRICTED,
            file_read=True,
            file_write=True,
        ),
    )
    repo.save_agent(agent)
    return agent, repo


def test_beh025_mcp_server_lifecycle(temp_agent):
    """Test BEH-025: CapabilitiesGuard exposed via Stdio MCP Server."""
    agent, repo = temp_agent
    server = MCPServer(agent_slug=agent.slug, repository=repo)

    # 1. initialize
    init_req = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}
    init_resp = asyncio.run(server.handle_request(init_req))
    assert init_resp["id"] == 1
    assert "tools" in init_resp["result"]["capabilities"]
    assert init_resp["result"]["serverInfo"]["name"] == f"beherit-{agent.slug}-mcp"

    # 2. tools/list
    tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
    tools_resp = asyncio.run(server.handle_request(tools_req))
    tools = tools_resp["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    assert "execute_bash" in tool_names
    assert "read_file" in tool_names
    assert "write_file" in tool_names
    assert "list_dir" in tool_names

    # 3. tools/call: write_file
    call_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "write_file",
            "arguments": {"path": "mcp_test.txt", "content": "Hello from MCP Server!"},
        },
    }
    call_resp = asyncio.run(server.handle_request(call_req))
    assert call_resp["id"] == 3
    assert not call_resp["result"]["isError"]

    # 4. tools/call: read_file
    read_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "read_file",
            "arguments": {"path": "mcp_test.txt"},
        },
    }
    read_resp = asyncio.run(server.handle_request(read_req))
    assert read_resp["id"] == 4
    assert "Hello from MCP Server!" in read_resp["result"]["content"][0]["text"]

    # 5. Verify mcp.json contains beherit-sandbox
    agent_dir = repo.base_dir / agent.slug
    mcp_file = agent_dir / "mcp.json"
    assert mcp_file.exists()
    mcp_data = json.loads(mcp_file.read_text(encoding="utf-8"))
    assert "beherit-sandbox" in mcp_data["mcpServers"]
    assert mcp_data["mcpServers"]["beherit-sandbox"]["args"] == ["-m", "beherit.cli", "mcp", "--agent-slug", agent.slug]


def test_beh026_local_rag_semantic_search(tmp_path):
    """Test BEH-026: KnowledgeChunker and KnowledgeRAG hybrid vector/BM25 search."""
    chunker = KnowledgeChunker(chunk_size=200, chunk_overlap=30)
    sample_text = (
        "Rozdział 1: Architektura Bezpieczeństwa.\n\n"
        "Wszystkie zapytania API muszą być autoryzowane tokenem sesji HMAC. "
        "Dostęp do terminala podlega regułom piaskownicy CapabilitiesGuard.\n\n"
        "Rozdział 2: Wytyczne Finansowe.\n\n"
        "Koszty wywołań SDK wynoszą 0 PLN dodatkowych opłat, ponieważ wykorzystywane są aktywne subskrypcje."
    )
    chunks = chunker.split_text(sample_text)
    assert len(chunks) >= 2

    # RAG indexing & search
    db_file = tmp_path / "rag_test.db"
    db_mgr = DatabaseManager(db_path=db_file)
    rag = KnowledgeRAG(db_manager=db_mgr, chunker=chunker)

    # Setup mock knowledge directory
    knowledge_dir = tmp_path / "knowledge"
    knowledge_dir.mkdir(parents=True)
    doc1 = knowledge_dir / "security.md"
    doc1.write_text(sample_text, encoding="utf-8")

    total_chunks = rag.sync_agent_knowledge(agent_slug="rag-agent", knowledge_dir=knowledge_dir)
    assert total_chunks >= 2

    # Query for security
    results = rag.search(agent_slug="rag-agent", query="CapabilitiesGuard piaskownica", top_k=2)
    assert len(results) > 0
    assert "CapabilitiesGuard" in results[0]["content"]
    assert results[0]["score"] > 0.0

    # Query for financial
    fin_results = rag.search(agent_slug="rag-agent", query="Koszty wywołań subskrypcje", top_k=2)
    assert len(fin_results) > 0
    assert "subskrypcje" in fin_results[0]["content"].lower()


def test_beh027_rfc8628_device_authorization_flow(tmp_path):
    """Test BEH-027: RFC 8628 Device Authorization Grant, polling, approval, and token refresh."""
    auth_mgr = AgentAuthManager(base_data_dir=tmp_path)
    slug = "oauth-agent"

    # 1. Start flow
    flow = auth_mgr.start_oauth_flow(slug, provider=EngineType.ANTIGRAVITY)
    assert "flow_id" in flow
    assert "device_code" in flow
    assert "user_code" in flow
    assert "-" in flow["user_code"]  # Standard XXXX-XXXX format
    assert flow["verification_uri"] == "https://www.google.com/device"
    assert flow["verification_uri_complete"].startswith("https://www.google.com/device?user_code=")
    assert flow["interval"] == 5

    # 2. Poll while pending
    poll_pending = auth_mgr.poll_device_token(slug, flow["flow_id"])
    assert poll_pending["status"] == "authorization_pending"
    assert poll_pending["interval"] == 5

    # 3. User approval in browser
    approved = auth_mgr.approve_device_flow(slug, flow["flow_id"], custom_token="mock_access_token_123")
    assert approved["status"] == "success"
    assert approved["access_token"] == "mock_access_token_123"
    assert auth_mgr.is_authenticated(slug)

    # Check session.json contents
    session = json.loads(auth_mgr.get_session_file(slug).read_text(encoding="utf-8"))
    assert session["auth_method"] == "rfc8628_device_code"
    assert session["token_type"] == "Bearer"
    assert "refresh_token" in session

    # 4. Token refresh using refresh_token
    refreshed = auth_mgr.refresh_access_token(slug)
    assert refreshed["status"] == "refreshed"
    assert refreshed["access_token"].startswith("beh_rot_")


def test_beh028_chat_cancel_endpoint(test_client):
    """Test BEH-028: POST /api/agents/{slug}/chat/cancel returns cancelled status."""
    # Create agent via client
    create_resp = test_client.post(
        "/api/agents",
        json={
            "name": "Cancel Test Agent",
            "slug": "cancel-agent",
            "engine": "antigravity",
            "model": "gemini-3.8-flash",
            "master_prompt": "You are a test assistant.",
        },
    )
    assert create_resp.status_code in (201, 409)

    # Cancel chat
    cancel_resp = test_client.post("/api/agents/cancel-agent/chat/cancel")
    assert cancel_resp.status_code == 200
    data = cancel_resp.json()
    assert data["status"] == "cancelled"
    assert data["slug"] == "cancel-agent"


def test_beh029_health_endpoints(test_client):
    """Test BEH-029: Health endpoints for container orchestrator checks."""
    resp1 = test_client.get("/health")
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "healthy"

    resp2 = test_client.get("/api/health")
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "healthy"
