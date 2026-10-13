"""Azure Event Hubs 5.15 async transport; no implicit cloud emulation."""
import asyncio
from datetime import datetime, timezone
import os
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .logging import log
from .models import EventEnvelope
from .sources import EventSource

class AzureConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    namespace: str | None = Field(default=None, repr=False)
    eventhub_name: str | None = Field(default=None, repr=False)
    connection_string: SecretStr | None = Field(default=None, repr=False, exclude=True)
    consumer_group: str = '$Default'
    queue_size: int = Field(default=64, gt=0, le=10000)
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff_seconds: float = Field(default=0.5, ge=0, le=30)
    receive_timeout_seconds: float = Field(default=60, gt=0, le=3600)
    publish_timeout_seconds: float = Field(default=30, gt=0, le=300)
    batch_size: int = Field(default=32, gt=0, le=1000)
    flush_seconds: float = Field(default=0.25, gt=0, le=5)

    @model_validator(mode='after')
    def valid(self):
        if not self.eventhub_name:
            raise ValueError('Set AZURE_EVENT_HUB_NAME')
        if self.connection_string is None and not self.namespace:
            raise ValueError('Set AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE (preferred) or explicit connection-string fallback')
        if self.namespace and (not self.namespace.endswith('.servicebus.windows.net')
                or any(c in self.namespace for c in '/;@ \n')):
            raise ValueError('Namespace must be an Azure Event Hubs DNS hostname')
        return self

    @classmethod
    def from_env(cls):
        return cls(namespace=os.getenv('AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE') or None,
            eventhub_name=os.getenv('AZURE_EVENT_HUB_NAME') or None,
            connection_string=os.getenv('AZURE_EVENT_HUB_CONNECTION_STRING') or None,
            consumer_group=os.getenv('AZURE_EVENT_HUB_CONSUMER_GROUP', '$Default'),
            queue_size=int(os.getenv('REALTIME_QUEUE_SIZE', '64')),
            max_attempts=int(os.getenv('REALTIME_MAX_ATTEMPTS', '3')),
            backoff_seconds=float(os.getenv('REALTIME_BACKOFF_SECONDS', '.5')),
            receive_timeout_seconds=float(os.getenv('REALTIME_RECEIVE_TIMEOUT_SECONDS', '60')),
            publish_timeout_seconds=float(os.getenv('REALTIME_PUBLISH_TIMEOUT_SECONDS', '30')),
            batch_size=int(os.getenv('REALTIME_BATCH_SIZE', '32')),
            flush_seconds=float(os.getenv('REALTIME_FLUSH_SECONDS', '.25')))

class AzureTransportError(RuntimeError):
    pass

async def retry(operation, config, *, sleep=asyncio.sleep, envelope=None, timeout=None):
    from azure.eventhub.exceptions import ConnectError, ConnectionLostError, OperationTimeoutError
    transient = (ConnectError, ConnectionLostError, OperationTimeoutError, TimeoutError)
    for attempt in range(config.max_attempts):
        try:
            return await operation() if timeout is None else await asyncio.wait_for(operation(), timeout)
        except transient:
            log('AZURE_TRANSIENT_FAILURE', envelope, attempt=attempt + 1)
            if attempt + 1 == config.max_attempts:
                raise AzureTransportError('Event Hubs transient retry attempts exhausted') from None
            await sleep(config.backoff_seconds * 2 ** attempt)
        except asyncio.CancelledError:
            raise
        except Exception:
            # No raw SDK exception can expose a credential or connection string.
            log('AZURE_PERMANENT_FAILURE', envelope)
            raise AzureTransportError('Event Hubs operation failed; check authentication, permissions and configuration') from None

def serialize(envelope):
    from azure.eventhub import EventData
    checked = EventEnvelope.model_validate(envelope.model_dump(mode='json'))
    data = EventData(checked.model_dump_json())
    data.content_type = 'application/json'
    data.properties = {'schema_version': checked.schema_version}
    return data

def deserialize(data):
    return EventEnvelope.model_validate_json(data.body_as_str(encoding='UTF-8'))

