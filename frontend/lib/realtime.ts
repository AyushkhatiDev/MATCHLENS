import {Message,Snapshot,Preferences,defaultPreferences,messageSchema} from './types';
export function applyMessage(current:Snapshot,message:Message,allowCursorReset?:boolean):Snapshot{
 if(message.match_id!==current.match.match_id)return current;
 if(message.type==='RESET'){
  if(message.payload.match.match_id!==current.match.match_id)return current;
  // A fresh connection may observe a restarted backend with a lower cursor.
  // Within one connection, delayed/duplicate snapshots cannot rewind state.
  const openingRestart=allowCursorReset===undefined&&message.sequence_number===0&&current.stream_cursor>0&&message.payload.state.total_events===0;
  if(message.sequence_number<=current.stream_cursor&&!allowCursorReset&&!openingRestart)return current;
  return {...message.payload,stream_cursor:message.sequence_number};
 }
 if(message.sequence_number<=current.stream_cursor)return current;
 if(message.type==='EVENT_RECEIVED'&&message.payload.match_id!==current.match.match_id)return current;
 if(message.type==='MATCH_STATE_UPDATED')return {...current,state:message.payload,stream_cursor:message.sequence_number};
 if(message.type==='EVENT_RECEIVED')return {...current,events:[...current.events.filter(e=>e.event_id!==message.payload.event_id),message.payload].slice(-200),stream_cursor:message.sequence_number};
 return {...current,insights:[...current.insights.filter(i=>i.insight_id!==message.payload.insight_id),message.payload],stream_cursor:message.sequence_number};
}
export function parseMessage(json:string){return messageSchema.safeParse(JSON.parse(json));}
export function readPreferences():Preferences{
 try{const value=JSON.parse(localStorage.getItem('matchlens.preferences')||'{}');return {...defaultPreferences,...Object.fromEntries(Object.entries(value).filter(([key,value])=>typeof value==='string'&&key in defaultPreferences)),audience_mode:['fan','analyst','broadcast','player'].includes(value.audience_mode)?value.audience_mode:'fan',language:['en','hi','es'].includes(value.language)?value.language:'en'};}catch{return defaultPreferences;}
}
export function savePreferences(value:Preferences){try{localStorage.setItem('matchlens.preferences',JSON.stringify(value));}catch{/* Private/storage-disabled browsers keep preferences for this session. */}}
