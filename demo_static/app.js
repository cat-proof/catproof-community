'use strict';
const $=id=>document.getElementById(id),M=globalThis.CatProofDemo;
let demo=M.create(),selected='invoice',step=0;
const tour=[
 ['Discover the landscape','Seven fictional agents have ten source identities across five platforms. The business inventory stays separate from the provider inventory.'],
 ['One agent, multiple identities','Open Order exception assistant. Two explicitly linked source identities support one accountable record. Names never auto-merge agents.'],
 ['Make ownership explicit','Open Invoice triage and assign its fictional finance owner. A database role or a developer account is source evidence, not necessarily a business owner.'],
 ['Refresh evidence. Keep the decision.','Simulate an invoice source refresh. The source timestamps and metadata change; the canonical owner and lifecycle remain. Inspect the separate history events.']
];
function el(tag,text,cls){const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;}
function badge(text,cls=''){return el('span',text,'badge '+cls);}
function openRecord(id){selected=id;renderDetail();if(!$('demo-detail').open)$('demo-detail').showModal();}
function render(){
 const counts=M.metrics(demo);
 $('demo-metrics').replaceChildren(...[[counts.records,'Canonical agents'],[counts.identities,'Source identities'],[counts.sources,'Simulated sources'],[counts.missingOwners,'Missing an owner']].map(([n,label])=>{const c=el('article');c.append(el('strong',n),el('span',label));return c;}));
 $('demo-sources').replaceChildren(...demo.sources.map((name,i)=>{const c=el('div');c.append(el('span',String(i+1).padStart(2,'0'),'source-number'),el('strong',name),el('small','SIMULATED CONNECTION'));return c;}));
 const query=$('demo-search').value.toLowerCase(),filter=$('demo-filter').value;
 const rows=demo.records.filter(a=>(!query||[a.name,a.purpose,a.owner].join(' ').toLowerCase().includes(query))&&(filter==='all'||filter==='owner'&&!a.owner||filter==='multiple'&&a.identities.length>1||filter==='stale'&&a.identities.some(i=>i.stale)));
 $('demo-rows').replaceChildren(...rows.map(a=>{const row=el('tr'),name=el('td'),link=el('button',a.name,'agent-link');link.onclick=()=>openRecord(a.id);name.append(link,el('small',a.purpose));const owner=el('td');owner.append(a.owner?el('span',a.owner):badge('Owner needed','warning'));const life=el('td');life.append(badge(a.lifecycle));const ids=el('td');ids.append(el('strong',a.identities.length+(a.identities.length===1?' identity':' identities')),el('small',a.identities.map(i=>i.source).join(' · ')));const attention=el('td');attention.append(badge(!a.owner?'Missing owner':a.identities.some(i=>i.stale)?'Stale evidence':a.review==='unreviewed'?'Review needed':'Reviewed',!a.owner||a.identities.some(i=>i.stale)||a.review==='unreviewed'?'warning':''));row.append(name,owner,life,ids,attention);return row;}));
 $('demo-empty').hidden=rows.length>0;
 $('step-label').textContent='GUIDED STORY · '+(step+1)+' OF '+tour.length;$('step-title').textContent=tour[step][0];$('step-copy').textContent=tour[step][1];$('step-count').textContent=(step+1)+' / '+tour.length;$('previous-step').disabled=step===0;$('next-step').textContent=step===tour.length-1?'Back to start ↺':'Next →';
}
function renderDetail(){
 const a=M.find(demo,selected);$('detail-name').textContent=a.name;$('detail-purpose').textContent=a.purpose;$('detail-owner').textContent=a.owner||'No accountable owner assigned';$('detail-lifecycle').textContent='Canonical lifecycle: '+a.lifecycle+' · Review: '+a.review;$('assign-owner').hidden=!!a.owner;$('assign-owner').textContent=selected==='invoice'?'Assign Jordan Lee (fictional)':'Assign Jordan Lee (fictional demo owner)';
 $('demo-identities').replaceChildren(...a.identities.map(i=>{const card=el('article');card.append(badge('FICTIONAL SOURCE'),el('h4',i.source),el('p','Source ID: '+i.id),el('p','Source owner: '+i.owner),el('p','Source status: '+i.status),el('p','Last seen: '+i.lastSeen));if(i.stale)card.append(badge('Stale evidence','warning'));return card;}));
 $('demo-history').replaceChildren(...a.history.map(event=>{const card=el('article',undefined,'history-event');card.append(el('strong',event.label),el('p',event.detail));return card;}));$('detail-message').textContent='';
}
$('assign-owner').onclick=()=>{M.assignOwner(demo,selected);render();renderDetail();$('detail-message').textContent='Simulated owner assigned in this tab only.';};
$('simulate-refresh').onclick=()=>{const owner=M.find(demo,selected).owner;M.refresh(demo,selected);render();renderDetail();$('detail-message').textContent=owner?'Simulated refresh complete. Accountable owner preserved: '+owner:'Simulated refresh complete. No business owner was inferred from the source.';};
$('close-detail').onclick=()=>$('demo-detail').close();$('open-invoice').onclick=()=>openRecord('invoice');
$('reset-demo').onclick=()=>{demo=M.create();step=0;$('demo-search').value='';$('demo-filter').value='all';$('demo-detail').close();render();$('demo-message').textContent='Fictional scenario reset. Live records were never changed.';};
$('start-tour').onclick=()=>{step=0;render();document.querySelector('.tour').scrollIntoView({behavior:'smooth',block:'center'});};
$('next-step').onclick=()=>{step=(step+1)%tour.length;render();if(step===1)openRecord('orders');if(step===2||step===3)openRecord('invoice');};$('previous-step').onclick=()=>{step=Math.max(0,step-1);render();};
$('copy-link').onclick=async()=>{try{await navigator.clipboard.writeText(location.origin+'/demo');$('demo-message').textContent='Public fictional demo link copied. No sign-in is required.';}catch{$('demo-message').textContent='Share this page: '+location.origin+'/demo';}};
$('demo-search').oninput=render;$('demo-filter').onchange=render;render();
