import {z} from 'zod';
import {eventSchema} from './types';
import {Point,sensitivitySchema,sequenceSchema} from './temporal';

export const sequenceSourcesSchema=z.object({
  sequence_id:z.string(),timestamp:z.number().nonnegative(),total:z.number().int().nonnegative(),
  next_offset:z.number().int().nonnegative().nullable(),events:z.array(eventSchema).max(200),
});

// Validate identity and the canonical baseline as well as response shape. Never
// attach a response for a different clock/sequence to the currently pinned reading.
export function parseChoices(value:unknown,point:Point){
  const result=sequenceSchema.parse(value);
  if(result.timestamp!==point.timestamp||new Set(result.sequences.map(c=>c.sequence_id)).size!==result.sequences.length||
    result.sequences.some(c=>c.start<Math.max(0,point.timestamp-300)||c.end>point.timestamp||c.end<c.start||!Number.isInteger(c.events)||c.events<1))
    throw Error('Inconsistent response');
  return result;
}
export function parseSensitivity(value:unknown,point:Point,sequenceId:string){
  const result=sensitivitySchema.parse(value);
  if(result.timestamp!==point.timestamp||result.sequence_id!==sequenceId||result.window_seconds!==300||
    result.observed_state!==point.state||!result.removed_event_ids.length||
    new Set(result.removed_event_ids).size!==result.removed_event_ids.length||
    result.observed.length!==8||result.without_sequence.length!==8||result.deltas.length!==8||
    result.observed.some((d,i)=>JSON.stringify(d)!==JSON.stringify(point.dimensions[i])||
      d.name!==result.without_sequence[i].name||d.unit!==result.without_sequence[i].unit||
      d.formula!==result.without_sequence[i].formula||d.name!==result.deltas[i].name||d.unit!==result.deltas[i].unit))
    throw Error('Inconsistent response');
  return result;
}
export function parseSources(value:unknown,matchId:string,cutoff:number,sequenceId:string,offset:number){
  const result=sequenceSourcesSchema.parse(value);
  if(result.timestamp!==cutoff||result.sequence_id!==sequenceId||result.total<offset+result.events.length||
    result.next_offset!==(offset+result.events.length<result.total?offset+result.events.length:null)||
    (result.next_offset!==null&&!result.events.length)||
    new Set(result.events.map(e=>e.event_id)).size!==result.events.length||
    result.events.some(e=>e.match_id!==matchId||e.timestamp<0||e.timestamp>cutoff||e.possession_sequence_id!==sequenceId))
    throw Error('Inconsistent response');
  return result;
}
