// Team controls use server-enforced registry roles; no tokens are persisted in the browser.
const securityDialog=document.createElement('dialog');securityDialog.id='security-dialog';securityDialog.setAttribute('aria-labelledby','security-dialog-title');
securityDialog.innerHTML=`<div class="dialog-heading"><span class="eyebrow">REGISTRY ACCESS</span><button id="close-security" type="button" aria-label="Close team and security">✕</button></div><h2 id="security-dialog-title">Team & security</h2><p id="security-status" role="status"></p><div id="security-content"></div>`;
document.body.append(securityDialog);
$('close-security').onclick=()=>securityDialog.close();
securityDialog.addEventListener('close',()=>{$('security-content').replaceChildren();$('security-status').textContent='';});
$('security-settings').onclick=()=>{securityDialog.showModal();loadSecurity();};
const secField=(form,label,name,type='text')=>{const l=make('label',label),input=make('input');input.name=name;input.type=type;input.required=true;l.append(input);form.append(l);return input;};
const secSelect=(form,label,name,values)=>{const l=make('label',label),input=make('select');input.name=name;for(const [value,text] of values)input.append(new Option(text,value));l.append(input);form.append(l);return input;};
function secForm(target,title,button,submit){const form=make('form',undefined,'security-form');form.append(make('h3',title));target.append(form);form.addEventListener('submit',async e=>{e.preventDefault();const b=form.querySelector('button[type=submit]');b.disabled=true;try{await submit(Object.fromEntries(new FormData(form)),form);}catch(error){$('security-status').textContent=error.message;}finally{b.disabled=false;form.querySelectorAll('input[type=password]').forEach(i=>i.value='');}});const finish=()=>{const b=make('button',button,'primary');b.type='submit';form.append(b);};return {form,finish};}
async function loadSecurity(){
 const target=$('security-content');target.replaceChildren(make('p','Loading registry security…'));
 try{
 const data=await api('/api/security');target.replaceChildren();
 target.append(make('p','Your role: '+data.role+'. Sessions expire after '+data.idle_minutes+' minutes of inactivity and at most eight hours.','hint'));
 if(data.bootstrap_account){
  const upgrade=secForm(target,'Replace shared team sign-in','Use my email and retire shared sign-in',async(values)=>{const r=await fetch('/account/upgrade',{method:'POST',body:new URLSearchParams(values)});if(r.redirected){location.reload();return;}const result=await r.json();throw Error(result.error||'Account upgrade failed.');});
  upgrade.form.append(make('p','This changes your account to email/password, disables shared team sign-in and signs out existing team sessions. Invite teammates with their own email accounts afterwards. Existing registry records stay here.','hint'));
  secField(upgrade.form,'Your name','name').value=window.catproofAccount.name;secField(upgrade.form,'Your email','email','email');secField(upgrade.form,'Current team password','current_password','password');const next=secField(upgrade.form,'New personal password','new_password','password');next.minLength=12;next.maxLength=128;upgrade.finish();
 }else if(data.bootstrap_enabled&&data.role==='owner')target.append(make('p','Compatibility sign-in remains enabled for the original team. An original team account can retire it from this panel. Disable it before a production enterprise rollout.','security-warning'));
 if(data.registries.length>1){const s=secForm(target,'Open another registry','Open registry',async(v)=>{await api('/api/security/switch',v);location.reload();});secSelect(s.form,'Registry','registry_id',data.registries.map(r=>[r.id,r.name+' · '+r.role]));s.finish();}
 target.append(make('h3','Registry members'));
 const members=make('div',undefined,'security-members');target.append(members);
 for(const member of data.members){const row=make('article',undefined,'event');row.append(make('strong',member.name),make('p',member.email+' · '+member.role));if(data.role==='owner'&&!data.bootstrap_account){const s=secForm(row,'Change access','Save access',async(v)=>{if(v.role==='remove'&&!confirm('Remove this member’s registry access and sign out their sessions in this registry?'))return;await api('/api/security/member',{...v,user_id:member.id});await loadSecurity();});secSelect(s.form,'Role','role',[['owner','Owner · manage access and connections'],['editor','Editor · records and refresh'],['viewer','Viewer · read and export'],['remove','Remove access']]).value=member.role;secField(s.form,'Confirm your password','password','password');s.finish();}members.append(row);}
 if(data.role==='owner'&&!data.bootstrap_account){
  const peer=secForm(target,'Read-only registry federation','Create peer token',async v=>{
   const result=await api('/api/security/peer-token',v);await loadSecurity();
   const box=make('div',undefined,'invite-code');box.append(make('strong','Save this token in the receiving registry’s server credentials.'),make('p','Catalog read access only. Expires in 90 days. This token is shown once.'));
   const input=make('textarea');input.readOnly=true;input.value=result.token;input.setAttribute('aria-label','Read-only peer token');box.append(input);$('security-content').prepend(box);
  });
  secField(peer.form,'Receiving registry label','label').maxLength=100;secField(peer.form,'Confirm your password','password','password');peer.finish();
  for(const t of data.peer_tokens||[]){
   const row=make('article',undefined,'event');row.append(make('strong',t.label),make('small',t.revoked_at?'Revoked':'Expires '+new Date(t.expires_at*1000).toLocaleString()));
   if(!t.revoked_at){const f=secForm(row,'Revoke catalog access','Revoke peer token',async v=>{await api('/api/security/revoke-peer-token',{...v,id:t.id});await loadSecurity();});secField(f.form,'Confirm your password','password','password');f.finish();}target.append(row);
  }
  const invite=secForm(target,'Invite a teammate','Create private invitation',async(v,form)=>{const result=await api('/api/security/invite',v);form.querySelectorAll('input[type=password]').forEach(i=>i.value='');const box=make('div',undefined,'invite-code');box.append(make('strong','Share this code privately with '+result.email),make('p','They must sign up or sign in using this exact email, then paste the code below. It expires in 48 hours and works once. This is not email verification.','hint'));const input=make('textarea');input.readOnly=true;input.value=result.code;input.setAttribute('aria-label','Private invitation code');box.append(input);form.append(box);$('security-status').textContent='Invitation created. The code is shown only here; close this panel to clear it.';});
  secField(invite.form,'Teammate email','email','email');secSelect(invite.form,'Access','role',[['viewer','Viewer · read and export'],['editor','Editor · records and refresh']]);secField(invite.form,'Confirm your password','password','password');invite.finish();
  target.append(make('p','Invitations are not emailed automatically. Do not paste invitation codes in public channels.','hint'));
  for(const i of data.invites){const row=make('article',undefined,'event');row.append(make('strong',i.email+' · '+i.role),make('small','Expires '+new Date(i.expires_at*1000).toLocaleString()));const f=secForm(row,'Revoke invitation','Revoke invitation',async v=>{await api('/api/security/cancel-invite',{...v,id:i.id});await loadSecurity();});secField(f.form,'Confirm your password','password','password');f.finish();target.append(row);}
 }
 const accept=secForm(target,'Have an invitation?','Join registry',async v=>{await api('/api/security/accept',v);location.reload();});secField(accept.form,'Private invitation code','code').autocomplete='off';accept.finish();
 target.append(make('h3','Your signed-in sessions'),make('p','Revoking a session takes effect on its next request.','hint'));
 const others=action('Sign out all other sessions',async()=>{try{await api('/api/security/revoke-session',{id:'others'});await loadSecurity();}catch(e){$('security-status').textContent=e.message;}});target.append(others);
 for(const session of data.sessions){const row=make('article',undefined,'event');row.append(make('strong',session.current?'This session':'Another session'),make('small','Last active '+(session.last_seen?new Date(session.last_seen*1000).toLocaleString():'Before session tracking')));row.append(action(session.current?'Sign out this session':'Revoke session',async()=>{try{await api('/api/security/revoke-session',{id:session.id});if(session.current)location.href='/login';else await loadSecurity();}catch(e){$('security-status').textContent=e.message;}}));target.append(row);}
 if(data.role==='owner'){target.append(make('h3','Security audit · latest 500 events'));const link=make('a','Download security audit');link.href='/api/security/audit';link.download='catproof-security-audit.json';target.append(link);for(const event of data.events){const row=make('article',undefined,'event');const who=data.members.find(m=>m.id===event.actor);row.append(make('strong',event.action),make('p',(who?who.email:event.actor)+(event.target?' · '+event.target:'')),make('small',new Date(event.at*1000).toLocaleString()));target.append(row);}}
 target.append(make('p','Email verification, password recovery, SSO/MFA, automated email delivery and immutable external audit storage are not configured. Enterprise production also requires tested backups and a non-expiring database.','hint'));
 }catch(e){target.replaceChildren(make('p',e.message));}
}
