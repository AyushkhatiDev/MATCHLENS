"""Opt-in genuine Event Hubs → Fabric destination data-path tests."""
import asyncio,json,os
from pathlib import Path
import pytest
from simulator.production.cloud import fabric

@pytest.mark.skipif(os.getenv('MATCHLENS_RUN_FABRIC_TESTS')!='1',reason='Real Fabric data-path test is opt-in')
def test_real_fabric_eventstream_data_path():
    result=asyncio.run(fabric())
    Path('data/generated/fabric-live-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['status']=='live_verified' and result['integrity_verified']
    assert result['records_observed']>=result['unique_expected']+1
    assert result['metrics']['events_duplicate']>=1

@pytest.mark.skipif(os.getenv('MATCHLENS_RUN_CLOUD_E2E_TESTS')!='1',reason='Real Fabric plus Foundry end-to-end test is opt-in')
def test_real_eventhub_fabric_foundry_end_to_end():
    result=asyncio.run(fabric(agent_mode='foundry'))
    Path('data/generated/cloud-e2e-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    assert result['status']=='live_verified' and result['workflow_runtime']=='foundry'
    assert all(r['runtime_mode']=='foundry' and r['narrative'] and r['state']['explanation_validated']
               and all(a['tool_calls'] for a in r['state']['agent_executions']) for r in result['workflow_results'])
