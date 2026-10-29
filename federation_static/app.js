const $=id=>document.getElementById(id);
const make=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
let state={agents:[],sources:[],observations:[],assets:[],events:[],runs:[]},view='overview',active=null,catalog={},activeAsset=null,setupGuides={},guideFamilies={},topFive=[];
const fmt=s=>s?new Date(s).toLocaleString():'Never';
const inScope=a=>($('include-seeded').checked||a.provenance!=='seeded')&&($('include-paused').checked||(a.source_enabled!==false&&a.connection_enabled!==false));
const scoped=()=>state.agents.filter(inScope);
const labels={registered:'Registered',discovered:'Discovered',source_changed:'Source changed',registry_updated:'Canonical record updated',not_observed:'No longer observed',rediscovered:'Rediscovered',source_linked:'Identity linked',source_unlinked:'Identity unlinked',connection_added:'Registry connected',sync_failed:'Sync failed',connection_paused:'Registry paused',connection_enabled:'Registry enabled'};
function history(events,target){target.replaceChildren(...events.map(e=>{const c=make('article',undefined,'event');c.append(make('span',labels[e.kind]||e.kind,'badge'),make('strong',e.name),make('small',fmt(e.at)+(e.actor?' · '+e.actor:'')));for(const [key,value] of Object.entries(e.detail)){c.append(make('p',value&&typeof value==='object'?`${key}: ${value.before??'—'} → ${value.after??'—'}`:`${key}: ${value}`));}return c;}));if(!events.length)target.append(make('p','No recorded activity.'));}
function options(select,items,placeholder){const val=select.value;select.replaceChildren();if(placeholder)select.append(new Option(placeholder,''));for(const [id,name] of items)select.append(new Option(name,id));if(items.some(x=>x[0]===val))select.value=val;}
function action(label,fn,cls){const b=make('button',label,cls);b.type='button';b.addEventListener('click',fn);return b;}
function applyRegistryRole(scanButtons=true){
 const role=window.catproofAccount?.role;if(!role)return;
 const edit=['owner','editor'].includes(role),owner=role==='owner';
 for(const id of ['register','onboard-register','refresh','save','link-source'])$(id).hidden=!edit;
 for(const id of ['add-connection','onboard-connect','save-connection'])$(id).hidden=!owner;
 for(const id of ['agent-form','link-form','asset-link-form'])$(id).querySelectorAll('input,textarea,select,button').forEach(e=>e.disabled=!edit);
 if(scanButtons)document.querySelectorAll('button').forEach(b=>{if(['Pause','Enable','Connect this source','Configure connector'].includes(b.textContent))b.hidden=!owner;if(['Relink identity','Move to another canonical record','Split into a separate agent','Unlink asset','Link asset to agent','Sync this registry','Check setup'].includes(b.textContent))b.hidden=!edit;});
}
function render(){
 if($('onboarding'))$('onboarding').hidden=!(window.catproofAccount&&window.catproofAccount.workspace_id!=='legacy'&&state.agents.length===0&&state.sources.length===0);
 const agents=scoped(),needs=agents.filter(a=>a.attention.length&&a.lifecycle!=='retired');
 $('export').href='/api/export.csv'+($('include-seeded').checked?'?seeded=1':'');$('nav-count').textContent=needs.length;$('sync').textContent='Last successful sync '+fmt(state.synced_at);
 $('metrics').replaceChildren(...[[agents.length,'Agents'],[state.observations.filter(inScope).length,'Source identities'],[agents.filter(a=>a.source_count>1).length,'Agents across registries'],[needs.length,'Need attention']].map(([n,label])=>{const d=make('div',undefined,'metric');d.append(make('strong',n),make('span',label));return d;}));
 document.body.classList.toggle('identities-active',view==='identities');document.body.classList.toggle('records-active',['inventory','attention'].includes(view));document.body.classList.toggle('local-active',view==='local');document.body.classList.toggle('mapping-active',['mapping','overview'].includes(view));document.body.classList.toggle('overview-active',view==='overview');
 for(const name of ['inventory','sources','identities','activity','connectors','assets','local','mapping','reconcile'])$(name+'-view').hidden=name==='inventory'?!['inventory','attention'].includes(view):!(name==='mapping'&&view==='overview')&&view!==name;
 if(['mapping','overview'].includes(view)){window.CatProofWorkflows?.render();$('map-title').textContent=view==='overview'?(window.catproofAccount?.workspace_name||'Your workspace'):'Agent mappings';$('map-description').textContent=view==='overview'?'Your agent network at a glance. Select a registry to explore its agents.':'Find agents and their linked records across registries.';renderFederationMap();syncNavigation();applyRegistryRole(false);return;}
 renderAgentRecords(agents);
 $('identities').replaceChildren(...state.observations.filter(inScope).map(o=>{const a=state.agents.find(a=>a.registry_id===o.registry_id),tr=make('tr');let td=make('td');td.append(make('strong',o.name),make('small',o.source_id||o.id),make('small',o.provenance.toUpperCase()));tr.append(td);td=make('td');td.append(make('span',o.connection_name),make('small',o.source));tr.append(td);td=make('td');td.append(make('span',o.status,'badge '+o.status),make('small','Last seen '+fmt(o.last_seen)));tr.append(td);td=make('td');td.append(action(a.name,()=>openAgent(a.registry_id),'record-link'),action('Relink identity',()=>openLink(o.observation_id,o.registry_id),'record-link'));tr.append(td);tr.append(make('td',o.stale?'Stale':o.present?'Observed':'Not observed'));return tr;}));
 const ids=new Set(agents.map(a=>a.registry_id));history(state.events.filter(e=>!e.registry_id||ids.has(e.registry_id)),$('activity'));
 renderCatalog();renderAssets();
 $('connections').replaceChildren(...state.sources.map(s=>{const c=make('article',undefined,'source-card'+(!s.enabled?' paused':''));c.id='connection-'+s.id;c.append(make('h3',s.name),make('span',s.health==='paused'?'PAUSED':s.health==='pending'?'SAVED · NOT SYNCED':s.health==='error'?'SYNC ERROR':s.mode.toUpperCase(),'badge '+(s.mode==='live'?'active':'unreviewed')),make('p',s.kind+' · '+(s.url||'Fictional fixtures')),make('p',sourceError(s)||s.record_count+' agent identities · '+(s.asset_count||0)+' related assets'),make('p',s.coverage||''),make('small',s.verification||''),make('small','Last successful sync '+fmt(s.last_success)));if(s.kind==='lovable')c.append(make('p',s.credential_configured?'API key configured. Test the connection to verify access.':'API key needed. Click Configure API key to connect your Lovable workspace.'),make('p','Workspace ID: '+(s.config.workspace_id||'')),make('p',!s.last_success?'Saved, not successfully synced yet. Workspace ID alone does not authorize access.':'Projects appear under Apps, tools & identities.'));if(s.kind!=='lovable')c.append(make('p',s.credential_configured?'Credentials configured.':s.auth_optional?'Public endpoint or optional authentication.':'Credentials needed. Choose Configure credentials.'));const buttons=make('div',undefined,'actions');buttons.append(action('Check setup',()=>checkConnection(s.id)));if(catalog[s.kind]?.authentication?.modes.length&&(!window.catproofAccount||window.catproofAccount.role==='owner'))buttons.append(action(s.kind==='lovable'?(s.credential_configured?'Update API key':'Configure API key'):(s.credential_configured?'Update credentials':'Configure credentials'),()=>openCredential(s.id)));if(guideFamilies[s.kind])buttons.append(action('Setup guide',()=>openGuide(s.kind)));if(s.enabled){buttons.append(action('Preview records',()=>CatProofWorkflows.preview(s.id)));buttons.append(action('Sync this registry',()=>sync(s.id)));}if(s.kind==='lovable'||s.asset_count)buttons.append(action(s.kind==='lovable'?'View imported projects':'View related assets',()=>showConnectionAssets(s.id)));buttons.append(action(s.enabled?'Pause':'Enable',async()=>{try{state=await api('/api/connection-state',{connection_id:s.id,enabled:!s.enabled});render();}catch(e){$('message').textContent=e.message;}}));c.append(buttons);return c;}));
 $('runs').replaceChildren(...state.runs.slice(0,15).map(r=>{const c=make('article',undefined,'event');c.append(make('span',r.outcome,'badge '+(r.outcome==='succeeded'?'active':'error')),make('strong',state.sources.find(s=>s.id===r.connection_id)?.name||r.connection_id),make('small',fmt(r.completed_at)),make('p',sourceError({error:r.error})||r.observed+' records read'));return c;}));if(!state.runs.length)$('runs').append(make('p','No sync runs yet.'));
 syncNavigation();applyRegistryRole();window.CatProofWorkflows?.render();
}
let recordConnector='',recordPage=0,recordFiltered=[];
function connectorIcon(kind,label){const icon=make('span',({all:'▦',manual:'＋',snowflake:'❄',copilot:'◈',aws_bedrock:'⬡',google_registry:'G',atlas:'◎',mcp:'⌘',a2a:'⇄',databricks:'▱',servicenow:'S',lovable:'♥'})[kind]||(label||kind).slice(0,2).toUpperCase(),'connector-icon');icon.setAttribute('aria-hidden','true');return icon;}
function renderAgentRecords(agents){
 const eligible=view==='attention'?agents.filter(a=>a.attention.length&&a.lifecycle!=='retired'):agents,index=CatProofMapping.createIndex({...state,agents:eligible},{includeSeeded:$('include-seeded').checked,includePaused:$('include-paused').checked}),grouping=CatProofMapping.connectorGroups(index);
 if(recordConnector&&!['manual',...grouping.groups.map(g=>g.kind)].includes(recordConnector))recordConnector='';
 const label=kind=>({databricks:'Databricks',copilot:'Copilot Studio',snowflake:'Snowflake',aws_bedrock:'AWS Bedrock',google_registry:'Google Agent Registry'})[kind]||catalog[kind]?.label||kind,tiles=$('record-connectors');tiles.replaceChildren();
 const tile=(id,name,count,note)=>{const b=action('',()=>{recordConnector=id;recordPage=0;$('registry-filter').value='';render();},'record-connector');b.setAttribute('aria-pressed',String(recordConnector===id));b.append(connectorIcon(id||'all',name),make('span',name,'record-connector-name'),make('strong',String(count)),make('small',note));tiles.append(b);};
 tile('','All agents',index.records.length,'Unique agent records');
 for(const g of grouping.groups.sort((a,b)=>label(a.kind).localeCompare(label(b.kind))))tile(g.kind,label(g.kind),g.recordIds.size,g.connectionCount+' '+(g.connectionCount===1?'connection':'connections'));
 tile('manual','Added manually',grouping.manual.size,'Created in this workspace');
 const selected=recordConnector==='manual'?grouping.manual:grouping.groups.find(g=>g.kind===recordConnector)?.recordIds;
 const records=selected?index.records.filter(r=>selected.has(r.id)):index.records;
 const relevantSources=recordConnector&&recordConnector!=='manual'?index.sources.filter(s=>s.kind===recordConnector):index.sources;
 options($('registry-filter'),relevantSources.map(s=>[s.id,s.name]),'All connections');$('registry-filter').hidden=recordConnector==='manual'||!relevantSources.length;
 const result=CatProofMapping.page({...index,records},{query:$('search').value,source:$('registry-filter').value,page:recordPage,pageSize:25});
 const life=$('lifecycle-filter').value;const recordQuery=$('search').value.trim().toLowerCase();recordFiltered=records.filter(r=>(!life||r.lifecycle===life)&&(!recordQuery||r.searchText.includes(recordQuery))&&(!$('registry-filter').value||r.registryIds.has($('registry-filter').value)));
 // Apply status before pagination so counts and page boundaries stay accurate.
 const page=life?CatProofMapping.page({...index,records:records.filter(r=>r.lifecycle===life)},{query:$('search').value,source:$('registry-filter').value,page:recordPage,pageSize:25}):result;recordPage=page.page;
 $('list-title').textContent=view==='attention'?'Needs attention':recordConnector==='manual'?'Manually added agents':recordConnector?label(recordConnector)+' agents':'All agent records';
 $('count').textContent=page.total+' '+(page.total===1?'agent':'agents')+' · Select a name to open its record.';
 $('rows').replaceChildren(...page.rows.map(r=>{const tr=make('tr'),a=eligible.find(a=>a.registry_id===r.id);tr.dataset.agentId=r.id;let td=make('td');td.append(action(r.name,()=>openAgent(r.id),'agent-link'),make('small',a.provenance==='seeded'?'Demo record':a.purpose||'No description yet'));tr.append(td);td=make('td');
 const bindings=r.bindings.filter(b=>(!$('registry-filter').value||b.connectionId===$('registry-filter').value)&&(!recordConnector||recordConnector==='manual'||index.sources.find(s=>s.id===b.connectionId)?.kind===recordConnector));
 const seen=new Set();for(const b of bindings){if(seen.has(b.connectionId))continue;seen.add(b.connectionId);const kind=index.sources.find(s=>s.id===b.connectionId)?.kind||'discovery',chip=make('div',undefined,'record-source');chip.append(connectorIcon(kind,label(kind)),make('span',b.registry));td.append(chip);}
 if(!bindings.length)td.append(make('span',r.hasSourceRecords?'Source hidden by display options':r.manual?'Added manually':'No source identity linked','record-manual'));
 tr.append(td);td=make('td');td.append(make('span',r.owner|| (bindings.some(b=>b.owner)?'Source owner available':'Needs an owner'),r.owner?'':'missing'));for(const b of bindings.filter(b=>b.owner)){td.append(make('small',b.owner+' · '+b.registry+' · '+b.ownerType+' · '+(b.state==='observed'?'Source-reported':'Source-reported, '+b.state)));}if(!r.owner&&bindings.some(b=>b.owner))td.append(make('small','Choose a CatProof owner in the agent record.'));tr.append(td);td=make('td');td.append(make('span',r.lifecycle,'badge '+r.lifecycle));if(bindings.some(b=>b.state!=='observed'))td.append(make('small','Source needs a refresh','warning'));tr.append(td);td=make('td');const lastSeen=bindings.map(b=>b.lastSeen).filter(v=>Number.isFinite(Date.parse(v))).sort((a,b)=>Date.parse(b)-Date.parse(a))[0];td.append(make('span',lastSeen?fmt(lastSeen):r.hasSourceRecords?'Not recorded':'—'));tr.append(td);return tr;}));
 const empty=$('empty');empty.hidden=page.total>0;empty.replaceChildren();if(!page.total){empty.append(make('h3',recordConnector==='manual'?'No manually added agents':recordConnector?'No agent records from this connector yet':'No matching agents'),make('p',recordConnector&&recordConnector!=='manual'?'Open Connected registries to check setup and refresh this connector. Platforms that import tools or applications appear under Apps, tools & identities.':'Try another connector or clear your search and status filters. You can also add an agent yourself.'));empty.append(action('Clear filters',()=>{recordConnector='';recordPage=0;$('search').value='';$('lifecycle-filter').value='';$('registry-filter').value='';render();}));}
 $('records-prev').disabled=page.page===0;$('records-next').disabled=page.page>=page.pageCount-1;$('records-page').textContent=page.total?'Showing '+page.start+'–'+page.end+' of '+page.total:'0 agents';document.querySelector('.record-pager').hidden=page.total<=25;
}
$('records-prev').onclick=()=>{recordPage--;render();};$('records-next').onclick=()=>{recordPage++;render();};
for(const id of ['search','registry-filter','lifecycle-filter','include-seeded','include-paused'])$(id).addEventListener('input',()=>{recordPage=0;render();});
function sourceCard(o){const c=make('article',undefined,'source-observation');c.append(make('h4',o.connection_name),make('span',o.provenance.toUpperCase(),'badge'),make('p','Source agent: '+o.name),make('p','Vendor ID: '+(o.source_id||o.id),'identity-id'),make('p','Source: '+o.source+' · '+o.kind),make('p','Source owner: '+o.owner),make('p','Source status: '+o.source_status+' · '+o.status),make('p','Last observed: '+fmt(o.last_seen)),make('p',o.stale?'Stale: registry refresh failed or paused':o.present?'Present in latest registry read':'Not observed in latest successful registry read'));if(o.evidence&&Object.keys(o.evidence).length){const details=make('details');details.append(make('summary','Connector evidence'),make('pre',JSON.stringify(o.evidence,null,2)));c.append(details);}if(o.description){const d=make('details');d.append(make('summary','Source description'),make('p',o.description));c.append(d);}c.append(action('Move to another canonical record',()=>openLink(o.observation_id,o.registry_id)),action('Split into a separate agent',async()=>{try{state=await api('/api/unlink',{observation_id:o.observation_id});render();openAgent(active);}catch(e){$('save-message').textContent=e.message;}}));return c;}
let agentDetailTab='record';
function setAgentDetailTab(tab){
 agentDetailTab=tab;const graph=tab==='relationships';$('agent-record-panel').hidden=graph;$('agent-relationships-panel').hidden=!graph;$('detail').classList.toggle('relationships-active',graph);
 for(const key of ['record','relationships']){const button=$('agent-'+key+'-tab');button.setAttribute('aria-selected',String(key===tab));button.tabIndex=key===tab?0:-1;}
 if(graph&&active)CatProofGraphView.mount($('agent-relationships'),state,active,{includeSeeded:$('include-seeded').checked,includePaused:$('include-paused').checked},id=>{openAgent(id);setAgentDetailTab('relationships');});
}
for(const tab of ['record','relationships']){
 const button=$('agent-'+tab+'-tab');button.onclick=()=>setAgentDetailTab(tab);
 button.addEventListener('keydown',e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const next=e.key==='Home'?'record':e.key==='End'?'relationships':tab==='record'?'relationships':'record';setAgentDetailTab(next);$('agent-'+next+'-tab').focus();}});
}
function openAgent(id=null){if(active!==id)agentDetailTab='record';active=id;const a=state.agents.find(a=>a.registry_id===id);$('agent-name').textContent=a?a.name:'Add an agent';$('canonical-id').textContent=a?a.registry_id:'A canonical record exists independently of any vendor or registry.';$('existing-only').hidden=!a;
 for(const [input,key] of [['edit-name','name'],['purpose','purpose'],['business-owner','business_owner'],['lifecycle','lifecycle'],['review','review'],['notes','notes']])$(input).value=a?a[key]:key==='lifecycle'?'draft':key==='review'?'unreviewed':'';
 const map=$('federation-map');map.replaceChildren();if(a){const summary=make('div',undefined,'canonical-summary');summary.append(make('strong',a.business_owner||'Owner unassigned'),make('span',a.lifecycle+' lifecycle · '+a.linked_records.length+' identities across '+a.source_count+' registries'));map.append(summary);const nodes=make('div',undefined,'identity-map');for(const o of a.linked_records){const node=make('div');node.append(make('strong',o.connection_name),make('small',(o.provenance==='seeded'?'SEEDED · ':'')+o.name),make('small',o.source_id||o.id));nodes.append(node);}map.append(nodes);}
 $('save-message').textContent='';if(a){$('linked-sources').replaceChildren(...a.linked_records.map(sourceCard));if(!a.linked_records.length)$('linked-sources').append(make('p','No source identities linked. This canonical record and its history remain saved.'));history(state.events.filter(e=>e.registry_id===id),$('agent-history'));$('linked-assets').replaceChildren(...(a.linked_assets||[]).map(resource=>action(resource.name+' · '+resource.resource_type,()=>openAsset(resource.asset_id),'record-link')));if(!(a.linked_assets||[]).length)$('linked-assets').append(make('p','No related assets linked.')); }$('agent-detail-tabs').hidden=!a;setAgentDetailTab(a?agentDetailTab:'record');window.CatProofWorkflows?.agent(a);if(!$('detail').open)$('detail').showModal();}
