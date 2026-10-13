"""Public execution provenance: allowlisted observations, never SDK arguments."""
from typing import Literal
from pydantic import BaseModel

RuntimeStatus = Literal['MOCK', 'FOUNDRY LIVE', 'FOUNDRY UNAVAILABLE', 'FOUNDRY RESULT REJECTED']

class ExecutionStep(BaseModel):
    stage: str
    status: str
    duration_ms: float | None = None

class Execution(BaseModel):
    mode: Literal['mock', 'foundry', 'deterministic']
    status: RuntimeStatus | None = None
    state: Literal['PENDING', 'COMPLETE', 'UNAVAILABLE', 'REJECTED', 'NOT REQUESTED']
    trace_id: str | None = None
    steps: list[ExecutionStep] = []
    fallback: bool = False
    run_id: str | None = None


def pending(mode):
    return Execution(mode=mode, status='MOCK' if mode=='mock' else 'FOUNDRY UNAVAILABLE', state='PENDING',
        steps=[ExecutionStep(stage='Evidence packet assembled', status='OK')])


def projection(result, observations):
    rejected=any(e.code=='INVALID_EVIDENCE' for e in result.errors)
    complete=result.workflow_status=='COMPLETE' and result.state.explanation_validated and result.explanation is not None
    status='MOCK' if result.runtime_mode=='mock' else 'FOUNDRY RESULT REJECTED' if rejected else 'FOUNDRY LIVE' if complete else 'FOUNDRY UNAVAILABLE'
    steps=observed_steps(result.trace_id,observations)
    return Execution(mode=result.runtime_mode,status=status,state='COMPLETE' if complete else 'REJECTED' if rejected else 'UNAVAILABLE',
        trace_id=result.trace_id,steps=steps)


def observed_steps(trace_id,observations):
    stages={'Router':'Routing','tactical':'Tactical specialist','player':'Player specialist',
        'context':'Context specialist','EvidenceConsolidation':'Arbitration','explanation':'Explanation',
        'Validation':'Grounding validation','narrative':'Narrative','FinalResult':'Workflow result'}
    steps=[ExecutionStep(stage='Evidence packet assembled',status='OK')]
    for row in observations:
        if row['trace_id']==trace_id and row['agent'] in stages:
            steps.append(ExecutionStep(stage=stages[row['agent']],status=row['status'],duration_ms=round(row['duration_ms'],2)))
    return steps
