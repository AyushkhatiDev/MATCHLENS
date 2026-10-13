"""Vendor-independent workflow contract."""
from abc import ABC, abstractmethod
from ..models import Role, MatchAgentState, Finding, ExplanationResult, NarrativeResult
from ..tools import ToolSession

class AgentRuntime(ABC):
    mode: str

    @abstractmethod
    async def run(self, role: Role, state: MatchAgentState, tools: ToolSession
                  ) -> Finding | ExplanationResult | NarrativeResult:
        ...

    async def prepare(self) -> None:
        pass

    async def aclose(self) -> None:
        pass
