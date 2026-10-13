"""Deterministic local agents; performs actual tool calls and structured handoffs."""
from ..agents import AGENTS
from ..models import Role, MatchAgentState, Finding, ExplanationResult, NarrativeResult
from ..tools import ToolSession
from .base import AgentRuntime

class MockAgentRuntime(AgentRuntime):
    mode = 'mock'

    async def run(self, role: Role, state: MatchAgentState, tools: ToolSession
                  ) -> Finding | ExplanationResult | NarrativeResult:
        return await AGENTS[role]().run(state,tools)
