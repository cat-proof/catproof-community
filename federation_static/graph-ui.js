/* SVG and evidence inspector use textContent only, including vendor-supplied names. */
(function(){
'use strict';
const NS='http://www.w3.org/2000/svg',el=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;},svg=(tag,attrs={})=>{const e=document.createElementNS(NS,tag);for(const [key,value]of Object.entries(attrs))e.setAttribute(key,String(value));return e;};
const date=value=>value?new Date(typeof value==='number'?value*1000:value).toLocaleString():'Not recorded';
const titles={agent:'Canonical agent',owner:'Accountable owner',identity:'Source identity',registry:'Source registry',asset:'Related asset'};
const activate=(node,fn)=>{node.addEventListener('click',fn);node.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();fn();}});};
function mount(target,snapshot,agentId,scope,onOpen){
 target.replaceChildren();let selected=null,zoom=1;
 const intro=el('p','Explore recorded ownership, identity bindings and related assets. Runtime calls are not measured.','graph-intro');target.append(intro);
 const controls=el('div',undefined,'graph-controls');const field=(name,input)=>{const label=el('label',name);label.append(input);controls.append(label);return input;};
 const source=field('Source registry',el('select'));source.setAttribute('aria-label','Relationship source registry');
 const kind=field('Relationships',el('select'));kind.setAttribute('aria-label','Relationship type');for(const [id,label]of [['all','All relationships'],['identities','Source identities'],['assets','Related assets']]){const o=el('option',label);o.value=id;kind.append(o);}
 const attention=el('input');attention.type='checkbox';field('Needs attention only',attention);
 const refresh=el('button','Reset filters');refresh.type='button';controls.append(refresh);target.append(controls);
 const summary=el('p',undefined,'graph-summary');summary.setAttribute('role','status');target.append(summary);
 const layout=el('div',undefined,'graph-layout'),stageWrap=el('section',undefined,'graph-canvas'),inspector=el('aside',undefined,'graph-inspector');inspector.setAttribute('aria-label','Relationship evidence');
 const toolbar=el('div',undefined,'graph-toolbar');for(const [label,delta]of [['Zoom out',-.15],['Zoom in',.15]]){const b=el('button',label);b.type='button';b.onclick=()=>{zoom=Math.max(.5,Math.min(1.6,zoom+delta));size();};toolbar.append(b);}stageWrap.append(toolbar);
 const stage=el('div',undefined,'graph-stage');stage.setAttribute('aria-label','Agent relationship map');stageWrap.append(stage);layout.append(stageWrap,inspector);target.append(layout);
 const connections=el('details',undefined,'graph-connections');connections.append(el('summary','Browse every connection'));const connectionList=el('div');connections.append(connectionList);target.append(connections);
 let graph,drawing,height=460,width=1200;const names=new Map();
 const fit=el('button','Fit graph');fit.type='button';fit.onclick=()=>fitGraph();toolbar.append(fit);
 function fitGraph(){zoom=Math.max(.3,Math.min(1,stage.clientWidth/width,Math.max(250,Math.min(440,window.innerHeight-430))/height));size();}
 function size(){if(drawing){drawing.setAttribute('width',width*zoom);drawing.setAttribute('height',height*zoom);}}
 function showEdge(edge){selected=edge.id;inspector.replaceChildren(el('span','RELATIONSHIP EVIDENCE','eyebrow'),el('h3',edge.label),el('p',names.get(edge.from)+' → '+names.get(edge.to)));const dl=el('dl');
  const pairs=[['Evidence basis',edge.evidence.basis],['Source',edge.evidence.source],['Source identifier',edge.evidence.sourceId],['Provenance',edge.evidence.provenance==='seeded'?'SEEDED DEMO':edge.evidence.provenance],['Evidence state',edge.evidence.freshness],['Last observed',date(edge.evidence.lastSeen)],['Assignment recorded',edge.type==='owner'?date(edge.evidence.recordedAt):null],['Source status',edge.evidence.sourceStatus]];
  for(const [key,value]of pairs){if(value===undefined||value===null)continue;dl.append(el('dt',key),el('dd',String(value)));}inspector.append(dl);if(edge.evidence.verification)inspector.append(el('p',edge.evidence.verification,'graph-note'));mark();
 }
 function showNode(node){selected=node.id;inspector.replaceChildren(el('span',titles[node.kind].toUpperCase(),'eyebrow'),el('h3',node.label),el('p',node.detail||''));if(node.provenance==='seeded')inspector.append(el('p','SEEDED DEMO','badge'));
  if(node.kind==='agent'&&node.recordId!==agentId){const b=el('button','Open agent record');b.type='button';b.onclick=()=>onOpen(node.recordId);inspector.append(b);}
  inspector.append(el('h4','Recorded connections'));for(const edge of graph.edges.filter(e=>e.from===node.id||e.to===node.id)){const b=el('button',edge.label+' · '+names.get(edge.from===node.id?edge.to:edge.from),'graph-evidence-link');b.type='button';b.onclick=()=>showEdge(edge);inspector.append(b);}if(!graph.edges.some(e=>e.from===node.id||e.to===node.id))inspector.append(el('p','No connections match these filters.'));mark();
 }
 function mark(){stage.querySelectorAll('[data-graph-id]').forEach(n=>n.classList.toggle('graph-selected',n.getAttribute('data-graph-id')===selected));}
 function draw(){
  const base=CatProofGraph.build(snapshot,agentId,{...scope,kind:kind.value});const chosen=source.value;source.replaceChildren();const all=el('option','All source registries');all.value='';source.append(all);for(const item of base.sources){const o=el('option',item.label);o.value=item.id;source.append(o);}if(base.sources.some(s=>s.id===chosen))source.value=chosen;
  graph=CatProofGraph.build(snapshot,agentId,{...scope,sourceId:source.value,kind:kind.value,attentionOnly:attention.checked});names.clear();graph.nodes.forEach(n=>names.set(n.id,n.label));
  summary.textContent=graph.nodes.length+' '+(graph.nodes.length===1?'node':'nodes')+' · '+graph.edges.length+' recorded '+(graph.edges.length===1?'connection':'connections')+(graph.message?' · '+graph.message:'')+' · Uses the inventory’s seeded and paused-source scope.';
  stage.replaceChildren();connectionList.replaceChildren();inspector.replaceChildren(el('h3','Inspect a connection'),el('p','Select a node or a line to inspect its evidence. You can also browse every connection below the map.'));
  if(!graph.focusId){stage.append(el('p',graph.message));return;}
  const left=graph.nodes.filter(n=>['owner','registry'].includes(n.kind)),right=graph.nodes.filter(n=>['identity','asset'].includes(n.kind)),far=graph.nodes.filter(n=>n.kind==='agent'&&n.id!==graph.focusId);height=Math.max(460,Math.max(left.length,right.length,far.length)*92+80);
  const positions=new Map();positions.set(graph.focusId,{x:340,y:height/2-36});for(const [list,x]of [[left,28],[right,650],[far,960]])list.forEach((n,i)=>positions.set(n.id,{x,y:50+i*92}));
  width=far.length?1200:900;drawing=svg('svg',{viewBox:'0 0 '+width+' '+height,role:'group','aria-label':'Recorded agent relationships'});size();const title=svg('title');title.textContent='Relationships for '+(names.get(graph.focusId)||'agent');drawing.append(title);
  for(const edge of graph.edges){const a=positions.get(edge.from),b=positions.get(edge.to);if(!a||!b)continue;const forward=b.x>a.x,x1=a.x+(forward?208:0),x2=b.x+(forward?0:208),y1=a.y+36,y2=b.y+36,m=(x1+x2)/2;const path=svg('path',{d:`M ${x1} ${y1} C ${m} ${y1}, ${m} ${y2}, ${x2} ${y2}`,class:'graph-edge '+edge.type+(edge.attention?' attention':''),'data-graph-id':edge.id,tabindex:0,role:'button','aria-label':edge.label+': '+names.get(edge.from)+' to '+names.get(edge.to)});activate(path,()=>showEdge(edge));drawing.append(path);
   const button=el('button',names.get(edge.from)+' → '+edge.label+' → '+names.get(edge.to),'graph-evidence-link');button.type='button';button.onclick=()=>showEdge(edge);connectionList.append(button);
  }
  for(const node of graph.nodes){const p=positions.get(node.id),g=svg('g',{transform:`translate(${p.x} ${p.y})`,class:'graph-node '+node.kind+(node.id===graph.focusId?' focus':'')+(node.attention?' attention':''),'data-graph-id':node.id,tabindex:0,role:'button','aria-label':titles[node.kind]+': '+node.label});
   const title=svg('title');title.textContent=node.label+' · '+(node.detail||'');g.append(title,svg('rect',{width:208,height:72,rx:10}));
   for(const [y,text,cls]of [[17,titles[node.kind].toUpperCase(),'graph-node-kind'],[37,node.label,'graph-node-label'],[57,node.provenance==='seeded'?'SEEDED DEMO':node.freshness||node.detail||'','graph-node-detail']]){const t=svg('text',{x:12,y,class:cls});t.textContent=String(text).length>28?String(text).slice(0,26)+'…':text;g.append(t);}activate(g,()=>showNode(node));drawing.append(g);
  }
  stage.append(drawing);fitGraph();selected=null;
 }
 source.onchange=draw;kind.onchange=()=>{source.value='';draw();};attention.onchange=draw;refresh.onclick=()=>{source.value='';kind.value='all';attention.checked=false;zoom=1;draw();};draw();
}
window.CatProofGraphView={mount};
})();
