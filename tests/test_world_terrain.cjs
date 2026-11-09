/* Pure Node contract tests: real Cesium and shipped NOAA data, no browser automation. */
const fs=require('node:fs'),vm=require('node:vm'),zlib=require('node:zlib'),path=require('node:path'),assert=require('node:assert/strict');
const C=require(process.env.CESIUM_TEST_MODULE || '../resources/cesium/index.cjs');
const root=path.resolve(__dirname,'../platform/homelab');
const source=fs.readFileSync(path.join(root,'MosaicMapSources.js'),'utf8');
const scheme=new C.GeographicTilingScheme();
let nextTile=null,offline=false,throttled=false,calls=0;
const native={tilingScheme:scheme,hasWaterMask:false,hasVertexNormals:false,
 getLevelMaximumGeometricError:z=>100000/2**z,
 requestTileGeometry(x,y,z,request){calls++;if(throttled)return undefined;if(nextTile)return nextTile;return Promise.resolve({credits:[new C.Credit('Fixture upstream attribution')],interpolateHeight(rect,lon,lat){return 100+lon*2+lat*5}})}};
function context(load){let fetches=0;const ctx={window:{},Map,Set,Promise,AbortController,Float32Array,DataView,setTimeout,clearTimeout,
 fetch:async url=>{fetches++;if(offline)return{ok:false,status:503};const raw=zlib.gunzipSync(fs.readFileSync(path.join(root,'terrain-data',path.basename(url))));return{ok:true,arrayBuffer:async()=>raw.buffer.slice(raw.byteOffset,raw.byteOffset+raw.byteLength)};}};
 vm.runInNewContext(source,ctx);return{api:ctx.window.MosaicMapSources,Cesium:{...C,CesiumTerrainProvider:{fromUrl:load}},fetches:()=>fetches};}
(async()=>{
 let resolveMetadata;const ctx=context(()=>new Promise(r=>resolveMetadata=r));let healthy,message;
 const p=ctx.api.createTerrain(ctx.Cesium,(v,m)=>{healthy=v;message=m});
 assert.equal(p.requestTileGeometry(0,0,0),undefined,'Wait for metadata without queueing invalid terrain promises');
 resolveMetadata(native);await p.readyPromise;
 throttled=true;assert.equal(p.requestTileGeometry(1,1,3,new C.Request()),undefined,'Native backpressure survives the wrapper');throttled=false;
 const a=await p.requestTileGeometry(2,3,3);assert.equal(a.interpolateHeight(null,0,0),100,'Ordinary meshes are not resampled');
 const south=await C.sampleTerrain(p,8,[C.Cartographic.fromDegrees(0,-90),C.Cartographic.fromDegrees(180,-90),C.Cartographic.fromDegrees(-180,-90)]);
 for(const point of south)assert.equal(point.height,2802,'Single ice-covered south pole, all longitudes');
 const north=await C.sampleTerrain(p,8,[C.Cartographic.fromDegrees(0,90)]);assert.equal(north[0].height,15,'North pole is sea surface plus geoid, not seafloor');
 assert.equal(p.getTileDataAvailable(0,0,9),false,'Do not pretend polar grid has unbounded detail');
 assert.equal(p.availability.computeMaximumLevelAtPosition(C.Cartographic.fromDegrees(0,-89)),8);
 assert.equal(p.availability.computeMaximumLevelAtPosition(C.Cartographic.fromDegrees(0,0)),14);
 const polarCredits=await p.requestTileGeometry(0,255,8);assert.equal(polarCredits.credits[0].html,'Fixture upstream attribution','Polar blending retains provider data attribution');
 const level=8,y=249,x=256;
 const tiles=await Promise.all([p.requestTileGeometry(x,y,level),p.requestTileGeometry(x+1,y,level),p.requestTileGeometry(x,y+1,level)]);
 for(let i=0;i<65;i++){
   assert(Math.abs(tiles[0]._buffer[i*65+64]-tiles[1]._buffer[i*65])<.001,'Blended adjacent east/west edges');
   assert(Math.abs(tiles[0]._buffer[64*65+i]-tiles[2]._buffer[i])<.001,'Blended adjacent north/south edges');
 }
 const date=await Promise.all([p.requestTileGeometry(0,255,8),p.requestTileGeometry(511,255,8)]);
 for(let i=0;i<65;i++)assert.equal(date[0]._buffer[i*65],date[1]._buffer[i*65+64],'Polar antimeridian closes exactly');
 assert.equal(ctx.fetches(),2,'One shared download per hemisphere');
 const failing={...native,requestTileGeometry:()=>Promise.reject(new Error('upstream offline'))};
 const urls=[];const recover=context(url=>{urls.push(url);return Promise.resolve(url.includes('terrain.mosaicearth.app')?failing:native)});
 const recovered=recover.api.createTerrain(recover.Cesium);await recovered.readyPromise;
 assert.equal((await recovered.requestTileGeometry(5,5,6)).interpolateHeight(null,0,0),100,'Gateway outage recovers through direct provider');
 assert.equal(urls.length,2);recovered.destroy();
 const outage=context(()=>Promise.resolve(failing));let outageHealthy;
 const last=outage.api.createTerrain(outage.Cesium,v=>outageHealthy=v);await last.readyPromise;
 const coarse=await last.requestTileGeometry(5,5,6);assert(coarse instanceof C.HeightmapTerrainData);
 assert.equal(outageHealthy,false,'Coarse fallback is explicitly disclosed');
 assert.equal(last.availability.computeMaximumLevelAtPosition(C.Cartographic.fromDegrees(0,0)),6);
 assert(last.credit.html.includes('NOAA'));last.destroy();
 let resolveTile;nextTile=new Promise(r=>resolveTile=r);const cancelled=new C.Request();const pending=p.requestTileGeometry(6,6,6,cancelled);p.destroy();assert.equal(cancelled.cancelled,true);resolveTile({});await assert.rejects(pending,/cancelled/);nextTile=null;
 const fallback=context(()=>Promise.reject(new Error('metadata offline')));let fallbackStatus;
 const b=fallback.api.createTerrain(fallback.Cesium,v=>fallbackStatus=v);await b.readyPromise;assert.equal(fallbackStatus,false);assert(b.credit.html.includes('NOAA'));b.destroy();
 const unavailable=context(()=>Promise.resolve(native));offline=true;const q=unavailable.api.createTerrain(unavailable.Cesium);await q.readyPromise;await assert.rejects(q.requestTileGeometry(0,255,8),/503/);offline=false;await q.requestTileGeometry(0,255,8);q.destroy();
 console.log('PASS: stable gateway, live tile failover, bundled worldwide fallback, native throttle, metadata readiness/fallback, real polar heights, bounded resolution, shared edges/dateline, grid caching, error recovery and destruction cancellation');
})().catch(e=>{console.error(e);process.exitCode=1});
