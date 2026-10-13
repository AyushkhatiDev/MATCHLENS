"""Safe structured traces: never log payloads, credentials or exception strings."""
import json
import logging

logger = logging.getLogger('matchlens.realtime')

def log(code, envelope=None, **fields):
    # Caller fields are deliberately limited to fixed messages and numeric metrics.
    data = {'code': code, 'trace_id': None, 'match_id': None, 'event_id': None, 'sequence_number': None}
    if envelope is not None:
        # Untrusted external identifiers could contain credentials. Only log a digest
        # for arbitrary identifiers; simulator identifiers are still available in records.
        import hashlib
        safe = lambda v: hashlib.sha256(str(v).encode()).hexdigest()[:16]
        data.update(trace_id=safe(envelope.correlation_id), match_id=safe(envelope.match_id),
                    event_id=safe(envelope.event_id), sequence_number=envelope.sequence_number)
    data.update(fields)
    logger.info(json.dumps(data, separators=(',', ':')))
