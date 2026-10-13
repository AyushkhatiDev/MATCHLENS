import {describe,expect,it} from 'vitest';
import {apiOrigin} from '../next.config';

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
});
