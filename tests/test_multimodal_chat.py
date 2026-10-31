"""Unit tests for Inbound Multimodal Vision & Chat Attachments (BEH-064)."""

import io
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from beherit.api.routes import auth_mgr, repo
from beherit.core.database import DatabaseManager
from beherit.core.repository import AgentRepository
from beherit.engines.antigravity import AntigravityEngine
from beherit.engines.claude import ClaudeCodeEngine
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def vision_agent(tmp_path: Path):
    """Setup a test agent with workspace and SQLite database."""
    repo_inst = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Agent Multimodalny",
        slug="multimodal-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś ekspertem analizy obrazów i diagramów.",
    )
    repo_inst.save_agent(agent)
    return agent, tmp_path


def test_database_chat_attachments(tmp_path: Path):
    """Verify DatabaseManager persists and retrieves attachments in chat messages."""
    db_file = tmp_path / "test_chat.db"
    db_mgr = DatabaseManager(db_path=db_file)

    sid = db_mgr.create_chat_session("multimodal-agent", title="Wątek z obrazami")
    attachments = [
        {
            "filename": "screenshot_error.png",
            "content_type": "image/png",
            "url": "/api/agents/multimodal-agent/uploads/screenshot_error_123.png",
            "path": "/app/data/agents/multimodal-agent/uploads/screenshot_error_123.png",
            "is_image": True,
            "size_bytes": 1024,
        }
    ]

    msg_id = db_mgr.add_chat_message(
        session_id=sid,
        agent_slug="multimodal-agent",
        role="user",
        content="Co oznacza ten błąd na zrzucie ekranu?",
        attachments=attachments,
    )
    assert msg_id > 0

    messages = db_mgr.get_session_messages(sid)
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert len(messages[0]["attachments"]) == 1
    assert messages[0]["attachments"][0]["filename"] == "screenshot_error.png"
    assert messages[0]["attachments"][0]["is_image"] is True


def test_upload_chat_attachment_endpoints(vision_agent, monkeypatch):
    """Verify POST upload and GET download endpoints for attachments."""
    agent, tmp_path = vision_agent
    monkeypatch.setattr(repo, "base_dir", tmp_path)
    monkeypatch.setattr(auth_mgr, "base_dir", tmp_path)

    # 1. Upload mock PNG image
    fake_png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4"
    files = {"file": ("error_screen.png", io.BytesIO(fake_png_bytes), "image/png")}

    res_upload = client.post(f"/api/agents/{agent.slug}/chat/upload", files=files)
    assert res_upload.status_code == 200
    data = res_upload.json()
    assert data["filename"] == "error_screen.png"
    assert data["is_image"] is True
    assert data["content_type"] == "image/png"
    assert data["base64_data"] is not None
    assert "uploads" in data["path"]

    # 2. Retrieve uploaded image via GET
    saved_name = data["saved_filename"]
    res_get = client.get(f"/api/agents/{agent.slug}/uploads/{saved_name}")
    assert res_get.status_code == 200
    assert res_get.headers["content-type"] == "image/png"
    assert res_get.content == fake_png_bytes


@pytest.mark.asyncio
async def test_gemini_multimodal_parts(vision_agent):
    """Verify Gemini API payload constructs inlineData parts for images."""
    agent, tmp_path = vision_agent
    engine = AntigravityEngine()

    captured_payloads = []

    class MockStreamContext:
        def __init__(self, response):
            self.response = response
        async def __aenter__(self):
            return self.response
        async def __aexit__(self, *args):
            pass

    def mock_stream(method, url, headers=None, json=None):
        captured_payloads.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        async def fake_lines():
            yield "data: {\"candidates\": [{\"content\": {\"parts\": [{\"text\": \"Widzę błąd 404\"}]}}]}"
        mock_resp.aiter_lines = fake_lines
        return MockStreamContext(mock_resp)

    attachments = [
        {
            "filename": "ui_error.png",
            "content_type": "image/png",
            "base64_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
            "is_image": True,
        }
    ]

    with patch("httpx.AsyncClient.stream", side_effect=mock_stream):
        chunks = []
        async for chunk in engine._stream_gemini_api(
            agent=agent,
            message="Przeanalizuj zrzut",
            history=None,
            token="AIzaSyTestKey123",
            system_context="Ekspert UI",
            attachments=attachments,
        ):
            chunks.append(chunk)

        assert len(captured_payloads) > 0
        last_body = captured_payloads[0]
        user_parts = last_body["contents"][-1]["parts"]
        # Must have text part and inlineData part
        types = [list(p.keys())[0] for p in user_parts]
        assert "text" in types
        assert "inlineData" in types
        inline_part = next(p for p in user_parts if "inlineData" in p)
        assert inline_part["inlineData"]["mimeType"] == "image/png"


@pytest.mark.asyncio
async def test_claude_multimodal_blocks(vision_agent):
    """Verify Anthropic API payload constructs image content blocks."""
    agent, tmp_path = vision_agent
    engine = ClaudeCodeEngine()

    captured_payloads = []

    class MockStreamContext:
        def __init__(self, response):
            self.response = response
        async def __aenter__(self):
            return self.response
        async def __aexit__(self, *args):
            pass

    def mock_stream(method, url, headers=None, json=None):
        captured_payloads.append(json)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        async def fake_lines():
            yield "data: {\"type\": \"content_block_delta\", \"delta\": {\"type\": \"text_delta\", \"text\": \"Widzę wykres\"}}"
            yield "data: [DONE]"
        mock_resp.aiter_lines = fake_lines
        return MockStreamContext(mock_resp)

    attachments = [
        {
            "filename": "chart.jpg",
            "content_type": "image/jpeg",
            "base64_data": "/9j/4AAQSkZJRg==",
            "is_image": True,
        }
    ]

    with patch("httpx.AsyncClient.stream", side_effect=mock_stream):
        chunks = []
        async for chunk in engine._stream_anthropic_api(
            agent=agent,
            message="Zinterpretuj wykres",
            history=None,
            token="sk-ant-api03-test",
            system_context="Analityk",
            attachments=attachments,
        ):
            chunks.append(chunk)

        assert len(captured_payloads) > 0
        last_body = captured_payloads[0]
        user_blocks = last_body["messages"][-1]["content"]
        block_types = [b.get("type") for b in user_blocks]
        assert "image" in block_types
        assert "text" in block_types
        img_block = next(b for b in user_blocks if b.get("type") == "image")
        assert img_block["source"]["media_type"] == "image/jpeg"
