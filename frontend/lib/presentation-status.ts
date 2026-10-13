import type {Insight} from './types';

// Presentation only: lifecycle takes precedence over the legacy transport label.
export function runtimePresentation(insight:Insight|null,heroOnly=false){
 const e=insight?.execution;
 const grounded=!!(e?.state==='COMPLETE'&&insight?.truth?.status==='SUPPORTED'&&['Grounding validation','Arbitration'].every(stage=>e.steps.some(s=>s.stage===stage&&s.status==='OK')));
 if(e?.state==='NOT REQUESTED'||(!e&&heroOnly&&insight&&insight.moment.moment_type!=='GOAL'))return {kind:'not-requested',tone:'neutral',heading:'AGENT INTERPRETATION',badge:'NOT REQUESTED',context:'DETERMINISTIC REPLAY · FOUNDRY NOT REQUESTED',message:'This moment is presented from MatchLens deterministic evidence. Foundry activates for the designated intelligence moment.',grounded:false};
 if(e?.state==='PENDING')return {kind:'pending',tone:'pending',heading:e.mode==='mock'?'MOCK EXECUTION':'FOUNDRY EXECUTION',badge:'PENDING',context:e.mode==='mock'?'MOCK EXECUTION · IN PROGRESS':'FOUNDRY EXECUTION · IN PROGRESS',message:'PENDING · Requested interpretation is in progress. Grounding and arbitration have not yet been confirmed.',grounded:false};
 if(e?.state==='REJECTED')return {kind:'rejected',tone:'failure',heading:'AGENT INTERPRETATION',badge:e.mode==='foundry'?'FOUNDRY RESULT REJECTED':'RESULT REJECTED',context:e.mode==='foundry'?'FOUNDRY RESULT REJECTED':'MOCK RESULT REJECTED',message:'Generated interpretation rejected against the evidence. Deterministic match intelligence remains available.',grounded:false};
 if(e?.state==='UNAVAILABLE')return {kind:'unavailable',tone:'failure',heading:'AGENT INTERPRETATION',badge:e.mode==='foundry'?'FOUNDRY UNAVAILABLE':'INTERPRETATION UNAVAILABLE',context:e.mode==='foundry'?'FOUNDRY UNAVAILABLE':'MOCK UNAVAILABLE',message:'Requested interpretation unavailable. Deterministic match intelligence remains available.',grounded:false};
 if(e?.mode==='foundry'&&e.state==='COMPLETE'&&e.status==='FOUNDRY LIVE')return {kind:'live',tone:'success',heading:'FOUNDRY EXECUTION',badge:'FOUNDRY LIVE',context:grounded?'FOUNDRY LIVE · GROUNDED':'FOUNDRY LIVE · EXECUTION COMPLETE',message:grounded?'This moment completed genuine Foundry execution and mandatory application grounding.':'Foundry execution completed. Grounding and arbitration step records are not fully supplied.',grounded};
 if(e?.mode==='mock')return {kind:'mock',tone:'neutral',heading:'MOCK EXECUTION',badge:'MOCK',context:'OFFLINE MOCK INTERPRETATION',message:'Offline mock interpretation.',grounded};
 return {kind:'awaiting',tone:'neutral',heading:'AGENT INTERPRETATION',badge:'NO EXECUTION RECORD',context:insight?'DETERMINISTIC EVIDENCE · NO EXECUTION RECORD':'NO MOMENT SELECTED',message:'No execution record supplied for this moment.',grounded:false};
}

export function interpretationHeading(insight:Insight,heroOnly=false){
 const runtime=runtimePresentation(insight,heroOnly);
 return runtime.kind==='live'?'INTERPRETED BY FOUNDRY':runtime.kind==='mock'&&insight.execution?.state==='COMPLETE'?'INTERPRETED BY MOCK':runtime.kind==='not-requested'?'DETERMINISTIC EXPLANATION':runtime.heading;
}

