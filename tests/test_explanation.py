"""Phase 3 contracts, reconstruction, adversarial claims and live compatibility."""
import json
from pathlib import Path
import pytest
from pydantic import ValidationError
from simulator.generation.generator import generate_match
from simulator.engine.match_engine import MatchEngine
from simulator.intelligence.engine import IntelligenceEngine
from simulator.intelligence.models import (SignificantMoment, MomentType, SignificanceTier,
                                           Evidence, EvidenceType, RollingWindowMetrics)
from simulator.intelligence.explanation import (ExplanationEngine, ExplanationConfig,
    ExplanationValidator, ExplanationReport, MomentExplanation)
from simulator.intelligence.explanation.rules import rhythm
from simulator.intelligence.explanation.templates import TEMPLATES
from simulator.intelligence.explanation.models import ExplanationType
from simulator.models.events import EventType

@pytest.fixture(scope='module')
def fixture():
    match = generate_match(seed=42)
    events = MatchEngine(match).simulate()
    intel = IntelligenceEngine(match, events).analyze()
    engine = ExplanationEngine(source_events=events)
    return intel, events, engine, engine.explain(intel)

@pytest.fixture(scope='module')
def goal_fixture():
    for seed in (99, 1, 2, 3, 4, 5):
        match = generate_match(seed=seed)
        events = MatchEngine(match).simulate()
        if any(e.event_type == EventType.GOAL for e in events):
            intel = IntelligenceEngine(match, events).analyze()
            engine = ExplanationEngine(source_events=events)
            return intel, events, engine, engine.explain(intel)
    pytest.fail('No goal in verification seeds')

def test_deterministic_and_immutable(fixture):
    intel, _, engine, report = fixture
    before = intel.model_dump_json()
    assert engine.explain(intel).model_dump_json() == report.model_dump_json()
    assert intel.model_dump_json() == before

def test_json_roundtrip(fixture):
    report = fixture[3]
    assert ExplanationReport.model_validate_json(report.model_dump_json()) == report

def test_all_references_and_bounds(fixture):
    intel, events, engine, report = fixture
    ledger = {e.evidence_id: e for e in report.evidence}
    moments = {m.moment_id: m for m in intel.detected_moments}
    source_ids = {e.event_id for e in events}
    validator = ExplanationValidator()
    assert report.explanations
    for e in report.explanations:
        assert e.moment_id in moments
        assert 0 <= e.confidence <= 1
        assert 0 <= e.significance_score <= 100
        assert 1 <= len(e.evidence_ids) <= 6
        assert set(e.evidence_ids) <= ledger.keys()
        assert set(e.source_event_ids) <= source_ids
        assert e.significance_breakdown.score == moments[e.moment_id].significance_score
        for c in e.evidence_chains:
            assert set(c.evidence_ids) <= set(e.evidence_ids)
        assert all(ledger[eid].timestamp <= e.timestamp for eid in e.evidence_ids)
        validator.validate(e, engine=engine, intelligence=intel)

def test_projections_resolve(fixture):
    intel, events, _, report = fixture
    data = intel.model_dump(mode='json')
    emap = {e.event_id: e for e in events}
    for e in report.evidence:
        resolved = []
        for path in e.source_paths:
            if path.startswith('events/'):
                assert path.split('/')[1] in emap
                continue
            item = data
            for key in path.split('/'):
                item = item[int(key)] if isinstance(item, list) else item[key]
            resolved.append(item)
        if resolved and isinstance(resolved[0], (int, float)):
            assert e.value == resolved[0]
            if e.baseline_value is not None:
                assert e.baseline_value == resolved[1]

@pytest.mark.parametrize('kind,metric', [('PRESSURE_SHIFT','pressure_index'),
    ('FIELD_TILT_SHIFT','field_tilt'), ('STATE_CHANGE','state_transition'),
    ('SHOT_THREAT','shot_on_target')])