function openLink(oid=null,target=active){
 const observations=state.observations.filter(inScope),agents=scoped(),empty=$('link-empty');
 options($('observation-select'),observations.map(o=>[o.observation_id,o.name+' · '+o.connection_name+' · '+(o.source_id||o.id)]),'Choose a source record');
 options($('target-select'),agents.map(a=>[a.registry_id,a.name+' · '+a.registry_id]),'Choose an agent');
 if(oid)$('observation-select').value=oid;if(target)$('target-select').value=target;
 $('link-message').textContent='';empty.replaceChildren();const missing=!observations.length||!agents.length;
 empty.hidden=!missing;$('link-form').hidden=missing;
 if(missing){
  const filtered=state.observations.length&&!observations.length;
  empty.append(make('h3',!observations.length?(filtered?'Source records are hidden':'No source records to link yet'):'Add an agent first'),make('p',!observations.length?(filtered?'Your display options hide the available records. Show demo or paused records to select them.':state.sources.length?'Your connections have not imported any agent records yet. Open Connected registries, check setup, and refresh a connection.':'Connect a platform and import its agent records first. Then return here to link a record to this agent.'):'Create an agent record in your workspace, then link the imported record to it.'));
  const leave=next=>{if($('link-dialog').open)$('link-dialog').close();if($('detail').open)$('detail').close();view=next;render();};
  if(filtered)empty.append(action('Open display options',()=>{leave('identities');document.querySelector('.scope-options').open=true;}));
  else if(!observations.length){const next=state.sources.length?'sources':'connectors';empty.append(action(state.sources.length?'Open connected registries':'Connect a platform',()=>leave(next),'primary'));}
  else empty.append(action('Add an agent',()=>{$('link-dialog').close();openAgent();},'primary'));
 }
 $('link-dialog').showModal();
}
async function api(url,body){const r=await fetch(url,body?{method:'POST',headers:{'X-Registry-Action':'refresh','Content-Type':'application/json'},body:JSON.stringify(body)}:{});const result=await r.json();if(!r.ok){const error=Error(result.error||'Registry request failed');error.details=result;throw error;}return result;}
async function sync(cid=null){$('refresh').disabled=true;$('message').textContent='Syncing independent registries…';try{state=await api('/api/refresh',cid?{connection_id:cid}:{});render();$('message').textContent=state.sources.some(s=>s.health==='error')?'Sync completed with source errors. Canonical records and prior source evidence retained.':'Registry sync complete. Canonical decisions preserved.';}catch(e){$('message').textContent=e.message;}finally{$('refresh').disabled=false;}}
$('agent-form').addEventListener('submit',async e=>{e.preventDefault();$('save').disabled=true;const updates={name:$('edit-name').value,purpose:$('purpose').value,business_owner:$('business-owner').value,lifecycle:$('lifecycle').value,review:$('review').value,notes:$('notes').value};try{state=await api(active?'/api/annotate':'/api/agents',active?{registry_id:active,updates}:updates);const id=active||state.created_id;render();openAgent(id);$('agent-edit').open=true;$('save-message').textContent='Canonical record saved.';}catch(e){$('save-message').textContent=e.message;}finally{$('save').disabled=false;}});
$('connection-form').addEventListener('submit',async e=>{
 e.preventDefault();$('save-connection').disabled=true;
 try{
  const kind=$('connection-kind').value;
  const result=await api('/api/credentials/connect',{name:$('connection-name').value,kind,url:$('connection-url').value,source_filter:$('connection-filter').value,config:Object.fromEntries([...document.querySelectorAll('#connection-fields input')].map(input=>[input.dataset.key,input.value])),credentials:readAuth('connection')});
  clearAuth('connection');$('connection-dialog').close();await testSavedCredential(result.connection_id);
 }catch(e){if(e.details?.connection_id){try{state=await api('/api/inventory');$('connection-dialog').close();openSavedConnection(e.details.connection_id,'This connection is already saved. Open it to configure and test.');}catch(error){$('connection-message').textContent=error.message;}}else $('connection-message').textContent=e.message;}
 finally{clearAuth('connection');$('save-connection').disabled=false;}
});

