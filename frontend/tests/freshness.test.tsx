import React from 'react';
import {afterEach,describe,it,expect,vi} from 'vitest';
import {render,screen,within,fireEvent,act} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {insightSchema,snapshotSchema,Insight,Snapshot,Message} from '../lib/types';
import {loadMatch} from '../lib/api';
import {clock} from '../lib/format';
import {momentExplanationText} from '../lib/presentation-status';
import {MatchDNA,TemporalExperience} from '../components/temporal';
import Experience,{Broadcast,overlayPayload} from '../components/experience';

const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
const snapshot=snapshotSchema.parse(fixture.snapshot);
const insight=insightSchema.parse(fixture.insight);
vi.mock('../lib/api',()=>({base:'http://localhost:8000',loadMatch:vi.fn(),control:vi.fn(async()=>undefined)}));
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
const withoutExplanation=(status:Insight['workflow_status']):Insight=>({...insight,workflow_status:status,explanation:null,agent_explanation:null,narrative:null,execution:null,truth:null});
function moments(value:Insight){
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(Error('offline')));
 const data={...snapshot,insights:[value]};
 const view=render(<TemporalExperience data={data} mode="fan" insight={value} onMoment={()=>{}}/>);
 fireEvent.click(screen.getByRole('button',{name:'MOMENTS'}));
 return {...view,data};
}

describe('moment explanation lifecycle',()=>{
 it.each([
  ['NO_EXPLANATION','No explanation available for this moment.'],
  ['PROCESSING','Explanation pending.'],
  ['FAILED','Explanation unavailable. The requested workflow did not complete.'],
  ['COMPLETE','No explanation available for this moment.'],
  ['PARTIAL','No explanation available for this moment.'],
 ] as const)('renders %s honestly', (status,text)=>{
  const {container}=moments(withoutExplanation(status));
  expect(container.querySelector('.moment-library')).toHaveTextContent(text);
  if(status!=='PROCESSING')expect(container.querySelector('.moment-library')).not.toHaveTextContent('Explanation pending.');
 });
 it('preserves valid deterministic explanation text unchanged',()=>{
  const {container}=moments(insight);
  expect(within(container.querySelector('.moment-library') as HTMLElement).getByText(insight.explanation!.what_changed)).toBeInTheDocument();
 });
 it.each(['PENDING','UNAVAILABLE','REJECTED'] as const)('uses shared execution lifecycle %s',state=>{
  const value={...withoutExplanation('PARTIAL'),execution:{mode:'foundry' as const,state,status:'FOUNDRY UNAVAILABLE' as const,trace_id:null,fallback:false,run_id:null,steps:[]}};
  const {container}=moments(value),library=container.querySelector('.moment-library')!;
  expect(library).toHaveTextContent(state==='PENDING'?'Explanation pending.':state==='REJECTED'?'Generated interpretation rejected. No accepted explanation available.':'Explanation unavailable. The requested workflow did not complete.');
  expect(library).not.toHaveTextContent('FOUNDRY LIVE');
 });
 it('retains deterministic evidence while explicitly rejecting generated interpretation',()=>{
  const value={...insight,truth:{...insight.truth!,status:'REJECTED' as const}};
  const {container}=moments(value);
  expect(container.querySelector('.moment-library')).toHaveTextContent(`Generated interpretation rejected. Deterministic explanation: ${insight.explanation!.what_changed}`);
  expect(value.evidence).toEqual(insight.evidence);
 });
 it.each(['PROCESSING','FAILED'] as const)('keeps %s visible alongside deterministic material',workflow_status=>{
  const value={...insight,workflow_status,execution:null,truth:null};
  const {container}=moments(value);
  expect(container.querySelector('.moment-library')).toHaveTextContent(workflow_status==='PROCESSING'?'Explanation pending.':'Explanation unavailable.');
  expect(container.querySelector('.moment-library')).toHaveTextContent(`Deterministic explanation: ${insight.explanation!.what_changed}`);
 });
 it('pending execution does not inherit a stale rejected validation label',()=>{
  const value={...withoutExplanation('PROCESSING'),truth:{...insight.truth!,status:'REJECTED' as const},execution:{mode:'foundry' as const,state:'PENDING' as const,status:'FOUNDRY UNAVAILABLE' as const,trace_id:null,fallback:false,run_id:null,steps:[]}};
  const {container}=moments(value);
  expect(container.querySelector('.moment-library')).toHaveTextContent('Explanation pending.');
  expect(container.querySelector('.moment-library')).not.toHaveTextContent('Generated interpretation rejected.');
 });
 it.each([undefined,'UNRECOGNISED'])('does not invent pending for missing or unknown status %s',status=>{
  // Boundary schema still rejects these; the presentation utility is also conservative.
  const value={...withoutExplanation('COMPLETE'),workflow_status:status} as unknown as Insight;
  expect(insightSchema.safeParse(value).success).toBe(false);
  expect(momentExplanationText(value)).toBe('No explanation available for this moment.');
 });
 it('updates the same moment from processing to terminal without resetting',()=>{
  const pending=withoutExplanation('PROCESSING');const view=moments(pending);
  expect(view.container.querySelector('.moment-library')).toHaveTextContent('Explanation pending.');
  const terminal={...pending,workflow_status:'NO_EXPLANATION' as const};
  view.rerender(<TemporalExperience data={{...view.data,insights:[terminal]}} mode="fan" insight={terminal} onMoment={()=>{}}/>);
  expect(view.container.querySelector('.moment-library')).toHaveTextContent('No explanation available for this moment.');
  expect(view.container.querySelector('.moment-library')).not.toHaveTextContent('Explanation pending.');
 });
});

