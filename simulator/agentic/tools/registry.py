"""One registry backs MCP, Microsoft function tools, and deterministic mocks."""
from collections.abc import Callable
from functools import wraps
import inspect
from typing import Any
from pydantic import ValidationError
from ..models import ToolCallRecord
from .service import MatchLensTools
from .store import ToolError

TOOL_METHODS = {
    'match.get_summary':'get_match_summary', 'match.get_state':'get_current_match_state',
    'team.get_summary':'get_team_summary', 'team.get_pressure':'get_pressure_metrics',
    'team.get_field_tilt':'get_field_tilt', 'team.get_progression':'get_progression_metrics',
    'player.get_summary':'get_player_summary', 'player.get_activity':'get_player_activity',
    'events.get_recent':'get_recent_events', 'possession.get_sequence':'get_possession_sequence',
    'possession.get_recent':'get_recent_possession_sequences', 'shot.get_context':'get_shot_context',
    'evidence.get':'get_evidence', 'evidence.get_bundle':'get_evidence_bundle',
    'moment.get':'get_moment', 'moment.get_explanation_context':'get_explanation_context',
    'timeline.get':'get_timeline',
}
ROLE_TOOLS = {
    'tactical':{'team.get_pressure','team.get_field_tilt','team.get_progression','team.get_summary',
                'match.get_state','possession.get_recent','timeline.get','evidence.get','evidence.get_bundle'},
    'player':{'player.get_summary','player.get_activity','shot.get_context',
              'possession.get_sequence','evidence.get','evidence.get_bundle'},
    'context':{'match.get_summary','match.get_state','moment.get','moment.get_explanation_context',
               'possession.get_sequence','events.get_recent','timeline.get','evidence.get','evidence.get_bundle'},
    'explanation':{'moment.get_explanation_context','evidence.get','evidence.get_bundle'},
    'narrative':{'evidence.get','evidence.get_bundle'},
}

class ToolSession:
    def __init__(self, tools: MatchLensTools, allowed: set[str] | None = None, retain_trace: bool = True):
        self.tools = tools
        self.allowed = set(TOOL_METHODS) if allowed is None else allowed
        self.calls: list[ToolCallRecord] = []
        self.retain_trace = retain_trace

    def _record(self, record: ToolCallRecord) -> None:
        if self.retain_trace:
            self.calls.append(record)

    def call(self, name: str, **arguments: Any) -> dict[str, Any]:
        try:
            if name not in self.allowed or name not in TOOL_METHODS:
                raise ToolError('TOOL_NOT_ALLOWED','Tool is not available to this agent.')
            result = getattr(self.tools,TOOL_METHODS[name])(**arguments)
        except ValidationError:
            self._record(ToolCallRecord(tool=name if name in TOOL_METHODS else 'unknown',
                arguments={},status='ERROR',error_code='INVALID_ARGUMENT'))
            raise ToolError('INVALID_ARGUMENT','Arguments do not satisfy the tool schema.') from None
        except ToolError as error:
            # Invalid user/model-supplied argument strings may contain secrets;
            # never retain them in a failed invocation trace.
            self._record(ToolCallRecord(tool=name if name in TOOL_METHODS else 'unknown',
                arguments={},status='ERROR',error_code=error.code))
            raise
        evidence_ids = result.get('evidence_ids', [])
        if 'evidence_id' in result:
            evidence_ids = [result['evidence_id']]
        if 'evidence' in result:
            evidence_ids = [e['evidence_id'] for e in result['evidence']]
        self._record(ToolCallRecord(tool=name,arguments=arguments.copy(),
            evidence_ids=evidence_ids,status='OK'))
        return result

    def callable(self, name: str) -> Callable[..., dict[str, Any]]:
        original = getattr(self.tools, TOOL_METHODS[name])
        @wraps(original)
        def wrapped(**kwargs: Any) -> dict[str, Any]:
            return self.call(name,**kwargs)
        wrapped.__signature__ = inspect.signature(original)
        return wrapped

    def framework_tools(self):
        from agent_framework import tool
        return [tool(self.callable(name),name=name.replace('.','_'),
                     description=getattr(self.tools,TOOL_METHODS[name]).__doc__,
                     approval_mode='never_require',max_invocations=8) for name in sorted(self.allowed)]
