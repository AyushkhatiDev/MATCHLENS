"""Bounded primary evidence selection and explicit projection provenance."""
from simulator.intelligence.models import MatchIntelligence, EvidenceType, SignificantMoment
from simulator.models.events import MatchEvent
from .models import TraceEvidence

class EvidenceSelector:
    def __init__(self, intelligence: MatchIntelligence, events: list[MatchEvent], limit: int):
        self.intel, self.events, self.limit = intelligence, events, limit
        self.ledger = {e.evidence_id: TraceEvidence(**e.model_dump(),
                       source_paths=[f"evidence/{i}"]) for i, e in enumerate(intelligence.evidence)}

    def project(self, key: str, metric: str, value: float, timestamp: float,
                paths: list[str], *, baseline: float | None = None, team: str | None = None,
                window: str | None = None, facts: dict | None = None,
                event_ids: list[str] | None = None, origin: str = "PHASE2") -> TraceEvidence:
        e = TraceEvidence(evidence_id="exp_evi_" + key, metric_name=metric, value=value,
            baseline_value=baseline, delta=round(value-baseline, 4) if baseline is not None else None,
            timestamp=timestamp, team_id=team, window=window, source_paths=paths,
            facts=facts or {}, source_event_ids=event_ids or [], origin=origin,
            evidence_type=EvidenceType.SHIFT if baseline is not None else EvidenceType.METRIC)
        old = self.ledger.get(e.evidence_id)
        if old is not None and old != e:
            raise ValueError("Evidence ID collision")
        self.ledger[e.evidence_id] = e
        return e

    def select(self, moment: SignificantMoment, candidates: list[TraceEvidence]) -> list[TraceEvidence]:
        required = [self.ledger[eid] for eid in moment.evidence_ids]
        if not required:
            raise ValueError(f"Moment has no primary evidence: {moment.moment_id}")
        if len(required) > self.limit:
            raise ValueError("Trigger evidence exceeds configured primary evidence limit")
        # Candidates arrive in priority order: events/sequence, transition, windows.
        result = list(required)
        for e in candidates:
            if e.timestamp > moment.timestamp:
                raise ValueError("Future evidence cannot explain a live moment")
            if e.evidence_id not in {x.evidence_id for x in result} and len(result) < self.limit:
                result.append(e)
        return result
