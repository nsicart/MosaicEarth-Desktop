"use strict";
(() => {
 const message = document.getElementById('desktop-message');
 const purchase = document.getElementById('desktop-purchase');
 if (purchase) purchase.onclick = async () => {
  purchase.disabled = true; message.textContent = 'Preparing checkout…';
  try {
   const response = await fetch('/api/v2/checkout/desktop-license', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
   const data = await response.json();
   if (!response.ok || data.ok === false) throw Error(data.message || 'Checkout could not be started.');
   if (data.requiresStripe && data.checkoutUrl) location.href = data.checkoutUrl;
   else if(data.ok) location.reload();
   else throw Error('Checkout did not return a payment link.');
  } catch(error) {message.textContent = error.message; purchase.disabled = false;}
 };
 const manage = document.getElementById('desktop-devices');
 if (manage) manage.onclick = async () => {
  manage.disabled = true;
  try {
   const response = await fetch('/api/v2/desktop/activations'); const data = await response.json();
   if (!response.ok) throw Error(data.detail || 'Could not load devices.');
   const list = document.getElementById('desktop-device-list'); list.replaceChildren();
   for(const device of data.devices) {
    const row = document.createElement('p'), name = document.createElement('span'), remove = document.createElement('button');
    name.textContent = device.name + ' · activated ' + new Date(device.last_activation * 1000).toLocaleDateString();
    remove.textContent = 'Remove this computer';
    remove.onclick = async () => {
     if(!confirm('Remove '+device.name+' from your account? Its existing offline activation expires within 30 days.'))return;
     remove.disabled = true;
     try {const r=await fetch('/api/v2/desktop/activations/'+device.device,{method:'DELETE'});const d=await r.json();if(!r.ok)throw Error(d.detail || 'Removal failed');row.remove();message.textContent=d.message;}
     catch(e){message.textContent=e.message;remove.disabled=false;}
    };
    row.append(name,remove);list.append(row);
   }
   if(!data.devices.length)list.textContent='No activated computers yet. Sign in during installation to activate one.';
  } catch(error) {message.textContent=error.message;}
  finally {manage.disabled=false;}
 };
})();
