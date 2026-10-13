import React from 'react';
import {describe,it,expect,vi,afterEach} from 'vitest';
import {render,screen,fireEvent,waitFor,within,act} from '@testing-library/react';
import {readFileSync} from 'node:fs';
import {snapshotSchema} from '../lib/types';
import {pointSchema} from '../lib/temporal';
import {parseChoices,parseSensitivity,parseSources} from '../lib/stress-test';
import {StoryStressTest} from '../components/story-stress-test';
import Experience from '../components/experience';
const fixture=JSON.parse(readFileSync('tests/fixtures/stress-test.json','utf8'));
const point=pointSchema.parse(fixture.point);
const snapshot=snapshotSchema.parse(JSON.parse(readFileSync('tests/fixtures/temporal.json','utf8')).snapshot);
const data={...snapshot,state:{...snapshot.state,simulation_time:1274.43}};
const props={data,point,selectionKey:'current'};
vi.mock('../lib/api',()=>({base:'http://localhost:8000',loadMatch:vi.fn(),control:vi.fn()}));
afterEach(()=>vi.unstubAllGlobals());
function response(value:unknown){return {ok:true,json:async()=>value};}
function mockFetch(result=fixture.flip){
  const fetch=vi.fn().mockImplementation((url:string)=>Promise.resolve(response(url.includes('/sensitivity?')?result:url.includes('/events?')?fixture.sources:fixture.choices)));
  vi.stubGlobal('fetch',fetch);return fetch;
}
async function open(){fireEvent.click(screen.getByRole('button',{name:'Test this reading'}));await screen.findByRole('combobox',{name:'Observed possession sequence'});}
function omit(){fireEvent.click(screen.getByRole('button',{name:'Omit sequence from calculation'}));}
function deferred(){let resolve!:(value:unknown)=>void;const promise=new Promise(r=>resolve=r);return {resolve,promise};}

