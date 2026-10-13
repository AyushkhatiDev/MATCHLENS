"""Moment-level workflow with parallel specialists and explicit recovery policy."""
import asyncio
import hashlib
from ..agents import OUTPUT_SCHEMAS
from ..config import AgenticConfig
from ..grounding import EvidenceGroundingValidator, GroundingError, statement_catalog
from ..models import (AudienceProfile, MatchAgentState, Role, WorkflowResult,
                     AgentExecutionRecord, AgentError, TraceStep)
from ..tools import IntelligenceStore, MatchLensTools, ToolSession, ROLE_TOOLS, ToolError
from ..workflow.graph import build_workflow
from ..workflow.router import route_moment
from .base import AgentRuntime
from . import create_runtime
from .diagnostics import logger, failure_details

class MatchAgentRunner:
    def __init__(self, store: IntelligenceStore, config: AgenticConfig | None = None,
                 runtime: AgentRuntime | None = None):
        self.store = store
        self.config = config or AgenticConfig.from_env()
        self.runtime = runtime or create_runtime(self.config)
        if self.runtime.mode!=self.config.mode:
            raise ValueError('Runtime and configured mode disagree.')
        self.validator = EvidenceGroundingValidator()

    async def process_moment(self, moment_id: str,
                             audience_profile: AudienceProfile | None = None) -> WorkflowResult:
        base = self.store.explanation(moment_id).model_copy(deep=True)
        moment = self.store.moment(moment_id).model_copy(deep=True)
        scoped = self.store.scope(moment.timestamp)
        evidence = [scoped.evidence[eid].model_copy(deep=True) for eid in base.evidence_ids]
        profile = audience_profile or AudienceProfile()
        digest = hashlib.sha256((base.model_dump_json()+profile.model_dump_json()+self.runtime.mode).encode()).hexdigest()[:16]
        state = MatchAgentState(match_id=self.store.intelligence.match_id,current_time=moment.timestamp,
            triggered_moment=moment,explanation_context=base,evidence=evidence,
            statements=statement_catalog(base,evidence),confidence_cap=base.confidence,
            required_caveats=base.caveats.copy(),audience_profile=profile,trace_id='trace_'+digest)
        await self.runtime.prepare()
        async def operation(stage: str, current: MatchAgentState) -> None:
            await self._stage(stage,current,scoped)
        graph = build_workflow(operation,self.runtime.mode)
        run = await graph.run(state)
        outputs = run.get_outputs()
        if len(outputs)!=1:
            raise RuntimeError('Workflow did not yield exactly one final result.')
        result = outputs[0]
        return result if isinstance(result,WorkflowResult) else WorkflowResult.model_validate(result)

    async def _invoke(self, role: Role, state: MatchAgentState,
                      scoped: IntelligenceStore, validate: bool = True):
        session = ToolSession(MatchLensTools(scoped),ROLE_TOOLS[role])
        try:
            # Each specialist gets isolated input and a trace collector. They do
            # not mutate shared state while asyncio.gather is active.
            output = await asyncio.wait_for(self.runtime.run(role,state.model_copy(deep=True),session),
                                            timeout=self.config.timeout_seconds)
            output = OUTPUT_SCHEMAS[role].model_validate(output.model_dump() if hasattr(output,'model_dump') else output)
            if validate:
                self.validator.validate(output,state)
            retrieved = {eid for call in session.calls if call.status=='OK' for eid in call.evidence_ids}
            if not set(output.evidence_ids)<=retrieved:
                raise GroundingError('Agent did not retrieve the cited evidence through its tools.')
            return output,AgentExecutionRecord(agent=role,status='OK',tool_calls=session.calls,
                evidence_ids=output.evidence_ids),None
        except Exception as error:
            logger.warning('Agent invocation failed: %s', {
                'role':role, 'stage':'Specialists' if role in ('tactical','player','context') else role,
                'tool_count':len(session.allowed), 'tool_calls':len(session.calls),
                'schema':OUTPUT_SCHEMAS[role].__name__, 'runtime':self.runtime.mode,
                **failure_details(error)})
            if isinstance(error,GroundingError):
                code,message = 'INVALID_EVIDENCE','Agent output was rejected by evidence grounding.'
            elif isinstance(error,TimeoutError):
                code,message = 'TIMEOUT','Agent exceeded its configured execution timeout.'
            elif isinstance(error,ToolError):
                code,message = error.code,'Agent could not retrieve required deterministic evidence.'
            else:
                code,message = 'AGENT_FAILURE','Agent execution or structured-output validation failed.'
            return None,AgentExecutionRecord(agent=role,status='ERROR',tool_calls=session.calls),AgentError(
                agent=role,stage='Specialists' if role in ('tactical','player','context') else role,
                code=code,message=message,recoverable=role!='explanation')

    @staticmethod
    def _record_failure(state: MatchAgentState, error: AgentError) -> None:
        state.errors.append(error)
        if error.agent not in state.missing_agents:
            state.missing_agents.append(error.agent)
        caveat=f'{error.agent} agent unavailable; its findings were not fabricated.'
        if caveat not in state.required_caveats:
            state.required_caveats.append(caveat)

    async def _stage(self, stage: str, state: MatchAgentState, scoped: IntelligenceStore) -> None:
        if state.workflow_status=='FAILED' and stage!='FinalResult':
            state.trace_steps.append(TraceStep(stage=stage,status='SKIPPED',detail='Required preceding stage failed.'))
            return
        if stage=='Router':
            state.workflow_status='RUNNING'
            state.selected_agents=route_moment(state.explanation_context)
            state.trace_steps.append(TraceStep(stage=stage,detail=', '.join(state.selected_agents)))
        elif stage=='Specialists':
            results = await asyncio.gather(*(self._invoke(role,state,scoped) for role in state.selected_agents))
            for role,(finding,record,error) in zip(state.selected_agents,results):
                state.agent_executions.append(record)
                if error:
                    self._record_failure(state,error)
                else:
                    getattr(state,role+'_findings').append(finding)
            state.trace_steps.append(TraceStep(stage=stage,status='ERROR' if state.errors else 'OK',
                detail='Parallel specialist execution completed; records retain deterministic route order.'))
        elif stage=='EvidenceConsolidation':
            findings = [*state.tactical_findings,*state.player_findings,*state.context_findings]
            if not findings:
                state.workflow_status='FAILED'
                state.trace_steps.append(TraceStep(stage=stage,status='ERROR',detail='No validated specialist findings remain.'))
                return
            state.confidence_cap=round(max(0,min(state.confidence_cap,
                *(f.confidence for f in findings))-.1*len(state.missing_agents)),2)
            state.trace_steps.append(TraceStep(stage=stage,detail='Validated findings and cited evidence handed off; contradictions retained.'))
        elif stage=='ExplanationAgent':
            output,record,error = await self._invoke('explanation',state,scoped,validate=False)
            state.agent_executions.append(record)
            if error:
                self._record_failure(state,error)
                state.workflow_status='FAILED'
            else:
                state.explanation=output
            state.trace_steps.append(TraceStep(stage=stage,status='ERROR' if error else 'OK',detail='Structured specialist handoff.'))
        elif stage=='Validation':
            try:
                if state.explanation is None:
                    raise GroundingError('Explanation is missing.')
                self.validator.validate(state.explanation,state)
                state.explanation_validated=True
                state.confidence_cap=min(state.confidence_cap,state.explanation.confidence)
                state.trace_steps.append(TraceStep(stage=stage,detail='Explanation facts, citations, uncertainty and contradictions validated.'))
            except GroundingError as error:
                logger.warning('Explanation validation failed: %s', failure_details(error))
                self._record_failure(state,AgentError(agent='explanation',stage=stage,code='INVALID_EVIDENCE',
                    message='Explanation was rejected by evidence grounding.',recoverable=False))
                state.explanation=None
                state.workflow_status='FAILED'
                state.agent_executions[-1].status='ERROR'
                state.trace_steps.append(TraceStep(stage=stage,status='ERROR',detail='Narrative handoff blocked.'))
        elif stage=='NarrativeAgent':
            output,record,error = await self._invoke('narrative',state,scoped)
            state.agent_executions.append(record)
            if error:
                self._record_failure(state,error)
                # Explanation remains valid; mark the delivered partial result
                # with reduced confidence and the presentation failure caveat.
                state.confidence_cap=round(max(0,state.confidence_cap-.1),2)
                state.explanation.confidence=state.confidence_cap
                state.explanation.caveats=state.required_caveats.copy()
            else:
                state.narrative=output
            state.trace_steps.append(TraceStep(stage=stage,status='ERROR' if error else 'OK',detail='Validated explanation handed to presentation agent.'))
        elif stage=='FinalResult':
            if state.workflow_status!='FAILED':
                state.workflow_status='PARTIAL' if state.errors else 'COMPLETE'
            state.trace_steps.append(TraceStep(stage=stage,status='ERROR' if state.workflow_status=='FAILED' else 'OK',
                detail=state.workflow_status))

    async def aclose(self) -> None:
        await self.runtime.aclose()
