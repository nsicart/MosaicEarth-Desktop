// Exercise the client contract with real Cesium geometry and deterministic services.
// No credentials, network or chargeable Esri sessions are used by this test.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const C=require('../resources/cesium/index.cjs');
const code=fs.readFileSync(__dirname+'/../platform/homelab/MosaicMapSources.js','utf8');
const tick=()=>new Promise(r=>setImmediate(r));
function fixture({openOnly=false,budget=false,invalid=false}={}) {
 let now=Date.now(),sessions=0,native=0,images=0,fail=false,throttle=false,ignFails=false;
 const timers=new Map(),nonces=[],storage=new Map();let serial=0;
 class Eox {constructor(o){Object.assign(this,o);this.tilingScheme=new C.WebMercatorTilingScheme();this.rectangle=this.tilingScheme.rectangle;}requestImage(){return Promise.resolve({source:'eox'})}}
 class Ign {requestImage(){return ignFails?Promise.reject(new Error('national outage')):Promise.resolve({source:'ign'})}}
 const provider={requestImage(){images++;if(throttle)return undefined;return fail?Promise.reject(new Error('upstream URL has a private token')):Promise.resolve({source:'esri'});},
 getTileCredits(){return[new C.Credit('Esri data suppliers',true)]},tileDiscardPolicy:{isReady:()=>true,shouldDiscardImage:()=>false}};
 const cesium={...C,UrlTemplateImageryProvider:Eox,WebMapTileServiceImageryProvider:Ign,
 ArcGisMapServerImageryProvider:{async fromBasemapType(type,options){native++;assert.equal(type,C.ArcGisBaseMapType.SATELLITE);assert.equal(options.token,'session-'+sessions);return provider;}}};
 const w={MosaicMapConfig:{openOnly},crypto:require('node:crypto').webcrypto,
 sessionStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)}};
 const context={window:w,Map,Set,WeakSet,Promise,AbortController,Uint8Array,
 Date:class extends Date {static now(){return now;}},
 setTimeout:(f,ms)=>{timers.set(++serial,{f,at:now+ms});return serial;},clearTimeout:id=>timers.delete(id),
 fetch:async(url,o)=>{sessions++;assert.equal(o.method,'POST');assert.equal(o.credentials,'omit');assert.equal(o.cache,'no-store');assert(!o.headers.Authorization);nonces.push(JSON.parse(o.body).client);assert.match(nonces.at(-1),/^[a-f0-9]{64}$/);
 return{ok:!budget,json:async()=>budget?{error:'budget',retryAfter:3600}:invalid?{access_token:'MUST-NOT-BE-USED'}:{sessionToken:'session-'+sessions,expiresAt:now+43200000}};},
 document:{createElement:()=>({getContext:()=>({drawImage(){}})})}};
 vm.runInNewContext(code,context);
 return{make:mode=>w.MosaicMapSources.createImagery(cesium,mode||'satellite'),counts:()=>({sessions,native,images}),nonces,timers,
 fail:v=>{fail=v},throttle:v=>{throttle=v},ignFails:v=>{ignFails=v},
 async advance(ms){now+=ms;for(const[id,t]of [...timers])if(t.at<=now){timers.delete(id);t.f()}await tick();}};
}
(async()=>{
 const f=fixture(),p=f.make(),statuses=[];p.setStatusListener((...v)=>statuses.push(v));
 assert.equal(p.requestImage(3,2,3),undefined,'Metadata readiness preserves Cesium retry semantics');await tick();
 const firstImage=p.requestImage(3,2,3),capturedCredits=p.getTileCredits(3,2,3);
 assert(capturedCredits.some(c=>c.html.includes('Powered by Esri')),'Cesium reads attribution before the image promise resolves');
 assert.equal((await firstImage).source,'esri');
 assert(p.getTileCredits(3,2,3).some(c=>c.html.includes('Powered by Esri')));
 assert(p.getTileCredits(3,2,3).some(c=>c.html.includes('data suppliers')));
 f.throttle(true);assert.equal(p.requestImage(3,2,3),undefined,'Throttling does not become a broken promise');f.throttle(false);
 const other=f.make();other.requestImage(3,2,3);await tick();assert.equal(f.counts().sessions,1,'Same app reuses a session');
 f.fail(true);const failedImage=p.requestImage(3,2,3),beforeFailure=p.getTileCredits(3,2,3);
 assert.equal((await failedImage).source,'eox');assert.equal(statuses.at(-1)[0],false);
 assert(beforeFailure[0].html.includes('2016'),'The already-captured credit array follows the actual fallback image');
 assert(!statuses.at(-1)[1].includes('private token'));
 assert(p.getTileCredits(3,2,3)[0].html.includes('2016'),'Credits describe loaded fallback');
 f.fail(false);await f.advance(60051);assert.deepEqual(statuses.at(-1),[true,'',true],'Recovery asks the viewer to reload cached fallback tiles');
 assert.equal((await p.requestImage(3,2,3)).source,'esri');
 await f.advance(43200000);assert.equal(p.requestImage(3,2,3),undefined);await tick();
 assert.equal((await p.requestImage(3,2,3)).source,'esri');assert.equal(f.counts().sessions,2);assert.equal(f.nonces[0],f.nonces[1]);
 f.ignFails(true);const xy=p.tilingScheme.positionToTileXY(C.Cartographic.fromDegrees(2.17,41.38),14);
 assert.equal((await p.requestImage(xy.x,xy.y,14)).source,'esri','Regional errors use global detail');
 assert(p.getTileCredits(xy.x,xy.y,14).some(c=>c.html.includes('Esri')));
 p.destroy();other.destroy();assert.equal(f.timers.size,0,'Destroyed viewers do not keep retry timers');
 const cap=fixture({budget:true}),c=cap.make();let notice;c.setStatusListener((ok,msg)=>{notice={ok,msg}});c.requestImage(3,2,3);await tick();
 assert.equal((await c.requestImage(3,2,3)).source,'eox');assert.equal(notice.ok,false);assert(notice.msg.includes('monthly'));
 await cap.advance(60000);assert.equal(cap.counts().sessions,1,'Quota failures back off for an hour');c.destroy();
 const broken=fixture({invalid:true}),b=broken.make();b.requestImage(3,2,3);await tick();assert.equal((await b.requestImage(3,2,3)).source,'eox');assert.equal(broken.counts().native,0);b.destroy();
 const open=fixture({openOnly:true}),o=open.make();assert.equal((await o.requestImage(3,2,3)).source,'eox');o.destroy();
 const street=open.make('topographic');assert.equal(street.url,'https://tile.openstreetmap.org/{z}/{x}/{y}.png');assert.equal(open.counts().sessions,0);
 console.log('PASS: session isolation contract, reuse, expiry, direct token-scoped provider, attribution, throttling, budget fallback, recovery, regional failure, open-only mode and disposal');
})().catch(error=>{console.error(error);process.exitCode=1});
