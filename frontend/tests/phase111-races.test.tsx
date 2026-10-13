import React from 'react';
import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,act} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {snapshotSchema} from '../lib/types';
import {TemporalExperience} from '../components/temporal';
import Experience from '../components/experience';
import {loadMatch} from '../lib/api';
const data=snapshotSchema.parse(JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8')).snapshot);
vi.mock('../lib/api',()=>({base:'http://localhost:8000',loadMatch:vi.fn(),control:vi.fn()}));
afterEach(()=>{vi.unstubAllGlobals();});
it.each([true,false])('invalidates a pending guide command at a same-clock reset (ok=%s)',async(ok)=>{
 let resolve!:(value:unknown)=>void;const pending=new Promise(r=>resolve=r);
 const request=vi.fn(async(_url:string,options?:RequestInit)=>options?.method==='POST'?pending:{ok:true,json:async()=>[{stage:4,title:'Post-goal response window',timestamp:1500,note:'actual'}]});
 vi.stubGlobal('fetch',request);
 const props={data,mode:'fan' as const,insight:data.insights[0],onMoment:()=>{}};
 const {rerender}=render(<TemporalExperience {...props}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Guide 5: Post-goal response window'}));
 rerender(<TemporalExperience {...props} resetEpoch={1}/>);
 await act(async()=>resolve({ok}));
 expect(screen.queryByText('Stage unavailable or already passed. Restart from the initial prior.')).not.toBeInTheDocument();
 expect(screen.queryByText(/Checkpoint unavailable|Processing real replay events/)).not.toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Guide 5: Post-goal response window'})).toBeEnabled();
});

it('distinguishes expired browser ownership from a failed backend',async()=>{
 const message='Demo session expired or browser ownership unavailable. Retry connection to start a fresh session.';
 vi.mocked(loadMatch).mockRejectedValueOnce(Error(message));
 render(<Experience demo/>);
 expect(await screen.findByText(message)).toBeInTheDocument();
 expect(screen.getByRole('button',{name:'Retry connection'})).toBeEnabled();
 expect(screen.queryByText(/Backend error/)).not.toBeInTheDocument();
});
