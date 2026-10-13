"""Explicit real-service probes using the installed Phase 4/5 integrations."""
import asyncio,os,uuid,time
from pathlib import Path
from simulator.agentic import AgenticConfig,IntelligenceStore,MatchAgentRunner
from simulator.intelligence.explanation import ExplanationEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.live import load_match
from simulator.realtime.models import EventEnvelope,parse_event
from simulator.realtime.processor import EventProcessor
from simulator.realtime.azure import AzureConfig,AzureEventHubProducer,AzureEventHubConsumer,AzureEventHubSource
from .observability import Observability,distribution
from .evaluation import samples

SEED99=Path(__file__).resolve().parents[2]/'data/generated/phase5/seed99/match.jsonl'
def seed99():
    import json
    return load_match(SEED99),[parse_event(json.loads(line)) for line in SEED99.read_text().splitlines()]
def missing(service):
    if service=='foundry':
        values=[]
        if not os.getenv('FOUNDRY_PROJECT_ENDPOINT') and not os.getenv('FOUNDY_PROJECT_ENDPOINT'):values.append('FOUNDRY_PROJECT_ENDPOINT')
        if not os.getenv('FOUNDRY_MODEL'):values.append('FOUNDRY_MODEL')
        return values
    if service=='eventhub':
        values=[]
        if not os.getenv('AZURE_EVENT_HUB_NAME'):values.append('AZURE_EVENT_HUB_NAME')
        if not os.getenv('AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE') and not os.getenv('AZURE_EVENT_HUB_CONNECTION_STRING'):values.append('AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE (or explicit connection-string fallback)')
        return values
    return [key for key in ('FABRIC_KQL_ENDPOINT','FABRIC_KQL_DATABASE','FABRIC_WORKSPACE_ID',
                           'FABRIC_EVENTSTREAM_ID','FABRIC_EVENTHOUSE_ID') if not os.getenv(key)]
async def foundry():
    match,events=seed99();intel=IntelligenceEngine(match,events).analyze();report=ExplanationEngine(source_events=events).explain(intel)
    goal=next(e for e in report.explanations if e.explanation_type.value=='GOAL')
    config=AgenticConfig.from_env({**os.environ,'AGENTIC_MODE':'foundry'})
    observer=Observability();runner=observer.runner(IntelligenceStore(intel,report,events),config)
    started=time.perf_counter()
    try:result=await runner.process_moment(goal.moment_id)
    finally:await runner.aclose()
    return dict(status='live_verified' if result.workflow_status=='COMPLETE' and result.state.explanation_validated and result.narrative else 'failed',
        runtime=result.runtime_mode,workflow_status=result.workflow_status,trace_id=result.trace_id,
        match_id=result.match_id,moment_id=result.moment_id,duration_ms=(time.perf_counter()-started)*1000,
        errors=[e.code for e in result.errors],traces=list(observer.traces),result=result.model_dump(mode='json'))
