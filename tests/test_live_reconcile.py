"""Offline contract for `simulator.live --mode azure --fabric-reconcile`: full-match Fabric reconciliation."""
import copy
import json
import pytest
import simulator.live as live
import simulator.realtime.azure as azure_module
from simulator.presentation.api import DEFAULT_PATH
from simulator.production.transport import FabricTarget, VerificationFailed
from simulator.production.transport_report import EvidenceError, check_full, render
from simulator.realtime.azure import AzureConfig
from tests.test_streaming import EXPECTED, EventHub, StreamFabric, fakes
from tests.test_transport_verification import HUB, NAMESPACE, TARGET, path

FABRIC_ENV = {'FABRIC_WORKSPACE_ID': TARGET.workspace_id, 'FABRIC_EVENTSTREAM_ID': TARGET.eventstream_id,
              'FABRIC_EVENTHOUSE_ID': TARGET.eventhouse_id, 'FABRIC_KQL_ENDPOINT': TARGET.kql_endpoint,
              'FABRIC_KQL_DATABASE': TARGET.kql_database, 'FABRIC_SOURCE_CONSUMER_GROUP': '$Default'}


def configure(monkeypatch, hub, fabric, group='matchlens-verification', **fake_options):
    for name, value in {'AZURE_EVENT_HUB_FULLY_QUALIFIED_NAMESPACE': NAMESPACE, 'AZURE_EVENT_HUB_NAME': HUB,
                        'AZURE_EVENT_HUB_CONSUMER_GROUP': group, 'REALTIME_RECEIVE_TIMEOUT_SECONDS': '5', **FABRIC_ENV}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv('AZURE_EVENT_HUB_CONNECTION_STRING', raising=False)
    consumer, producer = fakes(hub, **fake_options)
    monkeypatch.setattr(azure_module, 'AzureEventHubConsumer', consumer)
    monkeypatch.setattr(azure_module, 'AzureEventHubProducer', producer)
    monkeypatch.setattr(live.FullMatchReconciler, 'reader', lambda self: fabric)


def run_cli(tmp_path, *extra):
    record = tmp_path / 'record.json'
    argv = ['--match', str(DEFAULT_PATH), '--mode', 'azure', '--speed', '0', '--quiet', '--output', str(tmp_path / 'out'),
            '--fabric-reconcile', '--fabric-timeout', '30', '--record', str(record), *extra]
    try:
        live.main(argv)
        code = 0
    except SystemExit as exit_:
        code = exit_.code
    return code, json.loads(record.read_text()) if record.exists() else None


@pytest.fixture(scope='module')
def verified(tmp_path_factory):
    # One full-match run serves every check that only inspects its record.
    tmp_path = tmp_path_factory.mktemp('verified')
    hub = EventHub()
    with pytest.MonkeyPatch.context() as monkeypatch:
        configure(monkeypatch, hub, StreamFabric(hub), duplicate={7})
        code, record = run_cli(tmp_path)
    assert code == 0
    return record, hub, tmp_path


def test_full_match_run_reconciles_all_four_stages_and_renders_evidence(verified):
    record, hub, tmp_path = verified
    assert record['kind'] == 'full_match_replay' and record['status'] == 'verified' and record['failure'] is None
    assert record['reconciliation']['reconciled'] and record['reconciliation']['expected'] == EXPECTED
    stages = record['reconciliation']['stages']
    assert {name: stages[name]['unique'] for name in stages} == dict.fromkeys(stages, EXPECTED)
    assert stages['eventhub_readback']['duplicates'] == 1 and stages['api_processed']['duplicates'] == 0
    assert all(e.correlation_id == f"{record['run_id']}:{e.sequence_number}" for e in hub.log)
    assert record['agent_runtime'] == 'mock' and record['foundry_called'] is False
    assert record['eventhubs']['readback_consumer_group'] == 'matchlens-verification'
    assert json.loads((tmp_path / 'out' / 'metrics.json').read_text())['fabric_verified'] is True
    report = render(record, record_sha256='0' * 64)
    assert '**Status: VERIFIED**' in report and 'Microsoft Foundry was not called' in report
    assert 'One run, not continuous monitoring' in report
    text = json.dumps(record)
    assert 'SharedAccess' not in text and 'token' not in text.lower()


def test_topology_mismatch_publishes_nothing_and_records_the_failure(monkeypatch, tmp_path):
    hub = EventHub()
    configure(monkeypatch, hub, StreamFabric(hub, path=path(endpoint='matchlens-other.servicebus.windows.net')))
    code, record = run_cli(tmp_path)
    assert code == 1 and hub.log == []
    assert record['status'] == 'failed' and record['failure']['stage'] == 'topology'
    assert record['fabric']['topology_verified'] is False and record['reconciliation'] is None


def test_partial_fabric_ingestion_fails_the_record(monkeypatch, tmp_path):
    hub = EventHub()
    configure(monkeypatch, hub, StreamFabric(hub, rows=lambda messages: messages[:1000]))
    code, record = run_cli(tmp_path, '--fabric-timeout', '2')
    assert code == 1 and record['status'] == 'failed'
    assert record['failure']['stage'] == 'fabric_destination' and f'missing {EXPECTED - 1000}' in record['failure']['reason']


def test_readback_on_the_fabric_consumer_group_is_refused_before_connecting(monkeypatch, tmp_path):
    hub = EventHub()
    configure(monkeypatch, hub, StreamFabric(hub), group='$Default')
    code, record = run_cli(tmp_path)
    assert code == 1 and record is None and hub.log == []


def test_connection_string_fallback_is_refused_for_a_verified_run():
    config = AzureConfig(namespace=NAMESPACE, eventhub_name=HUB, consumer_group='matchlens-verification',
                         connection_string='Endpoint=sb://x/;SharedAccessKeyName=k;SharedAccessKey=SECRET')
    with pytest.raises(VerificationFailed, match='Entra ID only'):
        live.FullMatchReconciler(config, 'a' * 32, 'match', EXPECTED, None, target=TARGET)


def test_reconcile_flag_requires_azure_mode():
    with pytest.raises(SystemExit):
        live.main(['--match', str(DEFAULT_PATH), '--mode', 'local', '--fabric-reconcile'])


@pytest.mark.parametrize('change,reason', [
    (lambda r: r.update(status='failed'), 'not a verified full-match run'),
    (lambda r: r.update(foundry_called=True), 'inconsistent'),
    (lambda r: r['reconciliation']['stages']['fabric'].update(missing=1, unique=EXPECTED - 1), 'lost, invented or altered'),
    (lambda r: r['reconciliation']['stages']['eventhub_readback'].update(altered=1), 'lost, invented or altered'),
    (lambda r: r['reconciliation']['stages']['api_processed'].update(duplicates=1, total=EXPECTED + 1), 'exactly once'),
    (lambda r: r['reconciliation']['stages'].update(fabric=None), 'stage fabric is incomplete'),
    (lambda r: r['eventhubs'].update(publish_acknowledged=EXPECTED - 1), 'whole published match'),
    (lambda r: r['fabric'].update(source_consumer_group='matchlens-verification'), 'separate consumer group'),
    (lambda r: r.update(token='x'), 'unexpected or missing fields'),
    (lambda r: r['source'].update(generator='Endpoint=sb://x/;SharedAccessKey=abc'), 'credential-like'),
])
def test_full_match_report_refuses_incomplete_or_unsafe_records(verified, change, reason):
    record = copy.deepcopy(verified[0])
    change(record)
    with pytest.raises(EvidenceError) as error:
        check_full(record)
    assert reason in str(error.value)
