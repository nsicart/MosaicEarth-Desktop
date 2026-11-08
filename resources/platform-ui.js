// Shared-interface Desktop behavior; public browsing never requires an account.
(() => {
  const nav = document.querySelector('#menu-nav .dropdown-menu');
  if (!nav) return;
  nav.style.maxHeight='calc(100dvh - 76px)';
  nav.style.overflowY='auto';
  const item = document.createElement('li');
  item.className = 'me-public-refresh';
  const refresh = document.createElement('button');
  refresh.type = 'button';
  refresh.className = 'dropdown-item';
  refresh.style.cssText = 'min-height:44px';
  refresh.textContent = 'Refresh public map';
  refresh.title = 'Refresh public assets and mosaics from MosaicEarth. Your local files are not uploaded.';
  item.append(refresh);
  nav.append(item);
  const notify = (message, error) => {
    if (window.toastr) window.toastr[error ? 'error' : 'info'](message);
  };
  async function loadCatalog(reload) {
    refresh.disabled = true;
    refresh.textContent = 'Refreshing public map…';
    try {
      const response = await fetch('/api/desktop/refresh-public-catalog', {method: 'POST'});
      const state = await response.json();
      if (state.state !== 'ready') throw new Error(state.message || 'Public catalog unavailable.');
      if (reload) location.reload();
      else notify('Public map refreshed. Local files remain on this computer.');
    } catch (error) {
      notify(error.message, true);
    } finally {
      refresh.disabled = false;
      refresh.textContent = 'Refresh public map';
    }
  }
  refresh.addEventListener('click', () => loadCatalog(true));
  for (const link of document.querySelectorAll('a[href]')) {
    const href=link.getAttribute('href');
    if (/^\/(?:en|es|Client)\/(?:Profile|CreditPacks|Desktop)(?:\.aspx)?$/.test(href)) {
      link.href='https://app.mosaicearth.app/en/'+href.split('/').pop().replace('.aspx','');
      link.target='_blank'; link.rel='noopener';
      if (href.includes('Profile')) link.textContent='Cloud account & storage ↗';
    }
    if (href === '/Logout.ashx') {
      link.hidden=true;
      link.style.display='none';
      link.dataset.desktopCloudDisconnect='true';
      link.textContent='Disconnect Cloud';
      link.addEventListener('click',async event=>{
        event.preventDefault();
        try {
          const response=await fetch('/api/desktop/cloud-logout',{method:'POST'});
          if (!response.ok) throw Error('Could not disconnect the cloud account.');
          location.reload();
        } catch(error) { notify(error.message,true); }
      });
    }
  }
  let browserScript;
  async function api(path, body) {
    const response=await fetch(path,body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();
    if(!response.ok || data.ok===false)throw Error(data.detail||data.message||'The account request could not complete. Retry.');
    return data;
  }
  window.openDesktopCloudSignIn=async()=>{
    try{
      if(!browserScript)browserScript=new Promise((resolve,reject)=>{
        const script=document.createElement('script');script.src='/desktop-browser-signin.js';
        script.onload=resolve;script.onerror=()=>{browserScript=null;reject(Error('Could not load sign-in. Reopen Desktop and retry.'));};document.head.append(script);
      });
      await browserScript;
      window.MosaicEarthBrowserSignIn({activation:false,
        start:()=>api('/api/desktop/browser-signin/start',{}),poll:()=>api('/api/desktop/browser-signin'),cancel:()=>api('/api/desktop/browser-signin/cancel',{}),
        open:async url=>{
          const control=await api('/api/desktop/host-controls');
          if(!control.url||!control.token)throw Error('Use the sign-in link.');
          const response=await fetch(control.url+'/api/browser-signin/open',{method:'POST',headers:{'Content-Type':'application/json','X-Mosaic-Workspace':control.token},body:JSON.stringify({url})});
          if(!response.ok)throw Error('Use the sign-in link.');
        },complete:()=>location.reload()});
    }catch(error){notify(error.message,true);}
  };
  const accountItem=document.createElement('li');
  accountItem.style.cssText='padding:12px 16px;max-width:340px;white-space:normal;overflow-wrap:anywhere;border-bottom:1px solid #344536';
  accountItem.setAttribute('aria-label','Desktop accounts');
  const licenseState=document.createElement('div'),cloudState=document.createElement('div'),connectButton=document.createElement('button');
  licenseState.textContent='Checking Desktop activation…';cloudState.textContent='Checking cloud connection…';
  cloudState.style.cssText='margin:8px 0';connectButton.type='button';connectButton.className='dropdown-item';
  connectButton.textContent='Connect cloud account';connectButton.style.cssText='min-height:44px;padding-left:0;color:#bde47f;white-space:normal';
  connectButton.onclick=window.openDesktopCloudSignIn;connectButton.hidden=true;connectButton.style.display='none';
  accountItem.append(licenseState,cloudState,connectButton);nav.prepend(accountItem);
  api('/api/desktop/account-status').then(state=>{
    licenseState.textContent=state.license.edition==='source'?'Source edition · no activation required':state.license.activated?'Desktop activated for '+state.license.email:'Desktop activation required';
    cloudState.textContent=state.cloud.logged_in?'Cloud connected as '+state.cloud.email:'Cloud not connected · files remain local';
    const label=document.getElementById('usernameEmail');
    if(label){label.textContent=state.cloud.logged_in?state.cloud.email:'Local · Cloud not connected';label.title=licenseState.textContent+'\n'+cloudState.textContent;}
    connectButton.hidden=state.cloud.logged_in;
    connectButton.style.display=state.cloud.logged_in?'none':'block';
    document.querySelectorAll('[data-desktop-cloud-disconnect]').forEach(link=>{link.hidden=!state.cloud.logged_in;link.style.display=state.cloud.logged_in?'block':'none';});
  }).catch(error=>{licenseState.textContent='Account status unavailable';cloudState.textContent=error.message;});
  const processingItem=document.createElement('li');
  processingItem.style.cssText='padding:12px 16px;max-width:340px;white-space:normal;border-bottom:1px solid #344536';
  const processingState=document.createElement('div'),processingDetail=document.createElement('small'),processingSettings=document.createElement('button');
  processingState.textContent='Checking local processing…';processingDetail.style.cssText='display:block;margin:8px 0;color:#c1d2c5';
  processingSettings.type='button';processingSettings.className='dropdown-item';processingSettings.textContent='Processing settings';
  processingSettings.style.cssText='min-height:44px;padding-left:0;color:#bde47f';
  processingSettings.onclick=async()=>{try{const control=await api('/api/desktop/host-controls');if(!control.url)throw Error('Reopen Desktop to change processing settings.');location.href=control.url+'/setup';}catch(error){notify(error.message,true);}};
  processingItem.append(processingState,processingDetail,processingSettings);accountItem.insertAdjacentElement('afterend',processingItem);
  api('/api/desktop/processing-status').then(state=>{
    const deviceLabel=device=>device==='cuda'?'GPU':'CPU';
    processingState.textContent='NodeODX: '+deviceLabel(state.nodeodx)+' · OpenSplat: '+deviceLabel(state.opensplat);
    processingDetail.textContent=state.device?state.device.name:(state.preference==='cpu'?'CPU selected':'Automatic · CPU available');
    if(state.gpu_available&&!state.gpu_installed)processingDetail.textContent+=' · GPU setup incomplete';
  }).catch(()=>{processingState.textContent='Processing status unavailable';processingDetail.textContent='Open Processing settings to check the local engines.';});
  const loginModal=document.getElementById('modalDesktopCloudLogin');
  if(loginModal){
    const body=loginModal.querySelector('.modal-body');
    const social=document.createElement('button');social.type='button';social.className='btn btn-primary';
    social.style.cssText='width:100%;min-height:44px;margin-bottom:16px;white-space:normal';
    social.textContent='Continue in browser · Google, GitHub, Microsoft or email';
    social.onclick=()=>{window.jQuery(loginModal).modal('hide');window.openDesktopCloudSignIn();};body.prepend(social);
    for(const [inputId,labelText] of [['txtDesktopCloudEmail','MosaicEarth email'],['txtDesktopCloudPassword','MosaicEarth password']]){
      const input=document.getElementById(inputId),label=input?.parentElement.querySelector('label');if(label){label.htmlFor=inputId;label.textContent=labelText;}
    }
  }
  window.connectDesktopCloudAccount=async()=>{
    const email=document.getElementById('txtDesktopCloudEmail'),password=document.getElementById('txtDesktopCloudPassword');
    if(!email.value.trim()||!password.value)return notify('Enter your MosaicEarth email and password, or continue in the browser.',true);
    try{await api('/api/desktop/cloud-login',{email:email.value.trim(),password:password.value});location.reload();}
    catch(error){notify(error.message,true);}finally{password.value='';}
  };
  window.disconnectDesktopCloudAccount=async()=>{
    try{await api('/api/desktop/cloud-logout',{});location.reload();}catch(error){notify(error.message,true);}
  };
  // Opening a public browsing page is a public-data request, never cloud sync.
  if (/\/(Map|PublicMosaics)\/?$/i.test(location.pathname)) {
    fetch('/api/desktop/public-catalog-status').then(r => r.json()).then(state => {
      if (state.state === 'not_loaded') loadCatalog(true);
    }).catch(() => notify('Public catalog status unavailable. Local work remains available.', true));
  }

  const panel = document.createElement('section');
  panel.setAttribute('aria-label', 'Workspace synchronization');
  panel.style.cssText = 'position:fixed;right:18px;bottom:32px;width:min(420px,calc(100vw - 36px));max-height:45vh;overflow:auto;z-index:10040;background:#111a14;border:1px solid #667c35;border-radius:12px;padding:14px;color:#ecf2e8;box-shadow:0 8px 30px #0008;display:none';
  document.body.append(panel);
  let timer;
  const seen = new Map();
  const make = (tag, text, parent) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (parent) parent.append(element);
    return element;
  };
  const action = (label, parent, handler) => {
    const button = make('button', label, parent);
    button.type = 'button'; button.className = 'btn btn-sm btn-outline-secondary';
    button.style.cssText = 'min-height:44px;margin:4px 6px 0 0;color:#e8f1e8;border-color:#8aa75f';
    button.addEventListener('click', handler);
    return button;
  };
  for (const select of document.querySelectorAll('select[id^="meVis_"],select[id^="assetVis_"],select[aria-label="MyMosaic Visibility"],#selectMyMapVisibility,#shareModalVisSelect')) {
    for (const option of select.options) {
      if (option.value === 'private') option.textContent='Local · on this computer';
      else { option.disabled=true; option.textContent+=' · use the cloud copy'; }
    }
    select.value='private';
    const note=make('small','To share online or publish, synchronize this item and open its cloud copy.');
    note.style.cssText='display:block;color:#b6c8bb;line-height:1.45;margin-top:7px';
    select.insertAdjacentElement('afterend',note);
  }
  async function transfer(direction, kind, id) {
    try {
      const response = await fetch('/api/desktop/sync-' + (direction === 'upload' ? 'to' : 'from') + '-cloud', {
        method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({kind,id})
      });
      const data = await response.json();
      if (!data.ok) {
        if (data.login_required && document.getElementById('modalDesktopCloudLogin')) window.jQuery('#modalDesktopCloudLogin').modal('show');
        throw new Error(data.message || 'Could not start synchronization.');
      }
      notify(direction === 'upload' ? 'Preparing a complete private cloud copy.' : 'Preparing a complete local copy.');
      pollTransfers();
    } catch (error) { notify(error.message, true); }
  }
  window.syncLocalProjectToCloud = id => transfer('upload', 'project', id);
  window.syncLocalMyMosaicToCloud = id => transfer('upload', 'mymosaic', id);

  async function importLegacy() {
    try {
      const response=await fetch('/api/desktop/import-legacy-workspace',{method:'POST'});
      const data=await response.json();
      if (!response.ok || !data.ok) throw new Error(data.detail || 'Could not start the local import.');
      pollTransfers();
    } catch(error) { notify(error.message,true); }
  }
  if (/\/(Projects|Projects.aspx)$/i.test(location.pathname)) {
    fetch('/api/desktop/legacy-workspace').then(r=>r.json()).then(state=>{
      if (!state.available) return;
      const host=document.querySelector('.myearth-quota-bar');
      if (!host) return;
      const section=make('section');
      section.style.cssText='border:1px solid #607b39;border-radius:10px;padding:16px;margin:15px 0;background:#132017;color:#e8f1e8';
      make('strong','Previous Desktop workspace found',section);
      make('p','Copy its local projects, photos, finished layers and MyMosaics into this version. The earlier application and its files will be preserved. Nothing is uploaded.',section);
      if (state.unfinished) make('p',state.unfinished+' unfinished task(s) will be copied with their source files so you can restart processing here.',section);
      action('Import previous local workspace',section,importLegacy);
      host.insertAdjacentElement('afterend',section);
    }).catch(()=>notify('Could not check the previous local workspace. Its files have not been changed.',true));
  }

  window.openDesktopLocalFolder = async () => {
    try {
      const control = await (await fetch('/api/desktop/host-controls')).json();
      if (!control.url || !control.token) throw new Error('Open this workspace through the MosaicEarth Desktop launcher to access its local folder.');
      const response = await fetch(control.url + '/api/platform/open-folder', {
        method:'POST', headers:{'Content-Type':'application/json','X-Mosaic-Workspace':control.token}, body:'{}'
      });
      if (!response.ok) throw new Error('The Desktop launcher could not open the folder. Reopen MosaicEarth Desktop and retry.');
    } catch (error) { notify(error.message, true); }
  };

  // Cloud processing is a deliberate copy and a separate price authorization
  // on the cloud service. A local task must never execute a billed cloud job.
  const selectEngine = window.selectTaskEngineMode;
  const submitEngine = window.submitTaskToSelectedEngine;
  if (selectEngine && submitEngine) {
    window.selectTaskEngineMode = mode => {
      selectEngine(mode);
      if (mode === 'cloud') {
        const host = document.getElementById('cloudEnginePanel');
        host.replaceChildren();
        make('p','First synchronize this project, including its source photos, to your cloud account. Open the cloud copy to choose the preset and review its current price before starting processing.',host);
        document.getElementById('processTask').textContent='COPY PROJECT TO CLOUD';
      }
    };
    window.submitTaskToSelectedEngine = () => {
      if (window.__meTaskEngineMode !== 'cloud') return submitEngine();
      const id=Number(new URLSearchParams(location.search).get('id'));
      if (!Number.isSafeInteger(id) || id <= 0) return notify('Open the task from MyEarth before synchronizing.',true);
      window.jQuery('#staticBackdrop3').modal('hide');
      transfer('upload','project',id);
    };
    const cloudChoice=document.getElementById('btnSelectCloudEngine');
    if (cloudChoice) cloudChoice.lastElementChild.textContent='Copy project · authorize processing in Cloud';
  }

  async function loadCloud() {
    const projectHost = document.getElementById('desktopCloudProjectsHost');
    const mosaicHost = document.getElementById('cloudMyMosaicsList');
    if (!projectHost && !mosaicHost) return;
    try {
      const response = await fetch('/api/desktop/cloud-workspace');
      const data = await response.json();
      if (!data.connected) return;
      if (!data.ok) throw new Error(data.message || 'Cloud workspace unavailable.');
      for (const [host, kind, rows] of [[projectHost,'project',data.projects || []],[mosaicHost,'mymosaic',data.mymosaics || []]]) {
        if (!host) continue;
        host.replaceChildren();
        if (!rows.length) make('p','No cloud items yet. Synchronize a local item to create a private cloud copy.',host);
        for (const row of rows) {
          const id = Number(row.id ?? row.ID);
          if (!Number.isSafeInteger(id) || id <= 0) continue;
          const card = make('article',undefined,host);
          card.style.cssText = 'padding:14px;margin:10px 0;background:#101713;border:1px solid #2b3c32;border-radius:8px';
          make('strong',row.name || row.NAME || 'Cloud workspace',card);
          make('p',kind === 'mymosaic' ? 'Cloud MyMosaic · ' + (row.visibility || 'private') : 'Cloud project · photos, tasks and layers',card);
          action('Download complete local copy',card,()=>transfer('download',kind,id));
          const link = make('a','Open in Cloud ↗',card);
          link.className='btn btn-sm btn-outline-secondary'; link.style.minHeight='44px';
          link.target='_blank'; link.rel='noopener';
          link.href='https://app.mosaicearth.app/' + (kind === 'mymosaic' ? 'MyMap.aspx?id=' : 'en/Projects?open_project=') + id;
        }
      }
    } catch (error) {
      for (const host of [projectHost,mosaicHost]) if (host) host.textContent=error.message;
    }
  }
  window.loadDesktopCloudProjects = loadCloud;
  window.loadCloudMyMosaicsList = loadCloud;
  loadCloud();

  async function pollTransfers() {
    clearTimeout(timer);
    try {
      const data = await (await fetch('/api/desktop/transfers')).json();
      const entries = Object.entries(data.transfers || {});
      panel.replaceChildren(); panel.style.display=entries.length ? 'block' : 'none';
      if (!entries.length) return;
      make('strong','Workspace synchronization',panel);
      let running=false;
      for (const [id,state] of entries) {
        const parts=id.split(':');
        const card=make('div',undefined,panel);card.style.cssText='border-top:1px solid #344536;margin-top:10px;padding-top:10px;overflow-wrap:anywhere';
        const label=make('p',state.message,card);label.setAttribute('role','status');
        if (state.total) {
          const progress=make('progress',undefined,card);progress.max=state.total;progress.value=state.done;progress.style.width='100%';
          progress.setAttribute('aria-label',parts[0] === 'upload' ? 'Upload progress' : 'Download progress');
          make('small',(state.done/1048576).toFixed(1)+' / '+(state.total/1048576).toFixed(1)+' MiB',card);
        }
        if (state.status === 'running') {
          running=true;
          action(state.pause ? 'Pausing…' : 'Pause',card,async()=>{
            await fetch('/api/desktop/pause-transfer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id})});
            pollTransfers();
          }).disabled=!!state.pause;
        } else if (state.status === 'failed' || state.status === 'paused') {
          if (parts[0] === 'migration') action('Resume',card,importLegacy);
          else if (parts.length === 3) action('Resume',card,()=>transfer(parts[0],parts[1],Number(parts[2])));
        } else if (state.status === 'complete') {
          if (seen.get(id) === 'running') { notify('Complete workspace synchronized and verified.'); loadCloud(); }
          const result=state.result && state.result.workspace;
          if ((parts[0] === 'download' || parts[0] === 'migration') && result) {
            const link=make('a','Open local copy',card);link.className='btn btn-sm btn-primary';
            link.href=result.kind === 'mymosaic' ? '/MyMap.aspx?id='+result.id : '/en/Projects?open_project='+result.id;
          } else if (parts[0] === 'upload' && result) {
            const link=make('a','Open cloud copy ↗',card);link.className='btn btn-sm btn-primary';
            link.target='_blank';link.rel='noopener';
            link.href='https://app.mosaicearth.app/' + (result.kind === 'mymosaic' ? 'MyMap.aspx?id=' : 'en/Projects?open_project=') + result.id;
          }
        }
        seen.set(id,state.status);
      }
      if (running) timer=setTimeout(pollTransfers,2000);
      else action('Close',panel,()=>{panel.style.display='none';});
    } catch (error) { notify('Synchronization status unavailable: '+error.message,true); }
  }
  pollTransfers();
})();
