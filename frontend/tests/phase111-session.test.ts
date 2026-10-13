import {it,expect,vi,afterEach} from 'vitest';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8')).snapshot;
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();vi.restoreAllMocks();sessionStorage.clear();});
function setup(){vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','true');vi.stubEnv('NEXT_PUBLIC_API_BASE_URL','https://api.example');}
const response=(data:unknown)=>({ok:true,json:async()=>data});
it('reuses the public identifier on refresh without putting owner secrets in JavaScript storage',async()=>{
 setup();let allocations=0;
 const fetch=vi.fn(async(url:string,options?:RequestInit)=>{
  expect(options?.credentials).toBe('include');
  if(url.endsWith('/api/sessions')){allocations++;return response({session_id:'a'.repeat(32)});}
  return response(url.endsWith('/api/matches')?[{match_id:fixture.match.match_id}]:fixture);
 });vi.stubGlobal('fetch',fetch);
 await (await import('../lib/api')).loadMatch();vi.resetModules();await (await import('../lib/api')).loadMatch();
 expect(allocations).toBe(1);expect(sessionStorage.getItem('matchlens.session:https://api.example')).toBe('a'.repeat(32));
});
it('shares one allocation across simultaneous load requests',async()=>{
 setup();let allocations=0;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>url.endsWith('/api/sessions')?(allocations++,response({session_id:'b'.repeat(32)})):response(url.endsWith('/api/matches')?[{match_id:fixture.match.match_id}]:fixture)));
 const api=await import('../lib/api');await Promise.all([api.loadMatch(),api.loadMatch(),api.loadMatch()]);expect(allocations).toBe(1);
});
it.each([403,404])('does not reuse an expired or ownerless identifier after %s',async status=>{
 setup();sessionStorage.setItem('matchlens.session:https://api.example','a'.repeat(32));let allocations=0;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.includes('/sessions/'+ 'a'.repeat(32)))return {ok:false,status,json:async()=>({detail:status===403?'Demo session ownership required; reconnect with the original browser':'Demo session expired; reload to start a fresh session'})};
  if(url.endsWith('/api/sessions')){allocations++;return response({session_id:'b'.repeat(32)});}
  return response(url.endsWith('/api/matches')?[{match_id:fixture.match.match_id}]:fixture);
 }));const api=await import('../lib/api');await expect(api.loadMatch()).rejects.toThrow(/browser ownership/);await expect(api.loadMatch()).resolves.toMatchObject({match:fixture.match});expect(allocations).toBe(1);
});
it('does not allocate a new child for an unrelated match404',async()=>{
 setup();let allocations=0;
 vi.stubGlobal('fetch',vi.fn(async(url:string)=>{
  if(url.endsWith('/api/sessions')){allocations++;return response({session_id:'a'.repeat(32)});}
  if(url.includes('/missing'))return {ok:false,status:404,json:async()=>({detail:'Match not found'})};
  return response(url.endsWith('/api/matches')?[{match_id:fixture.match.match_id}]:fixture);
 }));const api=await import('../lib/api');await expect(api.loadMatch('missing')).rejects.toThrow();await api.loadMatch();expect(allocations).toBe(1);
});
it('uses the plain local profile without cross-origin credentials',async()=>{
 vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','false');
 const fetch=vi.fn(async(url:string,_options?:RequestInit)=>response(url.endsWith('/api/matches')?[{match_id:fixture.match.match_id}]:fixture));vi.stubGlobal('fetch',fetch);
 await (await import('../lib/api')).loadMatch();expect(fetch.mock.calls[0][1]).toEqual({credentials:'same-origin'});
});