describe('selected Broadcast timestamp',()=>{
 it('attaches the actual source timestamp and updates with the insight',()=>{
  const {rerender}=render(<Broadcast insight={insight}/>);
  expect(screen.getByLabelText('Selected Broadcast moment')).toHaveTextContent(`SELECTED MOMENT · ${clock(insight.moment.timestamp)}`);
  const next={...insight,insight_id:'another',moment:{...insight.moment,timestamp:1500}};
  rerender(<Broadcast insight={next}/>);
  expect(screen.getByLabelText('Selected Broadcast moment')).toHaveTextContent('SELECTED MOMENT · 25:00');
 });
 it('keeps the timestamp in compact overlays and preserves the export contract',()=>{
  render(<Broadcast insight={insight}/>);
  fireEvent.change(screen.getByLabelText('Overlay size'),{target:{value:'compact'}});
  expect(screen.getByLabelText('Selected Broadcast moment')).toHaveTextContent(clock(insight.moment.timestamp));
  expect(overlayPayload(insight)).toMatchObject({insight_id:insight.insight_id,timestamp:insight.moment.timestamp,evidence_ids:insight.evidence.map(e=>e.evidence_id)});
 });
 it.each([undefined,null,NaN,Infinity,-1])('does not fabricate a timestamp for %s',timestamp=>{
  const value={...insight,moment:{...insight.moment,timestamp}} as unknown as Insight;
  render(<Broadcast insight={value}/>);
  expect(screen.getByLabelText('Selected Broadcast moment')).toHaveTextContent('SELECTED MOMENT · TIMESTAMP UNAVAILABLE');
 });
 it('does not label the empty desk as a selected moment',()=>{
  render(<Broadcast insight={null}/>);expect(screen.queryByLabelText('Selected Broadcast moment')).not.toBeInTheDocument();
 });
});

describe('Match DNA contributing-window freshness',()=>{
 it('has no invented metadata before publication',()=>{
  render(<MatchDNA temporal={{current:null,trajectory:[],dna:[],dna_method:snapshot.state.temporal!.dna_method}} home="HOME" away="AWAY"/>);
  expect(screen.getByText('No measured profile yet.')).toBeInTheDocument();
  expect(screen.queryByLabelText('Match DNA freshness')).not.toBeInTheDocument();
 });
 it('uses exactly the real projection inputs and preserves values and method',()=>{
  const temporal=snapshot.state.temporal!;const before=JSON.stringify(temporal);
  const {container,rerender}=render(<MatchDNA temporal={temporal} home="HOME" away="AWAY"/>);
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('MATCH-TO-DATE PROFILE · THROUGH 20:00 · 4 PUBLISHED WINDOWS');
  expect(screen.getByText(temporal.dna_method)).toHaveTextContent('stability excludes windows without passes');
  expect(container.querySelectorAll('.vector-metric')).toHaveLength(8);
  const metrics=container.querySelector('.vector-grid')!.textContent;
  rerender(<MatchDNA temporal={structuredClone(temporal)} home="HOME" away="AWAY"/>);
  expect(container.querySelector('.vector-grid')!.textContent).toBe(metrics);
  expect(JSON.stringify(temporal)).toBe(before);
 });
 it('counts published windows with unavailable stability without inventing a stability value',()=>{
  const temporal=structuredClone(snapshot.state.temporal!);
  temporal.trajectory[0].dimensions.find(d=>d.name==='Stability')!.home=null;
  temporal.dna.find(d=>d.name==='Stability')!.home=null;
  const {container}=render(<MatchDNA temporal={temporal} home="HOME" away="AWAY"/>);
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('4 PUBLISHED WINDOWS');
  expect(within(container.querySelector('.vector-grid') as HTMLElement).getByText('Stability').closest('.vector-metric')).toHaveTextContent('—');
 });
 it('updates for a contributing point and clears on empty or replacement projection',()=>{
  const temporal=snapshot.state.temporal!;
  const {rerender}=render(<MatchDNA temporal={temporal} home="HOME" away="AWAY"/>);
  const next={...temporal,trajectory:[...temporal.trajectory,{...temporal.current!,timestamp:1500}]};
  rerender(<MatchDNA temporal={next} home="HOME" away="AWAY"/>);
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('THROUGH 25:00 · 5 PUBLISHED WINDOWS');
  rerender(<MatchDNA temporal={null} home="HOME" away="AWAY"/>);
  expect(screen.queryByLabelText('Match DNA freshness')).not.toBeInTheDocument();
  const replacement={...temporal,trajectory:[{...temporal.trajectory[0],timestamp:300}]};
  rerender(<MatchDNA temporal={replacement} home="HOME" away="AWAY"/>);
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('THROUGH 05:00 · 1 PUBLISHED WINDOW');
 });
 it.each(['empty','invalid-time','missing-dimensions'] as const)('marks incomplete %s metadata unavailable',kind=>{
  const temporal=structuredClone(snapshot.state.temporal!);
  if(kind==='empty')temporal.trajectory=[];
  if(kind==='invalid-time')temporal.trajectory[0].timestamp=NaN;
  if(kind==='missing-dimensions')temporal.trajectory[0].dimensions=[];
  render(<MatchDNA temporal={temporal} home="HOME" away="AWAY"/>);
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('FRESHNESS UNAVAILABLE');
  expect(screen.getByLabelText('Match DNA freshness')).not.toHaveTextContent('THROUGH');
 });
 it('keeps match-to-date DNA scope after selecting an older checkpoint',()=>{
  vi.stubGlobal('fetch',vi.fn().mockRejectedValue(Error('offline')));
  const {container}=render(<TemporalExperience data={snapshot} mode="fan" insight={insight} onMoment={()=>{}}/>);
  fireEvent.click(container.querySelector('.temporal-timeline button')!);
  expect(screen.getByText(/INSPECTING CHECKPOINT/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'MATCH DNA'}));
  expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('THROUGH 20:00 · 4 PUBLISHED WINDOWS');
 });
});

