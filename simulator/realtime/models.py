"""Versioned transport wrapper; Phase 1 payloads remain unchanged."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny, field_validator, model_validator
from simulator.models.events import MatchEvent, EventType
from simulator.models import events as event_models

EVENT_MODELS = {m.model_fields['event_type'].default: m
    for m in vars(event_models).values() if isinstance(m, type) and issubclass(m, MatchEvent)
    and isinstance(m.model_fields['event_type'].default, EventType)}

def parse_event(value):
    if isinstance(value, MatchEvent):
        value = value.model_dump(mode='json')
    kind = EventType(value['event_type'])
    return EVENT_MODELS.get(kind, MatchEvent).model_validate(value)

class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False)
    event_id: str = Field(min_length=1)
    match_id: str = Field(min_length=1)
    event_type: EventType
    simulation_timestamp: float = Field(ge=0)
    produced_at: datetime
    sequence_number: int = Field(ge=0, strict=True)
    schema_version: Literal['1.0']
    payload: SerializeAsAny[MatchEvent]
    source: str = Field(default='local_replay', min_length=1)
    correlation_id: str = Field(min_length=1)

    @field_validator('payload', mode='before')
    @classmethod
    def typed_payload(cls, value):
        return parse_event(value)

    @field_validator('produced_at')
    @classmethod
    def utc(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('produced_at must include a timezone')
        return value.astimezone(timezone.utc)

    @model_validator(mode='after')
    def consistent(self):
        for name, actual in [('event_id', self.payload.event_id), ('match_id', self.payload.match_id),
                             ('event_type', self.payload.event_type),
                             ('simulation_timestamp', self.payload.timestamp)]:
            if getattr(self, name) != actual:
                raise ValueError('Envelope and payload disagree: ' + name)
        return self

    @classmethod
    def wrap(cls, event, sequence_number, source='local_replay', produced_at=None, correlation_id=None):
        return cls(event_id=event.event_id, match_id=event.match_id, event_type=event.event_type,
                   simulation_timestamp=event.timestamp, sequence_number=sequence_number,
                   produced_at=produced_at or datetime.now(timezone.utc), payload=event, source=source,
                   correlation_id=correlation_id or f'{event.match_id}:{sequence_number}', schema_version='1.0')

class PipelineMetrics(BaseModel):
    events_received: int = 0
    events_processed: int = 0
    events_failed: int = 0
    events_duplicate: int = 0
    events_late: int = 0
    queue_depth: int = 0
    queue_high_watermark: int = 0
    backpressure_count: int = 0
    processing_latency_ms: float = 0
    ingestion_latency_ms: float = 0
    end_to_end_latency_ms: float = 0
    agent_trigger_latency_ms: float = 0
    moment_detection_latency_ms: float = 0
    queue_wait_latency_ms: float = 0
    moments_detected: int = 0
    agent_workflows_triggered: int = 0
    agent_workflows_failed: int = 0
    simulation_time: float = 0
    wall_clock_time: datetime | None = None

class ProcessingRecord(BaseModel):
    trace_id: str | None = None
    match_id: str | None = None
    event_id: str | None = None
    sequence_number: int | None = None
    status: str
    produced_at: datetime | None = None
    received_at: datetime
    processing_started_at: datetime | None = None
    intelligence_updated_at: datetime | None = None
    moment_detected_at: datetime | None = None
    workflow_started_at: datetime | None = None
    completed_at: datetime | None = None
    processing_latency_ms: float = 0
    ingestion_latency_ms: float = 0
    end_to_end_latency_ms: float = 0
    agent_trigger_latency_ms: float = 0
    moment_detection_latency_ms: float = 0
    queue_wait_latency_ms: float = 0
