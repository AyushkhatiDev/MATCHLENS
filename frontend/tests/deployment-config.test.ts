import {describe,expect,it} from 'vitest';
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
