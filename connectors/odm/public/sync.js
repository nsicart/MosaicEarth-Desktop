"use strict";
(() => {
 const $ = id => document.getElementById(id);
 const base = new URL(".", document.currentScript.src).href;
 let transfer = "";
 async function api(action, payload={}) {
  const response = await fetch(base + "api", {method:"POST", headers:{"Content-Type":"application/json","X-CSRFToken":document.querySelector("[name=csrfmiddlewaretoken]").value}, body:JSON.stringify({action,...payload})});
  const data=await response.json(); if(!response.ok || data.ok===false) throw Error(data.error || "Request failed"); return data;
 }
 function signedIn(email){$("me-login").hidden=true;$("me-signout").hidden=false;$("me-status").textContent="Signed in as "+email;}
 api("session").then(d=>signedIn(d.email)).catch(()=>{});
 const error = e => {$("me-status").textContent=e.message;};
 async function localList(path) {
  const result=[];
  while(path) {const response=await fetch(path);if(!response.ok)throw Error("Cannot load local projects");const data=await response.json();result.push(...(Array.isArray(data)?data:data.results||[]));path=Array.isArray(data)?null:data.next;if(path&&new URL(path,location.origin).origin!==location.origin)throw Error("Invalid local pagination");}
  return result;
 }
 function options(select,items){select.replaceChildren(...items.map(item=>{const option=document.createElement("option");option.value=item.id;option.textContent=item.name||item.id;return option;}));}
 async function tasks(){const pid=$("me-source-project").value;if(pid)options($("me-task"),(await localList("/api/projects/"+encodeURIComponent(pid)+"/tasks/")).filter(t=>t.status===40));}
 $("me-source-project").onchange=()=>tasks().catch(error);
 localList("/api/projects/").then(items=>{options($("me-source-project"),items);options($("me-project"),items);return tasks();}).catch(error);
 $("me-login").onsubmit=async e=>{e.preventDefault();try{const d=await api("signin",{email:$("me-email").value,password:$("me-password").value});signedIn(d.email);}catch(e){error(e);}finally{$("me-password").value="";}};
 $("me-signout").onclick=()=>api("signout").then(()=>{$("me-status").textContent="Signed out";$("me-login").hidden=false;$("me-signout").hidden=true;$("me-catalog").replaceChildren();}).catch(error);
 async function start(action, payload){const d=await api(action,payload);transfer=d.transfer_id;$("me-pause").hidden=false;$("me-status").textContent="Transfer queued";}
 $("me-upload").onsubmit=e=>{e.preventDefault();start("push",{task_id:$("me-task").value.trim()}).catch(error);};
 $("me-pause").onclick=()=>api("pause",{transfer_id:transfer}).catch(error);
 $("me-browse").onclick=async()=>{try{const d=await api("catalog");$("me-catalog").replaceChildren();const entries=[...d.projects.map(p=>({name:p.name+" · latest",cloud_project_id:p.id})),...d.snapshots.filter(s=>s.status==="COMPLETE").map(s=>({name:s.name+" · "+new Date(s.created_at*1000).toLocaleString(),snapshot_id:s.id}))];for(const item of entries){const p=document.createElement("p"),b=document.createElement("button");b.className="btn btn-default";b.textContent="Download "+item.name;b.onclick=()=>start("pull",{...item,project_id:Number($("me-project").value)}).catch(error);p.append(b);$("me-catalog").append(p);}}catch(e){error(e);}};
 setInterval(async()=>{if(!transfer)return;try{const d=await api("status",{transfer_id:transfer});$("me-status").textContent=d.status+" · "+d.message;$("me-progress").hidden=!d.total;if(d.total){$("me-progress").max=d.total;$("me-progress").value=d.done;}if(!["running","queued"].includes(d.status)){transfer="";$("me-pause").hidden=true;if(d.result?.cloud_project_id){const a=document.createElement("a");a.href="https://app.mosaicearth.app/en/Projects?open_project="+d.result.cloud_project_id;a.target="_blank";a.rel="noopener";a.textContent=" Open MyEarth";$("me-status").append(a);}}}catch(e){error(e);transfer="";}},2000);
})();
