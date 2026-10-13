"""Verified Microsoft Agent Framework 1.20 / Foundry 1.14 direct-inference API."""
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Literal
from pydantic import Field, create_model
from ..agents import OUTPUT_SCHEMAS
from ..config import AgenticConfig
from ..models import Role, MatchAgentState, Finding, ExplanationResult, NarrativeResult
from ..tools import ToolSession
from ..grounding import EvidenceGroundingValidator, GroundingError
from .base import AgentRuntime
from .diagnostics import logger, failure_details

class FoundryAgentRuntime(AgentRuntime):
    mode = 'foundry'

    def __init__(self, config: AgenticConfig):
        if config.mode!='foundry':
            raise ValueError('Foundry runtime requires a validated Foundry configuration.')
        self.config = config
        self.stack = None
        self.client = None

    async def _start(self) -> None:
        # Started once before the workflow's concurrent specialists. Each call
        # creates its own Agent/session; only the supported async client is shared.
        if self.client is not None:
            return
        from azure.identity.aio import DefaultAzureCredential
        from azure.ai.projects.aio import AIProjectClient
        from agent_framework.foundry import FoundryChatClient
        stack = AsyncExitStack()
        try:
            credential = await stack.enter_async_context(DefaultAzureCredential(
                managed_identity_client_id=self.config.azure_client_id))
            project = await stack.enter_async_context(AIProjectClient(
                endpoint=self.config.project_endpoint,credential=credential))
            self.client = FoundryChatClient(project_client=project,model=self.config.model,
                                           credential=credential)
            stack.push_async_callback(self.client.client.close)
            self.stack = stack
        except BaseException:
            await stack.aclose()
            raise

    async def prepare(self) -> None:
        await self._start()

    async def run(self, role: Role, state: MatchAgentState, tools: ToolSession
                  ) -> Finding | ExplanationResult | NarrativeResult:
        await self._start()
        from agent_framework import Agent
        prompts = Path(__file__).resolve().parents[1]/'prompts'
        instructions = (prompts/'grounding.md').read_text()+'\n'+(prompts/(role+'.md')).read_text()
        schema = OUTPUT_SCHEMAS[role]
        base = state.explanation_context
        # Supply each specialist a small, role-relevant claim catalog. The full
        # state remains authoritative in the runner; no claims are rewritten.
        statements = state.statements
        if role == 'tactical':
            metric_ids = {e.evidence_id for e in state.evidence if e.metric_name in
                ('pressure_index','field_tilt','possession_pct','progressive_actions','pressure_count')}
            statements = [s for s in statements if s.statement_id == 'contradiction' or
                (s.category == 'metric' and set(s.evidence_ids) & metric_ids)]
            if not statements:
                statements = [s for s in state.statements if s.statement_id == 'consequence']
        elif role == 'player':
            metric_ids = {e.evidence_id for e in state.evidence if e.metric_name == 'shot_quality'
                          or e.player_id in base.affected_player_ids}
            statements = [s for s in statements if s.category == 'player' or
                          (s.category == 'metric' and set(s.evidence_ids) & metric_ids)]
        elif role == 'context':
            statements = [s for s in statements if s.statement_id in ('trigger','consequence')
                          or s.category == 'context']
        elif role == 'explanation':
            # The composite "why" repeats the atomic metric/consequence claims.
            # Use the atomic claims for an unambiguous specialist handoff.
            statements = [s for s in statements if s.statement_id != 'why']
        elif role == 'narrative' and state.explanation:
            statements = [s for s in statements if s.statement_id in state.explanation.statement_ids]
        fields = dict(
            statement_ids=(list[Literal[tuple(s.statement_id for s in statements)]], Field(min_length=1)),
            # The required linked-player statement cites the whole trigger
            # packet. A genuine run omitted citations for that selected claim.
            evidence_ids=(list[Literal[tuple(e.evidence_id for e in state.evidence)]],
                          Field(min_length=max(1,len(base.evidence_ids) if role=='player' else 1),
                                max_length=len(state.evidence))),
            confidence=(float,Field(ge=0,le=state.confidence_cap)))
        if role in ('tactical','player','context'):
            # UUIDs/opaque IDs must be selected, never regenerated as free text.
            # The original output model and strict grounding validator still
            # validate the result after this more restrictive wire schema.
            event_type = Literal[tuple(base.source_event_ids)] if base.source_event_ids else str
            fields.update(
                event_ids=(list[event_type], Field(default_factory=list, **({} if base.source_event_ids else {'max_length':0}))),
                timestamp=(Literal[state.current_time], ...),
                finding_type=(Literal[base.explanation_type.value], ...))
        elif role == 'explanation':
            fields.update(explanation_id=(Literal['agent_'+base.explanation_id],...),
                moment_id=(Literal[base.moment_id],...),what_happened=(Literal[base.what_happened],...),
                what_changed=(Literal[base.what_changed],...),
                why_it_matters=(str,Field(description='Exact full catalog texts for selected statement_ids, in order, excluding trigger/change, joined with ONE space. No new wording, arithmetic, rounding or punctuation.')))
        response_schema = create_model(schema.__name__+'Grounded', __base__=schema,**fields)
        agent = Agent(client=self.client,name='MatchLens_'+role,instructions=instructions,
            tools=tools.framework_tools(),default_options={'response_format':response_schema})
        payload = {'match_id':state.match_id,'moment_id':state.triggered_moment.moment_id,
            'timestamp':state.current_time,'explanation_type':base.explanation_type.value,
            'explanation_id':'agent_'+base.explanation_id,
            'what_happened':base.what_happened,'what_changed':base.what_changed,
            'headline_options':[base.title,base.explanation_type.value.replace('_',' ').title()],
            'statement_catalog':[s.model_dump(mode='json') for s in statements],
            'linked_player_ids':base.affected_player_ids,
            'linked_team_ids':sorted({e.team_id for e in state.evidence if e.team_id}),
            'source_event_ids':base.source_event_ids,
            'sequence_ids':sorted({e.facts['sequence_id'] for e in state.evidence
                                  if e.metric_name == 'possession_sequence'}),
            'confidence_cap':state.confidence_cap,'required_caveats':state.required_caveats,
            'evidence_ids':[e.evidence_id for e in state.evidence],
            'specialist_findings':[f.model_dump(mode='json') for f in
                [*state.tactical_findings,*state.player_findings,*state.context_findings]],
            'validated_explanation':state.explanation.model_dump(mode='json')
                if state.explanation_validated and state.explanation else None,
            'audience_profile':state.audience_profile.model_dump(mode='json')}
        if role=='player':payload['mandatory_evidence_ids']=base.evidence_ids
        if role == 'explanation':
            # Concrete copy examples address the observed free-text assembly
            # rejection. These are current evidence strings, not saved model
            # answers. The model still selects claims and retrieves citations;
            # the original validator remains authoritative for every response.
            catalog={s.statement_id:s for s in statements}
            useful=list(dict.fromkeys(sid for finding in
                [*state.tactical_findings,*state.player_findings,*state.context_findings]
                for sid in finding.statement_ids if sid in catalog and catalog[sid].category=='metric'))[:2]
            payload['explanation_copy_examples']=[]
            for supporting in ([],useful):
                ids=['trigger','change','consequence',*supporting]
                if 'contradiction' in catalog:ids.append('contradiction')
                payload['explanation_copy_examples'].append({'statement_ids':ids,
                    'why_it_matters':' '.join(catalog[sid].text for sid in ids if sid not in ('trigger','change'))})
        from agent_framework import Message, Content
        messages = [Message('user',[Content.from_text(json.dumps(payload))])]
        # A model may still omit a mandatory claim or mis-copy text. Give it one
        # explicit correction opportunity; do not repair or accept its claims in
        # application code. Both attempts use the same strict validator/tools.
        for attempt in range(2):
            response = await agent.run(messages)
            if response.value is None:
                raise ValueError('Foundry returned no structured output.')
            output = schema.model_validate(response.value.model_dump()
                if hasattr(response.value,'model_dump') else response.value)
            try:
                EvidenceGroundingValidator().validate(output,state)
                retrieved = {eid for call in tools.calls if call.status=='OK' for eid in call.evidence_ids}
                if not set(output.evidence_ids) <= retrieved:
                    raise GroundingError('Agent did not retrieve the cited evidence through its tools.')
                return output
            except GroundingError as error:
                if attempt:
                    raise
                logger.info('Foundry grounding correction: %s', {
                    'role':role,'attempt':attempt+1,'schema':schema.__name__,
                    'tool_count':len(tools.allowed),**failure_details(error)})
                messages.extend([Message('assistant',[Content.from_text(output.model_dump_json())]), Message('user',[Content.from_text(
                    'Your output was REJECTED by deterministic grounding: '+str(error)+
                    ' Return a corrected structured output. Do not change the evidence. '
                    'Copy selected catalog texts exactly, and retrieve cited packets. '
                    'For ExplanationResult statement_ids MUST include trigger and change '
                    'as well as consequence or why. why_it_matters joins only the other selected '
                    'statements, excluding trigger/change. Preserve all caveats and confidence caps.')])])

    async def aclose(self) -> None:
        if self.stack:
            await self.stack.aclose()
        self.stack = None
        self.client = None