def test_required_trigger(fixture, kind, metric):
    ledger = {e.evidence_id:e for e in fixture[3].evidence}
    explanations = [e for e in fixture[3].explanations if e.explanation_type.value == kind]
    assert explanations
    for e in explanations:
        assert metric in [ledger[eid].metric_name for eid in e.evidence_ids]
        if kind == 'STATE_CHANGE':
            t = e.context['state_transition']
            assert t['previous_state'] in e.what_happened and t['new_state'] in e.what_happened

def test_goal_sequence(goal_fixture):
    intel, events, _, report = goal_fixture
    goals = [e for e in report.explanations if e.explanation_type.value == 'GOAL']
    assert goals
    emap = {e.event_id: e for e in events}
    ledger = {e.evidence_id: e for e in report.evidence}
    for e in goals:
        assert any(ledger[eid].metric_name == 'goal' for eid in e.evidence_ids)
        g = e.goal_context
        assert g is not None and g.preceding_sequence_id
        assert g.passes is not None and g.shot_context
        assert any(emap[eid].event_type == EventType.GOAL for eid in g.direct_event_ids)
        assert all(emap[eid].timestamp <= e.timestamp for eid in g.contextual_event_ids)
        seq = next(s for s in intel.possession_sequences if s.sequence_id == g.preceding_sequence_id)
        assert g.passes == seq.passes and g.sequence_duration == seq.duration_s

def test_chronology(fixture):
    assert [e.timestamp for e in fixture[3].explanations] == sorted(e.timestamp for e in fixture[3].explanations)

def test_correct_window_and_percentage_points(fixture):
    intel, _, _, report = fixture
    for e in report.explanations:
        if e.explanation_type.value in ('PRESSURE_SHIFT','FIELD_TILT_SHIFT'):
            metric = e.supporting_metrics[e.evidence_ids[0]]
            assert metric.window == '5m'
            prev = max(w.timestamp for w in intel.rolling_windows if w.window_seconds == 300 and w.timestamp < e.timestamp)
            assert metric.baseline_timestamp == prev
            if e.explanation_type.value == 'FIELD_TILT_SHIFT':
                assert metric.percentage_delta is None
                assert metric.unit == 'percentage points'
                assert metric.team_id == intel.home_team.team_id

def test_pressure_context(fixture):
    e = next(e for e in fixture[3].explanations if e.explanation_type.value == 'PRESSURE_SHIFT')
    pressure = e.context['tactical_pressure']
    assert pressure['pressure_change']['metric'] == 'pressure_index'
    assert pressure['event_observations']
    assert pressure['forced_turnovers'] is None
    assert all(0 <= observation['intensity'] <= 1 for observation in pressure['event_observations'])

def test_intelligence_only_is_explicit(fixture):
    report = ExplanationEngine().explain(fixture[0])
    assert report.explanations
    assert all(any('without raw events' in c for c in e.caveats) for e in report.explanations)

def test_filtered_notable_and_always_high(fixture):
    report = ExplanationEngine(ExplanationConfig(minimum_score=100, explain_notable=False), fixture[1]).explain(fixture[0])
    assert report.explanations
    assert all(e.significance_tier in (SignificanceTier.HIGH, SignificanceTier.CRITICAL) for e in report.explanations)

def test_low_filtered(fixture):
    intel = fixture[0].model_copy(deep=True)
    m = intel.detected_moments[0].model_copy(update={'tier': SignificanceTier.LOW})
    intel.detected_moments = [m]
    intel.explanation_contexts = []
    assert not ExplanationEngine(source_events=fixture[1]).explain(intel).explanations
    assert ExplanationEngine(source_events=fixture[1]).explain_moment(m, intel) is None

@pytest.mark.parametrize('text', ['Team Ghost scored 987 goals.', 'Player Messi was the best player.',
    'Pressure caused a goal.', 'Elite pressing dominated.', 'The score increased by 123.',
    'Premier League champions won.', 'home possession_pct moved from 1 to 99.'])
