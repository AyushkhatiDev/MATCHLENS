"""Idempotent, match-isolated ingestion and real Phase 4 handoff."""
import asyncio
from collections import deque
from datetime import datetime, timezone
import hashlib
import time
from pydantic import ValidationError
from simulator.agentic.config import AgenticConfig
from simulator.agentic.runtime.runner import MatchAgentRunner
from simulator.agentic.tools import IntelligenceStore
from simulator.intelligence.explanation import ExplanationEngine
from simulator.intelligence.models import SignificanceTier
from .intelligence import IncrementalIntelligence, moment_key
from .logging import log
from .models import EventEnvelope, PipelineMetrics, ProcessingRecord

def utcnow():
    return datetime.now(timezone.utc)

class EventProcessor:
    def __init__(self, matches, *, metrics=None, explain_notable=False,
                 agent_config=None, runner_factory=MatchAgentRunner, result_callback=None,
                 record_limit=2000):
        if record_limit <= 0:
            raise ValueError('record_limit must be positive')
        self.matches = {m.match_id: IncrementalIntelligence(m) for m in matches}
        self.metrics = metrics or PipelineMetrics()
        self.explain_notable = explain_notable
        self.agent_config = agent_config or AgenticConfig(mode='mock')
        self.runner_factory = runner_factory
        self.result_callback = result_callback
        self.records = deque(maxlen=record_limit)
        self.results = deque(maxlen=record_limit)
        self.workflow_records = deque(maxlen=record_limit)
        self.seen_events = {}
        self.seen_sequences = {}
        self.last_order = {}
        self.locks = {mid: asyncio.Lock() for mid in self.matches}
        self.moments_seen = set()
        self.failed_matches = set()

    async def process(self, value, *, received_at=None):
        received = received_at or utcnow()
        self.metrics.events_received += 1
        try:
            # Roundtrip even model instances: nested payloads are mutable and
            # model_construct/model_copy are not validation boundaries.
            envelope = EventEnvelope.model_validate(value.model_dump(mode='json')
                if isinstance(value, EventEnvelope) else value)
        except (ValidationError, ValueError, KeyError, TypeError):
            self.metrics.events_failed += 1
            self.records.append(ProcessingRecord(status='INVALID_ENVELOPE', received_at=received,
                                                  completed_at=utcnow()))
            log('INVALID_ENVELOPE')
            return 'INVALID_ENVELOPE'
        record = ProcessingRecord(trace_id=envelope.correlation_id, match_id=envelope.match_id,
            event_id=envelope.event_id, sequence_number=envelope.sequence_number,
            produced_at=envelope.produced_at, received_at=received, status='RECEIVED')
        record.ingestion_latency_ms = max(0, (received - envelope.produced_at).total_seconds() * 1000)
        self.records.append(record)
        if envelope.match_id not in self.matches:
            return self._reject('UNKNOWN_MATCH', envelope, record)
        async with self.locks[envelope.match_id]:
            adapter = self.matches[envelope.match_id]
            payload_hash = hashlib.sha256(envelope.payload.model_dump_json().encode()).hexdigest()
            fingerprint = (envelope.event_id, envelope.sequence_number, payload_hash)
            event_key = (envelope.match_id, envelope.event_id)
            sequence_key = (envelope.match_id, envelope.sequence_number)
            previous = self.seen_events.get(event_key) or self.seen_sequences.get(sequence_key)
            if previous is not None:
                if previous != fingerprint:
                    return self._reject('IDENTITY_CONFLICT', envelope, record)
                self.metrics.events_duplicate += 1
                record.status = 'DUPLICATE'
                record.completed_at = utcnow()
                log(record.status, envelope)
                return record.status
            order = (envelope.simulation_timestamp, envelope.sequence_number)
            if envelope.match_id in self.last_order and order < self.last_order[envelope.match_id]:
                self.metrics.events_late += 1
                record.status = 'LATE_REJECTED'
                record.completed_at = utcnow()
                log(record.status, envelope)
                return record.status
            if envelope.match_id in self.failed_matches or adapter.sealed:
                return self._reject('MATCH_CLOSED', envelope, record)
            # Reject unknown roster references before state mutation.
            event = envelope.payload
            teams = set(adapter.teams)
            for name in ('team_id', 'pressing_team_id', 'target_team_id', 'from_team_id', 'to_team_id'):
                value = getattr(event, name, None)
                if value is not None and value not in teams:
                    return self._reject('UNKNOWN_TEAM', envelope, record)
            for name in ('player_id', 'receiver_player_id', 'target_player_id', 'scorer_player_id', 'assist_player_id'):
                value = getattr(event, name, None)
                if value is not None and value not in adapter.engine._players:
                    return self._reject('UNKNOWN_PLAYER', envelope, record)
            started = time.perf_counter()
            record.processing_started_at = utcnow()
            record.queue_wait_latency_ms = max(0, (record.processing_started_at - received).total_seconds() * 1000)
            self.metrics.queue_wait_latency_ms = record.queue_wait_latency_ms
            try:
                updates = adapter.push(event)
            except Exception:
                # Unexpected component failure closes this match; partial mutable
                # indexes must never be consumed as trustworthy later state.
                self.failed_matches.add(envelope.match_id)
                return self._reject('INTELLIGENCE_FAILURE', envelope, record)
            self.seen_events[event_key] = fingerprint
            self.seen_sequences[sequence_key] = fingerprint
            self.last_order[envelope.match_id] = order
            self.metrics.events_processed += 1
            record.intelligence_updated_at = utcnow()
            record.processing_latency_ms = (time.perf_counter() - started) * 1000
            self.metrics.processing_latency_ms = record.processing_latency_ms
            self.metrics.ingestion_latency_ms = record.ingestion_latency_ms
            self.metrics.simulation_time = envelope.simulation_timestamp
            for snapshot, moments in updates:
                fresh = []
                for moment in moments:
                    key = (envelope.match_id, moment_key(moment))
                    if key in self.moments_seen:
                        continue
                    self.moments_seen.add(key)
                    self.metrics.moments_detected += 1
                    record.moment_detected_at = utcnow()
                    record.moment_detection_latency_ms = (
                        record.moment_detected_at - record.received_at).total_seconds() * 1000
                    self.metrics.moment_detection_latency_ms = record.moment_detection_latency_ms
                    log('MOMENT_DETECTED', envelope, moment_type=moment.moment_type.value,
                        score=moment.significance_score, simulation_time=moment.timestamp)
                    if self.should_trigger(moment.tier):
                        fresh.append(moment)
                if fresh:
                    await self._workflows(envelope, record, snapshot, fresh, adapter)
            record.status = 'PROCESSED'
            record.completed_at = utcnow()
            record.end_to_end_latency_ms = max(0, (record.completed_at - envelope.produced_at).total_seconds() * 1000)
            self.metrics.end_to_end_latency_ms = record.end_to_end_latency_ms
            self.metrics.wall_clock_time = record.completed_at
            log(record.status, envelope, processing_latency_ms=record.processing_latency_ms,
                end_to_end_latency_ms=record.end_to_end_latency_ms)
            return record.status

    def should_trigger(self, tier):
        return tier in (SignificanceTier.CRITICAL, SignificanceTier.HIGH) or (
            tier == SignificanceTier.NOTABLE and self.explain_notable)

    def _reject(self, status, envelope, record):
        self.metrics.events_failed += 1
        record.status = status
        record.completed_at = utcnow()
        log(status, envelope)
        return status

    async def _workflows(self, envelope, record, snapshot, moments, adapter):
        # Snapshot events are cut off at creation, not the adapter's later prefix
        # when a checkpoint was sealed by the next event.
        events = [e for e in adapter.events if e.timestamp <= snapshot.summary.match_duration_s]
        runner = None
        try:
            report = ExplanationEngine(source_events=events).explain(snapshot)
            store = IntelligenceStore(snapshot, report, events)
            runner = self.runner_factory(store, config=self.agent_config)
            explained = {e.moment_id for e in report.explanations}
            for moment in moments:
                if moment.moment_id not in explained:
                    log('PHASE3_FILTERED', envelope, moment_type=moment.moment_type.value)
                    continue
                record.workflow_started_at = utcnow()
                record.agent_trigger_latency_ms = (record.workflow_started_at - record.moment_detected_at).total_seconds() * 1000
                self.metrics.agent_trigger_latency_ms = record.agent_trigger_latency_ms
                self.metrics.agent_workflows_triggered += 1
                workflow_record = {'trace_id': envelope.correlation_id, 'match_id': envelope.match_id,
                    'event_id': envelope.event_id, 'sequence_number': envelope.sequence_number,
                    'moment_id': moment.moment_id, 'live_moment_key': moment_key(moment),
                    'current_time': moment.timestamp, 'evidence_ids': moment.evidence_ids,
                    'produced_at': envelope.produced_at.isoformat(), 'received_at': record.received_at.isoformat(),
                    'intelligence_updated_at': record.intelligence_updated_at.isoformat(),
                    'moment_detected_at': record.moment_detected_at.isoformat(),
                    'workflow_started_at': record.workflow_started_at.isoformat(),
                    'agent_trigger_latency_ms': record.agent_trigger_latency_ms, 'status': 'RUNNING'}
                self.workflow_records.append(workflow_record)
                log('AGENTIC_WORKFLOW_TRIGGERED', envelope, moment_type=moment.moment_type.value)
                try:
                    result = await runner.process_moment(moment.moment_id)
                    self.results.append({'match_id': envelope.match_id, 'live_moment_key': moment_key(moment),
                        'moment_type': moment.moment_type.value, 'current_time': moment.timestamp,
                        'result': result.model_dump(mode='json')})
                    if result.workflow_status == 'FAILED':
                        self.metrics.agent_workflows_failed += 1
                    workflow_record['status'] = result.workflow_status
                except Exception:
                    self.metrics.agent_workflows_failed += 1
                    workflow_record['status'] = 'FAILED'
                    log('WORKFLOW_FAILURE', envelope)
                else:
                    if self.result_callback:
                        try:
                            await self.result_callback(result)
                        except Exception:
                            log('RESULT_CALLBACK_FAILURE', envelope)
                finally:
                    workflow_record['completed_at'] = utcnow().isoformat()
        except Exception:
            self.metrics.agent_workflows_failed += 1
            log('WORKFLOW_PREPARATION_FAILURE', envelope)
        finally:
            if runner is not None:
                try:
                    await runner.aclose()
                except Exception:
                    log('WORKFLOW_CLOSE_FAILURE', envelope)

    def final_intelligence(self, match_id):
        return self.matches[match_id].finish()