$('link-form').addEventListener('submit',async e=>{e.preventDefault();const oid=$('observation-select').value,target=$('target-select').value;if(!oid||!target){$('link-message').textContent='Choose a source record and an agent to continue.';return;}const old=state.observations.find(o=>o.observation_id===oid);if(!old){$('link-message').textContent='This source record is no longer available. Refresh the inventory.';return;}const submit=$('link-form').querySelector('button[type=submit]');submit.disabled=true;try{state=await api('/api/link',{observation_id:oid,registry_id:target,expected_current:old.registry_id});render();$('link-dialog').close();if(active)openAgent(active);CatProofWorkflows.linked({observationId:oid,from:old.registry_id,to:target});}catch(error){$('link-message').textContent=error.message;}finally{submit.disabled=false;}});

$('register').addEventListener('click',()=>openAgent());$('add-connection').addEventListener('click',()=>{$('connection-message').textContent='';$('connection-dialog').showModal();});$('link-source').addEventListener('click',()=>openLink());$('refresh').addEventListener('click',()=>sync());document.querySelectorAll('[data-close]').forEach(b=>b.addEventListener('click',()=>$(b.dataset.close).close()));document.querySelectorAll('aside button[data-view]').forEach(b=>b.addEventListener('click',()=>{$('message').textContent='';view=b.dataset.view;render();}));Promise.all([api('/api/inventory'),api('/api/connector-catalog'),api('/api/setup-guides')]).then(([s,c,g])=>{state=s;catalog=c.connectors;topFive=c.top_five;setupGuides=g.guides;guideFamilies=g.families;for(const [kind,spec] of Object.entries(catalog).sort(([,a],[,b])=>a.label.localeCompare(b.label,'en',{sensitivity:'base',numeric:true})))if(kind!=='local'&&![...$('connection-kind').options].some(o=>o.value===kind))$('connection-kind').append(new Option(spec.label,kind));$('connection-kind').replaceChildren(...[...$('connection-kind').options].sort((a,b)=>a.text.localeCompare(b.text,'en',{sensitivity:'base',numeric:true})));configureFields();render();}).catch(e=>$('message').textContent=e.message);

