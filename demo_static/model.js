/* Invented fixtures. No credentials, fetches, real tenant IDs or live state. */
(function(root){
 'use strict';
 const sources=['AWS Bedrock','Copilot Studio','Snowflake Cortex','Google Agent Registry','A2A Agent Card'];
 function create(){
  const identity=(source,id,owner,status='ready')=>({source,id:'demo-'+id,owner,status,lastSeen:'Scenario time · 09:40',stale:false});
  const agent=(id,name,purpose,owner,identities)=>({id,name,purpose,owner,lifecycle:'active',review:'reviewed',identities,history:[{label:'Scenario setup',detail:'Fictional source identities were deliberately linked to this canonical record.'}]});
  const records=[
   agent('orders','Order exception assistant','Coordinates delayed shipments and escalations.','Morgan Chen · Operations',[identity(sources[0],'aws-orders','Demo Ops role'),identity(sources[3],'gcp-orders','Demo platform team')]),
   agent('invoice','Invoice triage','Classifies invoice exceptions for a finance reviewer.','',[identity(sources[2],'sf-invoice','DEMO_FINANCE_ROLE'),identity(sources[4],'a2a-invoice','Not advertised')]),
   agent('hr','People policy assistant','Answers employee policy questions.','Casey Rivera · People',[identity(sources[1],'copilot-people','Demo HR maker')]),
   agent('support','Customer escalation assistant','Prepares context for human customer support.','Sam Patel · Support',[identity(sources[1],'copilot-support','Demo Support maker'),identity(sources[4],'a2a-support','Not advertised')]),
   agent('forecast','Supply forecast analyst','Summarizes planning signals for the supply team.','Alex Brooks · Supply chain',[identity(sources[2],'sf-forecast','DEMO_PLANNING_ROLE')]),
   agent('dispatch','Field dispatch planner','Suggests routing options for a dispatcher.','Taylor Reed · Field operations',[identity(sources[0],'aws-dispatch','Demo Field role')]),
   agent('procurement','Procurement intake','Organizes incoming purchase requests.','',[identity(sources[3],'gcp-procurement','Demo developer')])
  ];
  records[4].identities[0].stale=true;records[4].identities[0].lastSeen='Previous scenario snapshot';records[5].review='unreviewed';
  return {sources:[...sources],records,refreshes:0};
 }
 function metrics(state){return {records:state.records.length,identities:state.records.reduce((n,a)=>n+a.identities.length,0),sources:state.sources.length,missingOwners:state.records.filter(a=>!a.owner).length};}
 function find(state,id){const a=state.records.find(a=>a.id===id);if(!a)throw Error('Unknown demo record');return a;}
 function assignOwner(state,id){const a=find(state,id);if(a.owner)return false;a.owner='Jordan Lee · Finance (fictional)';a.history.unshift({label:'Simulated owner decision',detail:'Assigned Jordan Lee as accountable owner. Source ownership stays separate.'});return true;}
 function refresh(state,id){const a=find(state,id);state.refreshes++;for(const identity of a.identities){identity.lastSeen='Simulated refresh #'+state.refreshes;identity.status='metadata refreshed';identity.stale=false;}a.history.unshift({label:'Simulated source refresh',detail:'Source metadata changed. Canonical owner, lifecycle and review decisions were preserved.'});return a;}
 const model={create,metrics,find,assignOwner,refresh};root.CatProofDemo=model;if(typeof module!=='undefined')module.exports=model;
})(globalThis);
