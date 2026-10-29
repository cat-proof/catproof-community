let inventory = {agents: [], sources: []};
const $ = id => document.getElementById(id);
function element(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }
function date(s) { return s ? new Date(s).toLocaleString() : 'Never'; }
function render() {
  const a = inventory.agents;
  $('metrics').replaceChildren(...[[a.length,'Registered agents'],[a.filter(x=>x.owner==='Unassigned').length,'Without an owner'],[a.filter(x=>x.provenance==='live').length,'Live records'],[a.filter(x=>x.stale).length,'Stale records']].map(([n,label])=> { const d=element('div',undefined,'metric'); d.append(element('strong',n),element('span',label)); return d; }));
  $('sources').replaceChildren(...inventory.sources.map(s=>{ const d=element('article',undefined,'source-card'); d.append(element('h3',s.name),element('span',s.mode.toUpperCase(),'badge '+(s.mode==='live'?'active':'seeded')),element('p',s.health==='error'?s.error:s.mode==='seeded'?'Fictional fixtures · no credentials configured':'Read-only API inventory'),element('small','Last success: '+date(s.last_success))); if(s.health==='error')d.classList.add('failed'); return d; }));
  const q=$('search').value.toLowerCase();
  const rows=a.filter(x=>(x.name+' '+x.owner).toLowerCase().includes(q)&&(!$('source').value||x.source===$('source').value)&&(!$('status').value||x.status===$('status').value));
  $('count').textContent=rows.length+' of '+a.length+' agents'; $('empty').hidden=rows.length>0;
  $('rows').replaceChildren(...rows.map(x=>{const tr=element('tr'); let td=element('td'); td.append(element('strong',x.name),element('small',x.kind),element('small',x.source_id)); tr.append(td);tr.append(element('td',x.owner));td=element('td');td.append(element('span',x.status,'badge '+x.status),element('small','Source: '+x.source_status));tr.append(td);td=element('td');td.append(element('span',x.source),element('small',x.provenance==='seeded'?'SEEDED DEMO DATA':'LIVE API RECORD'));tr.append(td);td=element('td');td.append(element('span',date(x.last_seen)));if(x.stale)td.append(element('small','STALE · refresh failed','warning'));tr.append(td);return tr;}));
}
async function load(refresh=false) {
  $('refresh').disabled=true; $('message').textContent=refresh?'Refreshing sources…':'';
  try {const response=await fetch(refresh?'/api/refresh':'/api/inventory',refresh?{method:'POST',headers:{'X-Registry-Action':'refresh'}}:{});if(!response.ok)throw Error();inventory=await response.json();render();$('message').textContent=inventory.sources.some(s=>s.health==='error')?'Refresh completed with source errors. Retained records are marked stale.':refresh?'Inventory refreshed.':'';}
  catch {$('message').textContent='Cannot reach the local registry. Existing view retained; retry refresh.';}
  finally {$('refresh').disabled=false;}
}
$('refresh').addEventListener('click',()=>load(true));for(const id of ['search','source','status'])$(id).addEventListener('input',render);load();