class AzureEventHubProducer:
    def __init__(self, config, *, client=None):
        self.config = config
        self.client = client
        self.credential = None

    async def start(self):
        if self.client is None:
            from azure.eventhub.aio import EventHubProducerClient
            if self.config.connection_string is not None:
                self.client = EventHubProducerClient.from_connection_string(
                    self.config.connection_string.get_secret_value(), eventhub_name=self.config.eventhub_name,
                    retry_total=0, logging_enable=False)
            else:
                from azure.identity.aio import DefaultAzureCredential
                self.credential = DefaultAzureCredential()
                self.client = EventHubProducerClient(self.config.namespace, self.config.eventhub_name,
                    self.credential, retry_total=0, logging_enable=False)

    async def publish(self, envelope):
        await self.publish_batch([envelope])

    async def publish_stream(self, envelopes):
        """Bounded realtime batches, with at most one pending iterator read.

        Awaiting sends propagates backpressure to the replay; the pending read
        is retained across timer flushes so pacing sleeps are never restarted.
        Caller owns producer/source startup and shutdown.
        """
        iterator = aiter(envelopes)
        pending = None
        batch = []
        deadline = None
        count = 0
        loop = asyncio.get_running_loop()
        try:
            while True:
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                timeout = None if not batch else max(0, deadline - loop.time())
                done, _ = await asyncio.wait((pending,), timeout=timeout)
                if not done:
                    await self.publish_batch(batch)
                    count += len(batch)
                    batch = []
                    continue
                try:
                    envelope = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                if not batch:
                    deadline = loop.time() + self.config.flush_seconds
                batch.append(envelope)
                if len(batch) >= self.config.batch_size:
                    await self.publish_batch(batch)
                    count += len(batch)
                    batch = []
            if batch:
                await self.publish_batch(batch)
                count += len(batch)
            return count
        finally:
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            if hasattr(iterator, 'aclose'):
                await iterator.aclose()

    async def publish_batch(self, envelopes):
        if self.client is None:
            raise RuntimeError('Producer has not been started')
        # Group by match preserving per-match order. There is no cross-match
        # ordering guarantee and each batch has exactly one partition key.
        groups = {}
        for envelope in envelopes:
            groups.setdefault(envelope.match_id, []).append(envelope)
        for match_id, group in groups.items():
            batch = await retry(lambda: self.client.create_batch(partition_key=match_id), self.config,
                                envelope=group[0], timeout=self.config.publish_timeout_seconds)
            for envelope in group:
                data = serialize(envelope)
                try:
                    batch.add(data)
                except ValueError:
                    if len(batch) == 0:
                        raise AzureTransportError('Envelope exceeds Event Hubs batch size') from None
                    await retry(lambda: self.client.send_batch(batch), self.config, envelope=envelope, timeout=self.config.publish_timeout_seconds)
                    batch = await retry(lambda: self.client.create_batch(partition_key=match_id), self.config,
                                        envelope=envelope, timeout=self.config.publish_timeout_seconds)
                    try:
                        batch.add(data)
                    except ValueError:
                        raise AzureTransportError('Envelope exceeds Event Hubs batch size') from None
            if len(batch):
                await retry(lambda: self.client.send_batch(batch), self.config, envelope=group[-1], timeout=self.config.publish_timeout_seconds)

    async def stop(self):
        try:
            if self.client is not None:
                await self.client.close()
        finally:
            self.client = None
            if self.credential is not None:
                await self.credential.close()
                self.credential = None

