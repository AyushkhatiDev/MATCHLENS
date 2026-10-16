"""Interchangeable async sources and timestamp-based offline replay."""
import asyncio
import json
import math
from abc import ABC, abstractmethod
from pathlib import Path
from .models import EventEnvelope, parse_event

class EventSource(ABC):
    @abstractmethod
    async def start(self): ...
    @abstractmethod
    async def stop(self): ...
    @abstractmethod
    def events(self): ...

async def correlated_replay(replay, run_id, source='azure_replay', published=None):
    """Tag a started replay's envelopes with `{run_id}:{sequence}` for one publish run; optionally
    record exactly what was handed to the publisher."""
    async for envelope in replay.events():
        if not isinstance(envelope, EventEnvelope):
            raise ValueError('Replay input contains an invalid row; refusing to publish it')
        tagged = EventEnvelope.model_validate({**envelope.model_dump(mode='json'),
                                               'correlation_id': f'{run_id}:{envelope.sequence_number}', 'source': source})
        if published is not None:
            published.append(tagged)
        yield tagged


class LocalReplaySource(EventSource):
    def __init__(self, path, speed=1, *, sleep=asyncio.sleep):
        if not math.isfinite(speed) or speed < 0:
            raise ValueError('speed must be finite and nonnegative; 0 means instant')
        self.path = Path(path)
        self.speed = speed
        self.sleep = sleep
        self.running = False
        self._stop = asyncio.Event()

    async def start(self):
        if not self.path.is_file():
            raise ValueError('Replay JSONL file is missing')
        self._stop.clear()
        self.running = True

    async def stop(self):
        self.running = False
        self._stop.set()

    async def events(self):
        if not self.running:
            raise RuntimeError('Source has not been started')
        # File order is the sequence number; reject backwards input rather than
        # silently assigning a different identity. Malformed rows reach processor.
        previous = None
        sequence = 0
        with self.path.open() as stream:
            for line in stream:
                if not self.running:
                    break
                if not line.strip():
                    continue
                try:
                    event = parse_event(json.loads(line))
                except (ValueError, KeyError, TypeError):
                    yield {'invalid_replay_row': True}
                    sequence += 1
                    continue
                if previous is not None and event.timestamp < previous:
                    raise ValueError('Replay timestamps must be nondecreasing')
                gap = 0 if previous is None or self.speed == 0 else (event.timestamp - previous) / self.speed
                if gap:
                    delay = asyncio.create_task(self.sleep(gap))
                    stopped = asyncio.create_task(self._stop.wait())
                    try:
                        await asyncio.wait((delay, stopped), return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        for task in (delay, stopped):
                            if not task.done():
                                task.cancel()
                        await asyncio.gather(delay, stopped, return_exceptions=True)
                if not self.running:
                    break
                yield EventEnvelope.wrap(event, sequence)
                previous = event.timestamp
                sequence += 1
