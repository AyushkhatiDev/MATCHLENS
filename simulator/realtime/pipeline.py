"""Bounded, lossless source-to-processor handoff with structured cancellation."""
import asyncio
import os
from datetime import datetime, timezone
from .logging import log

async def run_pipeline(source, processor, *, queue_size=None, event_callback=None):
    size = int(os.getenv('REALTIME_QUEUE_SIZE', '64')) if queue_size is None else queue_size
    if size <= 0:
        raise ValueError('REALTIME_QUEUE_SIZE must be positive')
    queue = asyncio.Queue(maxsize=size)
    sentinel = object()
    metrics = processor.metrics
    async def produce():
        async for event in source.events():
            if queue.full():
                metrics.backpressure_count += 1
                log('SLOW_CONSUMER', event if hasattr(event, 'event_id') else None,
                    queue_depth=queue.qsize())
            await queue.put((event, datetime.now(timezone.utc)))
            metrics.queue_depth = queue.qsize()
            metrics.queue_high_watermark = max(metrics.queue_high_watermark, queue.qsize())
        await queue.put(sentinel)
    async def consume():
        while True:
            event = await queue.get()
            metrics.queue_depth = queue.qsize()
            try:
                if event is sentinel:
                    break
                event, received_at = event
                from .processor import EventProcessor
                if isinstance(processor, EventProcessor):
                    status = await processor.process(event, received_at=received_at)
                else:
                    status = await processor.process(event)
                if event_callback:
                    await event_callback(event, status)
            finally:
                queue.task_done()
    await source.start()
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(produce())
            group.create_task(consume())
    finally:
        await source.stop()
        metrics.queue_depth = 0
    return metrics