def test_unsupported_claims_rejected(fixture, text):
    intel, _, engine, report = fixture
    forged = report.explanations[0].model_copy(update={'why_it_matters': text})
    with pytest.raises(ValueError):
        ExplanationValidator().validate(forged, engine=engine, intelligence=intel)

@pytest.mark.parametrize('field,value', [('timestamp',999999), ('affected_team_id','ghost'),
    ('affected_player_ids',['ghost']), ('source_event_ids',['missing']),
    ('evidence_ids',['missing']), ('significance_score',0)])
def test_structured_forgery_rejected(fixture, field, value):
    forged = fixture[3].explanations[0].model_copy(update={field:value})
    with pytest.raises(ValueError):
        ExplanationValidator().validate(forged, engine=fixture[2], intelligence=fixture[0])

@pytest.mark.parametrize('mutation', ['missing_evidence','missing_event','negative','impossible','delta','future','team','duplicate'])
def test_inconsistent_input_rejected(fixture, mutation):
    intel = fixture[0].model_copy(deep=True)
    if mutation == 'missing_evidence':
        intel.detected_moments[0].evidence_ids.append('missing')
    elif mutation == 'missing_event':
        intel.evidence[0].source_event_ids.append('missing')
    elif mutation == 'negative':
        intel.evidence[0].value = -1
    elif mutation == 'impossible':
        intel.rolling_windows[0].home_possession_pct = 101
    elif mutation == 'delta':
        e = next(e for e in intel.evidence if e.delta is not None)
        e.delta += 1
    elif mutation == 'future':
        eid = intel.detected_moments[0].evidence_ids[0]
        next(e for e in intel.evidence if e.evidence_id == eid).timestamp = 99999
    elif mutation == 'team':
        intel.detected_moments[0].team_id = 'missing'
    else:
        intel.evidence.append(intel.evidence[0])
    with pytest.raises(ValueError):
        ExplanationEngine(source_events=fixture[1]).explain(intel)

@pytest.mark.parametrize('fields,label', [({'event_density':2},'LOW_EVENT'),
    ({'event_density':30,'turnovers':9},'CHAOTIC'),
    ({'event_density':20,'turnovers':6},'TRANSITION_HEAVY'),
    ({'event_density':30},'HIGH_TEMPO'), ({'event_density':20,'turnovers':4},'CONTESTED'),
    ({'event_density':20},'BALANCED'),
    ({'event_density':20,'home_possession_pct':65,'home_field_tilt':65,
      'home_passes':10,'home_pass_success':9,'turnovers':2},'CONTROLLED')])
def test_rhythm_rules(fields, label):
    w = RollingWindowMetrics(timestamp=600, window_seconds=300, **fields)
    assert rhythm(w)[0] == label
    assert rhythm(w) == rhythm(w)

def test_rhythm_changes_and_sources(fixture):
    report = fixture[3]
    assert any(a.previous_rhythm and a.previous_rhythm != a.rhythm_type for a in report.rhythm_assessments)
    assert all(a.evidence_ids and a.contributing_metrics for a in report.rhythm_assessments)
    assert all(0 <= a.confidence <= 1 for a in report.rhythm_assessments)

