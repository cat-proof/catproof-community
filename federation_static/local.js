// Device presence and configured resources are evidence, never inferred canonical agents.
const localNode=(tag,text,cls)=>make(tag,text,cls);
const localError=e=>{$('local-status').textContent=e.message;};
function localForm(parent,title,button,fields,submit){
 const form=localNode('form',undefined,'security-form');form.noValidate=true;form.append(localNode('h3',title));
 for(const [label,name,type] of fields){const l=localNode('label',label),input=localNode('input');input.name=name;input.type=type;input.required=true;input.maxLength=name==='label'?100:128;if(type==='password')input.autocomplete='current-password';l.append(input);form.append(l);}
 const b=localNode('button',button,'primary');b.type='submit';form.append(b);const feedback=localNode('p',undefined,'hint');feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');form.append(feedback);parent.append(form);
 form.onsubmit=async e=>{e.preventDefault();const missing=[...form.querySelectorAll('input[required]')].find(x=>!x.value.trim());if(missing){feedback.textContent=missing.name==='label'?'Enter a device name, for example My Mac.':'Enter your existing CatProof sign-in password.';missing.focus();return;}b.disabled=true;feedback.textContent='Working…';try{await submit(Object.fromEntries(new FormData(form)));feedback.textContent='Done. See the result below.';}catch(error){feedback.textContent=error.message;feedback.setAttribute('role','alert');localError(error);}finally{b.disabled=false;form.querySelectorAll('input[type=password]').forEach(x=>x.value='');}};
}
async function loadLocal(){
 $('local-status').textContent='Loading devices…';
 try{
 const data=await api('/api/local/devices');const root=$('local-setup');root.replaceChildren();const details=localNode('details');details.open=!data.devices.length;details.append(localNode('summary','Set up a collector · download, enroll and run'));const setup=localNode('div');details.append(setup);root.append(details);
 setup.append(localNode('p',data.limitation,'hint'));
 const guide=localNode('a','Read installation & privacy guide ↗');guide.href='/local/guide.md';guide.target='_blank';guide.rel='noopener';setup.append(guide);
 const download=localNode('a','Download collector (Python 3.11+)');download.href='/local/collector.py';download.download='catproof-local.py';setup.append(localNode('p'));setup.append(download);
 setup.append(localNode('p','1. Download and inspect the collector. 2. Preview with: python3 catproof-local.py scan. 3. Enroll using a one-time code. 4. Run it in a terminal to send a heartbeat every minute. Configuration-name discovery and lifecycle hooks are optional.','hint'));
 const command=localNode('pre','python3 catproof-local.py enroll --server '+location.origin+(location.hostname==='localhost'||location.hostname==='127.0.0.1'?' --ca-file /tmp/catproof-local-ca.crt':'')+' && python3 catproof-local.py run');setup.append(command);
 const secret=localNode('div');secret.id='local-pairing-secret';secret.setAttribute('aria-live','polite');
 if(data.role==='owner'){
  setup.append(localNode('p','Use your existing CatProof sign-in password below to authorize this device. You are not creating a new password. Generating a code displays it here; click Copy enrollment code before switching to your terminal.','hint'));
  localForm(setup,'Step 1 · Authorize this device','Generate enrollment code',[['Device label','label','text'],['Your existing CatProof sign-in password','password','password']],async values=>{
   const result=await api('/api/local/pair',values);const target=$('local-pairing-secret');target.replaceChildren();target.closest('details').open=true;target.append(localNode('p','Step 2 · Copy this generated code. Then paste it into the terminal’s One-time enrollment code prompt. This is not your password. It expires in ten minutes and works once.'));
   const code=localNode('input');code.readOnly=true;code.value=result.code;code.autocomplete='off';code.setAttribute('aria-label','One-time local enrollment code');code.onclick=()=>code.select();target.append(code);
   $('local-status').textContent='Code generated. Click Copy enrollment code, then paste into your terminal. Nothing is copied automatically.';
   target.append(action('Copy enrollment code',async()=>{try{await navigator.clipboard.writeText(code.value);$('local-status').textContent='Code copied. Paste it into the collector’s hidden prompt within ten minutes. Run the collector only after enrollment succeeds.';}catch{code.focus();code.select();$('local-status').textContent='Copy unavailable. Select the code and copy it manually; do not share it in chat.';}}));
   target.append(action('Hide code',()=>target.replaceChildren()));target.scrollIntoView({block:'center',behavior:'smooth'});
  });
  setup.append(secret);
  for(const p of data.pending)localForm(setup,'Pending: '+p.label,'Cancel enrollment',[['Confirm your password','password','password']],async v=>{await api('/api/local/cancel-pairing',{...v,id:p.id});await loadLocal();});
 }else setup.append(localNode('p','Ask a registry owner to enroll or revoke devices.'));
 const list=$('local-devices');list.replaceChildren();
 const summary=localNode('div',undefined,'device-summary');for(const [n,label]of [[data.devices.length,'Enrolled devices'],[data.devices.filter(d=>d.status==='reporting').length,'Reporting now'],[data.devices.filter(d=>d.status!=='reporting'&&!d.revoked_at).length,'Not reporting']]){const item=localNode('div');item.append(localNode('strong',String(n)),localNode('span',label));summary.append(item);}list.append(summary);
 for(const d of data.devices){
  const card=localNode('article',undefined,'source-card device-card'),heading=localNode('div',undefined,'device-heading');heading.append(localNode('h3',d.label),localNode('span',d.status.toUpperCase(),'badge '+(d.status==='reporting'?'active':'pending')));card.append(heading,localNode('small','Enrolled by '+d.owner),localNode('p','Last report: '+(d.last_seen?new Date(d.last_seen*1000).toLocaleString():'Waiting for the first report')));
  const tools=d.inventory.tools||[],installed=localNode('div',undefined,'device-tools');for(const t of tools){const item=localNode('div');item.append(localNode('strong',(t.tool==='codex'?'Codex':['claude','claude_code'].includes(t.tool)?'Claude Code':t.tool)+' · '+(t.installed?'Detected':'Not detected')),localNode('small',t.version?'Version '+t.version:'Version not reported'));installed.append(item);}if(!tools.length)installed.append(localNode('p','No tool inventory reported yet. Run the enrolled collector to send a report.','hint'));card.append(installed);
  const sessions=d.inventory.sessions||[],details=localNode('details');details.append(localNode('summary','Reported configuration & recent activity'));
  for(const t of tools){details.append(localNode('h4',t.tool),localNode('p','Configured agents: '+((t.agents||[]).join(', ')||'None reported')),localNode('p','MCP servers: '+((t.mcp_servers||[]).join(', ')||'None reported')));}
  details.append(localNode('p',sessions.length+' recent session observations. These are reports, not an active-process count.','hint'));for(const s of sessions)details.append(localNode('small',s.tool+' · '+s.event+' · '+new Date(s.at*1000).toLocaleString()));card.append(details);
  if(data.role==='owner'&&!d.revoked_at){const manage=localNode('details');manage.append(localNode('summary','Manage reporting access'));manage.append(localNode('p','Credential expires: '+new Date(d.expires_at*1000).toLocaleDateString(),'hint'));localForm(manage,'Revoke reporting access','Revoke device',[['Confirm your password','password','password']],async v=>{await api('/api/local/revoke',{...v,id:d.id});await loadLocal();});card.append(manage);}list.append(card);
 }
 if(!data.devices.length)list.append(localNode('p','No enrolled devices yet. Use the setup steps above to connect your first device.'));
 $('local-status').textContent=data.devices.length+' '+(data.devices.length===1?'device':'devices')+' · presence becomes offline after three minutes without a report.';
 }catch(e){localError(e);}
}
$('local-refresh').onclick=loadLocal;
document.querySelector('[data-view="local"]').addEventListener('click',loadLocal);
// Clear enrollment secrets when leaving the view or tab; they are never stored in browser storage.
document.querySelectorAll('aside button[data-view]').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.view!=='local')$('local-pairing-secret')?.replaceChildren();}));
document.addEventListener('visibilitychange',()=>{if(document.hidden)$('local-pairing-secret')?.replaceChildren();});

// Navigation is available only after its controller has attached. Recover an
// early programmatic navigation while this deferred script was downloading.
document.querySelector('[data-view="local"]').disabled=false;
if(view==='local')loadLocal();