// Moment summaries retain deterministic explanations without endorsing a rejected agent result.
export function momentExplanationText(insight:Insight){
 const runtime=runtimePresentation(insight);
 const text=insight.explanation?.what_changed;
 const withDeterministic=(message:string)=>text?`${message} Deterministic explanation: ${text}`:message;
 if(runtime.kind==='rejected'||(runtime.kind!=='pending'&&insight.workflow_status!=='PROCESSING'&&insight.truth?.status==='REJECTED'))return withDeterministic(`Generated interpretation rejected.${text?'':' No accepted explanation available.'}`);
 if(runtime.kind==='unavailable'||insight.workflow_status==='FAILED')return withDeterministic('Explanation unavailable. The requested workflow did not complete.');
 if(insight.workflow_status!=='NO_EXPLANATION'&&(runtime.kind==='pending'||insight.workflow_status==='PROCESSING'))return withDeterministic('Explanation pending.');
 return text||'No explanation available for this moment.';
}

// Missing records are absence of provenance, not a new workflow lifecycle or failure.
export function councilPresentation(insight:Insight|null,heroOnly=false){
 const runtime=runtimePresentation(insight,heroOnly);
 const mock=insight?.execution?.mode==='mock';
 const context=runtime.kind==='not-requested'?'Selected moment · deterministic state':runtime.kind==='pending'?`Selected moment · ${mock?'mock':'Foundry'} execution`:runtime.kind==='live'?'Selected moment · validated Foundry result':runtime.kind==='rejected'?`Selected moment · rejected ${mock?'mock':'Foundry'} result`:runtime.kind==='unavailable'?`Selected moment · ${mock?'mock':'Foundry'} execution failed`:runtime.kind==='mock'?'Selected moment · offline mock result':insight?'Selected moment · execution record not supplied':'No moment selected';
 const heading=runtime.kind==='not-requested'?'DETERMINISTIC EVIDENCE':runtime.kind==='pending'?mock?'MOCK EXECUTION IN PROGRESS':'FOUNDRY EXECUTION IN PROGRESS':runtime.kind==='live'?'FOUNDRY LIVE':runtime.kind==='rejected'?runtime.badge:runtime.kind==='unavailable'?runtime.badge:runtime.kind==='mock'?runtime.grounded?'MOCK · grounded and arbitrated':'MOCK RESULT':'DETERMINISTIC EVIDENCE';
 const detail=runtime.kind==='not-requested'?'AGENTS NOT REQUESTED':runtime.kind==='pending'?'Waiting for the specialist workflow to complete.':runtime.grounded?'GROUNDED · ARBITRATED — Grounding PASSED · Arbitration ACCEPTED':runtime.kind==='live'?'Execution complete · grounding/arbitration step records not fully supplied':runtime.kind==='rejected'?'VALIDATION FAILED · The generated interpretation was not accepted against the evidence.':runtime.kind==='unavailable'?'The requested workflow did not complete.':runtime.kind==='mock'?'Offline mock interpretation.':insight?'No agent execution record supplied.':'Select a measured moment to inspect its evidence.';
 return {...runtime,selectionContext:context,councilHeading:heading,councilDetail:detail};
}

export function specialistPresentation(insight:Insight|null,agent:string,heroOnly=false){
 const runtime=runtimePresentation(insight,heroOnly),specialist=insight?.truth?.specialists.find(s=>s.agent.toLowerCase()===agent);
 if(runtime.kind==='not-requested')return 'NOT REQUESTED';
 if(runtime.kind==='pending')return 'PENDING';
 // No finding or missing optional validation state cannot establish a failure.
 return specialist?.validation_state||(runtime.kind==='unavailable'?'UNAVAILABLE':'NO RESULT REPORTED');
}

export function validationPresentation(insight:Insight,heroOnly=false){
 if(insight.truth)return insight.truth.status;
 const runtime=runtimePresentation(insight,heroOnly);
 return runtime.kind==='not-requested'?'NOT REQUESTED':runtime.kind==='pending'?'PENDING':runtime.kind==='rejected'?'REJECTED':runtime.kind==='unavailable'?'UNAVAILABLE':'NO VALIDATION RECORD';
}