def test_live_prefix_matches_replay(fixture):
    intel, events, _, report = fixture
    target = next(e for e in report.explanations if e.explanation_type.value == 'PRESSURE_SHIFT' and e.timestamp > 600)
    cut = target.timestamp
    prefix = intel.model_copy(deep=True)
    prefix.detected_moments = [m for m in intel.detected_moments if m.timestamp <= cut]
    prefix.evidence = [e for e in intel.evidence if e.timestamp <= cut]
    prefix.rolling_windows = [w for w in intel.rolling_windows if w.timestamp <= cut]
    prefix.state_transitions = [t for t in intel.state_transitions if t.timestamp <= cut]
    prefix.tactical_states = [s for s in intel.tactical_states if s.timestamp <= cut]
    prefix.explanation_contexts = [c for c in intel.explanation_contexts if c.timestamp <= cut]
    live = ExplanationEngine(source_events=[e for e in events if e.timestamp <= cut])
    result = live.explain_moment(next(m for m in prefix.detected_moments if m.moment_id == target.moment_id), prefix)
    # Projection paths may differ when a consumer removes interleaved windows;
    # claims, evidence IDs and confidence remain identical.
    actual, expected = result.model_dump(), target.model_dump()
    for data in (actual, expected):
        for evidence in data['context']['selected_evidence']:
            evidence.pop('source_paths')
    assert actual == expected

def test_unknown_moment_rejected(fixture):
    with pytest.raises(ValueError):
        fixture[2].explain_moment(fixture[0].detected_moments[0].model_copy(update={'moment_id':'unknown'}), fixture[0])

def test_template_safety():
    with pytest.raises(ValueError):
        TEMPLATES[ExplanationType.GOAL].render(subject='home')

def test_bounds_pydantic(fixture):
    data = fixture[3].explanations[0].model_dump()
    for field, value in [('confidence', 1.1), ('significance_score',101), ('timestamp',-1)]:
        with pytest.raises(ValidationError):
            MomentExplanation.model_validate({**data, field:value})

def test_cli(tmp_path, capsys):
    from simulator.run import main
    main(['--seed','99','--duration','90','--output',str(tmp_path/'match.jsonl'),'--explain'])
    report = ExplanationReport.model_validate_json((tmp_path/'explanations.json').read_text())
    assert report.explanations
    assert (tmp_path/'match_intelligence.json').exists()
    assert 'WHY IT MATTERS:' in capsys.readouterr().out

@pytest.mark.parametrize('moment_type,metric,kind,player', [
    (MomentType.POSSESSION_SHIFT, 'possession_pct', 'POSSESSION_SWING', False),
    (MomentType.PLAYER_SPIKE, 'progressive_passes', 'PLAYER_ACTIVITY_SHIFT', True),
    (MomentType.TACTICAL_SHIFT, 'progressive_actions', 'PROGRESSION_SURGE', False),
])
def test_phase2_supplied_additional_types(fixture, moment_type, metric, kind, player):
    # Phase 2 currently does not emit these moments. Explain supplied typed
    # packets without inferring new player time series from full-match totals.
    intel = fixture[0].model_copy(deep=True)
    pid = intel.players[0].player_id if player else None
    evidence = Evidence(evidence_id='supplied_evidence', metric_name=metric, value=4 if player else 64,
        baseline_value=1 if player else 48, delta=3 if player else 16, window='5m',
        timestamp=600, team_id=intel.home_team.team_id, player_id=pid,
        evidence_type=EvidenceType.PLAYER_SHIFT if player else EvidenceType.SHIFT)
    if moment_type == MomentType.POSSESSION_SHIFT:
        windows = sorted([w for w in intel.rolling_windows if w.window_seconds == 300 and w.timestamp <= 600],
                         key=lambda w: w.timestamp)
        evidence.value = windows[-1].home_possession_pct
        evidence.baseline_value = windows[-2].home_possession_pct
        evidence.delta = round(evidence.value-evidence.baseline_value, 4)
    moment = SignificantMoment(moment_id='supplied_moment' , timestamp=600, minute=10,
        moment_type=moment_type, significance_score=70, tier=SignificanceTier.HIGH,
        team_id=intel.home_team.team_id, player_id=pid, evidence_ids=[evidence.evidence_id],
        reason_codes=['supplied_phase2_packet'])
    intel.evidence.append(evidence)
    intel.detected_moments.append(moment)
    exp = ExplanationEngine(source_events=fixture[1]).explain_moment(moment, intel)
    assert exp.explanation_type.value == kind
    assert exp.evidence_ids[0] == evidence.evidence_id
    assert exp.supporting_metrics[evidence.evidence_id].baseline == evidence.baseline_value
    if player:
        assert pid in exp.what_happened and pid in exp.what_changed

