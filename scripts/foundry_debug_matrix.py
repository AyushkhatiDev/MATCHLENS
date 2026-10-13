"""Opt-in live isolation matrix; outputs only MatchLens data, never SDK bodies."""
import asyncio
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict
from agent_framework import Agent
from simulator.agentic import AgenticConfig, IntelligenceStore, MatchAgentRunner
from simulator.agentic.runtime import FoundryAgentRuntime
from simulator.agentic.tools import ToolSession, MatchLensTools, ROLE_TOOLS
from simulator.agentic.grounding import EvidenceGroundingValidator
from simulator.agentic.runtime.diagnostics import failure_details
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.explanation import ExplanationEngine

class Probe(BaseModel):
    model_config = ConfigDict(extra='forbid')
    match_id: str

async def main(workflow_only=False):
    config = AgenticConfig.from_env()
    if config.mode != 'foundry':
        raise ValueError('Matrix requires explicitly configured Foundry mode.')
    match = generate_match(seed=99)
    events = MatchEngine(match).simulate()
    intel = IntelligenceEngine(match, events).analyze()
    report = ExplanationEngine(source_events=events).explain(intel)
    store = IntelligenceStore(intel, report, events)
    goal = next(e for e in report.explanations if e.explanation_type.value == 'GOAL')
    mock = MatchAgentRunner(store, AgenticConfig())
    state = (await mock.process_moment(goal.moment_id)).state
    await mock.aclose()
    state.tactical_findings = []; state.player_findings = []; state.context_findings = []
    state.explanation = None; state.narrative = None; state.explanation_validated = False
    scoped = store.scope(state.current_time)
    outputs = {}
    original_validate = EvidenceGroundingValidator.validate
    def recorded_validate(validator, output, current):
        role = getattr(output,'agent', 'explanation' if hasattr(output,'why_it_matters') else 'narrative')
        outputs[role] = output.model_dump(mode='json')
        return original_validate(validator,output,current)
    from unittest.mock import patch
    recording = patch.object(EvidenceGroundingValidator,'validate',recorded_validate)
    recording.start()
    class RecordedRuntime(FoundryAgentRuntime):
        async def run(self, role, state, tools):
            value = await super().run(role,state,tools)
            outputs[role] = value.model_dump(mode='json')
            return value
    runtime = RecordedRuntime(config)
    results = []
    try:
        await runtime.prepare()
        for label, with_tool, structured in ([] if workflow_only else [('A',False,False),('B',True,False),('C',False,True),('D',True,True)]):
            session = ToolSession(MatchLensTools(scoped), {'moment.get'})
            options = {'response_format': Probe} if structured else {}
            agent = Agent(client=runtime.client, name='MatchLensProbe'+label,
                instructions='Return the match_id. '+('Call moment_get first using the supplied moment_id.' if with_tool else ''),
                tools=session.framework_tools() if with_tool else [], default_options=options)
            try:
                response = await agent.run(json.dumps({'match_id':state.match_id,'moment_id':goal.moment_id}))
                assert state.match_id in response.text
                assert not with_tool or session.calls
                assert not structured or isinstance(response.value, Probe)
                results.append({'case':label,'status':'PASS','tool_calls':len(session.calls)})
            except Exception as error:
                results.append({'case':label,'status':'FAIL',**failure_details(error)})
            print(json.dumps(results[-1]),flush=True)
        for label, role in ([] if workflow_only else [('E','tactical'),('F','player'),('G','context')]):
            session = ToolSession(MatchLensTools(scoped), ROLE_TOOLS[role])
            entry = {'case':label,'role':role}
            try:
                output = await runtime.run(role,state,session)
                entry['output'] = output.model_dump(mode='json')
                EvidenceGroundingValidator().validate(output,state)
                retrieved = {eid for c in session.calls if c.status=='OK' for eid in c.evidence_ids}
                assert set(output.evidence_ids) <= retrieved
                entry['status'] = 'PASS'
            except Exception as error:
                entry.update(status='FAIL',**failure_details(error))
            entry['tool_calls'] = [c.model_dump(mode='json') for c in session.calls]
            results.append(entry)
            print(json.dumps({k:v for k,v in entry.items() if k not in ('output','tool_calls')}),flush=True)
        result = await MatchAgentRunner(store,config,runtime).process_moment(goal.moment_id)
        results.append({'case':'H','status':result.workflow_status,'result':result.model_dump(mode='json'),
                        'raw_outputs':outputs})
        print(json.dumps({'case':'H','status':result.workflow_status,'errors':[e.model_dump() for e in result.errors]}),flush=True)
    finally:
        recording.stop()
        await runtime.aclose()
    path = Path('data/generated/foundry-debug-matrix.json')
    path.write_text(json.dumps({'cases':results,'statements':[s.model_dump(mode='json') for s in state.statements]},indent=2)+'\n')
    assert all(c['status'] in ('PASS','COMPLETE') for c in results), 'Live matrix failed; inspect safe diagnostic report.'
    assert result.state.explanation_validated and result.narrative
    assert all(a.status=='OK' and a.tool_calls for a in result.state.agent_executions)

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--workflow-only',action='store_true')
    asyncio.run(main(parser.parse_args().workflow_only))