describe('Story Stress Test',()=>{
  it('requires a published checkpoint and does not invent measurements',()=>{
    const fetch=mockFetch();render(<StoryStressTest {...props} point={null}/>);
    expect(screen.getByRole('button',{name:'Test this reading'})).toBeDisabled();expect(fetch).not.toHaveBeenCalled();
  });
  it('computes the real 20:00 flip, shows supplied units, and restores without mutating the snapshot',async()=>{
    const fetch=mockFetch(),before=JSON.stringify(data);render(<StoryStressTest {...props}/>);await open();
    expect(screen.getByRole('combobox')).toHaveValue(fixture.flip.sequence_id);
    expect(screen.getByRole('combobox')).toHaveTextContent('IHU · 16:51–17:35 · 10 events');
    omit();await screen.findByText(/Classification changes from Home control to High pressure/);
    expect(fetch.mock.calls.at(-1)![0]).toContain('timestamp=1200');
    const result=screen.getByLabelText('Model sensitivity result');
    expect(within(result).getByText('High pressure')).toBeInTheDocument();
    expect(within(result).getAllByText('64.2 %').length).toBeGreaterThan(0);
    expect(within(result).getAllByText('+2.7 percentage points').length).toBeGreaterThan(0);
    expect(within(result).getAllByText('-19.2 index').length).toBeGreaterThan(0);
    expect(screen.getByText(/replay clock 21:14/)).toBeInTheDocument();
    expect(result.textContent).not.toMatch(/FOUNDRY LIVE|GROUNDED|SUPPORTED|ACCEPTED|PASSED/);
    fireEvent.click(screen.getByRole('button',{name:'Restore original'}));
    expect(screen.queryByLabelText('Model sensitivity result')).not.toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Test this reading'})).toHaveFocus();expect(JSON.stringify(data)).toBe(before);
  });
  it('keeps the pinned cutoff while replay publishes a new checkpoint',async()=>{
    const fetch=mockFetch();const {rerender}=render(<StoryStressTest {...props}/>);await open();
    rerender(<StoryStressTest {...props} point={{...point,timestamp:1500}} data={{...data,state:{...data.state,simulation_time:1500,total_events:data.state.total_events+1}}}/>);
    omit();await screen.findByText(/Classification changes/);
    expect(fetch.mock.calls.at(-1)![0]).toContain('timestamp=1200');expect(screen.getByText(/replay clock 25:00/)).toBeInTheDocument();
  });
  it('reports an unchanged classification without claiming general robustness',async()=>{
    mockFetch(fixture.unchanged);render(<StoryStressTest {...props}/>);await open();
    fireEvent.change(screen.getByRole('combobox'),{target:{value:fixture.unchanged.sequence_id}});omit();
    await screen.findByText('Classification unchanged for this tested omission.');
    expect(screen.getByLabelText('Model sensitivity result').textContent).not.toMatch(/robust|guaranteed/i);
  });
  it.each(['selection','reset','rewind'])('ignores a late calculation after %s',async(kind)=>{
    const pending=deferred(),fetch=mockFetch();const {rerender}=render(<StoryStressTest {...props}/>);await open();
    fetch.mockImplementationOnce(()=>pending.promise);omit();
    if(kind==='selection')rerender(<StoryStressTest {...props} selectionKey="checkpoint/600" point={{...point,timestamp:600}}/>);
    if(kind==='reset')rerender(<StoryStressTest {...props} resetEpoch={1}/>);
    if(kind==='rewind')rerender(<StoryStressTest {...props} data={{...data,state:{...data.state,simulation_time:0,total_events:0}}} point={null}/>);
    await act(async()=>pending.resolve(response(fixture.flip)));
    expect(screen.queryByLabelText('Model sensitivity result')).not.toBeInTheDocument();
  });
  it('ignores old sequence lookup after restore',async()=>{
    const pending=deferred();vi.stubGlobal('fetch',vi.fn(()=>pending.promise));render(<StoryStressTest {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:'Test this reading'}));
    fireEvent.click(screen.getByRole('button',{name:'Restore original'}));await act(async()=>pending.resolve(response(fixture.choices)));
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
  it('ignores old source records after selecting another sequence',async()=>{
    const fetch=mockFetch(),pending=deferred();render(<StoryStressTest {...props}/>);await open();
    fetch.mockImplementationOnce(()=>pending.promise);fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));
    fireEvent.change(screen.getByRole('combobox'),{target:{value:fixture.unchanged.sequence_id}});
    await act(async()=>pending.resolve(response(fixture.sources)));
    expect(screen.queryByRole('button',{name:/Inspect Pass/})).not.toBeInTheDocument();
  });
  it('inspects actual historical sources absent from the recent snapshot and returns focus',async()=>{
    mockFetch();render(<StoryStressTest {...props} data={{...data,events:[]}}/>);await open();
    fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));await screen.findByText('10 actual source records available.');
    const button=screen.getAllByRole('button',{name:/Inspect .* · .* · .* ·/})[0];fireEvent.click(button);
    expect(screen.getByRole('button',{name:'← Back to sequence'})).toHaveFocus();
    fireEvent.click(screen.getByText('VIEW RAW EVENT'));
    expect(screen.getByText(/"possession_sequence_id"/)).toHaveTextContent(fixture.flip.sequence_id);
    fireEvent.click(screen.getByRole('button',{name:'← Back to sequence'}));
    await waitFor(()=>expect(screen.getAllByRole('button',{name:/Inspect .* · .* · .* ·/})[0]).toHaveFocus());
  });
  it('retains removed IDs when source loading fails',async()=>{
    const fetch=mockFetch();render(<StoryStressTest {...props}/>);await open();omit();await screen.findByLabelText('Model sensitivity result');
    fetch.mockRejectedValueOnce(Error('offline'));fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));
    await screen.findByText(/Source records unavailable/);expect(screen.getByText('Removed source events (10)')).toBeInTheDocument();
  });
  it.each(['selection','reload','sources'])('clears a source focus when changing %s',async(kind)=>{
    mockFetch();const onSource=vi.fn();render(<StoryStressTest {...props} onSource={onSource}/>);await open();
    fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));await screen.findByText('10 actual source records available.');
    fireEvent.click(screen.getAllByRole('button',{name:/Inspect .* · .* · .* ·/})[0]);expect(onSource).toHaveBeenLastCalledWith([fixture.sources.events[0]]);
    if(kind==='selection')fireEvent.change(screen.getByRole('combobox'),{target:{value:fixture.unchanged.sequence_id}});
    if(kind==='reload')fireEvent.click(screen.getByRole('button',{name:'Reload sequences'}));
    if(kind==='sources')fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));
    expect(onSource).toHaveBeenLastCalledWith([]);
    await waitFor(()=>expect(screen.queryByRole('button',{name:'← Back to sequence'})).not.toBeInTheDocument());
  });
  it('handles an empty observed window without enabling an invented experiment',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>response({timestamp:1200,sequences:[]})));render(<StoryStressTest {...props}/>);
    fireEvent.click(screen.getByRole('button',{name:'Test this reading'}));await screen.findByText('No eligible observed sequences at this checkpoint.');
    expect(screen.queryByRole('button',{name:'Omit sequence from calculation'})).not.toBeInTheDocument();
  });
  it.each(['network','malformed','unavailable','expired'])('rejects %s results while preserving canonical data',async(kind)=>{
    const before=JSON.stringify(data),fetch=mockFetch();render(<StoryStressTest {...props}/>);await open();
    if(kind==='network')fetch.mockRejectedValueOnce(Error('offline'));
    if(kind==='malformed')fetch.mockResolvedValueOnce(response({...fixture.flip,timestamp:1274.43}));
    if(kind==='unavailable')fetch.mockResolvedValueOnce({ok:false,status:404,json:async()=>({detail:'Sequence unavailable in this window'})});
    if(kind==='expired')fetch.mockResolvedValueOnce({ok:false,status:404,json:async()=>({detail:'Demo session expired; reload to start a fresh session'})});
    omit();await screen.findByRole('alert');expect(screen.queryByLabelText('Model sensitivity result')).not.toBeInTheDocument();
    expect(JSON.stringify(data)).toBe(before);if(kind==='expired')expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
  });
  it('makes loading explicit and preserves the noncausal limitation',async()=>{
    const pending=deferred(),fetch=mockFetch();render(<StoryStressTest {...props}/>);await open();fetch.mockImplementationOnce(()=>pending.promise);omit();
    expect(screen.getByRole('button',{name:'Calculating…'})).toBeDisabled();expect(screen.getByText(/Later events are held fixed; the match is not resimulated/)).toBeInTheDocument();
    await act(async()=>pending.resolve(response(fixture.flip)));
  });
  it.each([400,1001])('bounds and discloses a %s-record source history',async total=>{
    const fetch=mockFetch();render(<StoryStressTest {...props}/>);await open();
    fetch.mockImplementation(async(url:string)=>{
      const offset=Number(new URL(url).searchParams.get('offset'));
      const count=Math.min(200,total-offset),end=offset+count;
      return response({...fixture.sources,total,next_offset:end<total?end:null,
        events:Array.from({length:count},(_,i)=>({...fixture.sources.events[0],event_id:`bounded-source-${offset+i}`}))});
    });
    fireEvent.click(screen.getByRole('button',{name:'Inspect sequence source records'}));
    await screen.findByText(total===400?'400 actual source records available.':'Showing 1000 of 1001 source records; remaining records unavailable in this bounded view.',{}, {timeout:5000});
    expect(fetch.mock.calls.filter(([url])=>String(url).includes('/events?'))).toHaveLength(total===400?2:5);
    expect(screen.getAllByRole('button',{name:/Inspect Recovery/})).toHaveLength(Math.min(total,1000));
  });
});