@pytest.mark.parametrize('state,kind', [('HOME_CONTROL','CONTROL_SHIFT'),
    ('HIGH_PRESSURE','HIGH_PRESSURE_PHASE'), ('CHAOTIC_PHASE','CHAOS_PHASE'),
    ('TRANSITION_PHASE','TRANSITION_PHASE')])
def test_tactical_shift_phase_types(fixture, state, kind):
    intel = fixture[0].model_copy(deep=True)
    t = next((t for t in intel.state_transitions if t.new_state.value == state), None)
    if t is None:
        from simulator.intelligence.models import MatchStateName
        # An explicit supplied Phase 2 packet covers a state absent in seed 42.
        t = intel.state_transitions[0].model_copy(update={'new_state': MatchStateName(state)})
        intel.state_transitions[0] = t
    moment = SignificantMoment(moment_id='tactical_packet', timestamp=t.timestamp, minute=t.minute,
        moment_type=MomentType.TACTICAL_SHIFT, significance_score=70, tier=SignificanceTier.HIGH,
        evidence_ids=t.evidence_ids)
    intel.detected_moments.append(moment)
    exp = ExplanationEngine(source_events=fixture[1]).explain_moment(moment, intel)
    assert exp.explanation_type.value == kind

def test_no_future_data_used_for_sequence(fixture):
    intel = fixture[0].model_copy(deep=True)
    moment = next(m for m in intel.detected_moments if m.moment_type == MomentType.SHOT_ON_TARGET)
    event = next(e for e in fixture[1] if e.event_id == moment.source_event_ids[0])
    sequence = next(s for s in intel.possession_sequences if s.sequence_id == event.possession_sequence_id)
    sequence.end_time = moment.timestamp + 100
    sequence.duration_s = sequence.end_time - sequence.start_time
    exp = ExplanationEngine(source_events=fixture[1]).explain_moment(moment, intel)
    assert 'preceding_sequence' not in exp.context

def test_state_context_team_attribution(fixture):
    intel, _, _, report = fixture
    exp = next(e for e in report.explanations if e.context.get('state_transition',{}).get('new_state') == 'HOME_CONTROL')
    assert any(c.team_id == intel.home_team.team_id and c.metric == 'field_tilt'
               for c in exp.supporting_metrics.values())

def test_live_engine_on_actual_phase2_prefix(fixture):
    # Analyze an unfinished event prefix: no MATCH_END is required.
    match = generate_match(seed=42)
    events = [e for e in fixture[1] if e.timestamp <= 1800]
    intel = IntelligenceEngine(match, events).analyze()
    report = ExplanationEngine(source_events=events).explain(intel)
    assert report.explanations
    assert all(e.timestamp <= events[-1].timestamp for e in report.explanations)

def test_shift_conflict_with_rolling_snapshot_rejected(fixture):
    intel = fixture[0].model_copy(deep=True)
    evidence = next(e for e in intel.evidence if e.metric_name == 'pressure_index')
    evidence.value += 1
    evidence.delta += 1
    with pytest.raises(ValueError, match='rolling current'):
        ExplanationEngine(source_events=fixture[1]).explain(intel)

def test_context_timestamp_rejected(fixture):
    intel = fixture[0].model_copy(deep=True)
    intel.explanation_contexts[0].timestamp += 1
    with pytest.raises(ValueError, match='context timestamp'):
        ExplanationEngine(source_events=fixture[1]).explain(intel)

def test_sparse_report_without_moments(fixture):
    intel = fixture[0].model_copy(deep=True)
    intel.detected_moments = []
    intel.explanation_contexts = []
    assert not ExplanationEngine(source_events=fixture[1]).explain(intel).explanations
