"""Engine Registry and Factory for SDK Adapters."""

from typing import Dict, Optional
from beherit.core.auth_manager import AgentAuthManager
from beherit.engines.antigravity import AntigravityEngine
from beherit.engines.base import BaseAgentEngine
from beherit.engines.claude import ClaudeCodeEngine
from beherit.engines.codex import OpenAICodexEngine
from beherit.models.agent import EngineType


class EngineRegistry:
    """Factory and registry for SDK execution engines."""

    def __init__(self, auth_manager: Optional[AgentAuthManager] = None):
        self.auth_manager = auth_manager or AgentAuthManager()
        self._engines: Dict[EngineType, BaseAgentEngine] = {
            EngineType.ANTIGRAVITY: AntigravityEngine(auth_manager=self.auth_manager),
            EngineType.CLAUDE_CODE: ClaudeCodeEngine(auth_manager=self.auth_manager),
            EngineType.CODEX: OpenAICodexEngine(auth_manager=self.auth_manager),
        }

    def get_engine(self, engine_type: EngineType) -> BaseAgentEngine:
        """Return engine adapter corresponding to the engine type."""
        if isinstance(engine_type, str):
            try:
                engine_type = EngineType(engine_type)
            except ValueError:
                engine_type = EngineType.ANTIGRAVITY
        return self._engines.get(engine_type, self._engines[EngineType.ANTIGRAVITY])