function sourceError(source){return source.error&&/server environment|environment variable|_TOKEN|_PROFILE|_ACCESS_KEY|Credential environment/.test(source.error)?'Credentials need attention. Open this connection and choose Configure credentials, then test the connection.':source.error;}

function openSavedConnection(id,message){view='sources';render();$('message').textContent=message;const card=$('connection-'+id);if(card){card.classList.add('connection-highlight');card.scrollIntoView({behavior:'smooth',block:'center'});}}
function showConnectionAssets(id){options($('asset-source-filter'),state.sources.map(s=>[s.id,s.name]),'All connections');$('asset-source-filter').value=id;view='assets';render();}
$('asset-source-filter').addEventListener('change',renderAssets);

let credentialConnection=null;
function openCredential(id){
 credentialConnection=id;const source=state.sources.find(s=>s.id===id);$('credential-title').textContent=(catalog[source.kind]?.label||source.kind)+' credentials';$('credential-description').textContent=source.name+' · '+source.url;renderAuth('credential',source.kind);$('credential-error').textContent='';$('credential-remove').hidden=source.credential_storage!=='encrypted';$('credential-dialog').showModal();
}
function renderAuth(context,kind){
 const spec=catalog[kind]?.authentication||{modes:[],hint:''},select=$(context+'-auth-mode');select.replaceChildren(...spec.modes.map(m=>new Option(m.label,m.id)));select.dataset.kind=kind;$(context+'-auth-hint').textContent=spec.hint;renderAuthFields(context);
}
function renderAuthFields(context){
 const select=$(context+'-auth-mode'),spec=catalog[select.dataset.kind]?.authentication,mode=spec?.modes.find(m=>m.id===select.value),target=$(context+'-auth-fields');clearAuth(context);target.replaceChildren();
 for(const field of mode?.fields||[]){const label=make('label',field.label),input=make('input');input.type='password';input.autocomplete='off';input.required=field.required;input.maxLength=8192;input.dataset.secret=field.key;input.id=context+'-secret-'+field.key;input.spellcheck=false;label.append(input);target.append(label);}
}
function readAuth(context){return {mode:$(context+'-auth-mode').value,...Object.fromEntries([...document.querySelectorAll('#'+context+'-auth-fields input')].filter(i=>i.value!=='').map(i=>[i.dataset.secret,i.value]))};}
function clearAuth(context){document.querySelectorAll('#'+context+'-auth-fields input').forEach(i=>i.value='');}
for(const context of ['connection','credential'])$(context+'-auth-mode').addEventListener('change',()=>renderAuthFields(context));
async function testSavedCredential(id){
 state=await api('/api/inventory');if(!state.sources.find(s=>s.id===id)?.enabled){openSavedConnection(id,'Credentials saved securely. Enable this connection when you are ready to preview it.');return;}
 openSavedConnection(id,'Connection saved. Preview the source before importing.');await CatProofWorkflows.preview(id);
}
$('credential-form').addEventListener('submit',async event=>{
 event.preventDefault();$('credential-save').disabled=true;
 try{await api('/api/credentials/save',{connection_id:credentialConnection,credentials:readAuth('credential')});clearAuth('credential');$('credential-dialog').close();await testSavedCredential(credentialConnection);}catch(error){$('credential-error').textContent=error.message;}finally{clearAuth('credential');$('credential-save').disabled=false;}
});
$('credential-remove').onclick=async()=>{
 if(!confirm('Remove saved credentials and pause the connection? Existing imported evidence is retained.'))return;
 try{await api('/api/credentials/remove',{connection_id:credentialConnection});$('credential-dialog').close();state=await api('/api/inventory');openSavedConnection(credentialConnection,'Saved credentials removed and connection paused.');}catch(error){$('credential-error').textContent=error.message;}
};
$('credential-dialog').addEventListener('close',()=>{clearAuth('credential');$('credential-error').textContent='';});
$('connection-dialog').addEventListener('close',()=>{clearAuth('connection');});
document.addEventListener('visibilitychange',()=>{if(document.hidden){clearAuth('credential');clearAuth('connection');}});

