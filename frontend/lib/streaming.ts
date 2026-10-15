import {z} from 'zod';

const stageSchema=z.object({total:z.number(),unique:z.number(),missing:z.number(),missing_sample:z.array(z.string()),duplicates:z.number(),unexpected:z.number(),altered:z.number()});
export const fabricStates=['NOT CONFIGURED','PENDING','TOPOLOGY VERIFIED','VERIFIED','FAILED','NOT VERIFIED'] as const;
export const runSchema=z.object({run_id:z.string().regex(/^[a-f0-9]{32}$/),state:z.enum(['starting','topology','connecting','publishing','consuming','reconciling','complete','failed']),
 started_at:z.string(),finished_at:z.string().nullable(),expected_events:z.number(),published:z.number(),publish_acknowledged:z.number(),
 api_received:z.number(),api_processed:z.number(),processing_errors:z.number(),last_event_at:z.string().nullable(),consumer_ready:z.boolean(),
 fabric:z.enum(fabricStates),reconciled:z.boolean(),
 stages:z.object({published:stageSchema.nullable(),eventhub_readback:stageSchema.nullable(),api_processed:stageSchema.nullable(),fabric:stageSchema.nullable()}).nullable(),
 failure:z.object({stage:z.string(),reason:z.string()}).nullable()});
export const streamStatusSchema=z.union([
 z.object({enabled:z.literal(false),source_mode:z.literal('local')}),
 z.object({enabled:z.literal(true),source_mode:z.literal('eventhub'),consumer_group:z.string(),speed:z.number(),active:z.boolean(),run:runSchema.nullable()}),
]);
export type StreamStatus=z.infer<typeof streamStatusSchema>;
export type StreamRun=z.infer<typeof runSchema>;

// Fabric is only ever described as verified after a run-scoped Eventhouse query matched every published event.
export function fabricPresentation(run:StreamRun|null){
 const state=run?.fabric;
 if(state==='VERIFIED'&&run?.reconciled&&run.stages?.fabric)return {tone:'success',label:'FABRIC VERIFIED',detail:`Run-scoped Eventhouse query matched all ${run.stages.fabric.unique} events.`};
 if(state==='VERIFIED')return {tone:'failure',label:'FABRIC NOT VERIFIED',detail:'Fabric status lacks a reconciled run-scoped result.'};
 if(state==='TOPOLOGY VERIFIED')return {tone:'pending',label:'FABRIC PENDING',detail:'Eventstream topology verified; waiting for the run-scoped Eventhouse query.'};
 if(state==='PENDING')return {tone:'pending',label:'FABRIC PENDING',detail:'Fabric verification runs after the match has streamed.'};
 if(state==='FAILED')return {tone:'failure',label:'FABRIC NOT VERIFIED',detail:'Fabric topology or Eventhouse reconciliation failed for this run.'};
 if(state==='NOT VERIFIED')return {tone:'neutral',label:'FABRIC NOT VERIFIED',detail:'The run stopped before Fabric could be checked.'};
 return {tone:'neutral',label:'FABRIC NOT CHECKED',detail:'Fabric verification is not configured for this deployment.'};
}

export function runLabel(run:StreamRun|null){
 if(!run)return 'No synthetic match streamed yet';
 return {starting:'Starting',topology:'Checking Fabric topology',connecting:'Connecting consumer',publishing:'Publishing to Event Hubs',
  consuming:'Receiving remaining events',reconciling:'Reconciling in Fabric',complete:'Stream complete',failed:'Stream failed'}[run.state];
}
