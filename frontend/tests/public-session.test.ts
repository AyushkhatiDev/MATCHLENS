import {it,expect,vi,afterEach} from 'vitest';
import {readFileSync} from 'node:fs';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
afterEach(()=>{vi.unstubAllEnvs();vi.unstubAllGlobals();sessionStorage.clear();});
it('connects the deployed frontend to its isolated API session without user-entered credentials',async()=>{
 vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','true');vi.stubEnv('NEXT_PUBLIC_API_BASE_URL','https://api.example');
 const request=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>({session_id:'a'.repeat(32)})}).mockResolvedValueOnce({ok:true,json:async()=>[{match_id:fixture.snapshot.match.match_id}]}).mockResolvedValueOnce({ok:true,json:async()=>fixture.snapshot});
 vi.stubGlobal('fetch',request);const api=await import('../lib/api');await api.loadMatch();
 expect(request.mock.calls[0]).toEqual(['https://api.example/api/sessions',{method:'POST',credentials:'include'}]);
 expect(api.base).toBe('https://api.example/sessions/'+'a'.repeat(32));
 expect(request.mock.calls[2][0]).toContain('/sessions/'+ 'a'.repeat(32) +'/api/matches/');
});
it('rejects invalid session identifiers before requesting match evidence',async()=>{
 vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','true');const request=vi.fn().mockResolvedValue({ok:true,json:async()=>({session_id:'../invalid'})});vi.stubGlobal('fetch',request);
 const api=await import('../lib/api');await expect(api.loadMatch()).rejects.toThrow('Invalid demo session');expect(request).toHaveBeenCalledTimes(1);
});
it('allocates a fresh session on explicit retry after the old session expires',async()=>{
 vi.resetModules();vi.stubEnv('NEXT_PUBLIC_DEMO_SESSIONS','true');vi.stubEnv('NEXT_PUBLIC_API_BASE_URL','https://api.example');
 let allocations=0,lookups=0;const request=vi.fn(async(url:string)=>{
  if(url.endsWith('/api/sessions'))return {ok:true,json:async()=>({session_id:(++allocations===1?'a':'b').repeat(32)})};
  if(url.endsWith('/api/matches')){if(url.includes('/'+ 'a'.repeat(32)+'/')&&++lookups>1)return {ok:false,status:404,json:async()=>({detail:'Demo session expired; reload to start a fresh session'})};return {ok:true,json:async()=>[{match_id:fixture.snapshot.match.match_id}]};}
  return {ok:true,json:async()=>fixture.snapshot};
 });vi.stubGlobal('fetch',request);const api=await import('../lib/api');await api.loadMatch();await expect(api.loadMatch()).rejects.toThrow();await expect(api.loadMatch()).resolves.toMatchObject({match:fixture.snapshot.match});expect(allocations).toBe(2);expect(api.base).toBe('https://api.example/sessions/'+'b'.repeat(32));
});