function renderCatalog(){
 $('connector-catalog').replaceChildren(...Object.entries(catalog).filter(([kind])=>$('show-all-connectors').checked||topFive.includes(kind)).sort(([,a],[,b])=>a.label.localeCompare(b.label,'en',{sensitivity:'base',numeric:true})).map(([kind,spec])=>{const card=make('article',undefined,'source-card');const identity=make('div',undefined,'connector-identity'),icon=make('span',({snowflake:'❄',copilot:'◈',aws_bedrock:'⬡',google_registry:'G',atlas:'◎',mcp:'⌘',a2a:'⇄',databricks:'▱',servicenow:'S',lovable:'♥'})[kind]||spec.label.slice(0,2).toUpperCase(),'connector-icon');icon.setAttribute('aria-hidden','true');identity.append(icon,make('h3',spec.label));card.append(identity,make('span',spec.category.toUpperCase(),'badge'),make('p',spec.coverage));const details=make('details',undefined,'connector-details');details.append(make('summary','Coverage & authentication'),make('small',spec.verification),make('p','Credentials: '+spec.auth));card.append(details);const link=make('a','API documentation');link.href=spec.reference;link.target='_blank';link.rel='noopener';details.append(link);if(guideFamilies[kind])details.append(action('Setup guide',()=>openGuide(kind)));for(const connection of state.sources.filter(s=>s.kind===kind))card.append(action('Open saved connection: '+connection.name,()=>openSavedConnection(connection.id,'Existing connection opened. Check setup and sync to import records.')));card.append(action(kind==='local'?'Set up local collector':'Configure connector',()=>{if(kind==='local'){view='local';render();loadLocal();return;}active=null;$('connection-kind').value=kind;$('connection-name').value=spec.label;configureFields();$('connection-dialog').showModal();}));return card;}));
}
function configureFields(){
 const kind=$('connection-kind').value,spec=catalog[kind];$('connection-url').closest('label').hidden=kind==='lovable';$('save-connection').textContent='Save & preview';$('connection-fields').replaceChildren();$('connection-guidance').replaceChildren();renderAuth('connection',kind);
 const origins={lovable:'https://api.lovable.dev',copilot:'https://api.powerplatform.com',m365:'https://graph.microsoft.com',entra:'https://graph.microsoft.com',google_registry:'https://agentregistry.googleapis.com',google_runtime:'https://us-central1-aiplatform.googleapis.com',mcp:'https://registry.modelcontextprotocol.io',aws_bedrock:'https://bedrock.us-east-1.amazonaws.com',aws_agentcore:'https://bedrock-agentcore.us-east-1.amazonaws.com',aws_registry:'https://agent-registry-control.us-east-1.amazonaws.com'};
 if(origins[kind])$('connection-url').value=origins[kind];else $('connection-url').value='';
 $('connection-filter').value='';$('connection-filter').closest('label').hidden=!spec.supports_source_filter;
 if(spec){$('connection-guidance').append(make('p',spec.coverage),make('small','Live tenant access has not been verified. Missing credentials produce a visible error; no demo substitution.'));
 for(const field of spec.fields){const label=make('label',field.label);const input=make('input');input.dataset.key=field.key;input.required=field.required||field.required_on_create;input.placeholder=field.example;input.maxLength=4000;if(['region','api_version','card_path'].includes(field.key))input.value=field.example;label.append(input);$('connection-fields').append(label);}}

}

