from .base import AgentRuntime
from .mock import MockAgentRuntime
from .foundry import FoundryAgentRuntime
from ..config import AgenticConfig

def create_runtime(config: AgenticConfig) -> AgentRuntime:
    return MockAgentRuntime() if config.mode=='mock' else FoundryAgentRuntime(config)

__all__ = ['AgentRuntime','MockAgentRuntime','FoundryAgentRuntime','create_runtime']
