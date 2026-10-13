import React from 'react';
import {it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,waitFor,act} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {snapshotSchema,Message} from '../lib/types';
import {applyMessage,parseMessage} from '../lib/realtime';
import {MotionPreference} from '../components/motion';
import {TemporalExperience} from '../components/temporal';
import Experience from '../components/experience';
const fixture=JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8'));
const snapshot=snapshotSchema.parse(fixture.snapshot);
vi.mock('../lib/api',()=>({base:'http://localhost:8000',loadMatch:vi.fn(async()=>structuredClone(snapshot)),control:vi.fn(async()=>undefined)}));
afterEach(()=>{vi.useRealTimers();vi.restoreAllMocks();vi.unstubAllGlobals();});
const reset=(sequence:number):Extract<Message,{type:'RESET'}>=>({type:'RESET',match_id:snapshot.match.match_id,timestamp:snapshot.state.simulation_time,sequence_number:sequence,trace_id:null,payload:{...structuredClone(snapshot),stream_cursor:sequence}});

it('rejects a RESET whose payload belongs to another match',()=>{
 const message=reset(snapshot.stream_cursor+1);message.payload.match.match_id='foreign';
 expect(parseMessage(JSON.stringify(message)).success).toBe(false);expect(applyMessage(snapshot,message)).toBe(snapshot);
});
it('rejects an event with a matching header and foreign payload identity',()=>{
 const message:Message={type:'EVENT_RECEIVED',match_id:snapshot.match.match_id,timestamp:1200,sequence_number:snapshot.stream_cursor+1,trace_id:null,payload:{event_id:'foreign',match_id:'foreign',timestamp:1200,event_type:'PASS'}};
 expect(parseMessage(JSON.stringify(message)).success).toBe(false);expect(applyMessage(snapshot,message)).toBe(snapshot);
});
it('does not roll back state for a stale or duplicate RESET on the same connection',()=>{
 expect(applyMessage(snapshot,reset(snapshot.stream_cursor))).toBe(snapshot);
 expect(applyMessage(snapshot,reset(Math.max(1,snapshot.stream_cursor-1)))).toBe(snapshot);
});
it.each([-1,1.5])('rejects an invalid stream cursor %s',sequence=>expect(parseMessage(JSON.stringify(reset(sequence))).success).toBe(false));
it('allows lower restart cursors only at a new transport connection',()=>{
 const message=reset(1);expect(applyMessage(snapshot,message,true).stream_cursor).toBe(1);
 const opening=reset(0);opening.payload.state.total_events=0;
 expect(applyMessage(snapshot,opening,false)).toBe(snapshot);
});
it('closes an inspector on an explicit reset even when clock and event count stay equal',()=>{
 vi.stubGlobal('fetch',vi.fn().mockRejectedValue(Error('offline')));
 const {rerender}=render(<TemporalExperience data={snapshot} mode="fan" insight={snapshot.insights[0]} onMoment={()=>{}}/>);
 fireEvent.click(screen.getByRole('button',{name:'SHOW ME WHAT CHANGED'}));expect(screen.getByRole('dialog',{hidden:true})).toBeInTheDocument();
 rerender(<TemporalExperience resetEpoch={1} data={snapshot} mode="fan" insight={snapshot.insights[0]} onMoment={()=>{}}/>);
 expect(screen.queryByRole('dialog',{hidden:true})).not.toBeInTheDocument();
});
it('keeps motion controls usable when browser storage reads are denied',()=>{
 vi.spyOn(Storage.prototype,'getItem').mockImplementation(()=>{throw new DOMException('denied','SecurityError');});
 expect(()=>render(<MotionPreference/>)).not.toThrow();expect(screen.getByRole('checkbox',{name:/Reduce motion/})).toBeEnabled();
});
it('keeps the reduced-motion override when persistence writes are denied',async()=>{
 vi.spyOn(Storage.prototype,'setItem').mockImplementation(()=>{throw new DOMException('denied','SecurityError');});
 const errors:Event[]=[];const receive=(e:Event)=>{errors.push(e);e.preventDefault();};window.addEventListener('error',receive);
 try{render(<MotionPreference/>);fireEvent.click(screen.getByRole('checkbox',{name:/Reduce motion/}));await waitFor(()=>expect(document.documentElement.dataset.motion).toBe('reduced'));expect(errors).toHaveLength(0);}
 finally{window.removeEventListener('error',receive);}
});

it('bounds failed transport retries, reconnects explicitly, and ignores a closed source',async()=>{
 class Events{
  static all:Events[]=[];
  onopen:(()=>void)|null=null;onerror:(()=>void)|null=null;
  onmessage:((event:{data:string})=>void)|null=null;
  close=vi.fn();constructor(){Events.all.push(this);}
 }
 vi.stubGlobal('EventSource',Events);
 render(<Experience demo/>);
 await screen.findByRole('heading',{name:snapshot.match.home.name});
 const first=Events.all[0];
 vi.useFakeTimers();act(()=>first.onerror?.());act(()=>vi.advanceTimersByTime(10000));
 expect(first.close).toHaveBeenCalledOnce();expect(screen.getByRole('button',{name:'Reconnect'})).toBeEnabled();
 vi.useRealTimers();fireEvent.click(screen.getByRole('button',{name:'Reconnect'}));
 await waitFor(()=>expect(Events.all).toHaveLength(2));
 act(()=>Events.all[1].onopen?.());expect(screen.getByText('CONNECTED')).toBeInTheDocument();
 act(()=>{first.onerror?.();first.onmessage?.({data:'invalid old frame'});});
 expect(screen.getByText('CONNECTED')).toBeInTheDocument();expect(screen.queryByText(/invalid update was rejected/)).not.toBeInTheDocument();
});
