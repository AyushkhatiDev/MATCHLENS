"""File adapters for existing Phase 1/2/3 outputs; no analytics logic."""
import json
from pathlib import Path
from simulator.intelligence.models import MatchIntelligence
from simulator.intelligence.explanation.models import ExplanationReport
from simulator.models import events as event_models
from simulator.models.events import MatchEvent, EventType
from .tools import IntelligenceStore

EVENT_MODELS = {model.model_fields['event_type'].default:model
    for model in vars(event_models).values() if isinstance(model,type) and issubclass(model,MatchEvent)
    and isinstance(model.model_fields['event_type'].default,EventType)}

def load_store(intelligence_path: str, explanations_path: str, events_path: str,
               current_time: float | None = None) -> IntelligenceStore:
    intel = MatchIntelligence.model_validate_json(Path(intelligence_path).read_text())
    report = ExplanationReport.model_validate_json(Path(explanations_path).read_text())
    events = []
    for line in Path(events_path).read_text().splitlines():
        if line.strip():
            value = json.loads(line)
            cls = EVENT_MODELS.get(EventType(value['event_type']),MatchEvent)
            events.append(cls.model_validate(value))
    return IntelligenceStore(intel,report,events,current_time)
