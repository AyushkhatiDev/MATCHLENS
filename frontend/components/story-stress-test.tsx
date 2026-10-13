'use client';
import {useEffect,useRef,useState} from 'react';
import {ArrowRight,RotateCcw} from 'lucide-react';
import {base} from '@/lib/api';
import {Point,Sensitivity,sequenceSchema} from '@/lib/temporal';
import {Snapshot,FootballEvent} from '@/lib/types';
import {clock,label,number} from '@/lib/format';
import {parseChoices,parseSensitivity,parseSources} from '@/lib/stress-test';
import {SourceEventButton,SourceEventInspector} from './intelligence-canvas';

type Choices=ReturnType<typeof sequenceSchema.parse>;
const DEMO_SEQUENCE='37a664fc-86cf-4c2f-b07f-44f6afe57b27';
const CAVEAT='Model sensitivity only. Later events are held fixed; the match is not resimulated. The scoreline is not recalculated.';
const value=(n:number|null,unit:string)=>n===null?'Unavailable':`${number(n)} ${unit}`;
const delta=(n:number|null,unit:string)=>n===null?'Unavailable':`${n>0?'+':''}${number(n)} ${unit==='%'?'percentage points':unit}`;

export function StoryStressTest({data,point,selectionKey,resetEpoch=0,onSource}:{
  data:Snapshot;point:Point|null;selectionKey:string;resetEpoch?:number;onSource?:(events:FootballEvent[])=>void;
}){
  const [pinnedIdentity,setPinnedIdentity]=useState('');
  const [pinned,setPinned]=useState<Point|null>(null),[choices,setChoices]=useState<Choices|null>(null);
  const [selected,setSelected]=useState(''),[result,setResult]=useState<Sensitivity|null>(null);
  const [busy,setBusy]=useState(false),[status,setStatus]=useState(''),[error,setError]=useState('');
  const [sources,setSources]=useState<FootballEvent[]>([]),[source,setSource]=useState<FootballEvent|null>(null);
  const [sourceStatus,setSourceStatus]=useState(''),[sourcesBusy,setSourcesBusy]=useState(false);
  const heading=useRef<HTMLHeadingElement>(null),opener=useRef<HTMLButtonElement>(null);
  const request=useRef(0),sourceRequest=useRef(0),controller=useRef<AbortController|null>(null),sourceController=useRef<AbortController|null>(null);
  const identity=`${base}/${data.match.match_id}/${resetEpoch}/${selectionKey}`;
  // The render-time identity check also protects the brief interval before effects
  // run on reset. Separate tokens let source inspection and calculation coexist.
  const liveIdentity=useRef(identity);liveIdentity.current=identity;
  const prior=useRef({time:data.state.simulation_time,count:data.state.total_events});
  function invalidate(){request.current++;sourceRequest.current++;controller.current?.abort();sourceController.current?.abort();}
  function clear(){if(source)onSource?.([]);invalidate();setPinned(null);setChoices(null);setSelected('');setResult(null);setSources([]);setSource(null);setBusy(false);setSourcesBusy(false);setError('');setSourceStatus('');}
  useEffect(()=>{clear();setStatus('');},[identity]); // explicit selection/reset only; not a moving replay clock
  useEffect(()=>{
    if(data.state.simulation_time<prior.current.time||data.state.total_events<prior.current.count){clear();setStatus('Replay reset. Select a published checkpoint to test again.');}
    prior.current={time:data.state.simulation_time,count:data.state.total_events};
  },[data.state.simulation_time,data.state.total_events]);
  useEffect(()=>()=>invalidate(),[]);

  async function get(path:string,signal:AbortSignal){
    let response:Response;
    try{response=await fetch(`${base}${path}`,{signal,credentials:base.includes('/sessions/')?'include':'same-origin'});}
    catch{throw Error('Request unavailable. Retry when the API is ready.');}
    if(!response.ok){
      const body=await response.json().catch(()=>({}));
      if([403,404].includes(response.status)&&typeof body.detail==='string'&&(body.detail.startsWith('Demo session expired')||body.detail.startsWith('Demo session ownership required')))throw Error('Demo session expired. Reload to start a fresh session.');
      if(response.status===409)throw Error('Replay reset. Select a published checkpoint to test again.');
      if(response.status===404)throw Error('Sequence unavailable at this checkpoint. Reload sequences and retry.');
      throw Error('Request unavailable. Retry when the API is ready.');
    }
    return response.json();
  }
  const message=(e:unknown)=>e instanceof Error&&/^(Demo session expired|Replay reset|Sequence unavailable|Request unavailable)/.test(e.message)?e.message:'Invalid or inconsistent response rejected. The original reading is unchanged.';
  function fail(e:unknown){const text=message(e);if(/^(Demo session expired|Replay reset)/.test(text))clear();setError(text);setStatus('');}
  async function load(target:Point){
    if(source)onSource?.([]);
    invalidate();const token=request.current,origin=identity,abort=new AbortController();controller.current=abort;
    setPinnedIdentity(identity);setPinned(target);setChoices(null);setSelected('');setResult(null);setSources([]);setSource(null);setSourceStatus('');setSourcesBusy(false);setBusy(true);setError('');setStatus('Loading observed sequences…');
    try{
      const parsed=parseChoices(await get(`/api/matches/${encodeURIComponent(data.match.match_id)}/sequences?timestamp=${target.timestamp}`,abort.signal),target);
      if(token!==request.current||origin!==liveIdentity.current)return;
      setChoices(parsed);
      setSelected(target.timestamp===1200&&parsed.sequences.some(c=>c.sequence_id===DEMO_SEQUENCE)?DEMO_SEQUENCE:parsed.sequences[0]?.sequence_id||'');
      setStatus(parsed.sequences.length?'Select an observed sequence to test.':'No eligible observed sequences at this checkpoint.');
      heading.current?.focus();
    }catch(e){if(token===request.current&&origin===liveIdentity.current)fail(e);}
    finally{if(token===request.current&&origin===liveIdentity.current)setBusy(false);}
  }
  function choose(id:string){if(source)onSource?.([]);invalidate();setSelected(id);setResult(null);setSources([]);setSource(null);setSourceStatus('');setSourcesBusy(false);setBusy(false);setError('');setStatus('Sequence selected. The original reading is unchanged.');}
  async function calculate(){
    if(!pinned||!selected)return;
    controller.current?.abort();const token=++request.current,origin=identity,abort=new AbortController();controller.current=abort;
    const cutoff=pinned,id=selected;setBusy(true);setResult(null);setError('');setStatus('Calculating with the selected sequence omitted…');
    try{
      const parsed=parseSensitivity(await get(`/api/matches/${encodeURIComponent(data.match.match_id)}/sensitivity?sequence_id=${encodeURIComponent(id)}&timestamp=${cutoff.timestamp}`,abort.signal),cutoff,id);
      if(token!==request.current||origin!==liveIdentity.current)return;
      setResult(parsed);setStatus(parsed.observed_state===parsed.altered_state?'Classification unchanged for this tested omission.':`Classification changes from ${label(parsed.observed_state)} to ${label(parsed.altered_state)} for this tested omission.`);
    }catch(e){if(token===request.current&&origin===liveIdentity.current)fail(e);}
    finally{if(token===request.current&&origin===liveIdentity.current)setBusy(false);}
  }
  async function inspectSources(){
    if(!pinned||!selected)return;
    if(source)onSource?.([]);
    sourceController.current?.abort();const token=++sourceRequest.current,origin=identity,abort=new AbortController();sourceController.current=abort;
    const cutoff=pinned.timestamp,id=selected,mid=data.match.match_id;setSourcesBusy(true);setSourceStatus('Loading source records…');setSources([]);setSource(null);
    try{
      const records:FootballEvent[]=[];let offset=0,total=0;
      for(let page=0;page<5;page++){
        const parsed=parseSources(await get(`/api/matches/${encodeURIComponent(mid)}/sequences/${encodeURIComponent(id)}/events?timestamp=${cutoff}&offset=${offset}&limit=200`,abort.signal),mid,cutoff,id,offset);
        if(token!==sourceRequest.current||origin!==liveIdentity.current)return;
        if(page&&parsed.total!==total)throw Error('Inconsistent response');
        total=parsed.total;records.push(...parsed.events);
        if(new Set(records.map(e=>e.event_id)).size!==records.length)throw Error('Inconsistent response');
        if(parsed.next_offset===null)break;offset=parsed.next_offset;
      }
      setSources(records);setSourceStatus(records.length===total?`${records.length} actual source records available.`:`Showing ${records.length} of ${total} source records; remaining records unavailable in this bounded view.`);
    }catch(e){if(token===sourceRequest.current&&origin===liveIdentity.current){if(/^(Demo session expired|Replay reset)/.test(message(e)))fail(e);else setSourceStatus(`Source records unavailable. ${message(e)}`);}}
    finally{if(token===sourceRequest.current&&origin===liveIdentity.current)setSourcesBusy(false);}
  }
  const choice=choices?.sequences.find(c=>c.sequence_id===selected);
  const team=(id:string|null)=>[data.match.home,data.match.away].find(t=>t.team_id===id)?.short_name||id||'Team not supplied';
  const visible=pinned&&pinned.timestamp<=data.state.simulation_time&&pinnedIdentity===identity;
  function restore(){clear();setStatus('Original reading restored. Canonical match data was not changed.');opener.current?.focus();onSource?.([]);}
  return <section className="story-stress-test" aria-label="Story Stress Test">
    <div className="stress-entry"><div><span className="eyebrow">TEST THE MEASURED READING</span><p>{pinned?`Pinned published checkpoint ${clock(pinned.timestamp)}`:point?`Published checkpoint ${clock(point.timestamp)} · ${label(point.state)}`:'A published checkpoint is needed before testing.'}</p>{pinned&&point&&point.timestamp!==pinned.timestamp&&<small>Selected reading {clock(point.timestamp)}. Start a new test to replace the pinned calculation.</small>}</div><button ref={opener} className="stress-primary" disabled={!point||busy} aria-expanded={!!visible} aria-controls="stress-panel" onClick={()=>point&&void load(point)}>Test this reading <ArrowRight size={16}/></button></div>
    {visible&&<div id="stress-panel" className="stress-panel"><div className="section-heading"><h2 ref={heading} tabIndex={-1}>Story Stress Test</h2><button onClick={restore}><RotateCcw size={14}/> Restore original</button></div>
      <p className="stress-cutoff">Calculation pinned at <b>{clock(pinned.timestamp)}</b> · five-minute window · replay clock {clock(data.state.simulation_time)}</p>
      <p className="sensitivity-caveat">{CAVEAT}</p><p className="muted">Original events, score, explanation and Foundry result remain unchanged. This calculation has no agent interpretation or agent validation.</p>
      <button disabled={busy} onClick={()=>void load(pinned)}>Reload sequences</button>
      {choices&&!!choices.sequences.length&&<div className="stress-actions"><label>Observed possession sequence<select aria-label="Observed possession sequence" disabled={busy} value={selected} onChange={e=>choose(e.target.value)}>{choices.sequences.map(c=><option key={c.sequence_id} value={c.sequence_id}>{team(c.team_id)} · {clock(c.start)}–{clock(c.end)} · {c.events} events in window</option>)}</select></label><button disabled={!selected||busy} onClick={()=>void calculate()}>{busy?'Calculating…':'Omit sequence from calculation'}</button><button disabled={!selected||sourcesBusy} onClick={()=>void inspectSources()}>Inspect sequence source records</button></div>}
      {choice&&<details className="stress-identifiers"><summary>Selected sequence identifier</summary><code>{choice.sequence_id}</code><p>Window segment {clock(choice.start)}–{clock(choice.end)}. All observed events carrying this ID by the cutoff are omitted, including any earlier segment.</p></details>}
      <p role="status" aria-atomic="true">{status}</p>{error&&<p role="alert">{error}</p>}
      {result&&<div className="stress-result" aria-label="Model sensitivity result"><div className="stress-readings"><article><small>OBSERVED READING · {clock(result.timestamp)}</small><strong>{label(result.observed_state)}</strong></article><article><small>SEQUENCE OMITTED · {clock(result.timestamp)}</small><strong>{label(result.altered_state)}</strong></article></div>
        <StressMetrics result={result} names={['Control','Pressure','Territory']} home={data.match.home.short_name} away={data.match.away.short_name}/>
        <details><summary>All eight supplied dimensions</summary><StressMetrics result={result} home={data.match.home.short_name} away={data.match.away.short_name}/></details>
        <details><summary>Removed source events ({result.removed_event_ids.length})</summary>{result.removed_event_ids.map(id=><p key={id}><code>{id}</code>{!sources.some(e=>e.event_id===id)&&' · Source record not loaded'}</p>)}</details><p className="muted">{result.method}</p></div>}
      <p role="status">{sourceStatus}</p>{source?<SourceEventInspector event={source} data={data} backLabel="Back to sequence" onBack={()=>{setSource(null);onSource?.([]);requestAnimationFrame(()=>document.getElementById(`stress-source-${source.event_id}`)?.querySelector('button')?.focus());}}/>:<div className="source-ledger">{sources.map(e=><div key={e.event_id} id={`stress-source-${e.event_id}`}><SourceEventButton event={e} data={data} onClick={()=>{setSource(e);onSource?.([e]);}}/></div>)}</div>}
    </div>}
    {!visible&&status&&<p role="status">{status}</p>}
    {!visible&&error&&<p role="alert">{error}</p>}
  </section>;
}

function StressMetrics({result,names,home,away}:{result:Sensitivity;names?:string[];home:string;away:string}){
  return <div className="stress-metrics">{result.observed.filter(d=>!names||names.includes(d.name)).map(d=>{
    const altered=result.without_sequence.find(a=>a.name===d.name)!,change=result.deltas.find(a=>a.name===d.name)!;
    return <article key={d.name}><h3>{d.name} <small>{d.unit}</small></h3>{(['home','away'] as const).map(side=><div className="stress-metric-row" key={side}><span>{side==='home'?home:away}</span><div><small>Observed</small><b>{value(d[side],d.unit)}</b></div><div><small>Omitted</small><b>{value(altered[side],d.unit)}</b></div><div><small>Change</small><span>{delta(change[side],d.unit)}</span></div></div>)}<details><summary>How measured</summary><p>{d.formula}</p></details></article>;
  })}</div>;
}
