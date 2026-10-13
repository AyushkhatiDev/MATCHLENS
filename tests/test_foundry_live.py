"""Explicit opt-in integration test; offline suites never invoke Azure services."""
import asyncio
import os
import pytest
pytest.importorskip('agent_framework')
from simulator.agentic import AgenticConfig, IntelligenceStore, MatchAgentRunner
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.explanation import ExplanationEngine

@pytest.mark.foundry
@pytest.mark.skipif(os.environ.get('MATCHLENS_RUN_FOUNDRY_TESTS')!='1',
                    reason='Live Foundry test is opt-in; mock and SDK contract tests cover offline mode.')
def test_live_foundry_goal():
    config=AgenticConfig.from_env()
    if config.mode!='foundry':
        pytest.fail('Live integration requires AGENTIC_MODE=foundry and genuine configuration.')
    match=generate_match(seed=99)
    events=MatchEngine(match).simulate()
    intel=IntelligenceEngine(match,events).analyze()
    report=ExplanationEngine(source_events=events).explain(intel)
    store=IntelligenceStore(intel,report,events)
    goal=next(e for e in report.explanations if e.explanation_type.value=='GOAL')
    async def run():
        runner=MatchAgentRunner(store,config)
        try:
            result=await runner.process_moment(goal.moment_id)
            assert result.runtime_mode=='foundry'
            assert result.workflow_status=='COMPLETE', 'A real Foundry agent failed or its claims were rejected.'
            assert result.narrative and result.state.explanation_validated
            assert all(a.tool_calls for a in result.state.agent_executions)
        finally:
            await runner.aclose()
    asyncio.run(run())
