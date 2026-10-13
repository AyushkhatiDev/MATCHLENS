"""Real Microsoft Agent Framework graph; explicit, inspectable stage edges."""
from collections.abc import Awaitable, Callable
from agent_framework import Executor, WorkflowBuilder, WorkflowContext, handler
from ..models import MatchAgentState, WorkflowResult

STAGES = ('Router','Specialists','EvidenceConsolidation','ExplanationAgent',
          'Validation','NarrativeAgent','FinalResult')

class StageExecutor(Executor):
    def __init__(self, stage: str, operation: Callable[[str, MatchAgentState], Awaitable[None]], mode: str):
        super().__init__(id=stage)
        self.stage, self.operation, self.mode = stage, operation, mode

    @handler
    async def handle(self, state: MatchAgentState,
                     ctx: WorkflowContext[MatchAgentState, WorkflowResult]) -> None:
        await self.operation(self.stage,state)
        if self.stage=='FinalResult':
            await ctx.yield_output(WorkflowResult(trace_id=state.trace_id,match_id=state.match_id,
                moment_id=state.triggered_moment.moment_id,runtime_mode=self.mode,
                workflow_status=state.workflow_status,selected_agents=state.selected_agents,
                missing_agents=state.missing_agents,explanation=state.explanation,
                narrative=state.narrative,state=state,errors=state.errors,
                confidence=state.explanation.confidence if state.explanation else 0,
                evidence_ids=state.explanation.evidence_ids if state.explanation else [],
                final_explanation_id=state.explanation.explanation_id if state.explanation else None))
        else:
            await ctx.send_message(state)

def build_workflow(operation: Callable[[str, MatchAgentState], Awaitable[None]], mode: str):
    nodes = [StageExecutor(name,operation,mode) for name in STAGES]
    builder = WorkflowBuilder(start_executor=nodes[0],name='MatchLensIntelligence',
        description='Routed specialists → grounded explanation → validated narrative.')
    for source,target in zip(nodes,nodes[1:]):
        builder.add_edge(source,target)
    return builder.build()
