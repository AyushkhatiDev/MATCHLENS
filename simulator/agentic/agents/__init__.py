from .tactical_agent import TacticalAgent
from .player_agent import PlayerAgent
from .context_agent import ContextAgent
from .explanation_agent import ExplanationAgent
from .narrative_agent import NarrativeAgent
from ..models import TacticalFinding, PlayerFinding, ContextFinding, ExplanationResult, NarrativeResult

AGENTS = {'tactical':TacticalAgent,'player':PlayerAgent,'context':ContextAgent,
          'explanation':ExplanationAgent,'narrative':NarrativeAgent}
OUTPUT_SCHEMAS = {'tactical':TacticalFinding,'player':PlayerFinding,'context':ContextFinding,
                  'explanation':ExplanationResult,'narrative':NarrativeResult}
