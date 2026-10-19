import {describe,expect,it,vi} from 'vitest';
import {getPathMatch} from 'next/dist/shared/lib/router/utils/path-match';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {apiOrigin} from '../next.config';

const dockerfile=readFileSync(resolve(process.cwd(),'Dockerfile'),'utf8');

describe('same-origin API proxy configuration',()=>{
 it('is disabled unless an API origin is configured',()=>{expect(apiOrigin('')).toBeNull();expect(apiOrigin(undefined)).toBeNull();});
 it('accepts bare HTTPS origins and loopback HTTP for local verification',()=>{
  expect(apiOrigin('https://matchlens-api.example.azurecontainerapps.io/')).toBe('https://matchlens-api.example.azurecontainerapps.io');
  expect(apiOrigin('http://127.0.0.1:8000')).toBe('http://127.0.0.1:8000');
 });
 it('rejects plaintext remote origins, paths and embedded credentials',()=>{
  for(const value of ['http://matchlens-api.example.com','https://matchlens-api.example.com/api','https://user:secret@matchlens-api.example.com','https://matchlens-api.example.com/?token=x'])
   expect(()=>apiOrigin(value)).toThrow('MATCHLENS_API_ORIGIN must be a bare HTTPS origin.');
 });
 it('passes all production API settings into the Next.js build stage',()=>{
  const build=dockerfile.slice(0,dockerfile.indexOf('FROM node:24-alpine',1));
  expect(build).toMatch(/^ARG MATCHLENS_API_ORIGIN(?:=.*)?$/m);
  expect(build).toMatch(/^ENV MATCHLENS_API_ORIGIN=\$MATCHLENS_API_ORIGIN$/m);
  expect(build).toMatch(/^ARG NEXT_PUBLIC_API_BASE_URL=/m);
  expect(build).toMatch(/^ENV NEXT_PUBLIC_API_BASE_URL=\$NEXT_PUBLIC_API_BASE_URL/m);
  expect(build).toMatch(/^ARG NEXT_PUBLIC_DEMO_SESSIONS=/m);
  expect(build).toMatch(/^ENV NEXT_PUBLIC_DEMO_SESSIONS=\$NEXT_PUBLIC_DEMO_SESSIONS$/m);
  expect(build).toContain('RUN npm run build');
 });
});

describe('production same-origin proxy routing',()=>{
 type Rewrite={source:string;destination:string};
 async function rewrites(){
  vi.resetModules();vi.stubEnv('MATCHLENS_API_ORIGIN','https://api.example');
  const config=(await import('../next.config')).default as {rewrites:()=>Promise<Rewrite[]>};
  vi.unstubAllEnvs();
  return config.rewrites();
 }
 // Next.js's own matcher for rewrite sources; returns the proxied URL or null when Next.js serves the path.
 async function proxied(pathname:string){
  for(const rule of await rewrites()){
   const params=getPathMatch(rule.source,{removeUnnamedParams:true,strict:true})(pathname);
   if(params)return rule.destination.replace(':path*',[params.path??[]].flat().join('/')).replace(/\/$/,'');
  }
  return null;
 }
 it('forwards the shared stream data routes to the API origin',async()=>{
  expect(await proxied('/stream/api/matches')).toBe('https://api.example/stream/api/matches');
  expect(await proxied('/stream/api/matches/d67c4725-2894-4d6c-8aca-b5d0c169b0e4')).toBe('https://api.example/stream/api/matches/d67c4725-2894-4d6c-8aca-b5d0c169b0e4');
  for(const path of ['matches/m/stream','matches/m/guide','matches/m/sequences','matches/m/sensitivity','matches/m/sequences/s/events','data-plane','comparison'])
   expect(await proxied(`/stream/api/${path}`)).toBe(`https://api.example/stream/api/${path}`);
 });
 it('keeps the /stream page and the recorded experience on Next.js',async()=>{
  for(const page of ['/stream','/demo','/','/analyst','/broadcast','/settings','/match/m','/player/p','/streaming','/stream/other'])expect(await proxied(page)).toBeNull();
 });
 it('leaves the existing API and session proxy routes unchanged',async()=>{
  expect(await proxied('/api/sessions')).toBe('https://api.example/api/sessions');
  expect(await proxied('/api/stream/status')).toBe('https://api.example/api/stream/status');
  expect(await proxied('/sessions/'+'a'.repeat(32)+'/api/matches')).toBe('https://api.example/sessions/'+'a'.repeat(32)+'/api/matches');
  expect((await rewrites()).map(r=>r.source)).toEqual(['/api/:path*','/sessions/:path*','/stream/api/:path*']);
 });
});
