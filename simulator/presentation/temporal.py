"""Temporal projections of the existing engine. No language-model arithmetic.

Five-minute published windows are sampled, not continuous tracking. DNA is the
arithmetic mean of those windows (overlapping inclusive boundaries are retained).
Sensitivity holds time/window fixed and removes actual sequence events only.
"""
from functools import lru_cache
from pathlib import Path
from datetime import date
import json
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.explanation import ExplanationEngine

ROOT = Path(__file__).resolve().parents[2]
CAVEAT = 'Simulation sensitivity, not a real-world causal claim.'

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class Dimension(Model):
    name: str
    home: float | None
    away: float | None
    unit: str
    formula: str

class Point(Model):
    timestamp: float
    state: str
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    dimensions: list[Dimension]
    score: list[int] = Field(default_factory=lambda:[0,0])

class Temporal(Model):
    current: Point | None = None
    trajectory: list[Point] = Field(default_factory=list)
    dna: list[Dimension] = Field(default_factory=list)
    dna_method: str = 'Arithmetic mean of published five-minute windows; stability excludes windows without passes.'

class Delta(Model):
    metric: str
    baseline: float | None
    current: float
    delta: float | None
    unit: str
    evidence_id: str
    window: str | None = None
    baseline_timestamp: float | None = None
    current_timestamp: float

class Change(Model):
    before: str | None = None
    after: str | None = None
    state_timestamp: float | None = None
    deltas: list[Delta] = Field(default_factory=list)

class Claim(Model):
    text: str
    evidence_ids: list[str]
    statement_id: str

class Specialist(Model):
    agent: str
    status: str
    validation_state: Literal['SUPPORTED','REJECTED','UNAVAILABLE','PENDING'] = 'UNAVAILABLE'
    findings: list[Claim]
    confidence: float | None
    caveats: list[str]
    conflicts: list[str]
    tools: list[dict]

class Truth(Model):
    status: Literal['SUPPORTED','PARTIALLY SUPPORTED','REJECTED']
    scope: str
    claims: list[Claim]
    specialists: list[Specialist]
    arbitration: str
    missing_agents: list[str]
    confidence_cap: float
    confidence: float | None
    caveats: list[str]
    validation: list[dict]


def dimensions(w):
    def d(name,h,a,unit,formula):return Dimension(name=name,home=h,away=a,unit=unit,formula=formula)
    return [
        d('Control',w.home_possession_pct,w.away_possession_pct,'%', 'Team event share in the window; not possession duration'),
        d('Pressure',w.home_pressure_index,w.away_pressure_index,'index','Existing engine pressure frequency/share index, 0–100'),
        d('Territory',w.home_field_tilt,w.away_field_tilt,'%', 'Team share of attacking-third actions'),
        d('Progression',w.home_progressive_actions,w.away_progressive_actions,'actions','Existing progressive passes and forward carries'),
        d('Threat',w.home_shots,w.away_shots,'shots','Observed shots; not expected goals'),
        d('Tempo',w.event_density,w.event_density,'events/min','All events divided by nominal window minutes; shared match rhythm'),
        d('Stability',round(w.home_pass_success/w.home_passes*100,1) if w.home_passes else None,round(w.away_pass_success/w.away_passes*100,1) if w.away_passes else None,'%', 'Successful passes / pass attempts; null when no passes'),
        d('Transition intensity',w.turnovers,w.turnovers,'turnovers','Observed possession changes in the window; shared match value'),
    ]


def temporal_projection(windows, states, events):
    state_by_time={s.timestamp:s for s in states}
    points=[]
    goals=[e for e in events if e.event_type.value=='GOAL']
    for w in windows:
        if w.window_seconds!=300:continue
        s=state_by_time.get(w.timestamp)
        if s is None:continue
        g=next((g for g in reversed(goals) if g.timestamp<=w.timestamp),None)
        points.append(Point(timestamp=w.timestamp,state=s.state.value,confidence=s.confidence,evidence_ids=s.evidence_ids,dimensions=dimensions(w),score=[g.home_score,g.away_score] if g else [0,0]))
    dna=[]
    if points:
        for i,d in enumerate(points[0].dimensions):
            values={side:[getattr(p.dimensions[i],side) for p in points if getattr(p.dimensions[i],side) is not None] for side in ('home','away')}
            dna.append(d.model_copy(update={side:round(sum(v)/len(v),2) if v else None for side,v in values.items()}))
    return Temporal(current=points[-1] if points else None,trajectory=points,dna=dna)


