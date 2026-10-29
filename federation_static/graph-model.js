/* Read-only projection of explicit registry bindings. No fuzzy identity matching. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.CatProofGraph=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
function scoped(record,options){return (options.includeSeeded||record.provenance!=='seeded')&&(options.includePaused||(record.connection_enabled!==false&&record.source_enabled!==false));}
function freshness(record){if(record.connection_enabled===false)return 'paused';if(record.stale)return 'stale';if(record.present===false)return 'not observed';return record.last_seen?'observed':'unknown';}
function proof(record){return {source:record.connection_name||record.source||'Canonical registry',sourceId:record.source_id||record.id||null,connectionId:record.connection_id||null,lastSeen:record.last_seen||null,provenance:record.provenance||'manual',freshness:freshness(record),sourceStatus:record.source_status||record.status||null};}
function build(snapshot,agentId,options={}){
 const opts={includeSeeded:false,includePaused:false,sourceId:'',kind:'all',attentionOnly:false,...options};
 const agent=(snapshot.agents||[]).find(a=>a.registry_id===agentId);const nodes=new Map(),edges=[];
 if(!agent||!scoped(agent,opts))return {focusId:null,nodes:[],edges:[],sources:[],message:'This record is outside the current inventory scope.'};
 const focusId='agent:'+agentId,add=node=>{if(!nodes.has(node.id))nodes.set(node.id,node);return node.id;};
 const edge=(from,to,type,label,evidence,attention=false)=>edges.push({id:type+':'+from+'>'+to,from,to,type,label,evidence,attention});
 add({id:focusId,kind:'agent',label:agent.name,recordId:agentId,detail:agent.lifecycle||'draft',provenance:agent.provenance,attention:!agent.business_owner});
 const ownerId=add({id:'owner:'+agentId,kind:'owner',label:agent.business_owner||'Owner unassigned',detail:agent.business_owner?'Assigned in CatProof':'Needs an accountable owner',attention:!agent.business_owner});
 const ownerEvent=(snapshot.events||[]).find(e=>e.registry_id===agentId&&e.detail&&Object.hasOwn(e.detail,'business_owner'));
 edge(ownerId,focusId,'owner','Accountable owner',{source:'Canonical registry',basis:'Canonical business_owner field',recordId:agentId,provenance:agent.provenance||'manual',freshness:agent.business_owner?'assigned':'missing',recordedAt:ownerEvent?.at||null,verification:'Owner assignment recorded. Owner acceptance has not been collected.'},!agent.business_owner);
 const connectedSources=new Map();
 const eligible=r=>scoped(r,opts)&&(!opts.sourceId||r.connection_id===opts.sourceId);
 if(opts.kind!=='assets')for(const identity of agent.linked_records||[]){
  if(!scoped(identity,opts))continue;
  if(identity.connection_id)connectedSources.set(identity.connection_id,identity.connection_name||identity.source||identity.connection_id);
  if(!eligible(identity))continue;
  const p=proof(identity),attention=['stale','paused','not observed','unknown'].includes(p.freshness);
  const iid=add({id:'identity:'+(identity.observation_id||identity.connection_id+':'+identity.source_id),kind:'identity',label:identity.name,detail:identity.connection_name||identity.source,provenance:identity.provenance,attention,freshness:p.freshness});
  edge(focusId,iid,'identity','Source identity',{...p,basis:'Recorded canonical-to-source identity binding',observationId:identity.observation_id,verification:'Explicit registry binding. Similar names do not create links.'},attention);
  if(identity.connection_id){const rid=add({id:'registry:'+identity.connection_id,kind:'registry',label:identity.connection_name||identity.source,detail:identity.kind||'Source registry',attention,freshness:p.freshness});edge(rid,iid,'registry','Observed in registry',{...p,basis:'Connector inventory observation'},attention);}
 }
 if(opts.kind!=='identities')for(const asset of agent.linked_assets||[]){
  if(!scoped(asset,opts))continue;
  if(asset.connection_id)connectedSources.set(asset.connection_id,asset.connection_name||asset.source||asset.connection_id);
  if(!eligible(asset))continue;
  const p=proof(asset),attention=['stale','paused','not observed','unknown'].includes(p.freshness),aid=add({id:'asset:'+asset.asset_id,kind:'asset',label:asset.name,detail:asset.resource_type||'Related asset',provenance:asset.provenance,attention,freshness:p.freshness});
  edge(focusId,aid,'asset','Linked asset',{...p,basis:'Explicit asset-to-agent association',assetId:asset.asset_id,verification:'Related evidence. Tool usage and runtime calls have not been measured.'},attention);
  for(const relatedId of asset.linked_agents||[]){if(relatedId===agentId)continue;const related=(snapshot.agents||[]).find(a=>a.registry_id===relatedId);if(!related||!scoped(related,opts))continue;
   const rid=add({id:'agent:'+relatedId,kind:'agent',label:related.name,recordId:relatedId,detail:'Shares a linked asset',provenance:related.provenance,attention:!related.business_owner});
   edge(aid,rid,'shared_asset','Also linked to',{...p,basis:'Both canonical records explicitly link this asset',assetId:asset.asset_id,verification:'Shared asset association. An agent-to-agent call has not been established.'},attention);
  }
 }
 const filtered=opts.attentionOnly?edges.filter(e=>e.attention):edges;const ids=new Set([focusId,...filtered.flatMap(e=>[e.from,e.to])]);
 return {focusId,nodes:[...nodes.values()].filter(n=>ids.has(n.id)),edges:filtered,sources:[...connectedSources].map(([id,label])=>({id,label})).sort((a,b)=>a.label.localeCompare(b.label)),message:filtered.length?'':'No relationships match these filters.'};
}
return {build,freshness};
});
