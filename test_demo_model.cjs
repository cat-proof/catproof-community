const test=require('node:test'),assert=require('node:assert/strict');
const M=require('./demo_static/model.js');
test('fictional inventory metrics are derived from actual scenario records',()=>{
 const s=M.create();assert.deepEqual(M.metrics(s),{records:7,identities:10,sources:5,missingOwners:2});
 assert.ok(s.records.every(a=>a.identities.every(i=>i.id.startsWith('demo-'))));
});
test('explicit ownership remains independent of source metadata and simulated refresh',()=>{
 const s=M.create(),before=structuredClone(M.find(s,'invoice').identities);
 assert.equal(M.assignOwner(s,'invoice'),true);assert.equal(M.metrics(s).missingOwners,1);
 const owner=M.find(s,'invoice').owner;M.refresh(s,'invoice');const a=M.find(s,'invoice');
 assert.equal(a.owner,owner);assert.equal(a.lifecycle,'active');assert.equal(a.review,'reviewed');
 assert.deepEqual(a.identities.map(i=>i.owner),before.map(i=>i.owner));
 assert.ok(a.identities.every(i=>i.lastSeen==='Simulated refresh #1'));
 assert.deepEqual(a.history.slice(0,2).map(e=>e.label),['Simulated source refresh','Simulated owner decision']);
 assert.equal(M.assignOwner(s,'invoice'),false);
});
test('refresh never infers business ownership and resets are isolated',()=>{
 const s=M.create();M.refresh(s,'invoice');assert.equal(M.find(s,'invoice').owner,'');
 M.assignOwner(s,'invoice');assert.equal(M.find(M.create(),'invoice').owner,'');
 assert.equal(M.find(s,'forecast').identities[0].stale,true);M.refresh(s,'forecast');assert.equal(M.find(s,'forecast').identities[0].stale,false);
 assert.throws(()=>M.find(s,'missing'),/Unknown demo record/);
});