def change_projection(explanation, states, timestamp):
    published=[s for s in states if s.timestamp<=timestamp]
    # A transition is reported only at its actual published checkpoint.
    after=published[-1] if published else None
    before=published[-2] if len(published)>1 and after.timestamp==timestamp else after
    return Change(before=before.state.value if before else None,after=after.state.value if after else None,
        state_timestamp=after.timestamp if after else None,
        deltas=[Delta(**m.model_dump(include={'metric','baseline','current','delta','unit','evidence_id','window','baseline_timestamp','current_timestamp'})) for m in explanation.supporting_metrics.values()] if explanation else [])


def truth_projection(result):
    s=result.state
    used=set(result.explanation.statement_ids if result.explanation else [])|set(result.narrative.statement_ids if result.narrative else [])
    claims=[Claim(text=c.text,evidence_ids=c.evidence_ids,statement_id=c.statement_id) for c in s.statements if c.statement_id in used]
    executions={e.agent:e for e in s.agent_executions}
    specialists=[]
    for role in ('tactical','player','context'):
        fs=getattr(s,f'{role}_findings'); ex=executions.get(role)
        specialists.append(Specialist(agent=role,status='OK' if ex and ex.status=='OK' else 'ERROR' if ex else 'NOT SELECTED' if role not in s.selected_agents else 'UNAVAILABLE',
            validation_state='SUPPORTED' if ex and ex.status=='OK' and fs else 'REJECTED' if any(e.agent==role and e.code=='INVALID_EVIDENCE' for e in s.errors) else 'UNAVAILABLE',
            findings=[Claim(text=f.finding,evidence_ids=f.evidence_ids,statement_id=','.join(f.statement_ids)) for f in fs],
            confidence=min((f.confidence for f in fs),default=None),caveats=list(dict.fromkeys(c for f in fs for c in f.caveats)),
            conflicts=[c for f in fs for c in getattr(f,'contradictions',[])],
            tools=[{'tool':t.tool,'status':t.status,'evidence_ids':t.evidence_ids} for t in ex.tool_calls] if ex else []))
    conflicts=[c.text for c in s.statements if c.category=='contradiction']
    status='SUPPORTED' if s.explanation_validated and result.workflow_status=='COMPLETE' else 'PARTIALLY SUPPORTED' if s.explanation_validated else 'REJECTED'
    successful=sum(bool(x.findings) and x.status=='OK' for x in specialists)
    arbitration=f'{successful} specialists returned findings. '+('Conflicting metric directions retained; this is not a specialist vote.' if conflicts else 'No explicit directional conflict recorded; this is not a specialist vote.')
    return Truth(status=status,scope='Agent explanation validation',claims=claims,specialists=specialists,arbitration=arbitration,
        missing_agents=s.missing_agents,confidence_cap=s.confidence_cap,confidence=result.explanation.confidence if s.explanation_validated and result.explanation else None,
        caveats=list(dict.fromkeys(s.required_caveats+conflicts+(result.explanation.caveats if result.explanation else []))),
        validation=[{'stage':t.stage,'status':t.status} for t in s.trace_steps])


@lru_cache(maxsize=1)
def comparison():
    from simulator.generation.generator import generate_match
    from simulator.engine.match_engine import MatchEngine
    results=[]
    for seed in (1,3):
        match=generate_match(seed=seed);events=MatchEngine(match).simulate()
        report=IntelligenceEngine(match,events).analyze()
        explanations=ExplanationEngine(source_events=events).explain(report)
        goals=[e for e in events if e.event_type.value=='GOAL']
        results.append({'seed':seed,'match_id':match.match_id,'home':match.home_team.name,'away':match.away_team.name,
            'score':[goals[-1].home_score,goals[-1].away_score] if goals else [0,0],
            'temporal':temporal_projection(report.rolling_windows,report.tactical_states,events).model_dump(mode='json'),
            'turning_points':[{'timestamp':e.timestamp,'title':e.title,'what_changed':e.what_changed,'evidence_ids':e.evidence_ids,'source_event_ids':e.source_event_ids,'confidence':e.confidence,
                'evidence':[x.model_dump(mode='json') for x in explanations.evidence if x.evidence_id in e.evidence_ids],
                'source_events':[x.model_dump(mode='json') for x in events if x.event_id in e.source_event_ids]} for e in explanations.explanations[:12]]})
    return {'synthetic':True,'same_score':results[0]['score']==results[1]['score'],'matches':results,'method':'Existing generator, fixed seeds 1 and 3; existing batch intelligence and deterministic explanations. No agent workflows executed for this comparison.'}


def sequence_choices(events,timestamp):
    groups={}
    for e in events:
        sid=getattr(e,'possession_sequence_id',None)
        if sid and max(0,timestamp-300)<=e.timestamp<=timestamp:
            groups.setdefault(sid,[]).append(e)
    return [{'sequence_id':sid,'start':v[0].timestamp,'end':v[-1].timestamp,'events':len(v),'team_id':v[0].team_id} for sid,v in groups.items()]