function renderAssets(){
 options($('asset-source-filter'),state.sources.map(s=>[s.id,s.name]),'All connections');const cid=$('asset-source-filter').value,source=state.sources.find(s=>s.id===cid);
 $('asset-status').textContent=source?(source.error?'Latest sync failed: '+sourceError(source):!source.enabled?'This connection is paused. Enable it to refresh.':!source.last_success?'Saved, not synced yet. Open Federated registries → Check setup, then Sync this registry.':source.asset_count+' related assets from the last successful sync.'):'Related assets appear here after a successful sync. Choose a connection to see its status.';
 const assets=(state.assets||[]).filter(inScope).filter(a=>!cid||a.connection_id===cid);$('asset-list').replaceChildren(...assets.map(a=>{const card=make('article',undefined,'source-card');card.append(make('span',a.resource_type+' · '+a.provenance.toUpperCase(),'badge'),make('h3',a.name),make('p',a.connection_name+' · '+a.source),make('small',(a.stale?'STALE · ':a.present?'Observed · ':'No longer observed · ')+fmt(a.last_seen)),make('p',a.description||''),...(a.owner?[make('p','Source owner: '+a.owner+' · Status: '+a.status)]:[]),action('Details & agent links',()=>openAsset(a.asset_id)));return card;}));if(!assets.length)$('asset-list').append(make('p','No related assets in this view. Open your saved connection to check setup and sync its related assets.'));
}
function openAsset(id){activeAsset=id;const a=(state.assets||[]).find(a=>a.asset_id===id);$('asset-name').textContent=a.name;$('asset-description').textContent=a.description||'';$('asset-evidence').textContent=JSON.stringify({source:a.source,source_id:a.source_id,registry:a.connection_name,evidence:a.evidence},null,2);options($('asset-agent'),scoped().map(a=>[a.registry_id,a.name+' · '+a.registry_id]));$('asset-message').textContent='';$('asset-associations').replaceChildren(...a.linked_agents.map(id=>{const name=state.agents.find(a=>a.registry_id===id)?.name||id;return action('Unlink '+name,async()=>{try{state=await api('/api/asset-link',{asset_id:activeAsset,registry_id:id,linked:false});render();openAsset(activeAsset);}catch(e){$('asset-message').textContent=e.message;}});}));if(!$('asset-dialog').open)$('asset-dialog').showModal();}
$('asset-link-form').addEventListener('submit',async e=>{e.preventDefault();try{state=await api('/api/asset-link',{asset_id:activeAsset,registry_id:$('asset-agent').value});render();openAsset(activeAsset);$('asset-message').textContent='Asset linked as related evidence. Agent ownership unchanged.';}catch(e){$('asset-message').textContent=e.message;}});
$('connection-kind').addEventListener('change',configureFields);

