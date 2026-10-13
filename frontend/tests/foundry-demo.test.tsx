import React from 'react';
import {it,expect} from 'vitest';
import {render,screen} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {insightSchema} from '../lib/types';
import {ExecutionTrace,InterpretationCouncil} from '../components/intelligence-canvas';
import {ChangePanel} from '../components/temporal';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
const execution={mode:'foundry',status:'FOUNDRY LIVE',state:'COMPLETE',trace_id:'actual-trace',fallback:false,steps:[{stage:'Grounding validation',status:'OK',duration_ms:12.5}]};
it('renders verified execution, actual timings and measured/interpreted separation',()=>{
 const insight=insightSchema.parse({...fixture.insight,execution});
 render(<ChangePanel insight={insight} mode="analyst"/>);
 expect(screen.getByText('FOUNDRY LIVE')).toBeInTheDocument();
 expect(screen.getByText('MEASURED BY MATCHLENS')).toBeInTheDocument();
 expect(screen.getByText('INTERPRETED BY FOUNDRY')).toBeInTheDocument();
 expect(screen.getByText('12.5 ms')).toBeInTheDocument();
});
it.each(['PENDING','UNAVAILABLE','REJECTED'] as const)('cannot display live for %s fixture',state=>{
 const insight=insightSchema.parse({...fixture.insight,agent_explanation:null,narrative:null,truth:null,execution:{...execution,state,status:state==='REJECTED'?'FOUNDRY RESULT REJECTED':'FOUNDRY UNAVAILABLE'}});
 render(<ExecutionTrace insight={insight}/>);
 expect(screen.queryByText('FOUNDRY LIVE')).not.toBeInTheDocument();
 expect(screen.getByRole('status')).toHaveTextContent(state==='PENDING'?'PENDING':state==='REJECTED'?'rejected':'unavailable');
});
it('renders actual council validation states and pending findings without invention',()=>{
 const insight=insightSchema.parse({...fixture.insight,truth:null,execution:{...execution,state:'PENDING',status:'FOUNDRY UNAVAILABLE'}});
 render(<InterpretationCouncil insight={insight}/>);
 expect(screen.getAllByText('Validation · PENDING')).toHaveLength(3);
 expect(screen.getAllByText('No specialist finding supplied.')).toHaveLength(3);
});
it('mock executions remain explicitly offline',()=>{
 const insight=insightSchema.parse({...fixture.insight,execution:{...execution,mode:'mock',status:'MOCK'}});
 render(<ExecutionTrace insight={insight}/>);
 expect(screen.getByText('MOCK')).toBeInTheDocument();
 expect(screen.queryByText('FOUNDRY LIVE')).not.toBeInTheDocument();
});
