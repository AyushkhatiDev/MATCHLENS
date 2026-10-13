import type {NextConfig} from 'next';

// Optional same-origin proxy: browsers then treat the API's session-ownership cookie as first-party,
// which Safari and private windows require when the frontend and API are on different sites.
export function apiOrigin(value=process.env.MATCHLENS_API_ORIGIN){
 if(!value)return null;
 const url=new URL(value);
 const loopback=url.protocol==='http:'&&['127.0.0.1','localhost'].includes(url.hostname);
 if((url.protocol!=='https:'&&!loopback)||url.username||url.password||url.search||url.hash||!['','/'].includes(url.pathname))
  throw Error('MATCHLENS_API_ORIGIN must be a bare HTTPS origin.');
 return url.origin;
}

const origin=apiOrigin();
const config:NextConfig={output:'standalone',poweredByHeader:false,
 ...(origin?{async rewrites(){return [{source:'/api/:path*',destination:`${origin}/api/:path*`},
                                       {source:'/sessions/:path*',destination:`${origin}/sessions/:path*`}];}}:{})};
export default config;