async def eventhub():
    match,events=seed99()
    from simulator.generation.generator import generate_match
    from simulator.engine.match_engine import MatchEngine
    other=generate_match(seed=42)
    other_events=MatchEngine(other).simulate()
    # A legitimate synthetic match can be scoreless. Never assume every seed
    # contains a goal, or fabricate one to satisfy a verification probe.
    prefix=lambda seq:seq[:next((i+1 for i,e in enumerate(seq) if e.event_type.value=='GOAL'),len(seq))]
    run_id=uuid.uuid4().hex;envelopes=[]
    for group in (prefix(events),prefix(other_events)):
        envelopes.extend(EventEnvelope.wrap(event,index,source='phase7_live_verification',correlation_id=run_id) for index,event in enumerate(group))
    published=[envelopes[0],envelopes[0],*envelopes[1:]]
    config=AzureConfig.from_env();consumer=AzureEventHubConsumer(config)
    source=AzureEventHubSource(consumer,correlation_ids=[run_id],expected_event_count=len(envelopes))
    producer=AzureEventHubProducer(config);observer=Observability()
    processor=EventProcessor([match,other],runner_factory=observer.runner)
    producer_ok=False;started=time.perf_counter();observed=[]
    expected={(e.match_id,e.event_id,e.sequence_number):e for e in envelopes}
    integrity=True
    async def checked_event(envelope,status):
        nonlocal integrity
        original=expected.get((envelope.match_id,envelope.event_id,envelope.sequence_number))
        integrity=integrity and original==envelope
        observed.append(envelope)
    # Reuse the production bounded pipeline; publish waits for its consumer
    # initialization, while inference runs independently of receive callbacks.
    from simulator.realtime.pipeline import run_pipeline
    try:
        receiving=asyncio.create_task(run_pipeline(source,processor,queue_size=config.queue_size,event_callback=checked_event))
        try:
            await asyncio.wait_for(consumer.ready.wait(),config.receive_timeout_seconds)
            await producer.start();await producer.publish_batch(published);producer_ok=True
            await asyncio.wait_for(receiving,config.receive_timeout_seconds)
        finally:
            receiving.cancel();await asyncio.gather(receiving,return_exceptions=True)
    finally:await source.stop();await producer.stop()
    counts={mid:len(adapter.events) for mid,adapter in processor.matches.items()}
    valid=integrity and processor.metrics.events_processed==len(envelopes) and processor.metrics.events_duplicate==1 and len(counts)==2 and all(counts.values()) and processor.metrics.agent_workflows_triggered>0 and not processor.metrics.agent_workflows_failed
    return dict(status='live_verified' if valid else 'failed',run_id=run_id,producer_success=producer_ok,
        consumer_success=processor.metrics.events_processed==len(envelopes),published_count=len(published),
        unique_expected=len(envelopes),integrity_verified=integrity,received_count=len(observed),
        namespace=config.namespace,eventhub=config.eventhub_name,consumer_group=config.consumer_group,
        offsets=dict(consumer.positions),metrics=processor.metrics.model_dump(mode='json'),match_event_counts=counts,
        workflow_runtime='mock',latency_samples={field:distribution([getattr(r,field) for r in processor.records if getattr(r,field)>0]) for field in ('processing_latency_ms','moment_detection_latency_ms','agent_trigger_latency_ms','end_to_end_latency_ms')},
        wall_ms=(time.perf_counter()-started)*1000,workflow_results=[r['result']['workflow_status'] for r in processor.results],
        semantics='match_id partition key; at-least-once, in-process deduplication, no global ordering or durable exactly-once claim')
async def fabric_query():
    # Read-only KQL query, no resource or ingestion creation. Eventstream setup remains manual.
    import httpx
    from azure.identity.aio import DefaultAzureCredential
    from urllib.parse import urlsplit
    endpoint=os.environ['FABRIC_KQL_ENDPOINT'].rstrip('/')
    url=urlsplit(endpoint)
    if url.scheme!='https' or not url.hostname or not url.hostname.endswith('.kusto.fabric.microsoft.com') or url.username or url.query or url.path:
        raise ValueError('Invalid Fabric query endpoint')
    match,events=seed99();ids={e.event_id:e for e in events}
    query=f'MatchEvents | where match_id == "{match.match_id}" | project match_id,event_id,event_type,timestamp,team_id,player_id | take 2000'
    async with DefaultAzureCredential() as credential:
        token=await credential.get_token('https://api.kusto.windows.net/.default')
        async with httpx.AsyncClient(timeout=60) as client:
            response=await client.post(endpoint+'/v1/rest/query',headers={'Authorization':'Bearer '+token.token},json={'db':os.environ['FABRIC_KQL_DATABASE'],'csl':query})
            response.raise_for_status();data=response.json()
    tables=[table for table in data.get('Tables',[]) if {c['ColumnName'] for c in table.get('Columns',[])}>={'match_id','event_id','event_type','timestamp','team_id','player_id'}]
    rows=[]
    for table in tables:
        columns=[c['ColumnName'] for c in table['Columns']];rows.extend(dict(zip(columns,row)) for row in table['Rows'])
    valid=[]
    for row in rows:
        event=ids.get(row['event_id'])
        if event and row['match_id']==match.match_id and row['event_type']==event.event_type.value and row['timestamp']==event.timestamp and (row['team_id'] or None)==event.team_id and (row['player_id'] or None)==event.player_id:valid.append(row['event_id'])
    return dict(status='live_query_verified' if valid and len(valid)==len(rows) else 'failed',rows_returned=len(rows),rows_source_verified=len(valid),
        query=query,eventstream_receipt='not_automatically_verified; capture Eventstream source metrics separately',
        end_to_end_status='not_verified_without_eventstream_receipt_and_run_correlation',scope='Read-only seed99 source-reference query verification; does not establish the ingestion path by itself')

