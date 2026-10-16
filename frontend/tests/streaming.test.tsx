import {afterEach,describe,expect,it,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {render,screen} from '@testing-library/react';
import {StreamPanel} from '../components/stream-experience';
import {StreamRun,StreamStatus,fabricPresentation,streamStatusSchema} from '../lib/streaming';

const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();sessionStorage.clear();});
const stage=(unique:number,extra:object={})=>({total:unique,unique,missing:0,missing_sample:[],duplicates:0,unexpected:0,altered:0,...extra});
function run(overrides:Partial<StreamRun>={}):StreamRun{
 return {run_id:'c'.repeat(32),state:'complete',started_at:'2026-10-16T10:00:00+00:00',finished_at:'2026-10-16T10:05:00+00:00',
  expected_events:1728,published:1728,publish_acknowledged:1728,api_received:1730,api_processed:1728,processing_errors:0,
  last_event_at:'2026-10-16T10:04:59+00:00',consumer_ready:false,fabric:'NOT CONFIGURED',reconciled:false,
  stages:{published:stage(1728),eventhub_readback:stage(1728,{total:1730,duplicates:2}),api_processed:stage(1728),fabric:null},failure:null,...overrides};
}
const enabled=(value:StreamRun|null,active=false):StreamStatus=>({enabled:true,source_mode:'eventhub',consumer_group:'matchlens-api',speed:20,active,run:value});

describe('Fabric status presentation',()=>{
 it('reports verified only after a reconciled run-scoped Fabric result',()=>{
  expect(fabricPresentation(run({fabric:'VERIFIED',reconciled:true,stages:{...run().stages!,fabric:stage(1728,{total:1730,duplicates:2})}})).label).toBe('FABRIC VERIFIED');
  expect(fabricPresentation(run({fabric:'VERIFIED',reconciled:false})).label).toBe('FABRIC NOT VERIFIED');
  expect(fabricPresentation(run({fabric:'TOPOLOGY VERIFIED'})).label).toBe('FABRIC PENDING');
  expect(fabricPresentation(run({fabric:'FAILED'})).label).toBe('FABRIC NOT VERIFIED');
  expect(fabricPresentation(run()).label).toBe('FABRIC NOT CHECKED');
  expect(fabricPresentation(null).label).toBe('FABRIC NOT CHECKED');
 });
 it('rejects malformed status payloads',()=>{
  expect(streamStatusSchema.safeParse({enabled:true,source_mode:'eventhub'}).success).toBe(false);
  expect(streamStatusSchema.safeParse(enabled(run({run_id:'not-a-run'}))).success).toBe(false);
  expect(streamStatusSchema.safeParse(enabled(run({fabric:'LIVE' as never}))).success).toBe(false);
 });
});

describe('StreamPanel',()=>{
 it('keeps recorded replay as the path when streaming is not enabled',()=>{
  render(<StreamPanel status={{enabled:false,source_mode:'local'}} error="" busy={false} onStart={()=>{}}/>);
  expect(screen.getByText('EVENT HUBS STREAM · NOT ENABLED')).toBeInTheDocument();
  expect(screen.getByRole('link',{name:'Open the recorded replay'})).toHaveAttribute('href','/demo');
  expect(screen.queryByRole('button')).toBeNull();
 });
 it('shows source, counts and an unverified Fabric state accurately',()=>{
  render(<StreamPanel status={enabled(run())} error="" busy={false} onStart={()=>{}}/>);
  expect(screen.getByText('matchlens-api')).toBeInTheDocument();
  expect(screen.getByText('1728 / 1728')).toBeInTheDocument();
  expect(screen.getByText('1730 (1728 processed)')).toBeInTheDocument();
  expect(screen.getByText('FABRIC NOT CHECKED')).toBeInTheDocument();
  expect(screen.queryByText('FABRIC VERIFIED')).toBeNull();
  expect(screen.getByRole('button',{name:'Start synthetic match'})).toBeEnabled();
 });
 it('disables starting while a run is active and surfaces failures',()=>{
  render(<StreamPanel status={enabled(run({state:'failed',failure:{stage:'consuming',reason:'API consumer stopped before receiving the whole run'}}),true)} error="" busy={false} onStart={()=>{}}/>);
  expect(screen.getByRole('button',{name:'Streaming in progress'})).toBeDisabled();
  expect(screen.getByRole('alert')).toHaveTextContent('Stream failed at consuming: API consumer stopped before receiving the whole run');
 });
});

describe('stream source selection',()=>{
 it('reads the shared stream without allocating a session, then restores the recorded session',async()=>{
  vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','true');vi.stubEnv('NEXT_PUBLIC_API_BASE_URL','https://web.example');
  const request=vi.fn(async(url:string)=>{
   if(url.endsWith('/api/sessions'))return {ok:true,json:async()=>({session_id:'a'.repeat(32)})};
   if(url.endsWith('/api/matches'))return {ok:true,json:async()=>[{match_id:fixture.snapshot.match.match_id}]};
   return {ok:true,json:async()=>fixture.snapshot};
  });
  vi.stubGlobal('fetch',request);const api=await import('../lib/api');
  await api.loadMatch(undefined,{stream:true});
  expect(api.base).toBe('https://web.example/stream');
  expect(request.mock.calls.map(c=>c[0])).toEqual(['https://web.example/stream/api/matches',`https://web.example/stream/api/matches/${fixture.snapshot.match.match_id}`]);
  await api.loadMatch();
  expect(api.base).toBe('https://web.example/sessions/'+'a'.repeat(32));
  await api.loadMatch(undefined,{stream:true});
  expect(api.base).toBe('https://web.example/stream');
  await api.loadMatch();
  expect(api.base).toBe('https://web.example/sessions/'+'a'.repeat(32));
  expect(request.mock.calls.filter(c=>c[0].endsWith('/api/sessions'))).toHaveLength(1);
 });
 it('starts a run and reports the server reason when one is refused',async()=>{
  vi.resetModules();vi.stubEnv('NEXT_PUBLIC_API_BASE_URL','https://web.example');
  const request=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>enabled(run({state:'publishing',published:10}),true)})
   .mockResolvedValueOnce({ok:false,status:409,json:async()=>({detail:'A synthetic match is already streaming; watch it or wait for it to finish'})});
  vi.stubGlobal('fetch',request);const api=await import('../lib/api');
  await expect(api.startStreamRun()).resolves.toMatchObject({active:true});
  expect(request.mock.calls[0]).toEqual(['https://web.example/api/stream/runs',{method:'POST'}]);
  await expect(api.startStreamRun()).rejects.toThrow('already streaming');
 });
});