function openGuide(kind){
 const body=$('guide-content');body.replaceChildren();const lines=(setupGuides[guideFamilies[kind]]||'Guide unavailable').split('\n');let code=null;
 for(const line of lines){if(line.startsWith('```')){if(code){body.append(make('pre',code.join('\n')));code=null;}else code=[];continue;}if(code){code.push(line);continue;}if(!line.trim())continue;const heading=line.match(/^(#{1,3}) (.+)$/);if(heading){body.append(make('h'+heading[1].length,heading[2]));}else if(/^https:\/\//.test(line)){const link=make('a',line);link.href=line;link.target='_blank';link.rel='noopener';body.append(link);}else body.append(make('p',line));}
 $('guide-dialog').showModal();
}
async function checkConnection(cid){$('setup-checks').replaceChildren(make('p','Checking local configuration…'));$('setup-dialog').showModal();try{const result=await api('/api/connection-check',{connection_id:cid});$('setup-checks').replaceChildren(make('h2',result.ready?'Ready to test a live sync':'Setup needs attention'),...result.checks.map(c=>{const row=make('article',undefined,'event');row.append(make('strong',(c.ok?'✓ ':'○ ')+c.label),make('p',c.detail));return row;}),make('p',result.limitation));}catch(e){$('setup-checks').replaceChildren(make('p',e.message));}}
$('show-all-connectors').addEventListener('change',renderCatalog);

api('/api/me').then(account=>{window.catproofAccount=account;$('workspace-label').textContent=account.workspace_name+' / '+account.name+' · '+account.role;$('workspace-home-name').textContent=account.workspace_name;$('account-controls').hidden=false;$('change-password').hidden=account.workspace_id==='legacy';$('workspace-reference').textContent='Registry reference: '+account.workspace_id;render();}).catch(()=>{});
$('change-password').onclick=()=>$('password-dialog').showModal();
$('onboard-register').onclick=()=>openAgent();
$('onboard-connect').onclick=()=>{$('connection-message').textContent='';$('connection-dialog').showModal();};

let federationMapController=null;
function renderFederationMap(){
 if(!federationMapController)federationMapController=CatProofMappingView.create($('federation-mapping'),{onExplore:()=>{if(view==='overview'){view='mapping';render();}},onOpen:(id,graph)=>{agentDetailTab='record';openAgent(id);if(graph)setAgentDetailTab('relationships');applyRegistryRole();},onReload:async()=>{state=await api('/api/inventory');return state;}});
 federationMapController.update(state,{includeSeeded:$('include-seeded').checked,includePaused:$('include-paused').checked});
}

// Navigation only changes the visible projection; it never refreshes a provider.
let navigationView='overview';
function syncNavigation(){
 document.querySelectorAll('aside button[data-view]').forEach(b=>{
  const selected=b.dataset.view===view;b.classList.toggle('selected',selected);
  if(selected)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
 });
 const active=document.querySelector('aside button[data-view="'+view+'"]')?.closest('.nav-group');
 document.querySelectorAll('.nav-group').forEach(group=>{if(navigationView!==view)group.open=group===active;group.classList.toggle('active-section',group===active);});
 navigationView=view;
}