def fabric_envelopes(data, expected):
    """Validate complete stored canonical envelopes, not just six projections."""
    rows=[]
    for table in data.get('Tables',[]):
        columns=[c['ColumnName'] for c in table.get('Columns',[])]
        if 'envelope' not in columns:continue
        for values in table.get('Rows',[]):
            row=dict(zip(columns,values))['envelope']
            if isinstance(row,str):
                import json
                row=json.loads(row)
            envelope=EventEnvelope.model_validate(row)
            key=(envelope.match_id,envelope.event_id,envelope.sequence_number)
            if key not in expected or envelope!=expected[key]:
                raise ValueError('Fabric destination changed the canonical envelope')
            rows.append(envelope)
    return rows

async def fabric(*, agent_mode='mock'):
    """Real Event Hubs → configured Eventstream → KQL → existing processor probe.

    No ingestion shortcut: only Event Hubs is written; KQL is read-only. A fresh
    correlation ID and full-envelope equality prove the observed data path.
    """
    import json,base64,httpx
    from urllib.parse import urlsplit
    from azure.identity.aio import DefaultAzureCredential
    workspace=str(uuid.UUID(os.environ['FABRIC_WORKSPACE_ID']))
    stream=str(uuid.UUID(os.environ['FABRIC_EVENTSTREAM_ID']))
    house=str(uuid.UUID(os.environ['FABRIC_EVENTHOUSE_ID']))
    endpoint=os.environ['FABRIC_KQL_ENDPOINT'].rstrip('/')
    url=urlsplit(endpoint)
    if url.scheme!='https' or not url.hostname or not url.hostname.endswith('.kusto.fabric.microsoft.com') or url.username or url.query or url.path:
        raise ValueError('Invalid Fabric query endpoint')
    match,events=seed99()
    prefix=events[:next(i+1 for i,e in enumerate(events) if e.event_type.value=='GOAL')]
    run_id=uuid.uuid4().hex
    envelopes=[EventEnvelope.wrap(e,i,source='fabric_live_verification',correlation_id=run_id) for i,e in enumerate(prefix)]
    expected={(e.match_id,e.event_id,e.sequence_number):e for e in envelopes}
    query='MatchEvents | where correlation_id == "'+run_id+'" | project envelope'
    config=AzureConfig.from_env();producer=AzureEventHubProducer(config)
    observed=[]
    async with DefaultAzureCredential() as credential, httpx.AsyncClient(timeout=60) as client:
        fabric_token=await credential.get_token('https://api.fabric.microsoft.com/.default')
        headers={'Authorization':'Bearer '+fabric_token.token}
        api='https://api.fabric.microsoft.com/v1'
        for item,kind in ((stream,'Eventstream'),(house,'Eventhouse')):
            response=await client.get(f'{api}/workspaces/{workspace}/items/{item}',headers=headers)
            response.raise_for_status()
            if response.json().get('type')!=kind:raise ValueError('Fabric item type mismatch')
        definition=await client.post(f'{api}/workspaces/{workspace}/items/{stream}/getDefinition',headers=headers)
        definition.raise_for_status()
        if definition.status_code==202:
            location=definition.headers.get('Location','')
            if not location.startswith(api+'/operations/'):raise ValueError('Unexpected Fabric operation URL')
            for _ in range(30):
                status=await client.get(location,headers=headers);status.raise_for_status()
                if status.json().get('status')=='Succeeded':
                    definition=await client.get(location+'/result',headers=headers);definition.raise_for_status();break
                if status.json().get('status')=='Failed':raise ValueError('Fabric definition operation failed')
                await asyncio.sleep(2)
            else:raise TimeoutError('Fabric definition operation timed out')
        parts=definition.json().get('definition',{}).get('parts',[])
        topology=json.loads(base64.b64decode(next(p['payload'] for p in parts if p['path']=='eventstream.json')))
        if not any(s.get('type')=='AzureEventHub' for s in topology.get('sources',[])):
            raise ValueError('Eventstream has no Azure Event Hubs source')
        if not any(d.get('type')=='Eventhouse' and d.get('properties',{}).get('itemId')==house
                   for d in topology.get('destinations',[])):
            raise ValueError('Eventstream is not linked to the configured Eventhouse')
        kql_token=await credential.get_token('https://api.kusto.windows.net/.default')
        try:
            await producer.start();await producer.publish_batch([envelopes[0],envelopes[0],*envelopes[1:]])
        finally:await producer.stop()
        for _ in range(60):
            response=await client.post(endpoint+'/v1/rest/query',headers={'Authorization':'Bearer '+kql_token.token},
                json={'db':os.environ['FABRIC_KQL_DATABASE'],'csl':query})
            response.raise_for_status();observed=fabric_envelopes(response.json(),expected)
            if len({e.event_id for e in observed})==len(envelopes) and len(observed)>=len(envelopes)+1:break
            await asyncio.sleep(2)
        else:raise TimeoutError('Published run was not fully observed in Fabric')
    agent=AgenticConfig(mode='mock') if agent_mode=='mock' else AgenticConfig.from_env({**os.environ,'AGENTIC_MODE':'foundry'})
    processor=EventProcessor([match],agent_config=agent)
    # KQL storage does not imply source order. Restore the canonical per-match
    # sequence before feeding the unchanged incremental intelligence engine.
    for envelope in sorted(observed,key=lambda e:(e.match_id,e.sequence_number)):
        await processor.process(envelope)
    results=[entry['result'] for entry in processor.results]
    valid=processor.metrics.events_processed==len(envelopes) and processor.metrics.events_duplicate>=1 and results and all(
        r['workflow_status']=='COMPLETE' and r['state']['explanation_validated'] and r['narrative'] for r in results)
    return dict(status='live_verified' if valid else 'failed',run_id=run_id,workspace_id=workspace,
        eventstream_id=stream,eventhouse_id=house,records_observed=len(observed),unique_expected=len(envelopes),
        integrity_verified=True,workflow_runtime=agent_mode,metrics=processor.metrics.model_dump(mode='json'),
        workflow_results=results,scope='Real Event Hubs publication, Eventstream topology, correlated KQL data, canonical validation and existing intelligence workflow; no public deployment claim')
async def verify(services=('foundry','eventhub','fabric')):
    results={}
    for name in services:
        absent=missing(name)
        if absent:results[name]={'status':'not_configured','missing_configuration':absent};continue
        try:results[name]=await asyncio.wait_for(globals()[name](),timeout=600)
        except Exception as error:results[name]={'status':'failed','error_type':type(error).__name__,'message':'Live probe failed; check identity, permissions, network, resource configuration or grounded output. Raw error withheld.'}
    return results
