"""Offline contract for rendering transport-verification evidence; records come from the real verifier code."""
import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from simulator.production.transport_report import EvidenceError, check, render
from tests.test_transport_verification import FakeFabric, Hub, path, run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def verified():
    record, _ = run()
    assert record['status'] == 'verified'
    return record


def test_verified_record_renders_exact_run_counts_topology_and_limits(verified):
    report = render(verified, record_sha256='0' * 64)
    assert f"run `{verified['run_id']}`" in report and '**Status: VERIFIED**' in report
    assert '374 unique events + 1 deliberate duplicate = 375 messages' in report
    assert '375 read back, 374 unique' in report and '375 rows, 374 unique' in report
    assert '`matchlens-example.servicebus.windows.net` / `matchlens-events`' in report
    assert '(AzureEventHubExtended), consumer group `$Default`' in report and 'consumer group `matchlens-verification`' in report
    assert 'table `MatchEvents`' in report and verified['fabric']['kql_database_item_id'] in report
    assert 'One bounded run, not continuous monitoring' in report
    assert 'replays a recorded local file' in report and 'does not read from Event Hubs or Fabric' in report
    assert 'Microsoft Foundry was not called' in report and '`foundry_called: false`' in report
    assert 'SHA-256 `' + '0' * 64 + '`' in report
    assert render(verified, record_sha256='0' * 64) == report


def mutated(record, change):
    value = copy.deepcopy(record)
    change(value)
    return value


@pytest.mark.parametrize('change,reason', [
    (lambda r: r.update(status='failed'), 'not a verified run'),
    (lambda r: r.update(failure={'stage': 'publish', 'reason': 'x'}), 'not a verified run'),
    (lambda r: r.update(foundry_called=True), 'must not claim'),
    (lambda r: r.update(agents_run=True), 'must not claim'),
    (lambda r: r.update(run_id='not-a-run'), '32-character hex'),
    (lambda r: r.update(finished_at='2000-01-01T00:00:00+00:00'), 'ordered'),
    (lambda r: r.update(started_at=r['started_at'][:19]), 'timezone-aware'),
    (lambda r: r['source'].update(deliberate_duplicates=0, published_messages=374), 'deliberate duplicates'),
    (lambda r: r['eventhubs'].update(readback_total=374), 'Event Hubs counts'),
    (lambda r: r['fabric'].update(rows_unique=373), 'Fabric row counts'),
    (lambda r: r['fabric'].update(rows_total=374), 'Fabric row counts'),
    (lambda r: r['fabric'].update(topology_verified=False), 'not verified'),
    (lambda r: r.update(identifiers_consistent=False), 'not verified'),
    (lambda r: r['fabric'].update(source_consumer_group='matchlens-verification'), 'separate from Fabric'),
    (lambda r: r['fabric'].update(connection_entity='other-hub'), 'does not match the published hub'),
    (lambda r: r['fabric'].update(destination_table='Other'), 'destination table'),
    (lambda r: r.update(token='x'), 'unexpected or missing fields'),
    (lambda r: r['eventhubs'].update(connection_string='x'), 'unexpected or missing fields'),
    (lambda r: r['fabric'].pop('connection_id'), 'unexpected or missing fields'),
    (lambda r: r['source'].update(generator='Endpoint=sb://x/;SharedAccessKey=abc'), 'credential-like text'),
])
def test_unverified_inconsistent_or_unsafe_records_are_refused(verified, change, reason):
    with pytest.raises(EvidenceError) as error:
        check(mutated(verified, change))
    assert reason in str(error.value)


def test_a_record_from_a_failed_run_is_refused():
    hub = Hub()
    record, _ = run(hub=hub, fabric=FakeFabric(hub, path=path(entity='matchlens-events-old')))
    assert record['status'] == 'failed'
    with pytest.raises(EvidenceError):
        render(record, record_sha256='0' * 64)


def test_command_renders_report_and_refuses_failed_records(verified, tmp_path):
    record, report = tmp_path / 'record.json', tmp_path / 'report.md'
    record.write_text(json.dumps(verified))
    command = [sys.executable, '-m', 'scripts.transport_report', '--record', str(record), '--output', str(report)]
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert verified['run_id'] in report.read_text()
    report.unlink()
    record.write_text(json.dumps({**verified, 'foundry_called': True}))
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0 and 'must not claim agent or Foundry execution' in result.stderr
    assert not report.exists()
