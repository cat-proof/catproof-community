const test=require('node:test'),assert=require('node:assert/strict'),G=require('./federation_static/graph-model.js');
const observation=(id,connection='c1',extra={})=>({observation_id:id,connection_id:connection,connection_name:connection==='c1'?'Databricks':'ServiceNow',name:'Same agent name',source_id:'vendor-123',source:'vendor',kind:'agent',provenance:'live',last_seen:'2026-10-27T12:00:00Z',present:true,stale:false,...extra});
function fixture(){const asset={asset_id:'mcp1',name:'Finance MCP',resource_type:'mcp_server',connection_id:'c3',connection_name:'Tool registry',source_id:'mcp-vendor',provenance:'live',last_seen:'2026-10-27T12:00:00Z',present:true,linked_agents:['a','b','hidden']};return {agents:[{registry_id:'a',name:'Invoice assistant',business_owner:'Finance team',lifecycle:'active',provenance:'live',linked_records:[observation('o1'),observation('o2','c2')],linked_assets:[asset]},{registry_id:'b',name:'Approval assistant',provenance:'manual',business_owner:'',linked_records:[],linked_assets:[asset]},{registry_id:'hidden',name:'Seeded agent',provenance:'seeded',linked_records:[],linked_assets:[]}],events:[]};}
test('canonical owner stays independent from vendor owner and identity names never merge',()=>{
 const s=fixture();s.agents[0].linked_records[0].owner='Vendor admin';const graph=G.build(s,'a');
 assert.equal(graph.nodes.filter(n=>n.kind==='identity').length,2);assert.equal(graph.nodes.find(n=>n.kind==='owner').label,'Finance team');
 assert.equal(graph.edges.filter(e=>e.type==='identity').length,2);assert.ok(graph.nodes.some(n=>n.id==='registry:c1'));assert.ok(graph.nodes.some(n=>n.id==='registry:c2'));
 assert.ok(!graph.nodes.some(n=>n.label==='Vendor admin'));assert.ok(graph.edges.every(e=>e.evidence.basis));
});
test('only explicitly linked assets and shared agents are projected, never inferred calls',()=>{
 const s=fixture();s.agents.push({registry_id:'unrelated',name:'Invoice assistant',provenance:'manual',linked_records:[],linked_assets:[]});
 const g=G.build(s,'a');assert.ok(g.nodes.some(n=>n.id==='agent:b'));assert.ok(!g.nodes.some(n=>n.id==='agent:unrelated'||n.id==='agent:hidden'));
 assert.equal(g.edges.filter(e=>e.type==='shared_asset').length,1);assert.equal(g.edges.find(e=>e.type==='asset').label,'Linked asset');assert.ok(!g.edges.some(e=>/calls|uses/.test(e.type)));
});
test('seeded, paused, source and relationship filters preserve explicit scope',()=>{
 const s=fixture();s.agents[0].linked_records.push(observation('demo','seed',{provenance:'seeded'}),observation('paused','pause',{connection_enabled:false}));
 let g=G.build(s,'a');assert.ok(!g.nodes.some(n=>['identity:demo','identity:paused'].includes(n.id)));
 g=G.build(s,'a',{includeSeeded:true,includePaused:true});assert.ok(g.nodes.some(n=>n.id==='identity:demo'&&n.provenance==='seeded'));assert.ok(g.nodes.some(n=>n.id==='identity:paused'&&n.freshness==='paused'));
 g=G.build(s,'a',{sourceId:'c2'});assert.equal(g.nodes.filter(n=>n.kind==='identity').length,1);assert.equal(g.nodes.filter(n=>n.kind==='asset').length,0);
 g=G.build(s,'a',{kind:'assets'});assert.equal(g.nodes.filter(n=>n.kind==='identity').length,0);assert.equal(g.nodes.filter(n=>n.kind==='asset').length,1);
 assert.equal(G.build(s,'hidden').nodes.length,0);assert.equal(G.build(s,'missing').edges.length,0);
});
test('attention distinguishes missing ownership and stale/not-observed evidence',()=>{
 const s=fixture();s.agents[0].business_owner='';s.agents[0].linked_records[0].stale=true;s.agents[0].linked_records[1].present=false;
 const g=G.build(s,'a',{attentionOnly:true});assert.ok(g.edges.some(e=>e.type==='owner'&&e.evidence.freshness==='missing'));assert.ok(g.edges.some(e=>e.evidence.freshness==='stale'));assert.ok(g.edges.some(e=>e.evidence.freshness==='not observed'));assert.ok(g.edges.every(e=>e.attention));
});
test('graph is deterministic and does not mutate records or include credential configuration',()=>{
 const s=fixture();s.sources=[{id:'c1',config:{token:'should-never-appear'},credential_prefix:'SECRET'}];const before=JSON.stringify(s);const a=G.build(s,'a'),b=G.build(s,'a');
 assert.deepEqual(a,b);assert.equal(JSON.stringify(s),before);assert.ok(!JSON.stringify(a).includes('should-never-appear'));assert.ok(!JSON.stringify(a).includes('SECRET'));
 assert.ok(a.edges.every(e=>a.nodes.some(n=>n.id===e.from)&&a.nodes.some(n=>n.id===e.to)));
});
test('local records work before any connector sync and ownership changes update graph',()=>{
 const s={agents:[{registry_id:'local',name:'Local agent',provenance:'manual',business_owner:'',linked_records:[],linked_assets:[]}]};
 assert.equal(G.build(s,'local').edges.length,1);assert.equal(G.build(s,'local').nodes.find(n=>n.kind==='owner').attention,true);
 s.agents[0].business_owner='Support';assert.equal(G.build(s,'local').nodes.find(n=>n.kind==='owner').label,'Support');assert.equal(G.build(s,'local',{attentionOnly:true}).edges.length,0);
});
