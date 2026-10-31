"""Bidirectional Telegram Bot Service for Beherit AI Agents (BEH-068)."""

import asyncio
import logging
from typing import Any

import httpx

from beherit.config import settings
from beherit.core.database import DatabaseManager
from beherit.core.repository import AgentRepository
from beherit.engines import EngineRegistry
from beherit.models.agent import AgentModel

logger = logging.getLogger(__name__)


class TelegramBotService:
    """Manages Telegram bot polling, message dispatching, authorization, and streaming agent turns."""

    def __init__(
        self,
        token: str | None = None,
        allowed_user_ids: str | None = None,
        default_chat_id: str | None = None,
        db_manager: DatabaseManager | None = None,
        repository: AgentRepository | None = None,
    ):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self.default_chat_id = default_chat_id or settings.TELEGRAM_CHAT_ID
        self.allowed_user_ids: set[int] = set()
        raw_ids = allowed_user_ids or settings.TELEGRAM_ALLOWED_USER_IDS
        if raw_ids:
            for item in str(raw_ids).split(","):
                item_clean = item.strip()
                if item_clean.isdigit():
                    self.allowed_user_ids.add(int(item_clean))

        self.db = db_manager or DatabaseManager()
        self.repo = repository or AgentRepository()
        self.engine_registry = EngineRegistry()

        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._user_active_agents: dict[int, str] = {}  # user_id -> agent_slug
        self._user_active_sessions: dict[int, str] = {}  # user_id -> session_id

    @property
    def is_configured(self) -> bool:
        """Return True if Telegram Bot Token is provided."""
        return bool(self.token and self.token.strip())

    @property
    def base_url(self) -> str:
        return f"https://api.telegram.org/bot{self.token}"

    def is_user_authorized(self, user_id: int) -> bool:
        """Check if Telegram user is authorized to interact with Beherit."""
        if not self.allowed_user_ids:
            # If no whitelist is configured, allow if default_chat_id matches or allow all
            if self.default_chat_id and self.default_chat_id.isdigit():
                return int(self.default_chat_id) == user_id
            return True
        return user_id in self.allowed_user_ids

    async def get_me(self) -> dict[str, Any] | None:
        """Fetch bot metadata from Telegram API."""
        if not self.is_configured:
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/getMe")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("result")
        except Exception as e:
            logger.warning(f"Failed to fetch Telegram getMe: {e}")
        return None

    async def send_message(
        self,
        chat_id: int | str,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        """Send a message to a Telegram chat."""
        if not self.is_configured:
            return False

        # Telegram message length limit is 4096 chars
        chunks = []
        if len(text) <= 4000:
            chunks = [text]
        else:
            for i in range(0, len(text), 4000):
                chunks.append(text[i : i + 4000])

        success = True
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for chunk in chunks:
                    payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
                    if parse_mode:
                        payload["parse_mode"] = parse_mode
                    if reply_markup:
                        payload["reply_markup"] = reply_markup

                    resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                    if resp.status_code != 200:
                        # Fallback without markdown if parsing failed
                        if parse_mode:
                            payload.pop("parse_mode", None)
                            resp = await client.post(f"{self.base_url}/sendMessage", json=payload)
                        if resp.status_code != 200:
                            success = False
                            logger.error(f"Telegram sendMessage error: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            success = False

        return success

    async def send_chat_action(self, chat_id: int | str, action: str = "typing") -> None:
        """Send a chat status action (e.g. typing) to Telegram."""
        if not self.is_configured:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(f"{self.base_url}/sendChatAction", json={"chat_id": chat_id, "action": action})
        except Exception:
            pass

    async def send_notification(self, text: str, chat_id: str | int | None = None) -> bool:
        """Send alert or broadcast notification to target chat or default owner chat."""
        target = chat_id or self.default_chat_id
        if not target:
            return False
        return await self.send_message(target, text)

    def _get_active_agent_for_user(self, user_id: int) -> AgentModel | None:
        """Get or initialize the selected agent for this Telegram user."""
        slug = self._user_active_agents.get(user_id)
        if slug:
            agent = self.repo.load_agent(slug)
            if agent:
                return agent

        # Default to first available agent
        agents = self.repo.list_agents()
        if agents:
            selected = agents[0]
            self._user_active_agents[user_id] = selected.slug
            return selected
        return None

    def _get_active_session_for_user(self, user_id: int, agent_slug: str) -> str:
        """Get or create chat session ID for this user and agent."""
        key = user_id
        if key in self._user_active_sessions:
            return self._user_active_sessions[key]

        session_id = self.db.create_chat_session(agent_slug, title=f"Telegram Chat ({user_id})")
        self._user_active_sessions[key] = session_id
        return session_id

    async def handle_update(self, update: dict[str, Any]) -> None:
        """Process an incoming update from Telegram."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        user = message.get("from", {})
        user_id = user.get("id")
        text = (message.get("text") or "").strip()

        if not chat_id or not user_id:
            return

        # Security check
        if not self.is_user_authorized(user_id):
            warning = (
                f"⛔ Brak autoryzacji.\n\nTwój identyfikator użytkownika Telegram: `{user_id}`.\n"
                "Dodaj ten ID do zmiennej TELEGRAM_ALLOWED_USER_IDS na serwerze Beherit."
            )
            await self.send_message(chat_id, warning, parse_mode="Markdown")
            return

        if not text:
            return

        # Handle commands
        if text.startswith("/"):
            await self._handle_command(chat_id, user_id, text)
            return

        # Regular conversational message -> Agent Turn
        agent = self._get_active_agent_for_user(user_id)
        if not agent:
            await self.send_message(chat_id, "⚠️ Brak zarejestrowanych agentów w instancji Beherit.")
            return

        session_id = self._get_active_session_for_user(user_id, agent.slug)

        # Store user message
        self.db.add_chat_message(session_id, agent.slug, role="user", content=text)

        # Fetch prior messages for multi-turn context
        prior_history = self.db.get_session_messages(session_id)
        history_list = [{"role": m["role"], "content": m["content"]} for m in prior_history[:-1]]

        await self.send_chat_action(chat_id, "typing")

        engine = self.engine_registry.get_engine(agent.engine)
        response_tokens: list[str] = []
        error_msg: str | None = None

        try:
            async for chunk in engine.stream_chat(agent, text, history=history_list):
                evt = chunk.get("event")
                data = chunk.get("data")
                if evt == "token":
                    response_tokens.append(data if isinstance(data, str) else str(data))
                elif evt == "error":
                    error_msg = str(data)
                    break
        except Exception as e:
            error_msg = str(e)

        if error_msg:
            reply = f"❌ Błąd wykonania agenta {agent.name}:\n{error_msg}"
        else:
            reply = "".join(response_tokens).strip() or "Odpowiedź agenta jest pusta."

        # Save assistant message
        self.db.add_chat_message(session_id, agent.slug, role="assistant", content=reply)

        # Send response back to Telegram
        await self.send_message(chat_id, reply)

    async def _handle_command(self, chat_id: int, user_id: int, command_text: str) -> None:
        """Handle /start, /help, /agents, /agent, /status, /todo, /clear commands."""
        parts = command_text.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]  # strip bot username if present
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/start", "/help"):
            active = self._get_active_agent_for_user(user_id)
            active_name = active.name if active else "Brak"
            msg = (
                "🧿 **Witaj w Beherit AI Bot!**\n\n"
                "Ten bot pozwala na bezpośrednią interakcję z Twoimi autonomicznymi agentami AI.\n\n"
                "**Dostępne polecenia:**\n"
                "• `/agents` - Lista dostępnych agentów\n"
                "• `/agent <slug>` - Wybór aktywnego agenta do rozmowy\n"
                "• `/status` - Stan serwera i aktywnego agenta\n"
                "• `/todo` - Podgląd aktywnego planu zadań todo.md\n"
                "• `/clear` - Rozpocznij nowy wątek rozmowy\n"
                "• `/help` - Wyświetl tę pomoc\n\n"
                f"Aktualnie wybrany agent: **{active_name}**\n"
                "Możesz od razu wysłać wiadomość tekstową, aby rozpocząć rozmowę."
            )
            await self.send_message(chat_id, msg, parse_mode="Markdown")

        elif cmd == "/agents":
            agents = self.repo.list_agents()
            active = self._get_active_agent_for_user(user_id)
            lines = ["📋 **Dostępni agenci w Beherit:**\n"]
            for a in agents:
                is_curr = "👉 " if active and active.slug == a.slug else "• "
                lines.append(f"{is_curr}**{a.name}** (`{a.slug}`)\n  Model: `{a.model}`, Silnik: `{a.engine.value}`")
            lines.append("\nAby wybrać agenta, wpisz: `/agent <slug>`")
            await self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

        elif cmd == "/agent":
            if not arg:
                await self.send_message(chat_id, "Podaj identyfikator agenta, np. `/agent jakistam`")
                return

            agent = self.repo.load_agent(arg)
            if not agent:
                await self.send_message(chat_id, f"❌ Nie znaleziono agenta o identyfikatorze: `{arg}`")
                return

            self._user_active_agents[user_id] = agent.slug
            # Reset session on agent switch
            self._user_active_sessions.pop(user_id, None)
            await self.send_message(
                chat_id,
                f"✅ Przełączono na agenta: **{agent.name}** (`{agent.slug}`).\nMożesz teraz wysyłać zapytania.",
                parse_mode="Markdown",
            )

        elif cmd == "/status":
            active = self._get_active_agent_for_user(user_id)
            agents = self.repo.list_agents()
            msg = (
                "ℹ️ **Status Platformy Beherit:**\n"
                f"• Zarejestrowani agenci: {len(agents)}\n"
                f"• Aktywny agent: **{active.name if active else 'Brak'}** (`{active.slug if active else '-'}`)\n"
                f"• Środowisko: `{settings.BEHERIT_ENV}`\n"
                f"• Bezpieczeństwo: Autoryzowany użytkownik (`{user_id}`)"
            )
            await self.send_message(chat_id, msg, parse_mode="Markdown")

        elif cmd in ("/todo", "/plan"):
            active = self._get_active_agent_for_user(user_id)
            if not active:
                await self.send_message(chat_id, "Wybierz najpierw agenta za pomocą `/agent <slug>`.")
                return

            from beherit.core.todo import AgentTodoManager
            todo_mgr = AgentTodoManager()
            summary = todo_mgr.get_summary(active)
            todos = todo_mgr.read_todos(active)

            lines = [f"📋 **Plan zadań todo.md dla {active.name}:**"]
            lines.append(f"Postęp: {summary['done']}/{summary['total']} ({summary['progress_percent']}%)\n")
            if not todos:
                lines.append("Brak zadań w todo.md.")
            else:
                for t in todos:
                    icon = "✅" if t.status == "done" else ("⏳" if t.status == "in_progress" else "▫️")
                    lines.append(f"{icon} #{t.id}: {t.text}")

            await self.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")

        elif cmd == "/clear":
            self._user_active_sessions.pop(user_id, None)
            active = self._get_active_agent_for_user(user_id)
            await self.send_message(
                chat_id,
                f"🧹 Wątek rozmowy z agentem **{active.name if active else ''}** został wyczyszczony. Rozpoczynamy nową sesję.",
                parse_mode="Markdown",
            )
        else:
            await self.send_message(chat_id, f"Nieznane polecenie: {cmd}. Wpisz `/help`, aby zobaczyć listę.")

    async def start_polling(self) -> None:
        """Start long-polling loop in background."""
        if not self.is_configured:
            logger.info("Telegram Bot token not configured; polling will not start.")
            return

        self._running = True
        logger.info("🤖 Starting Beherit Telegram Bot polling service...")

        offset = 0
        async with httpx.AsyncClient(timeout=45.0) as client:
            while self._running:
                try:
                    params: dict[str, Any] = {"offset": offset, "timeout": 30}
                    resp = await client.get(f"{self.base_url}/getUpdates", params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        updates = data.get("result", [])
                        for u in updates:
                            update_id = u.get("update_id", 0)
                            offset = max(offset, update_id + 1)
                            asyncio.create_task(self.handle_update(u))
                    elif resp.status_code == 409:
                        logger.warning("Telegram 409 Conflict: another bot instance is polling. Waiting...")
                        await asyncio.sleep(5)
                    else:
                        logger.warning(f"Telegram polling status {resp.status_code}: {resp.text}")
                        await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Telegram polling connection error: {e}")
                    await asyncio.sleep(3)

        self._running = False
        logger.info("Telegram Bot polling stopped.")

    async def stop(self) -> None:
        """Gracefully stop polling."""
        self._running = False
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass


telegram_bot_service = TelegramBotService()