class AzureEventHubConsumer:
    def __init__(self, config, *, client=None):
        self.config = config
        self.client = client
        self.credential = None
        self.positions = {}
        self.ready = asyncio.Event()
        self.initialized_partitions = set()
        self.partition_count = None

    async def start(self):
        if self.client is None:
            from azure.eventhub.aio import EventHubConsumerClient
            options = {'consumer_group': self.config.consumer_group, 'retry_total': 0, 'logging_enable': False}
            if self.config.connection_string is not None:
                self.client = EventHubConsumerClient.from_connection_string(
                    self.config.connection_string.get_secret_value(), eventhub_name=self.config.eventhub_name, **options)
            else:
                from azure.identity.aio import DefaultAzureCredential
                self.credential = DefaultAzureCredential()
                self.client = EventHubConsumerClient(self.config.namespace, self.config.eventhub_name,
                    credential=self.credential, **options)
        partitions = await retry(self.client.get_partition_ids, self.config)
        self.partition_count = len(partitions)
        self.ready.clear()
        self.initialized_partitions.clear()

    async def receive(self, handler, *, starting_position=None):
        if self.client is None:
            raise RuntimeError('Consumer has not been started')
        async def initialized(context):
            self.initialized_partitions.add(context.partition_id)
            if len(self.initialized_partitions) == self.partition_count:
                self.ready.set()
        async def on_event(context, data):
            if data is None:
                return
            try:
                envelope = deserialize(data)
            except (ValueError, KeyError, TypeError):
                log('AZURE_INVALID_ENVELOPE')
                await handler({'invalid_azure_envelope': True})
            else:
                await handler(envelope)
            # This is an in-process resume cursor after downstream acceptance,
            # not durable checkpointing. Source's bounded queue acceptance may
            # precede processing; a crash can replay, and processor dedups.
            self.positions[context.partition_id] = data.offset
        async def operation():
            # The SDK catches exceptions raised by on_error. Supervise receive
            # via a separate future so partition errors actually reach our
            # bounded retry policy rather than silently spinning inside the SDK.
            failure = asyncio.get_running_loop().create_future()
            async def on_error(context, error):
                if not failure.done():
                    failure.set_result(error)
            receiving = asyncio.create_task(self.client.receive(on_event,
                starting_position=self.positions or starting_position or '-1',
                starting_position_inclusive=False, prefetch=self.config.queue_size,
                on_partition_initialize=initialized, on_error=on_error))
            try:
                await asyncio.wait((receiving, failure), return_when=asyncio.FIRST_COMPLETED)
                if failure.done():
                    raise failure.result() from None
                await receiving
            finally:
                failure.cancel()
                receiving.cancel()
                await asyncio.gather(receiving, return_exceptions=True)
        await retry(operation, self.config)

    async def stop(self):
        try:
            if self.client is not None:
                await self.client.close()
        finally:
            self.client = None
            if self.credential is not None:
                await self.credential.close()
                self.credential = None

class AzureEventHubSource(EventSource):
    def __init__(self, consumer, *, correlation_ids=None, expected_event_count=None):
        self.consumer = consumer
        self.queue = asyncio.Queue(maxsize=consumer.config.queue_size)
        self.correlation_ids = set(correlation_ids) if correlation_ids is not None else None
        self.expected_event_count = expected_event_count
        self.accepted = set()
        self.task = None
        self.error = None
        self.done = object()

    async def start(self):
        try:
            await self.consumer.start()
        except BaseException:
            await self.consumer.stop()
            raise
        async def handler(envelope):
            if isinstance(envelope, EventEnvelope):
                if self.correlation_ids is not None and envelope.correlation_id not in self.correlation_ids:
                    return
                self.accepted.add((envelope.match_id, envelope.event_id, envelope.sequence_number))
            if self.queue.full():
                log('AZURE_SOURCE_BACKPRESSURE', envelope if isinstance(envelope, EventEnvelope) else None,
                    queue_depth=self.queue.qsize())
            await self.queue.put(envelope)
        async def receive():
            try:
                await self.consumer.receive(handler, starting_position=datetime.now(timezone.utc))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.error = AzureTransportError('Event Hubs receive failed')
            finally:
                # Normal stop cancels this task; do not block cancellation by
                # trying to append a sentinel into an abandoned full queue.
                if not asyncio.current_task().cancelling():
                    await self.queue.put(self.done)
        self.task = asyncio.create_task(receive())

    async def events(self):
        count = set()
        while True:
            try:
                item = await asyncio.wait_for(self.queue.get(), self.consumer.config.receive_timeout_seconds)
            except TimeoutError:
                raise AzureTransportError('Timed out waiting for Event Hubs events; no local fallback') from None
            if item is self.done:
                if self.error:
                    raise self.error
                return
            yield item
            if isinstance(item, EventEnvelope):
                count.add((item.match_id, item.event_id, item.sequence_number))
            if self.expected_event_count is not None and len(count) >= self.expected_event_count:
                return

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.consumer.stop()
