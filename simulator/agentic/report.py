"""Actual execution traces and structured results for the CLI demo."""
import json
from pathlib import Path
from .models import WorkflowResult

def write_agent_results(results: list[WorkflowResult], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'agentic_version':'0.4.0','results':[r.model_dump(mode='json') for r in results]},indent=2)+'\n')
    return path

def print_agent_result(result: WorkflowResult) -> None:
    print('\nMATCHLENS AGENTIC PIPELINE\n--------------------------')
    print(f'Mode: {result.runtime_mode}; trace: {result.trace_id}')
    print(f'Moment detected: {result.moment_id} at {result.state.current_time}s')
    print('Specialists selected: '+', '.join(result.selected_agents))
    for step in result.state.trace_steps:
        print(f'↓ {step.stage}: {step.status} — {step.detail}')
        if step.stage=='Specialists':
            for execution in result.state.agent_executions:
                if execution.agent in result.selected_agents:
                    print(f'  {execution.agent}: {execution.status}')
                    for call in execution.tool_calls:
                        print(f'    tool: {call.tool} {json.dumps(call.arguments,sort_keys=True)} [{call.status}]')
                    print('    evidence: '+', '.join(execution.evidence_ids))
    for execution in result.state.agent_executions:
        if execution.agent in ('explanation','narrative'):
            print(f'{execution.agent}: {execution.status}; tools: '+', '.join(call.tool for call in execution.tool_calls))
    print('Workflow status: '+result.workflow_status)
    if result.explanation:
        print('ExplanationResult:\n'+result.explanation.model_dump_json(indent=2))
    if result.narrative:
        print('NarrativeResult:\n'+result.narrative.model_dump_json(indent=2))
    if result.errors:
        print('Errors:\n'+json.dumps([e.model_dump() for e in result.errors],indent=2))