def sensitivity(match,events,timestamp,sequence_id):
    if not any(c['sequence_id']==sequence_id for c in sequence_choices(events,timestamp)):raise ValueError('Sequence is unavailable in this observed five-minute window')
    prefix=[e for e in events if e.timestamp<=timestamp]
    removed=[e for e in prefix if getattr(e,'possession_sequence_id',None)==sequence_id]
    remaining=[e for e in prefix if getattr(e,'possession_sequence_id',None)!=sequence_id]
    engine=IntelligenceEngine(match,prefix)
    observed=engine._calc_window(prefix,timestamp,300)
    altered=engine._calc_window(remaining,timestamp,300)
    observed_state=engine._classify_single(observed)[0].value
    altered_state=engine._classify_single(altered)[0].value
    return {'sequence_id':sequence_id,'timestamp':timestamp,'window_seconds':300,'observed_state':observed_state,'altered_state':altered_state,
        'removed_event_ids':[e.event_id for e in removed],'observed':dimensions(observed),'without_sequence':dimensions(altered),
        'deltas':[{'name':a.name,'home':round(b.home-a.home,2) if a.home is not None and b.home is not None else None,'away':round(b.away-a.away,2) if a.away is not None and b.away is not None else None,'unit':a.unit} for a,b in zip(dimensions(observed),dimensions(altered))],
        'caveat':CAVEAT,'method':'Existing engine five-minute calculation and classifier; time held fixed, all observed events carrying the selected sequence ID removed. Later events held fixed, not resimulated. Scoreline is not recalculated.'}


class Verification(Model):
    status: Literal['live_verified_browser']
    verified_at_date: date
    workspace_id: UUID
    eventstream_id: UUID
    eventhouse_id: UUID
    replay_run_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    events_published: int = Field(ge=1)
    events_processed: int = Field(ge=1)
    table: Literal['MatchEvents']
    kql_result: dict[str,int]


def downstream_verification(path=ROOT/'docs/submission/fabric-browser-verification.json'):
    try:
        data=json.loads(Path(path).read_text())
        v=Verification.model_validate({k:data[k] for k in Verification.model_fields})
        if v.verified_at_date>date.today() or v.kql_result!={'Rows':v.events_published,'Unique':v.events_published,'Invalid':0} or v.events_processed!=v.events_published:raise ValueError('Inconsistent verification')
        return {'status':'LAST DOWNSTREAM VERIFICATION','date':str(v.verified_at_date),'replay_run_id':v.replay_run_id,'rows':v.kql_result['Rows'],'published':v.events_published,'table':v.table,
            'url':f'https://app.fabric.microsoft.com/groups/{v.workspace_id}/eventhouses/{v.eventhouse_id}',
            'eventstream_url':f'https://app.fabric.microsoft.com/groups/{v.workspace_id}/eventstreams/{v.eventstream_id}',
            'continuous_verification':False,'traffic':None,'last_observed_message':None}
    except (OSError,ValueError,KeyError,TypeError):
        return {'status':'UNVERIFIED','continuous_verification':False,'traffic':None,'last_observed_message':None}

@lru_cache(maxsize=4)
def guide(path):
    from simulator.live import load_match
    from simulator.realtime.models import parse_event
    events=[parse_event(json.loads(line)) for line in Path(path).read_text().splitlines() if line.strip()]
    report=IntelligenceEngine(load_match(path),events).analyze()
    pressure=next((m for m in report.detected_moments if m.moment_type.value=='PRESSURE_SHIFT' and m.tier.value in ('HIGH','CRITICAL')),None)
    transition=next((t for t in report.state_transitions if pressure and t.timestamp>pressure.timestamp),None)
    goal=next((e for e in events if e.event_type.value=='GOAL' and transition and e.timestamp>transition.timestamp),None)
    response=next((s for s in report.tactical_states if goal and s.timestamp>goal.timestamp),None)
    return [{'stage':0,'title':'Initial balanced prior','timestamp':0,'note':'BALANCED is an initial prior; no measured five-minute state yet.'}]+[{'stage':i,'title':title,'timestamp':obj.timestamp,'note':note,'event_id':getattr(obj,'event_id',None)} for i,title,obj,note in (
        (1,'Pressure shift',pressure,'Actual high-significance pressure evidence.'),
        (2,'State transition',transition,'Actual published state change. Any earlier goals remain in the timeline.'),
        (3,'Goal',goal,'The first goal after this state transition; not necessarily the first goal of the match.'),
        (4,'Post-goal response window',response,'The next measured checkpoint; sequence alone does not establish a causal response.')) if obj is not None]
