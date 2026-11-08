// Shared accessible browser sign-in dialog for setup and the full workspace.
window.MosaicEarthBrowserSignIn = function (options) {
  const dialog = document.createElement('dialog');
  dialog.style.cssText='max-width:540px;width:calc(100vw - 32px);border:1px solid #789c34;border-radius:14px;padding:26px;background:#111b14;color:#edf3ee;z-index:20000';
  const add = (tag,text) => {const e=document.createElement(tag);e.textContent=text;dialog.append(e);return e;};
  const title=add('h2',options.activation?'Activate this computer':'Connect MosaicEarth Cloud');
  title.id='desktopBrowserSignInTitle';dialog.setAttribute('aria-labelledby',title.id);
  add('p','Continue in your browser with Google, GitHub, Microsoft or your MosaicEarth email. Check the account and matching code before confirming.');
  if (options.activation) add('p','Use the account with your Desktop purchase or Studio plan. Cloud synchronization is a separate, optional connection.');
  const status=add('p','Ready to sign in.');status.setAttribute('role','status');status.style.overflowWrap='anywhere';
  const code=add('strong','');code.style.cssText='display:block;font-size:24px;letter-spacing:3px;margin:12px 0';
  const link=add('a','Open sign-in page ↗');link.hidden=true;link.target='_blank';link.rel='noopener noreferrer';link.style.cssText='color:#c5e697;margin:12px 0;min-height:44px';
  const button=add('button','Continue in browser');button.type='button';
  const cancel=add('button','Cancel');cancel.type='button';
  for (const b of [button,cancel]) b.style.cssText='min-height:44px;padding:10px 16px;border:1px solid #8aa65f;border-radius:8px;margin:8px 8px 0 0;background:#99cc00;color:#102005;font-weight:bold;cursor:pointer';
  cancel.style.background='transparent';cancel.style.color='#edf3ee';
  let timer, active=false, completed=false, closed=false, expires=0;
  async function close(){if(closed)return;closed=true;clearTimeout(timer);if(active&&!completed){try{await options.cancel();}catch(e){/* Server expiry also closes abandoned requests. */}}dialog.remove();}
  dialog.addEventListener('close',close);dialog.addEventListener('cancel',()=>dialog.close());cancel.onclick=()=>dialog.close();
  async function poll(){
    if(closed)return;
    try{
      if(Date.now()/1000>=expires)throw Error('Sign-in expired. Start again to get a new code.');
      const result=await options.poll();
      if(closed)return;
      if(result.status==='complete'){
        completed=true;status.textContent=(options.activation?'Activated for ':'Cloud connected as ')+result.email+'.';
        cancel.textContent='Continue';cancel.onclick=()=>{dialog.close();options.complete(result);};
        button.hidden=true;link.hidden=true;code.hidden=true;cancel.focus();return;
      }
      if(result.status!=='pending')throw Error(result.status==='denied'?'Sign-in canceled in the browser. Nothing was connected.':'This sign-in request is no longer active. Start again.');
      timer=setTimeout(poll,5000);
    }catch(error){status.textContent=error.message;status.setAttribute('role','alert');button.disabled=false;button.textContent='Start again';}
  }
  button.onclick=async()=>{
    button.disabled=true;status.setAttribute('role','status');status.textContent='Preparing sign-in…';
    try{
      if(active)await options.cancel();
      const state=await options.start();active=true;
      if(closed){await options.cancel();return;}
      expires=state.expires_at;code.textContent=state.user_code;code.hidden=false;
      button.textContent='Waiting for browser…';
      link.href=state.verification_uri;link.hidden=false;
      status.textContent='Waiting for confirmation in your browser. Your account will appear here when it succeeds.';
      try{await options.open(state.verification_uri);}catch(error){status.textContent='Open the sign-in page below, then return here after confirming the account.';}
      timer=setTimeout(poll,5000);
    }catch(error){status.textContent=error.message;status.setAttribute('role','alert');button.disabled=false;}
  };
  document.body.append(dialog);dialog.showModal();button.focus();
};
