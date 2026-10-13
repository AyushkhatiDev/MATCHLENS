import React from 'react';
import {it,expect,vi} from 'vitest';
import {render,screen,within,fireEvent,waitFor} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {insightSchema,snapshotSchema,Insight} from '../lib/types';
import {runtimePresentation,interpretationHeading} from '../lib/presentation-status';
import {ExecutionTrace,InterpretationCouncil,SourceEventInspector,SourceEventButton} from '../components/intelligence-canvas';
import {CloudStrip,TruthChain,MatchDNA} from '../components/temporal';
import {StateRibbon} from '../components/motion';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
const snapshot=snapshotSchema.parse(fixture.snapshot);
const executed=(state:string,status='FOUNDRY UNAVAILABLE'):Insight=>insightSchema.parse({...fixture.insight,execution:{mode:'foundry',state,status,trace_id:'recorded',fallback:false,steps:[{stage:'Grounding validation',status:'OK',duration_ms:1},{stage:'Arbitration',status:'OK',duration_ms:2}]}});
const deterministic=executed('NOT REQUESTED');
vi.mock('../lib/api',()=>({base:'http://localhost:8000'}));
it.each([
 ['NOT REQUESTED','FOUNDRY UNAVAILABLE','NOT REQUESTED','DETERMINISTIC REPLAY · FOUNDRY NOT REQUESTED'],
 ['PENDING','FOUNDRY UNAVAILABLE','PENDING','FOUNDRY EXECUTION · IN PROGRESS'],
 ['COMPLETE','FOUNDRY LIVE','FOUNDRY LIVE','FOUNDRY LIVE · GROUNDED'],
 ['UNAVAILABLE','FOUNDRY UNAVAILABLE','FOUNDRY UNAVAILABLE','FOUNDRY UNAVAILABLE'],
 ['REJECTED','FOUNDRY RESULT REJECTED','FOUNDRY RESULT REJECTED','FOUNDRY RESULT REJECTED'],
])('maps lifecycle %s consistently in execution and bottom context',async(state,status,badge,context)=>{
 const insight=executed(state,status),runtime=runtimePresentation(insight);expect(runtime.badge).toBe(badge);expect(runtime.context).toBe(context);
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));
 render(<><ExecutionTrace insight={insight}/><CloudStrip insight={insight} compact/></>);
 expect(within(screen.getByLabelText('Foundry execution provenance')).getByText(badge)).toBeInTheDocument();
 expect(within(screen.getByLabelText('Persistent runtime telemetry')).getByRole('status')).toHaveTextContent(context);
 await screen.findByText('APPLICATION TELEMETRY STALE');
 if(state!=='COMPLETE')expect(runtime.grounded).toBe(false);
});
it('deterministic council cannot show old supplied specialist successes or errors',()=>{
 render(<InterpretationCouncil insight={deterministic}/>);
 expect(screen.getByText('DETERMINISTIC EVIDENCE')).toBeInTheDocument();expect(screen.getByText('AGENTS NOT REQUESTED')).toBeInTheDocument();
 expect(screen.getAllByText('Validation · NOT REQUESTED')).toHaveLength(3);
 expect(screen.queryByText('UNAVAILABLE')).not.toBeInTheDocument();expect(screen.queryByText('SUPPORTED')).not.toBeInTheDocument();
 expect(screen.getByText(/Foundry activates for the designated intelligence moment/)).toBeInTheDocument();
});
it('pending never exposes live or validated headings even with legacy unavailable status',()=>{
 const insight={...executed('PENDING'),truth:null,agent_explanation:null};render(<><InterpretationCouncil insight={insight}/><ExecutionTrace insight={insight}/></>);
 expect(screen.getAllByText('Validation · PENDING')).toHaveLength(3);expect(screen.queryByText('FOUNDRY UNAVAILABLE')).toBeNull();expect(screen.queryByText(/VALIDATED BY MATCHLENS/)).toBeNull();expect(interpretationHeading(insight)).toBe('FOUNDRY EXECUTION');
});
it('live needs a backend complete/live record, and grounded needs both successful steps',()=>{
 const incomplete=executed('COMPLETE','FOUNDRY LIVE');incomplete.execution!.steps.pop();expect(runtimePresentation(incomplete).context).toBe('FOUNDRY LIVE · EXECUTION COMPLETE');
 expect(runtimePresentation(executed('COMPLETE')).kind).toBe('awaiting');
 const mock={...executed('COMPLETE','FOUNDRY LIVE'),execution:{...executed('COMPLETE','FOUNDRY LIVE').execution!,mode:'mock' as const,status:'MOCK' as const}};expect(runtimePresentation(mock).context).not.toContain('LIVE');
});
it('supported council displays validated finding states, never the raw OK role label',()=>{
 const insight=executed('COMPLETE','FOUNDRY LIVE');insight.truth!.specialists.forEach(s=>{s.validation_state='SUPPORTED';});render(<InterpretationCouncil insight={insight}/>);expect(screen.getByText('FOUNDRY LIVE')).toBeInTheDocument();expect(screen.queryByText('OK')).toBeNull();expect(screen.getAllByText('Validation · SUPPORTED')).toHaveLength(3);
});
it('source buttons prioritize event, time, team and player while retaining canonical IDs',()=>{
 const event=fixture.insight.source_events[0],click=vi.fn();render(<SourceEventButton event={event} data={snapshot} onClick={click}/>);const button=screen.getByRole('button');expect(button).toHaveAccessibleName(expect.stringContaining(event.event_id));expect(button.querySelector('.source-label')).toHaveTextContent([snapshot.match.home,snapshot.match.away].find(t=>t.team_id===event.team_id)!.name);expect(button.querySelector('.source-id')).toHaveTextContent(event.event_id);fireEvent.click(button);expect(click).toHaveBeenCalledOnce();
});
it('missing source coordinates are explicitly not supplied and raw payload is retained',()=>{
 const event={...fixture.insight.source_events[0],start_x:null,start_y:null};render(<SourceEventInspector event={event} insight={deterministic} data={snapshot} onBack={()=>{}}/>);expect(screen.getByText('Location').nextSibling).toHaveTextContent('Not supplied');expect(screen.getByText('VIEW RAW EVENT').closest('details')).not.toHaveAttribute('open');expect(screen.getByText('Record identifiers').closest('details')).not.toHaveAttribute('open');expect(screen.getByText(event.match_id)).toBeInTheDocument();expect(screen.getByRole('button',{name:/Back to truth chain/})).toHaveFocus();
});
it('source navigation preserves the selected claim and restores citation focus',async()=>{
 const insight=insightSchema.parse(fixture.insight),source=vi.fn(),trace=vi.fn();render(<TruthChain insight={insight} data={snapshot} onSource={source} onTrace={trace}/>);
 const claim=insight.truth!.claims.find(c=>c.evidence_ids.some(id=>insight.evidence.find(e=>e.evidence_id===id)?.source_event_ids.length))!;
 const details=screen.getByText(claim.text).closest('details')!;details.open=true;fireEvent(details,new Event('toggle'));await screen.findByLabelText('Selected claim evidence trace');
 const button=within(details).getAllByRole('button')[0];fireEvent.click(button);await screen.findByLabelText('Source event inspector');expect(source.mock.calls.at(-1)![0]).toHaveLength(1);expect(trace.mock.calls.at(-1)![0].length).toBeGreaterThan(0);
 fireEvent.click(screen.getByRole('button',{name:/Back to truth chain/}));await waitFor(()=>expect(screen.getByText(claim.text).closest('details')).toHaveAttribute('open'));expect(trace).toHaveBeenLastCalledWith(claim.evidence_ids);
});
it('selected source marker uses the actual timestamp and ID independently of checkpoints',()=>{
 const event=fixture.insight.source_events[0];const {container}=render(<StateRibbon points={snapshot.state.temporal!.trajectory} selected={null} selectedSource={event}/>);const marker=container.querySelector('.ribbon-source')!;expect(marker).toHaveAttribute('data-source-id',event.event_id);expect(marker.querySelector('title')).toHaveTextContent(event.event_id);expect(container.querySelectorAll('.ribbon-point')).toHaveLength(snapshot.state.temporal!.trajectory.length);
});
it('Match DNA explains the measured profile and retains the eight existing dimensions',()=>{
 const {container}=render(<MatchDNA temporal={snapshot.state.temporal} home="NBC" away="IHU"/>);expect(screen.getByText('Measured match profile')).toBeInTheDocument();expect(screen.getByText('Built from the match’s measured state dimensions.')).toBeInTheDocument();expect(container.querySelectorAll('.vector-metric')).toHaveLength(8);
});
it('recorded Event Hubs aggregate is labelled historical, never current session traffic',()=>{
 const bundled=JSON.parse(readFileSync('lib/recorded-transport.json','utf8'));expect(bundled.published).toBe(bundled.received);expect(bundled.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);expect(bundled.provenance).toContain('Historical aggregate');expect(bundled).not.toHaveProperty('run_id');
});
it('missing downstream receipt cannot claim a verified Eventstream path',async()=>{
 const data={input:'local',agent_runtime:'foundry',application_status:'waiting',processed_events:0,processed_rate:0,rate_window_seconds:10,last_processed_at:null,namespace:null,hub:null,published_events:null,eventstream_current_traffic:null,downstream:{status:'UNVERIFIED',continuous_verification:false,table:'MatchEvents',traffic:null,last_observed_message:null}};
 vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>data}));render(<CloudStrip insight={deterministic} compact/>);
 await screen.findByText('LOCAL REPLAY');expect(screen.queryByText('HISTORICAL · downstream path verified')).toBeNull();expect(screen.getByText('Current Fabric traffic is not monitored')).toBeInTheDocument();expect(screen.getByLabelText('Microsoft data plane').querySelector('details')).not.toHaveAttribute('open');
});
it('clarity styling retains existing motion, readable statuses and secondary source identifiers',()=>{
 const css=readFileSync('app/clarity.css','utf8'),motion=readFileSync('app/motion.css','utf8');expect(css).not.toContain('@keyframes');expect(css).toContain('.telemetry-dock.tone-pending i');expect(css).toContain('.telemetry-dock.tone-failure i');expect(css).toContain('.source-node .source-id');expect(motion).toContain('@media(prefers-reduced-motion:reduce)');expect(css).toContain('min-height:44px');
});
