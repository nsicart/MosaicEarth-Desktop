const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const Cesium=require('../resources/cesium/index.cjs');
let requests=0,active=0,peak=0,fail=false;
const context={window:{},Map,Promise,AbortController,Float32Array,setTimeout,clearTimeout,
fetch:async(url,{signal})=>{assert(!signal.aborted);requests++;active++;peak=Math.max(peak,active);await new Promise(r=>setImmediate(r));active--;if(fail)return{ok:false,status:503};const[z,x,y]=url.match(/terrarium\/(\d+)\/(\d+)\/(\d+)/).slice(1).map(Number);return{ok:true,blob:async()=>({x,y,z})}},
createImageBitmap:async b=>({...b,width:256,height:256,close(){}}),
document:{createElement(){return{getContext(){let image;return{drawImage(i){image=i},getImageData(){const data=new Uint8ClampedArray(256*256*4);for(let y=0;y<256;y++)for(let x=0;x<256;x++){const h=100+((image.x*256+x)%2048)/8+((image.y*256+y)%2048)/16;const v=h+32768,i=(y*256+x)*4;data[i]=Math.floor(v/256);data[i+1]=Math.floor(v)%256;data[i+2]=Math.round((v%1)*256);data[i+3]=255}return{data}}}}}}}};
vm.runInNewContext(fs.readFileSync(__dirname+'/../platform/homelab/MosaicMapSources.js','utf8'),context);
(async()=>{
 const p=context.window.MosaicMapSources.createTerrain(Cesium);
 assert(p.tilingScheme instanceof Cesium.GeographicTilingScheme);
 const [a,b,c]=await Promise.all([p.requestTileGeometry(2,1,3),p.requestTileGeometry(3,1,3),p.requestTileGeometry(2,2,3)]);
 for(let i=0;i<65;i++){assert.equal(a._buffer[i*65+64],b._buffer[i*65]);assert.equal(a._buffer[64*65+i],c._buffer[i]);}
 const rect=p.tilingScheme.tileXYToRectangle(2,1,3),lon=(rect.west+rect.east)/2,lat=(rect.north+rect.south)/2;
 const px=(lon+Math.PI)/(2*Math.PI)*4096-.5,py=(1-Math.asinh(Math.tan(lat))/Math.PI)/2*4096-.5;
 assert(Math.abs(a._buffer[32*65+32]-(100+px/8+py/16))<0.0001,'Correct reprojected pixel-centre elevation');
 assert(peak<=6,'Bounded network concurrency');const before=requests;await p.requestTileGeometry(2,1,3);assert.equal(requests,before,'Cached tiles reused');
 for(const x of [0,1]){const poles=await p.requestTileGeometry(x,0,0);assert(poles._buffer.every(Number.isFinite),'Both poles and dateline finite');}
 const deepest=await p.requestTileGeometry(1,1,14);assert.equal(deepest._childTileMask,0);assert.equal(p.getTileDataAvailable(0,0,15),false);
 const sampled=await Cesium.sampleTerrain(p,3,[Cesium.Cartographic.fromDegrees(-123.75,56.25)]);assert(Number.isFinite(sampled[0].height));
 let healthy;const q=context.window.MosaicMapSources.createTerrain(Cesium,v=>{healthy=v});fail=true;await assert.rejects(q.requestTileGeometry(2,1,3),/503/);assert.equal(healthy,false);fail=false;await q.requestTileGeometry(2,1,3);assert.equal(healthy,true);q.destroy();
 p.destroy();await assert.rejects(p.requestTileGeometry(4,4,3),/closed/);
 let ignFails=true,eoxRequests=0,ignRequests=0;
 class GlobalImagery {
   constructor(options){Object.assign(this,options);this.tilingScheme=new Cesium.WebMercatorTilingScheme();this.rectangle=this.tilingScheme.rectangle;}
   requestImage(){eoxRequests++;return Promise.resolve({provider:'eox'});}
 }
 class SpainImagery {constructor(options){Object.assign(this,options)}requestImage(){ignRequests++;return ignFails?Promise.reject(new Error('fixture outage')):Promise.resolve({provider:'ign'})}}
 const imageryCesium={...Cesium,UrlTemplateImageryProvider:GlobalImagery,WebMapTileServiceImageryProvider:SpainImagery};
 const satellite=context.window.MosaicMapSources.createImagery(imageryCesium,'satellite');
 const tile=satellite.tilingScheme.positionToTileXY(Cesium.Cartographic.fromDegrees(1.76,42.31),15);
 assert(satellite.getTileCredits(tile.x,tile.y,15)[0].html.includes('IGN'));
 await satellite.requestImage(tile.x,tile.y,15);
 assert(satellite.getTileCredits(tile.x,tile.y,15)[0].html.includes('2016'),'Fallback credits must identify the actual imagery');
 ignFails=false;assert.equal((await satellite.requestImage(tile.x,tile.y,15)).provider,'ign');
 assert(satellite.getTileCredits(tile.x,tile.y,15)[0].html.includes('IGN'),'Recovered imagery restores its correct credit');
 const outside=satellite.tilingScheme.positionToTileXY(Cesium.Cartographic.fromDegrees(-74,40),16);
 await satellite.requestImage(outside.x,outside.y,16);await satellite.requestImage(outside.x,outside.y,16);
 assert.equal(ignRequests,2,'Non-Spanish tiles must not query IGN');assert.equal(eoxRequests,2,'Global overzoom reuses the parent image');
 for(const [lon,lat] of [[.276828,43.118711],[-9.14,38.72]]) {
   const foreign=satellite.tilingScheme.positionToTileXY(Cesium.Cartographic.fromDegrees(lon,lat),16);
   assert(satellite.getTileCredits(foreign.x,foreign.y,16)[0].html.includes('2016'),'France and Portugal use global imagery');
   await satellite.requestImage(foreign.x,foreign.y,16);
 }
 assert.equal(ignRequests,2,'Country bounding boxes must not select foreign overview tiles');
 const streets=context.window.MosaicMapSources.createImagery(imageryCesium,'topographic');
 assert.equal(streets.url,'https://tile.openstreetmap.org/{z}/{x}/{y}.png');assert(streets.credit.html.includes('OpenStreetMap'));
 console.log('PASS: real Cesium sampling, projected elevations, exact mesh edges, polar coverage, cache reuse, max 6 requests, error recovery, cancellation, imagery routing, fallback attribution and parent reuse');
})().catch(e=>{console.error(e);process.exitCode=1});
