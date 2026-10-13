import {z} from 'zod';
import {temporalSchema,changeSchema,truthSchema,executionSchema} from './temporal';
export const eventSchema=z.object({event_id:z.string(),match_id:z.string(),event_type:z.string(),timestamp:z.number(),player_id:z.string().nullable().optional(),team_id:z.string().nullable().optional(),start_x:z.number().nullable().optional(),start_y:z.number().nullable().optional(),end_x:z.number().optional(),end_y:z.number().optional(),zone:z.string().optional()}).passthrough();
export type FootballEvent=z.infer<typeof eventSchema>;
const evidenceSchema=z.object({evidence_id:z.string(),metric_name:z.string(),value:z.number(),baseline_value:z.number().nullable(),delta:z.number().nullable(),window:z.string().nullable(),source_event_ids:z.array(z.string()),source_paths:z.array(z.string()).default([]),origin:z.string().optional()});
export type Evidence=z.infer<typeof evidenceSchema>;
const explanationSchema=z.object({title:z.string(),what_happened:z.string(),what_changed:z.string(),why_it_matters:z.string(),confidence:z.number(),caveats:z.array(z.string()),evidence_ids:z.array(z.string()),evidence_chains:z.array(z.object({chain_id:z.string(),evidence_ids:z.array(z.string()),logical_relationship:z.string(),strength:z.number()}))});
const narrativeSchema=z.object({headline:z.string(),summary:z.string(),confidence:z.number(),evidence_ids:z.array(z.string()),caveats:z.array(z.string())});
export const insightSchema=z.object({execution:executionSchema.nullable().default(null),change:changeSchema.nullable().default(null),truth:truthSchema.nullable().default(null),insight_id:z.string(),moment:z.object({moment_id:z.string(),timestamp:z.number(),moment_type:z.string(),significance_score:z.number(),tier:z.enum(['LOW','NOTABLE','HIGH','CRITICAL']),team_id:z.string().nullable(),player_id:z.string().nullable(),source_event_ids:z.array(z.string()),evidence_ids:z.array(z.string())}),explanation:explanationSchema.nullable(),evidence:z.array(evidenceSchema),source_events:z.array(eventSchema),agent_explanation:z.object({what_happened:z.string(),what_changed:z.string(),why_it_matters:z.string(),confidence:z.number(),caveats:z.array(z.string())}).nullable(),narrative:narrativeSchema.nullable(),workflow_status:z.enum(['PROCESSING','COMPLETE','PARTIAL','FAILED','NO_EXPLANATION']),trace_id:z.string().nullable()});
export type Insight=z.infer<typeof insightSchema>;
const teamSchema=z.object({team_id:z.string(),team_name:z.string(),pressure_index:z.number(),field_tilt_pct:z.number(),possession:z.object({possession_pct:z.number()}),passing:z.object({attempts:z.number(),accuracy:z.number(),progressive:z.number()}),attacking:z.object({shots:z.number(),progressive_actions:z.number()})});
const playerSchema=z.object({player_id:z.string(),name:z.string(),team_id:z.string(),position:z.string(),touches:z.number(),passes:z.number(),pass_accuracy:z.number(),progressive_passes:z.number(),pressure_events:z.number(),shots:z.number()});
export type Player=z.infer<typeof playerSchema>;
const windowSchema=z.object({timestamp:z.number(),window_seconds:z.number(),home_pressure_index:z.number(),away_pressure_index:z.number(),home_field_tilt:z.number(),away_field_tilt:z.number(),home_possession_pct:z.number(),away_possession_pct:z.number()});
export type Window=z.infer<typeof windowSchema>;
export const stateSchema=z.object({temporal:temporalSchema.nullable().default(null),status:z.enum(['waiting','running','paused','complete','error']),simulation_time:z.number(),home_score:z.number(),away_score:z.number(),total_events:z.number(),speed:z.number(),tactical_state:z.object({state:z.string(),confidence:z.number(),timestamp:z.number()}).nullable(),home_team:teamSchema,away_team:teamSchema,players:z.array(playerSchema),windows:z.array(windowSchema),possession_player_id:z.string().nullable()});
const clubSchema=z.object({team_id:z.string(),name:z.string(),short_name:z.string()});
export const snapshotSchema=z.object({match:z.object({match_id:z.string(),home:clubSchema,away:clubSchema,competition:z.string(),venue:z.string(),synthetic:z.boolean(),runtime_mode:z.enum(['mock','foundry']),event_source:z.enum(['local','eventhub']).default('local'),demo_controls:z.boolean().default(true),foundry_hero_only:z.boolean().default(false)}),state:stateSchema,events:z.array(eventSchema),insights:z.array(insightSchema),stream_cursor:z.number()});
export type Snapshot=z.infer<typeof snapshotSchema>;
const header={match_id:z.string(),timestamp:z.number().nonnegative(),sequence_number:z.number().int().nonnegative(),trace_id:z.string().nullable()};
export const messageSchema=z.discriminatedUnion('type',[
 z.object({...header,type:z.literal('RESET'),payload:snapshotSchema}),
 z.object({...header,type:z.literal('MATCH_STATE_UPDATED'),payload:stateSchema}),
 z.object({...header,type:z.literal('EVENT_RECEIVED'),payload:eventSchema}),
 ...(['MOMENT_DETECTED','EXPLANATION_READY','NARRATIVE_READY'] as const).map(type=>z.object({...header,type:z.literal(type),payload:insightSchema}))
]).superRefine((message,ctx)=>{
 if(message.type==='RESET'&&message.payload.match.match_id!==message.match_id||message.type==='EVENT_RECEIVED'&&message.payload.match_id!==message.match_id)
  ctx.addIssue({code:'custom',message:'Stream header and payload identities disagree.'});
});
export type Message=z.infer<typeof messageSchema>;
export type Mode='fan'|'analyst'|'broadcast'|'player';
export type Connection='connecting'|'connected'|'reconnecting'|'offline';
export type Preferences={favorite_team_id:string;favorite_player_id:string;language:'en'|'hi'|'es';audience_mode:Mode};
export const defaultPreferences:Preferences={favorite_team_id:'',favorite_player_id:'',language:'en',audience_mode:'fan'};
