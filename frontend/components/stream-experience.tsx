'use client';
import Link from 'next/link';
import {useCallback,useEffect,useState} from 'react';
import Experience from '@/components/experience';
import {startStreamRun,streamStatus} from '@/lib/api';
import {StreamStatus,fabricPresentation,runLabel} from '@/lib/streaming';

function time(value:string|null){if(!value)return '—';const date=new Date(value);return Number.isNaN(date.getTime())?'—':date.toISOString().slice(11,19)+' UTC';}

export function StreamPanel({status,error,busy,onStart}:{status:StreamStatus|null;error:string;busy:boolean;onStart:()=>void}){
 if(!status)return <section className="stream-panel" aria-label="Event Hubs stream"><p>{error||'Checking streaming status…'}</p></section>;
 if(!status.enabled)return <section className="stream-panel" aria-label="Event Hubs stream"><h2>EVENT HUBS STREAM · NOT ENABLED</h2><p>This deployment serves recorded replay only.</p><Link href="/demo">Open the recorded replay</Link></section>;
 const run=status.run;const fabric=fabricPresentation(run);
 return <section className="stream-panel" aria-label="Event Hubs stream">
  <h2>EVENT HUBS STREAM</h2>
  <p className="muted">Source: Azure Event Hubs · consumer group <code>{status.consumer_group}</code> · replay speed {status.speed}×</p>
  <dl>
   <dt>Run</dt><dd>{run?<code title={run.run_id}>{run.run_id.slice(0,8)}</code>:'—'} · {runLabel(run)}</dd>
   <dt>Consumer</dt><dd>{run?.consumer_ready?'CONNECTED':'NOT CONNECTED'}</dd>
   <dt>Published</dt><dd>{run?`${run.published} / ${run.expected_events}`:'—'}</dd>
   <dt>API received</dt><dd>{run?`${run.api_received} (${run.api_processed} processed)`:'—'}</dd>
   <dt>Processing errors</dt><dd>{run?run.processing_errors:'—'}</dd>
   <dt>Last event</dt><dd>{time(run?.last_event_at??null)}</dd>
   <dt>Fabric</dt><dd data-tone={fabric.tone}><strong>{fabric.label}</strong> · {fabric.detail}</dd>
  </dl>
  {run?.failure&&<p role="alert">Stream failed at {run.failure.stage}: {run.failure.reason}</p>}
  {error&&<p role="alert">{error}</p>}
  <button onClick={onStart} disabled={busy||status.active}>{status.active?'Streaming in progress':'Start synthetic match'}</button>
  <Link href="/demo">Recorded replay</Link>
 </section>;
}

export default function StreamExperience(){
 const [status,setStatus]=useState<StreamStatus|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const refresh=useCallback(async()=>{try{setStatus(await streamStatus());setError('');}catch{setError('Streaming status unavailable. The recorded replay remains available.');}},[]);
 useEffect(()=>{refresh();const timer=setInterval(refresh,3000);return()=>clearInterval(timer);},[refresh]);
 async function start(){setBusy(true);try{setStatus(await startStreamRun());setError('');}catch(failure){setError(failure instanceof Error?failure.message:'The synthetic match could not start.');}finally{setBusy(false);}}
 return <>
  <StreamPanel status={status} error={error} busy={busy} onStart={start}/>
  {status?.enabled&&<Experience stream/>}
 </>;
}
