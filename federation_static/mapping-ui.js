/* Lazy view: index on snapshot/scope change; render only visible cards and rows. */
(function(){
'use strict';
const el=(tag,text,cls)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;},date=value=>{if(!value)return 'Never';const d=new Date(value);return Number.isNaN(d.getTime())?'Not recorded':d.toLocaleString();};
function create(root,{onOpen,onReload,onExplore}){
 let snapshot=null,scopeKey='',scope={},index=null,tablePage=0,cardPage=0,timer=null;
 const globe=el('section',undefined,'agent-globe');root.append(globe);
 const metrics=el('div',undefined,'mapping-metrics');root.append(metrics);
 const heading=el('div',undefined,'mapping-heading');heading.append(el('h3','Connected registries'));const reload=el('button','Refresh this view');reload.type='button';heading.append(reload);root.append(heading);
 const status=el('p','Saved inventory only. This view does not contact source systems.','mapping-note');status.setAttribute('role','status');root.append(status);
 const cards=el('div',undefined,'mapping-cards');cards.id='mapping-cards';root.append(cards);
 const cardPager=el('div',undefined,'mapping-pager'),cardPrev=el('button','Previous registries'),cardNext=el('button','Next registries'),cardLabel=el('span');cardPrev.type=cardNext.type='button';cardPager.append(cardPrev,cardLabel,cardNext);root.append(cardPager);
 const tableHeading=el('h3','Agents by registry');root.append(tableHeading,el('p','Find an agent and see which registries have a linked record.','mapping-note'));
 const controls=el('div',undefined,'mapping-controls');const field=(text,input)=>{const l=el('label',text);l.append(input);controls.append(l);return input;};
 const search=field('Find an agent',el('input'));search.type='search';search.maxLength=200;search.placeholder='Agent, owner or source identity';search.setAttribute('aria-label','Search federation mappings');
 const source=field('Registry',el('select'));source.setAttribute('aria-label','Mapping registry');const focus=field('Show',el('select'));focus.setAttribute('aria-label','Mapping focus');
 for(const [value,label]of [['all','All agents'],['cross','Across multiple registries'],['owner','Need an owner'],['stale','Need a refresh'],['unlinked','No source identities']]){const o=el('option',label);o.value=value;focus.append(o);}
 const pageSize=field('Rows',el('select'));pageSize.setAttribute('aria-label','Mappings per page');for(const n of [25,50]){const o=el('option',String(n));o.value=n;pageSize.append(o);}
 const clear=el('button','Clear filters');clear.type='button';controls.append(clear);root.append(controls);
 const count=el('p',undefined,'mapping-note');count.id='mapping-count';count.setAttribute('role','status');root.append(count);
 const wrap=el('div',undefined,'table-wrap'),table=el('table'),thead=el('thead'),tr=el('tr');for(const name of ['Agent','Owner','Source identities','Evidence','Explore'])tr.append(el('th',name));thead.append(tr);const tbody=el('tbody');tbody.id='mapping-rows';table.append(thead,tbody);wrap.append(table);root.append(wrap);
 const empty=el('p','No agents to show. Clear the filters or add an agent.','mapping-note');root.append(empty);
 const pager=el('div',undefined,'mapping-pager'),prev=el('button','Previous mappings'),next=el('button','Next mappings'),pageLabel=el('span');prev.type=next.type='button';pager.append(prev,pageLabel,next);root.append(pager);
 function renderGlobe(){
  const ns='http://www.w3.org/2000/svg',shape=(tag,attrs)=>{const n=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,String(v));return n;};
  globe.replaceChildren();const copy=el('div',undefined,'globe-copy');copy.append(el('span','YOUR CONNECTED AGENT LANDSCAPE','eyebrow'),el('h3','Your agents, connected.'),el('p',index.totals.agents+' '+(index.totals.agents===1?'agent':'agents')+' across '+index.sources.length+' saved registries.'),el('small','Bright nodes are registries. Lines connect them to this workspace. Select a node to explore its agents.'));globe.append(copy);
  const drawing=shape('svg',{viewBox:'0 0 700 510',role:'group','aria-label':'Registry network globe'});
  const defs=shape('defs',{}),sphere=shape('radialGradient',{id:'registry-sphere',cx:'35%',cy:'25%',r:'80%'});
  for(const [offset,color]of [['0%','#25312f'],['48%','#0c1111'],['100%','#020303']])sphere.append(shape('stop',{offset,'stop-color':color}));defs.append(sphere);drawing.append(defs);
  drawing.append(shape('ellipse',{cx:350,cy:468,rx:175,ry:14,fill:'#000','fill-opacity':.4}));
  drawing.append(shape('circle',{cx:350,cy:255,r:207,fill:'none',stroke:'#a4e4c6','stroke-opacity':.08,'stroke-width':10}),shape('circle',{cx:350,cy:255,r:202,fill:'url(#registry-sphere)',stroke:'#b5e5ce','stroke-opacity':.35}));
  // Decorative spherical texture has no geographic or inventory meaning.
  const texture=shape('g',{'aria-hidden':'true','pointer-events':'none'});
  for(let i=0;i<460;i++){const z=1-2*(i+.5)/460,angle=i*2.399963,r=Math.sqrt(1-z*z),x=r*Math.cos(angle),depth=r*Math.sin(angle);if(depth<-.15)continue;const px=350+x*198,py=255+z*198;texture.append(shape('circle',{cx:px,cy:py,r:depth>0?1.1:.7,fill:'#b3d0c2','fill-opacity':.12+Math.max(0,depth)*.3}));}
  drawing.append(texture);
  const shown=index.sources.slice(0,24);
  shown.forEach((registry,i)=>{const a=i*2.39996,r=55+125*Math.sqrt(i/Math.max(1,shown.length-1)),x=350+Math.cos(a)*r,y=255+Math.sin(a)*r*.9;
   drawing.append(shape('path',{d:`M 350 255 Q ${350+(x-350)*.2} ${y-65} ${x} ${y}`,fill:'none',stroke:'#9df1c5','stroke-opacity':.65,'stroke-width':1.5}));
   const g=shape('g',{tabindex:0,role:'button','aria-label':registry.name+': '+registry.agents+' agents',class:'globe-node'}),title=shape('title',{});title.textContent=registry.name+' · '+registry.agents+' agents';g.append(title,shape('circle',{cx:x,cy:y,r:16,fill:'transparent'}),shape('circle',{cx:x,cy:y,r:Math.min(10,5+Math.sqrt(registry.agents)),fill:registry.health==='error'?'#f4b77b':'#b9ffd9',stroke:'#1b4636','stroke-width':3}));
   const choose=()=>{onExplore?.();source.value=registry.id;tablePage=0;renderTable();tableHeading.scrollIntoView({block:'start',behavior:'smooth'});};g.onclick=choose;g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose();}};drawing.append(g);
  });
  drawing.append(shape('circle',{cx:350,cy:255,r:13,fill:'#fff',stroke:'#7bd8aa','stroke-width':5}));const centerTitle=shape('title',{});centerTitle.textContent='This workspace';drawing.lastChild.append(centerTitle);globe.append(drawing);
  if(!shown.length)copy.append(el('p','Connect your first registry to populate the globe.'));
  if(index.sources.length>24)copy.append(el('small','Showing 24 registry nodes. Browse every registry below.'));
 }
 function renderCards(){const result=CatProofMapping.registryPage(index,cardPage);cardPage=result.page;cards.replaceChildren();
  for(const s of result.rows){const c=el('article',undefined,'mapping-card'),badge=el('span',({ok:'LAST SYNC SUCCEEDED',error:'SYNC ERROR',paused:'PAUSED',pending:'NOT SYNCED'})[s.health]||'NOT SYNCED','badge '+(s.health==='error'?'error':s.health==='paused'?'paused':''));c.append(badge,el('h4',s.name),el('small',s.kind));if(s.mode==='seeded')c.append(el('span','SEEDED DEMO','badge'));
   c.append(el('p',s.agents+' canonical agents · '+s.identities+' identities in scope'),el('p',s.assets+' related assets'+(s.attention?' · '+s.attention+' stale / missing identities':'')),el('small','Last successful sync: '+date(s.lastSuccess)));
   const b=el('button','Show agent mappings');b.type='button';b.setAttribute('aria-label','Show mappings from '+s.name);b.onclick=()=>{source.value=s.id;tablePage=0;renderTable();tableHeading.scrollIntoView({block:'start',behavior:'smooth'});};c.append(b);cards.append(c);
  }
  if(!result.total)cards.append(el('p','No connected registries yet. Manually registered agents still appear below.'));
  cardPrev.disabled=result.page===0;cardNext.disabled=result.page>=result.pageCount-1;cardLabel.textContent=result.total+' registries · page '+(result.page+1)+' of '+result.pageCount;cardPager.hidden=result.total<=8;
 }
 function renderTable(){if(!index)return;const result=CatProofMapping.page(index,{query:search.value,source:source.value,focus:focus.value,page:tablePage,pageSize:pageSize.value});tablePage=result.page;tbody.replaceChildren();
  for(const r of result.rows){const row=el('tr'),identity=el('td'),name=el('button',r.name,'agent-link');name.type='button';name.onclick=()=>onOpen(r.id,false);identity.append(name,el('small',r.id));if(r.provenance==='seeded')identity.append(el('span','SEEDED DEMO','badge'));row.append(identity,el('td',r.owner||'Unassigned',r.owner?'':'missing'));
   const bindings=el('td'),sorted=source.value?[...r.bindings.filter(b=>b.connectionId===source.value),...r.bindings.filter(b=>b.connectionId!==source.value)]:r.bindings;
   for(const b of sorted.slice(0,3)){const item=el('div',undefined,'mapping-binding');item.append(el('strong',b.registry),el('small',b.name+' · '+b.sourceId));if(b.provenance==='seeded')item.append(el('span','SEEDED DEMO','badge'));bindings.append(item);}
   if(!r.bindings.length)bindings.append(el('small','No source identity linked'));
   if(r.bindings.length>3){const more=el('button','+'+(r.bindings.length-3)+' more identities','record-link');more.type='button';more.onclick=()=>onOpen(r.id,true);bindings.append(more);}row.append(bindings);
   const evidence=el('td');evidence.append(el('span',r.registryCount+' registries · '+r.bindings.length+' identities'),el('small',r.attention?r.attention+' stale / missing source links':r.unlinked?'No source evidence':'Saved observations'),el('small','Last observed: '+date(r.lastSeen)));row.append(evidence);
   const action=el('td'),graph=el('button','Relationships','record-link');graph.type='button';graph.setAttribute('aria-label','Relationships for '+r.name);graph.onclick=()=>onOpen(r.id,true);action.append(graph);row.append(action);tbody.append(row);
  }
  count.textContent=result.start+'–'+result.end+' of '+result.total+' agents · '+result.pageSize+' rows per page';empty.hidden=result.total!==0;prev.disabled=result.page===0;next.disabled=result.page>=result.pageCount-1;pageLabel.textContent='Page '+(result.page+1)+' of '+result.pageCount;
 }
 function update(nextSnapshot,nextScope){const key=JSON.stringify(nextScope);if(snapshot===nextSnapshot&&scopeKey===key)return;const scopeChanged=scopeKey!==key;snapshot=nextSnapshot;scopeKey=key;scope=nextScope;index=CatProofMapping.createIndex(snapshot,scope);if(scopeChanged){tablePage=0;cardPage=0;}
  const chosen=source.value;source.replaceChildren();const all=el('option','All registries');all.value='';source.append(all);for(const s of index.sources){const o=el('option',s.name);o.value=s.id;source.append(o);}if(index.sources.some(s=>s.id===chosen))source.value=chosen;
  metrics.replaceChildren();for(const [n,label]of [[index.totals.agents,'Agents'],[index.totals.multiRegistry,'Across registries'],[index.totals.missingOwner,'Need an owner'],[index.totals.staleLinks,'Need a refresh']]){const c=el('div');c.append(el('strong',String(n)),el('span',label));metrics.append(c);}
  status.textContent='Saved inventory only. This view does not contact source systems.';renderGlobe();renderCards();renderTable();
 }
 const changed=()=>{clearTimeout(timer);tablePage=0;renderTable();};search.addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(changed,150);});source.onchange=focus.onchange=pageSize.onchange=changed;
 clear.onclick=()=>{search.value='';source.value='';focus.value='all';changed();};prev.onclick=()=>{tablePage--;renderTable();};next.onclick=()=>{tablePage++;renderTable();};cardPrev.onclick=()=>{cardPage--;renderCards();};cardNext.onclick=()=>{cardPage++;renderCards();};
 reload.onclick=async()=>{reload.disabled=true;status.textContent='Loading saved inventory…';try{update(await onReload(),scope);}catch{status.textContent='Saved inventory could not be loaded. The previous view is retained.';}finally{reload.disabled=false;}};
 return {update};
}
window.CatProofMappingView={create};
})();
