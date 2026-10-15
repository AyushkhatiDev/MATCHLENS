import {snapshotSchema} from './types';
import {streamStatusSchema} from './streaming';
const root=process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000';
export let base=root;
let session:Promise<void>|null=null;
// Streamed view reads the shared Event Hubs-fed match; recorded view keeps its private session.
let streaming=false;let sessionBase:string|null=null;
function selectSource(stream:boolean){streaming=stream;base=stream?`${root}/stream`:(sessionBase||root);}
const storageKey=`matchlens.session:${root}`;
function storedSession(value?:string|null){
 try{
  if(value===undefined)return sessionStorage.getItem(storageKey);
  if(value===null)sessionStorage.removeItem(storageKey);else sessionStorage.setItem(storageKey,value);
 }catch{/* Storage denial only disables refresh reuse; ownership stays in HttpOnly cookies. */}
 return null;
}
async function prepareSession(){
 if(streaming||process.env.NEXT_PUBLIC_DEMO_SESSIONS!=='true')return;
 if(!session)session=(async()=>{const saved=storedSession();if(saved&&/^[a-f0-9]{32}$/.test(saved)){sessionBase=`${root}/sessions/${saved}`;if(!streaming)base=sessionBase;return;}const response=await fetch(`${root}/api/sessions`,{method:'POST',credentials:process.env.NEXT_PUBLIC_DEMO_SESSIONS==='true'?'include':'same-origin'});if(!response.ok)throw Error('Demo capacity unavailable');const data=await response.json();if(typeof data.session_id!=='string'||!/^[a-f0-9]{32}$/.test(data.session_id))throw Error('Invalid demo session');sessionBase=`${root}/sessions/${data.session_id}`;if(!streaming)base=sessionBase;storedSession(data.session_id);})().catch(error=>{session=null;throw error;});
 await session;
}
export async function loadMatch(id?:string,options?:{stream?:boolean}){
 selectSource(!!options?.stream);
 await prepareSession();
 const requestBase=base;
 async function read(path:string){
  const response=await fetch(`${requestBase}${path}`,{credentials:process.env.NEXT_PUBLIC_DEMO_SESSIONS==='true'?'include':'same-origin'});
  if(!response.ok){
   if([403,404].includes(response.status)&&process.env.NEXT_PUBLIC_DEMO_SESSIONS==='true'){
    const body=await response.json().catch(()=>({}));
    if(typeof body.detail==='string'&&(body.detail.startsWith('Demo session expired')||body.detail.startsWith('Demo session ownership required'))){
     if(base===requestBase){session=null;sessionBase=null;base=root;storedSession(null);}
     throw Error('Demo session expired or browser ownership unavailable. Retry connection to start a fresh session.');
    }
   }
   throw Error('Match unavailable');
  }
  return response;
 }
 if(!id){const response=await read('/api/matches');const matches: {match_id:string}[]=await response.json();id=matches[0]?.match_id;if(!id)throw Error('No live match');}
 const response=await read(`/api/matches/${encodeURIComponent(id)}`);return snapshotSchema.parse(await response.json());
}
export async function control(id:string,action:string,speed:number){const response=await fetch(`${base}/api/matches/${id}/demo`,{method:'POST',credentials:process.env.NEXT_PUBLIC_DEMO_SESSIONS==='true'?'include':'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,speed})});if(!response.ok)throw Error('Demo control failed');}
export async function streamStatus(){const response=await fetch(`${root}/api/stream/status`,{cache:'no-store'});if(!response.ok)throw Error('Streaming status unavailable');return streamStatusSchema.parse(await response.json());}
export async function startStreamRun(){const response=await fetch(`${root}/api/stream/runs`,{method:'POST'});if(!response.ok){const body=await response.json().catch(()=>({}));throw Error(typeof body.detail==='string'?body.detail:'The synthetic match could not start.');}return streamStatusSchema.parse(await response.json());}
