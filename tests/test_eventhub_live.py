"""Opt-in real Event Hubs roundtrip; requires an existing hub and permissions."""
import asyncio
import os
import uuid
import pytest
from simulator.models.events import MatchEvent, EventType
from simulator.realtime.models import EventEnvelope
from simulator.realtime.azure import AzureConfig, AzureEventHubProducer, AzureEventHubConsumer, AzureEventHubSource

@pytest.mark.eventhub
@pytest.mark.skipif(os.getenv('MATCHLENS_RUN_EVENTHUB_TESTS') != '1', reason='Live Event Hubs test is opt-in')
def test_real_eventhub_roundtrip():
    async def exercise():
        config = AzureConfig.from_env()
        run_id = uuid.uuid4().hex
        event = MatchEvent(event_id=run_id, match_id='integration_' + run_id,
                           timestamp=0, minute=0, event_type=EventType.MATCH_START)
        envelope = EventEnvelope.wrap(event, 0, source='integration_test', correlation_id=run_id)
        source = AzureEventHubSource(AzureEventHubConsumer(config), correlation_ids=[run_id], expected_event_count=1)
        producer = AzureEventHubProducer(config)
        try:
            await source.start()
            await asyncio.wait_for(source.consumer.ready.wait(), config.receive_timeout_seconds)
            await producer.start()
            await producer.publish(envelope)
            received = [item async for item in source.events()]
            assert received == [envelope]
        finally:
            await source.stop()
            await producer.stop()
    asyncio.run(exercise())

@pytest.mark.eventhub
@pytest.mark.skipif(os.getenv('MATCHLENS_RUN_EVENTHUB_TESTS') != '1', reason='Live Event Hubs test is opt-in')
def test_real_eventhub_pipeline_integrity_duplicates_and_two_matches():
    import json
    from pathlib import Path
    from simulator.production.cloud import eventhub
    result=asyncio.run(eventhub())
    Path('data/generated/eventhub-live-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['status']=='live_verified'
    assert result['integrity_verified']
    assert result['metrics']['events_duplicate']==1
    assert result['metrics']['events_processed']==result['unique_expected']
    assert len(result['match_event_counts'])==2
    assert result['offsets'] and result['workflow_results']
    assert all(status=='COMPLETE' for status in result['workflow_results'])
