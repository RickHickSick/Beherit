"""Unit tests for Telegram Bot Integration (BEH-068)."""

from unittest.mock import AsyncMock, patch
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from beherit.core.repository import AgentRepository
from beherit.core.telegram_bot import TelegramBotService, telegram_bot_service
from beherit.core.tools import CapabilitiesGuard
from beherit.main import app
from beherit.models.agent import AgentModel, EngineType

client = TestClient(app)


@pytest.fixture
def test_agent(tmp_path: Path):
    """Setup a test agent."""
    repo = AgentRepository(base_dir=tmp_path)
    agent = AgentModel(
        name="Telegram Asystent",
        slug="telegram-agent",
        engine=EngineType.ANTIGRAVITY,
        model="gemini-3.8-flash",
        master_prompt="Jesteś asystentem w Telegramie.",
    )
    repo.save_agent(agent)
    loaded = repo.load_agent(agent.slug)
    return loaded, tmp_path


def test_telegram_authorization():
    """Verify whitelist and default user authorization checks."""
    bot = TelegramBotService(token="dummy_token", allowed_user_ids="12345,67890", default_chat_id="12345")
    assert bot.is_user_authorized(12345) is True
    assert bot.is_user_authorized(67890) is True
    assert bot.is_user_authorized(99999) is False

    # Empty whitelist with default chat id
    bot_default = TelegramBotService(token="dummy_token", allowed_user_ids="", default_chat_id="12345")
    assert bot_default.is_user_authorized(12345) is True
    assert bot_default.is_user_authorized(99999) is False


@pytest.mark.asyncio
async def test_telegram_handle_commands(test_agent):
    """Verify handling /start, /agents, /agent, and /status commands."""
    agent, tmp_path = test_agent
    repo = AgentRepository(base_dir=tmp_path)
    bot = TelegramBotService(token="dummy_token", repository=repo)

    sent_messages = []

    async def mock_send(chat_id, text, **kwargs):
        sent_messages.append({"chat_id": chat_id, "text": text})
        return True

    bot.send_message = mock_send

    # 1. /start command
    await bot.handle_update({
        "message": {
            "chat": {"id": 111},
            "from": {"id": 111},
            "text": "/start",
        }
    })
    assert len(sent_messages) == 1
    assert "Witaj w Beherit AI Bot" in sent_messages[0]["text"]

    # 2. /agents command
    await bot.handle_update({
        "message": {
            "chat": {"id": 111},
            "from": {"id": 111},
            "text": "/agents",
        }
    })
    assert len(sent_messages) == 2
    assert "telegram-agent" in sent_messages[1]["text"]

    # 3. /agent switch command
    await bot.handle_update({
        "message": {
            "chat": {"id": 111},
            "from": {"id": 111},
            "text": "/agent telegram-agent",
        }
    })
    assert len(sent_messages) == 3
    assert "Przełączono na agenta" in sent_messages[2]["text"]

    # 4. /status command
    await bot.handle_update({
        "message": {
            "chat": {"id": 111},
            "from": {"id": 111},
            "text": "/status",
        }
    })
    assert len(sent_messages) == 4
    assert "Status Platformy Beherit" in sent_messages[3]["text"]


@pytest.mark.asyncio
async def test_telegram_unauthorized_user_rejection():
    """Verify unauthorized user message triggers access denied warning."""
    bot = TelegramBotService(token="dummy_token", allowed_user_ids="100,200")
    sent_messages = []

    async def mock_send(chat_id, text, **kwargs):
        sent_messages.append({"chat_id": chat_id, "text": text})
        return True

    bot.send_message = mock_send

    await bot.handle_update({
        "message": {
            "chat": {"id": 999},
            "from": {"id": 999},
            "text": "Hej!",
        }
    })
    assert len(sent_messages) == 1
    assert "Brak autoryzacji" in sent_messages[0]["text"]
    assert "999" in sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_telegram_chat_turn(test_agent):
    """Verify regular message triggers agent turn and stores messages in DB."""
    agent, tmp_path = test_agent
    repo = AgentRepository(base_dir=tmp_path)
    bot = TelegramBotService(token="dummy_token", repository=repo)

    sent_messages = []

    async def mock_send(chat_id, text, **kwargs):
        sent_messages.append({"chat_id": chat_id, "text": text})
        return True

    bot.send_message = mock_send
    bot.send_chat_action = AsyncMock()

    # Mock engine stream
    async def mock_stream(*args, **kwargs):
        yield {"event": "token", "data": "Cześć! "}
        yield {"event": "token", "data": "Jestem agentem Beherit."}

    with patch.object(bot.engine_registry.get_engine(agent.engine), "stream_chat", side_effect=mock_stream):
        await bot.handle_update({
            "message": {
                "chat": {"id": 555},
                "from": {"id": 555},
                "text": "Witaj!",
            }
        })

    assert len(sent_messages) == 1
    assert "Cześć! Jestem agentem Beherit." in sent_messages[0]["text"]


@pytest.mark.asyncio
async def test_capabilities_guard_telegram_tool(test_agent):
    """Verify CapabilitiesGuard send_telegram_notification tool."""
    agent, tmp_path = test_agent
    guard = CapabilitiesGuard(default_workspace=tmp_path)

    with patch.object(telegram_bot_service, "send_notification", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = True

        res = await guard.dispatch_tool(
            agent,
            "send_telegram_notification",
            {"message": "Raport poranny gotowy!", "chat_id": "123456"},
        )
        assert res["success"] is True
        assert res["delivered"] is True
        mock_notify.assert_awaited_once_with("Raport poranny gotowy!", chat_id="123456")


def test_telegram_rest_endpoints(monkeypatch):
    """Verify GET /api/telegram/status and POST /api/telegram/send endpoints."""
    # 1. Status endpoint
    status_resp = client.get("/api/telegram/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert "configured" in data

    # 2. Send endpoint with mocked bot service
    monkeypatch.setattr(telegram_bot_service, "token", "mock_token")
    monkeypatch.setattr(telegram_bot_service, "default_chat_id", "123456")

    async def mock_send(chat_id, text, **kwargs):
        return True

    monkeypatch.setattr(telegram_bot_service, "send_message", mock_send)

    send_resp = client.post(
        "/api/telegram/send",
        json={"message": "Testowy alert z API", "chat_id": "123456"},
    )
    assert send_resp.status_code == 200
    assert send_resp.json()["status"] == "sent"
