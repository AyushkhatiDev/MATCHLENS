import React from 'react';
import {it,expect,vi} from 'vitest';
import {render,screen,within,fireEvent} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {insightSchema,snapshotSchema} from '../lib/types';
import {runtimePresentation,councilPresentation,specialistPresentation} from '../lib/presentation-status';
import {InterpretationCouncil,ExecutionTrace} from '../components/intelligence-canvas';
import {CloudStrip,TemporalExperience} from '../components/temporal';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
const result=(state:string)=>insightSchema.parse({...fixture.insight,execution:{mode:'foundry',state,status:state==='COMPLETE'?'FOUNDRY LIVE':state==='REJECTED'?'FOUNDRY RESULT REJECTED':'FOUNDRY UNAVAILABLE',trace_id:'recorded',fallback:false,steps:[{stage:'Grounding validation',status:'OK',duration_ms:1},{stage:'Arbitration',status:'OK',duration_ms:2}]},truth:state==='COMPLETE'?{...fixture.insight.truth,specialists:fixture.insight.truth.specialists.map((s:object)=>({...s,validation_state:'SUPPORTED'}))}:null});
vi.mock('../lib/api',()=>({base:'http://localhost:8000'}));
it.each([
 ['NOT REQUESTED','DETERMINISTIC EVIDENCE','Selected moment · deterministic state','DETERMINISTIC REPLAY · FOUNDRY NOT REQUESTED'],
 ['PENDING','FOUNDRY EXECUTION IN PROGRESS','Selected moment · Foundry execution','FOUNDRY EXECUTION · IN PROGRESS'],
 ['COMPLETE','FOUNDRY LIVE','Selected moment · validated Foundry result','FOUNDRY LIVE · GROUNDED'],
 ['UNAVAILABLE','FOUNDRY UNAVAILABLE','Selected moment · Foundry execution failed','FOUNDRY UNAVAILABLE'],
 ['REJECTED','FOUNDRY RESULT REJECTED','Selected moment · rejected Foundry result','FOUNDRY RESULT REJECTED'],
])('presents actual %s consistently and accessibly',async(state,heading,context,bar)=>{
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));const insight=result(state);const {container}=render(<><InterpretationCouncil insight={insight}/><CloudStrip insight={insight}/></>);
 const council=container.querySelector('.council')!;expect(within(council as HTMLElement).getByText(heading)).toBeInTheDocument();expect(within(council as HTMLElement).getByText(context)).toBeInTheDocument();expect(within(screen.getByLabelText('Persistent runtime telemetry')).getByRole('status')).toHaveTextContent(bar);
 expect(council).not.toHaveTextContent(/validation not confirmed|awaiting moment execution/i);
 if(state==='NOT REQUESTED'||state==='PENDING'){expect(container.querySelector('.tone-failure')).toBeNull();expect(council).not.toHaveTextContent('UNAVAILABLE');expect(council).not.toHaveTextContent('VALIDATION FAILED');expect(screen.getAllByText(`Validation · ${state}`)).toHaveLength(3);}
 if(state==='COMPLETE'){expect(council).toHaveTextContent('Grounding PASSED');expect(council).toHaveTextContent('Arbitration ACCEPTED');expect(council).toHaveTextContent('72% validated confidence');expect(screen.getAllByText('Validation · SUPPORTED')).toHaveLength(3);}
 await screen.findByText('APPLICATION TELEMETRY STALE');
});
it('missing execution/findings cannot invent pending or failure',()=>{
 const insight={...result('COMPLETE'),execution:null,truth:null};render(<InterpretationCouncil insight={insight}/>);expect(screen.getAllByText('NO RESULT REPORTED')).toHaveLength(3);expect(screen.queryByText('UNAVAILABLE')).toBeNull();expect(screen.queryByText('PENDING')).toBeNull();expect(runtimePresentation(insight).context).toBe('DETERMINISTIC EVIDENCE · NO EXECUTION RECORD');expect(runtimePresentation(null).context).toBe('NO MOMENT SELECTED');
});
it('omitted specialist validation remains omitted at parsing, not defaulted to unavailable',()=>{
 const insight=insightSchema.parse(fixture.insight);expect(insight.truth!.specialists[0].validation_state).toBeUndefined();expect(specialistPresentation(insight,'tactical')).toBe('NO RESULT REPORTED');
});
it('actual hero-only policy excludes non-goal requests without treating all absent records as not requested',()=>{
 const insight={...result('COMPLETE'),execution:null,truth:null,moment:{...result('COMPLETE').moment,moment_type:'STATE_CHANGE'}};expect(runtimePresentation(insight,true).kind).toBe('not-requested');expect(runtimePresentation(insight,false).kind).toBe('awaiting');expect(runtimePresentation({...insight,moment:{...insight.moment,moment_type:'GOAL'}},true).kind).toBe('awaiting');
});
it('pending overrides stale specialist failures and supplied successful steps until completion',()=>{
 const insight={...result('PENDING'),truth:result('COMPLETE').truth};insight.truth!.specialists.forEach(s=>{s.validation_state='UNAVAILABLE';});render(<InterpretationCouncil insight={insight}/>);expect(screen.getAllByText('Validation · PENDING')).toHaveLength(3);expect(screen.queryByText('UNAVAILABLE')).toBeNull();expect(screen.queryByText(/GROUNDED/)).toBeNull();expect(councilPresentation(insight).grounded).toBe(false);
});
it('a genuine hero transition exposes success only after the backend outcome and confirmation',()=>{
 const {rerender,container}=render(<InterpretationCouncil insight={result('NOT REQUESTED')}/>);expect(container).toHaveTextContent('AGENTS NOT REQUESTED');rerender(<InterpretationCouncil insight={result('PENDING')}/>);expect(container).toHaveTextContent('IN PROGRESS');expect(container).not.toHaveTextContent('FOUNDRY LIVE');rerender(<InterpretationCouncil insight={result('COMPLETE')}/>);expect(container).toHaveTextContent('FOUNDRY LIVE');expect(container).toHaveTextContent('GROUNDED · ARBITRATED');
});
it('successful but truncated provenance describes the missing step without claiming grounding',()=>{
 const insight=result('COMPLETE');insight.execution!.steps.pop();render(<InterpretationCouncil insight={insight}/>);expect(screen.getByText('FOUNDRY LIVE')).toBeInTheDocument();expect(screen.getByText(/step records not fully supplied/)).toBeInTheDocument();expect(screen.queryByText(/Grounding PASSED/)).toBeNull();
});
it.each(['NOT REQUESTED','PENDING','UNAVAILABLE','REJECTED'])('deterministic measurements and inspection remain usable for %s',state=>{
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));const insight=result(state),data=snapshotSchema.parse(fixture.snapshot);data.insights=[insight];const {container}=render(<TemporalExperience data={data} mode="fan" insight={insight} onMoment={()=>{}}/>);expect(container.querySelectorAll('.state-vector .vector-metric')).toHaveLength(8);expect(screen.getByRole('button',{name:'SHOW ME WHAT CHANGED'})).toBeEnabled();expect(screen.getByRole('button',{name:'MATCH DNA'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'SHOW ME WHAT CHANGED'}));expect(screen.getByRole('dialog',{hidden:true})).toHaveTextContent('Truth chain');
});

it('rejected council preserves actual failure caveats without inventing a grounding failure',()=>{
 const insight={...result('REJECTED'),truth:result('COMPLETE').truth};insight.truth!.caveats=['Explanation agent unavailable; no findings fabricated.'];render(<InterpretationCouncil insight={insight}/>);expect(screen.getByText(/Explanation agent unavailable; no findings fabricated/)).toBeInTheDocument();expect(screen.queryByText(/rejected by grounding/i)).toBeNull();expect(screen.getAllByText('Validation · SUPPORTED')).toHaveLength(3);
});
