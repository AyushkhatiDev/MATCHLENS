import asyncio,json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from simulator.production.config import AppConfig
from simulator.production.observability import health,metrics
from simulator.production.security import scan
from simulator.production.cloud import verify
from simulator.production.evaluation import evaluate
from simulator.presentation.api import create_app,DEFAULT_PATH
from simulator.presentation.service import MatchService
from simulator.agentic.config import AgenticConfig
from simulator.realtime.azure import AzureConfig,AzureTransportError

@pytest.mark.parametrize('env',[
 {'APP_ENV':'azure'}, {'APP_ENV':'azure','APP_FRONTEND_ORIGINS':'https://web.example','APP_DEMO_CONTROLS':'true'},
 {'EVENT_SOURCE':'eventhub'}, {'AGENTIC_MODE':'foundry'}, {'APP_SSE_SECONDS':'1000'},
 {'APP_MAX_SSE_SUBSCRIBERS':'0'}, {'APP_DEMO_CONTROLS':'secret-value'},
 {'APP_FRONTEND_ORIGINS':'https://user:secret@web.example'}, {'REALTIME_QUEUE_SIZE':'0'}])
def test_configuration_fail_closed_without_input_echo(env):
    with pytest.raises(ValueError) as error:AppConfig.from_env(env)
    assert 'secret-value' not in str(error.value) and 'secret@' not in str(error.value)

def staging(**changes):
    return AppConfig(environment='staging',frontend_origins=['https://web.example'],demo_controls=False,diagnostics=False,**changes)

def test_production_startup_health_ready_version_controls():
    with TestClient(create_app(DEFAULT_PATH,staging())) as client:
        assert client.get('/health').json()['status']=='healthy'
        assert client.get('/ready').status_code==200
        assert client.get('/version').json()['environment']=='staging'
        assert client.get('/metrics').status_code==404
        assert client.get('/traces').status_code==404
        mid=client.get('/api/matches').json()[0]['match_id']
        assert client.post('/api/matches/'+mid+'/demo',json={'action':'start','speed':100}).status_code==403
        assert client.get('/api/matches',headers={'Origin':'https://web.example'}).headers['access-control-allow-origin']=='https://web.example'
        assert 'access-control-allow-origin' not in client.get('/api/matches',headers={'Origin':'http://localhost:3000'}).headers
        assert client.get('/health').headers['x-content-type-options']=='nosniff'

def test_degraded_foundry_is_unverified_not_fake_healthy():
    config=staging(agent=AgenticConfig(mode='foundry',project_endpoint='https://project.example',model='deployment'))
    with TestClient(create_app(DEFAULT_PATH,config)) as client:
        data=client.get('/ready').json();assert data['status']=='degraded'
        assert data['agent_runtime']['status']=='unverified'
        assert 'project.example' not in json.dumps(data)

def test_failed_pipeline_not_ready_and_api_error_count():
    app=create_app(DEFAULT_PATH,AppConfig())
    with TestClient(app) as client:
        client.get('/api/matches/missing');app.state.service.status='error'
        assert client.get('/ready').status_code==503
        assert client.get('/metrics').json()['api_errors']>=2

def test_cloud_source_failure_never_switches_to_local(monkeypatch):
    from simulator.realtime.azure import AzureEventHubSource
    async def fail(self):raise AzureTransportError('Injected transport unavailable')
    monkeypatch.setattr(AzureEventHubSource,'start',fail)
    config=AppConfig(event_source='eventhub',azure=AzureConfig(namespace='fake.servicebus.windows.net',eventhub_name='hub'),demo_controls=False)
    service=MatchService(DEFAULT_PATH,config)
    asyncio.run(service.replay())
    assert service.status=='error' and service.clock==0 and not service.events
    assert health(service)['status']=='not_ready'

def test_missing_live_configuration_report_no_network(monkeypatch):
    for key in ('FOUNDRY_PROJECT_ENDPOINT','FOUNDY_PROJECT_ENDPOINT','FOUNDRY_MODEL','AZURE_EVENT_HUB_NAME','AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE','AZURE_EVENT_HUB_CONNECTION_STRING','FABRIC_KQL_ENDPOINT','FABRIC_KQL_DATABASE'):monkeypatch.delenv(key,raising=False)
    data=asyncio.run(verify())
    assert all(row['status']=='not_configured' for row in data.values())
    assert 'FOUNDRY_MODEL' in data['foundry']['missing_configuration']

def test_real_evaluation_and_specialist_failure():
    report=asyncio.run(evaluate(20))
    assert report['cases']==20 and report['grounded']==20
    assert all(row['rejected'] for row in report['negative_controls'])
    assert report['specialist_failure']['status']=='PARTIAL'
    assert report['specialist_failure']['confidence_reduced']
    assert report['traces'] and all(t['duration_ms']>=0 for t in report['traces'])
    assert any(t['tool_calls'] for t in report['traces'])

def test_secret_scan_reports_location_without_value(tmp_path):
    token='ghp_'+'a'*36
    (tmp_path/'leak.txt').write_text(token)
    data=scan(tmp_path)
    assert data['status']=='review_required' and data['findings'][0]['line']==1
    assert token not in json.dumps(data)

def test_deployment_profiles_and_docker_contract():
    for name in ('local','demo','staging'):
        values=dict(line.split('=',1) for line in Path('deploy/'+name+'.env.example').read_text().splitlines() if line and not line.startswith('#'))
        assert AppConfig.from_env(values).environment==name
    docker=Path('Dockerfile').read_text();frontend=Path('frontend/Dockerfile').read_text()
    assert 'USER 10001' in docker and '"--workers","1"' in docker
    assert '.next/standalone' in frontend and 'USER matchlens' in frontend
    assert '**/.env.*' in Path('.dockerignore').read_text()


def test_invalid_startup_input_is_sanitized():
    with pytest.raises(ValueError) as error:create_app(Path('/missing/secret-token.jsonl'),AppConfig())
    assert 'secret-token' not in str(error.value)

def test_stream_capacity_is_rejected_before_response_starts():
    app=create_app(DEFAULT_PATH,AppConfig(max_subscribers=1))
    queue,initial=app.state.service.subscribe()
    with TestClient(app) as client:
        mid=app.state.service.match.match_id
        assert client.get('/api/matches/'+mid+'/stream').status_code==503
    app.state.service.subscribers.discard(queue)

def test_azure_publish_policy_env_stays_server_side():
    config=AppConfig.from_env({'EVENT_SOURCE':'eventhub',
        'AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE':'demo.servicebus.windows.net',
        'AZURE_EVENT_HUB_NAME':'events','REALTIME_BATCH_SIZE':'8',
        'REALTIME_FLUSH_SECONDS':'.1','REALTIME_PUBLISH_TIMEOUT_SECONDS':'5'})
    assert (config.azure.batch_size,config.azure.flush_seconds,config.azure.publish_timeout_seconds)==(8,.1,5)
    assert 'demo.servicebus' not in config.model_dump_json()
    for path in Path('frontend/src').rglob('*'):
        if path.is_file():
            text=path.read_text()
            assert 'AZURE_EVENT_HUB_CONNECTION_STRING' not in text
            assert 'AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE' not in text
