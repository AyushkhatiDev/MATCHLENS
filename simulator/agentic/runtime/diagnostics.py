"""Bounded internal diagnostics; never log arbitrary exception bodies or inputs."""
import logging
import re
from ..grounding import GroundingError
from ..tools import ToolError

logger = logging.getLogger('matchlens.agentic')
SAFE_REASONS = frozenset({
    'Unknown or duplicate statement IDs.', 'Unknown statement IDs.',
    'Unknown, future or duplicate evidence IDs.',
    'Selected claims are missing their evidence citations.',
    'Confidence exceeds the deterministic evidence cap.',
    'Deterministic caveats were dropped or unsupported caveat claims were added.',
    'Finding text is not traceable to the selected claims.',
    'Fabricated timestamp or finding type.', 'Unknown or unsupported team ID.',
    'Unknown or unsupported player ID.', 'Unknown or unsupported event ID.',
    'Player finding requires the linked player and its cited statement.',
    'Contradictory metrics must remain explicit.',
    'Explanation identity does not match the triggered moment.',
    'Trigger facts or metric values were altered.',
    'Trigger and change claims require citations.',
    'Explanation is missing its supported interpretation.',
    'Explanation contains unsupported numerical or football claims.',
    'Narrative requires a validated explanation.',
    'Narrative cannot change confidence in the underlying explanation.',
    'Unsupported narrative headline.', 'Narrative presentation profile mismatch.',
    'Narrative attempted new analysis beyond the validated explanation.',
    'Narrative contains unsupported claims.', 'Conflicting evidence was omitted.',
    'Agent did not retrieve the cited evidence through its tools.',
})

def failure_details(error: Exception) -> dict:
    details = {'exception': type(error).__name__}
    # These exception messages originate in our fixed validators/tool code.
    if isinstance(error, GroundingError) and str(error) in SAFE_REASONS:
        details['message'] = str(error)
    else:
        details['message'] = 'Exception body withheld.'
    body = getattr(error, 'body', None)
    if isinstance(body, dict):
        body = body.get('error', body)
        if isinstance(body, dict):
            for key in ('code', 'param'):
                value = body.get(key)
                if isinstance(value, str) and re.fullmatch(r'[a-zA-Z0-9_.\[\]-]{1,160}', value):
                    details[key] = value
    if isinstance(error, ToolError) and error.code in (
            'NOT_FOUND','INVALID_ARGUMENT','FUTURE_DATA','UNAVAILABLE_AT_TIME','TOOL_NOT_ALLOWED','NOT_EXPLAINED'):
        details['tool_error_code'] = error.code
    response = getattr(error, 'response', None)
    status = getattr(response, 'status_code', None)
    if isinstance(status, int):
        details['http_status'] = status
    return details