describe('response boundaries',()=>{
  it.each(['timestamp','sequence','baseline','shape'])('rejects mismatched %s sensitivity',kind=>{
    const value=structuredClone(fixture.flip);
    if(kind==='timestamp')value.timestamp=1274.43;if(kind==='sequence')value.sequence_id='other';
    if(kind==='baseline')value.observed[0].home=99;if(kind==='shape')value.without_sequence.reverse();
    expect(()=>parseSensitivity(value,point,fixture.flip.sequence_id)).toThrow();
  });
  it('rejects future choices and source records from another match, sequence, or cutoff',()=>{
    expect(()=>parseChoices({...fixture.choices,timestamp:1500},point)).toThrow();
    for(const changes of [{timestamp:1500},{match_id:'other'},{possession_sequence_id:'other'}]){
      const value=structuredClone(fixture.sources);Object.assign(value.events[0],changes);
      expect(()=>parseSources(value,data.match.match_id,1200,fixture.flip.sequence_id,0)).toThrow();
    }
  });
});

it('invalidates an open experiment on a same-time SSE RESET',async()=>{
  const {loadMatch}=await import('../lib/api');
  const actual={...data,state:{...data.state,temporal:{...data.state.temporal!,current:point}}};
  vi.mocked(loadMatch).mockResolvedValue(actual);mockFetch();
  let stream:{onmessage:((event:{data:string})=>void)|null}|undefined;
  class Events{onopen=null;onerror=null;onmessage=null;close=vi.fn();constructor(){stream=this;}}
  vi.stubGlobal('EventSource',Events);render(<Experience demo/>);
  await screen.findByRole('button',{name:'Test this reading'});await open();omit();await screen.findByLabelText('Model sensitivity result');
  await act(async()=>stream!.onmessage?.({data:JSON.stringify({type:'RESET',match_id:actual.match.match_id,timestamp:actual.state.simulation_time,sequence_number:actual.stream_cursor+1,trace_id:null,payload:actual})}));
  expect(screen.queryByLabelText('Model sensitivity result')).not.toBeInTheDocument();expect(screen.queryByRole('combobox',{name:'Observed possession sequence'})).not.toBeInTheDocument();
});