it('keeps selection, evidence and DNA freshness coherent across real stream contracts and reset',async()=>{
 class Events{
  static latest:Events;onopen:(()=>void)|null=null;onerror:(()=>void)|null=null;
  onmessage:((event:{data:string})=>void)|null=null;close=vi.fn();constructor(){Events.latest=this;}
 }
 vi.stubGlobal('EventSource',Events);
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(Error('offline')));
 vi.mocked(loadMatch).mockResolvedValueOnce({...snapshot,insights:[insight]});
 render(<Experience demo initialMode="broadcast"/>);
 await screen.findByLabelText('Selected Broadcast moment');
 const source=Events.latest;let cursor=snapshot.stream_cursor;
 const send=(type:Message['type'],payload:unknown)=>act(()=>source.onmessage?.({data:JSON.stringify({type,match_id:snapshot.match.match_id,timestamp:1600,sequence_number:++cursor,trace_id:null,payload})}));
 fireEvent.click(screen.getByRole('button',{name:'MOMENTS'}));
 fireEvent.click(screen.getByText(insight.explanation!.what_changed).closest('button')!);
 expect(screen.getByRole('dialog',{hidden:true})).toHaveTextContent(insight.evidence[0].evidence_id);
 fireEvent.click(screen.getByRole('button',{name:'Close evidence inspector',hidden:true}));
 const later={...insight,insight_id:'later',moment:{...insight.moment,timestamp:1500},narrative:{...insight.narrative!,headline:'Later narrative'}};
 send('NARRATIVE_READY',later);
 const laterState={...snapshot.state,simulation_time:1600,home_score:2};
 send('MATCH_STATE_UPDATED',laterState);
 expect(screen.getByLabelText('Match score')).toHaveTextContent('2—');
 expect(screen.getByLabelText('Selected Broadcast moment')).toHaveTextContent(clock(insight.moment.timestamp));
 expect(screen.getByText('Machine-readable overlay JSON').closest('details')).toHaveTextContent(insight.insight_id);
 fireEvent.click(screen.getByRole('button',{name:'MATCH DNA'}));
 expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('THROUGH 20:00 · 4 PUBLISHED WINDOWS');
 const nextTemporal={...snapshot.state.temporal!,trajectory:[...snapshot.state.temporal!.trajectory,{...snapshot.state.temporal!.current!,timestamp:1500}]};
 send('MATCH_STATE_UPDATED',{...laterState,temporal:nextTemporal});
 expect(screen.getByLabelText('Match DNA freshness')).toHaveTextContent('THROUGH 25:00 · 5 PUBLISHED WINDOWS');
 const reset:Snapshot={...snapshot,state:{...snapshot.state,simulation_time:0,total_events:0,home_score:0,away_score:0,temporal:{current:null,trajectory:[],dna:[],dna_method:snapshot.state.temporal!.dna_method}},events:[],insights:[],stream_cursor:cursor+1};
 send('RESET',reset);
 expect(screen.queryByLabelText('Match DNA freshness')).not.toBeInTheDocument();
 expect(screen.queryByLabelText('Selected Broadcast moment')).not.toBeInTheDocument();
 expect(screen.getByText('No measured profile yet.')).toBeInTheDocument();
});
