/* Compact cached-inventory index. No network access and no graph construction. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.CatProofMapping=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
'use strict';
const compare=(a,b)=>String(a).localeCompare(String(b),'en',{sensitivity:'base',numeric:true});
const scoped=(r,o)=>(o.includeSeeded||r.provenance!=='seeded')&&(o.includePaused||(r.connection_enabled!==false&&r.source_enabled!==false));
function sourceOwner(value){const name=typeof value==='string'?value.trim():'';return /^(unassigned|unknown|none|n\/a|null|not set)$/i.test(name)?'':name;}
function createIndex(snapshot,options={}){
 const opts={includeSeeded:false,includePaused:false,...options};
 const sources=new Map((snapshot.sources||[]).map(s=>[s.id,{id:s.id,name:s.name,kind:s.kind,health:s.health||'pending',enabled:s.enabled!==false,mode:s.mode||'pending',lastSuccess:s.last_success||null,identities:0,assets:0,attention:0,agents:new Set()}]));
 const records=[];let staleLinks=0,missingOwner=0,multiRegistry=0;
 for(const a of snapshot.agents||[]){if(!scoped(a,opts))continue;
  const bindings=[];let attention=0,lastSeen=null,lastTime=0;const ids=new Set();
  for(const o of a.linked_records||[]){const source=sources.get(o.connection_id);if(!scoped(o,opts)||(!opts.includePaused&&source?.enabled===false))continue;
   const state=o.connection_enabled===false||source?.enabled===false?'paused':o.stale?'stale':o.present===false?'not observed':!o.last_seen?'unknown':'observed';const needs=state!=='observed';
   const b={id:o.observation_id,connectionId:o.connection_id,registry:o.connection_name||source?.name||o.source||'Unknown registry',name:o.name,sourceId:o.source_id||o.id||'Not recorded',owner:sourceOwner(o.owner),ownerType:o.evidence?.owner_type||(source?.kind==='databricks'?'Databricks creator':'Source-reported owner'),lastSeen:o.last_seen||null,provenance:o.provenance||'manual',state};bindings.push(b);
   if(o.connection_id)ids.add(o.connection_id);if(needs){attention++;staleLinks++;}
   if(source){source.identities++;if(needs)source.attention++;source.agents.add(a.registry_id);}
   const time=Date.parse(o.last_seen);if(Number.isFinite(time)&&time>lastTime){lastSeen=o.last_seen;lastTime=time;}
  }
  bindings.sort((a,b)=>compare(a.registry,b.registry)||compare(a.sourceId,b.sourceId));
  const owner=String(a.business_owner||'').trim(),crossRegistry=ids.size>1;if(!owner)missingOwner++;if(crossRegistry)multiRegistry++;
  const record={id:a.registry_id,name:a.name,owner,sourceOwners:bindings.filter(b=>b.owner),lifecycle:a.lifecycle||'draft',provenance:a.provenance||'manual',bindings,registryIds:ids,registryCount:ids.size,attention,lastSeen,crossRegistry,unlinked:bindings.length===0,hasSourceRecords:(a.linked_records||[]).length>0,manual:a.manual===1||(a.manual===undefined&&a.provenance==='manual')};
  record.searchText=[record.name,record.id,owner,...bindings.flatMap(b=>[b.registry,b.name,b.sourceId,b.owner])].join(' ').toLowerCase();records.push(record);
 }
 for(const asset of snapshot.assets||[]){const source=sources.get(asset.connection_id);if(source&&scoped(asset,opts)&&(opts.includePaused||source.enabled))source.assets++;}
 records.sort((a,b)=>compare(a.name,b.name)||compare(a.id,b.id));
 const registryList=[...sources.values()].map(s=>({...s,agents:s.agents.size})).sort((a,b)=>compare(a.name,b.name)||compare(a.id,b.id));
 return {records,sources:registryList,totals:{agents:records.length,multiRegistry,missingOwner,staleLinks}};
}
function page(index,options={}){
 const q=String(options.query||'').trim().slice(0,200).toLowerCase(),source=options.source||'',focus=options.focus||'all',pageSize=[25,50].includes(Number(options.pageSize))?Number(options.pageSize):25;
 const filtered=index.records.filter(r=>(!q||r.searchText.includes(q))&&(!source||r.registryIds.has(source))&&(focus==='cross'?r.crossRegistry:focus==='owner'?!r.owner:focus==='stale'?r.attention>0:focus==='unlinked'?r.unlinked:true));
 const pageCount=Math.max(1,Math.ceil(filtered.length/pageSize)),page=Math.min(pageCount-1,Math.max(0,Math.floor(Number(options.page)||0))),start=page*pageSize;
 return {rows:filtered.slice(start,start+pageSize),total:filtered.length,page,pageCount,pageSize,start:filtered.length?start+1:0,end:Math.min(start+pageSize,filtered.length)};
}
function registryPage(index,pageNumber=0){const pageCount=Math.max(1,Math.ceil(index.sources.length/8)),page=Math.min(pageCount-1,Math.max(0,Math.floor(Number(pageNumber)||0)));return {rows:index.sources.slice(page*8,page*8+8),page,pageCount,total:index.sources.length};}
function connectorGroups(index){
 const groups=new Map(),sourceKinds=new Map();
 for(const s of index.sources){const kind=s.kind||'discovery';sourceKinds.set(s.id,kind);if(!groups.has(kind))groups.set(kind,{kind,connectionCount:0,recordIds:new Set()});groups.get(kind).connectionCount++;}
 const manual=new Set();for(const r of index.records){if(!r.hasSourceRecords&&r.manual)manual.add(r.id);for(const kind of new Set(r.bindings.map(b=>sourceKinds.get(b.connectionId)||'discovery'))){if(!groups.has(kind))groups.set(kind,{kind,connectionCount:0,recordIds:new Set()});groups.get(kind).recordIds.add(r.id);}}
 return {groups:[...groups.values()],manual};
}
function parseCSV(input){
 if(typeof input!=='string'||input.length>250000)throw Error('Choose a CSV smaller than 250 KB.');
 input=input.replace(/^\uFEFF/,'');const rows=[];let row=[],cell='',quoted=false,closed=false;
 for(let i=0;i<input.length;i++){const c=input[i];if(quoted){if(c==='"'){if(input[i+1]==='"'){cell+='"';i++;}else{quoted=false;closed=true;}}else cell+=c;continue;}
  if(c==='"'){if(cell||closed)throw Error('Invalid CSV quoting.');quoted=true;continue;}
  if(c===','||c==='\n'||c==='\r'){row.push(cell);cell='';closed=false;if(c!==','){if(row.some(v=>v.trim()))rows.push(row);row=[];if(c==='\r'&&input[i+1]==='\n')i++;}continue;}
  if(closed)throw Error('Unexpected text after a quoted CSV value.');cell+=c;
 }
 if(quoted)throw Error('A quoted CSV value is not closed.');row.push(cell);if(row.some(v=>v.trim()))rows.push(row);
 if(rows.length<2)throw Error('Include a header row and at least one agent.');
 const aliases={owner:'business_owner',status:'lifecycle',description:'purpose'},headers=rows.shift().map(v=>aliases[v.trim().toLowerCase()]||v.trim().toLowerCase()),allowed=['name','purpose','business_owner','lifecycle','review','notes','registry_id','connectors','last_observed'];
 if(!headers.includes('name')||new Set(headers).size!==headers.length||headers.some(h=>!allowed.includes(h)))throw Error('Use the template columns: name, purpose, business_owner, lifecycle, review, notes. Export metadata columns are ignored.');
 if(rows.length>100)throw Error('Import at most 100 agents at a time.');
 return rows.map((cells,n)=>{if(cells.length!==headers.length)throw Error('Row '+(n+2)+' has the wrong number of columns.');const result={};headers.forEach((h,i)=>{if(['registry_id','connectors','last_observed'].includes(h))return;result[h]=cells[i].trim();});if(!result.name)throw Error('Row '+(n+2)+' needs an agent name.');if(!result.lifecycle)result.lifecycle='draft';if(!result.review)result.review='unreviewed';if(!['draft','active','suspended','retired'].includes(result.lifecycle)||!['reviewed','unreviewed'].includes(result.review))throw Error('Row '+(n+2)+' has an invalid status or review.');for(const [k,v]of Object.entries(result))if(v.length>(k==='purpose'?8000:k==='notes'?4000:200))throw Error('Row '+(n+2)+' has an oversized '+k+' field.');return result;});
}
function csv(headers,rows){const cell=v=>{let value=String(v??'');if(/^[\s]*[=+@-]/.test(value))value="'"+value;return '"'+value.replace(/"/g,'""')+'"';};return [headers,...rows].map(r=>r.map(cell).join(',')).join('\r\n');}
function matchCandidates(index,id){const current=index.records.find(r=>r.id===id);if(!current)return [];const normalize=s=>String(s).normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]+/gu,' ').trim(),name=normalize(current.name);if(!name)return [];
 return index.records.filter(r=>r.id!==id&&r.bindings.length&&normalize(r.name)===name&&(r.provenance==='seeded')===(current.provenance==='seeded')&&!r.bindings.some(b=>current.registryIds.has(b.connectionId))).slice(0,5).map(r=>({...r,reason:'Same normalized name'+(r.owner&&r.owner===current.owner?' and recorded owner':'')+'. This is a suggestion; verify the source records before linking.'}));
}
function changesSince(events,since){const cutoff=Date.parse(since||'');return (events||[]).filter(e=>!Number.isFinite(cutoff)||Date.parse(e.at)>cutoff).sort((a,b)=>Date.parse(b.at)-Date.parse(a.at));}
function reconcile(snapshot,options={}){
 const opts={includeSeeded:false,...options},sources=new Map((snapshot.sources||[]).map(s=>[s.id,s])),queues={unregistered:[],conflicts:[],missing:[],unavailable:[]};
 const norm=v=>String(v||'').normalize('NFKC').trim().toLowerCase();
 for(const a of snapshot.agents||[]){if(!opts.includeSeeded&&a.provenance==='seeded')continue;
  if(a.review!=='reviewed'){queues.unregistered.push({agent:a,reason:'This agent has not been approved in CatProof.'});continue;}
  const links=(a.linked_records||[]).filter(o=>opts.includeSeeded||o.provenance!=='seeded');
  if(!links.length&&a.lifecycle==='active')queues.missing.push({agent:a,reason:'Approved active agent has no linked deployment. Link a source record to check it.'});
  for(const o of links){const source=sources.get(o.connection_id),item={agent:a,observation:o};
   if(o.stale||o.connection_enabled===false||!source||source.enabled===false||source.health!=='ok'){queues.unavailable.push({...item,reason:'Source evidence is paused, stale or unavailable. Restore a successful read before resolving this record.'});continue;}
   if(o.present===false){if(a.lifecycle==='active')queues.missing.push({...item,reason:'Approved active agent was not returned by the latest successful source read.'});continue;}
   if(a.lifecycle==='retired')queues.missing.push({...item,reason:'Retired in CatProof, but still listed by the source. This does not prove it is running.'});
   for(const [field,sourceField,label]of [['name','name','Name'],['business_owner','owner','Owner']]){if((sourceField==='owner'?sourceOwner(o[sourceField]):norm(o[sourceField]))&&norm(a[field])!==norm(o[sourceField]))queues.conflicts.push({...item,field,sourceField,label,approved:a[field]||'',observed:o[sourceField]});}
  }
 }
 return queues;
}
return {createIndex,page,registryPage,connectorGroups,parseCSV,csv,matchCandidates,changesSince,reconcile,sourceOwner};
});
