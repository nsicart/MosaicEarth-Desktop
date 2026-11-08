(function (global) {
    'use strict';
    // Mapzen Terrarium pixels represent pixel centres. Sample neighbouring
    // pixels at tile boundaries so adjacent Cesium meshes share exact edges.
    function createOpenTerrain(Cesium, onStatus) {
        const scheme = new Cesium.GeographicTilingScheme();
        const errorEvent = new Cesium.Event();
        const maxLevel = 14, size = 65, cache = new Map();
        const availability = new Cesium.TileAvailability(scheme, maxLevel);
        const abort = new AbortController();
        const queue = [], failed = new Set();
        let active = 0;
        for (let z = 0; z <= maxLevel; z++) availability.addAvailableTileRange(z, 0, 0, 2 ** (z + 1) - 1, 2 ** z - 1);
        function drain() {
            while (active < 6 && queue.length) {
                const job = queue.shift(); active++;
                job.run().then(job.resolve, job.reject).finally(() => { active--; drain(); });
            }
        }
        function schedule(run) { return new Promise((resolve, reject) => { queue.push({run, resolve, reject}); drain(); }); }
        function tile(x, y, z) {
            const n = 2 ** z;
            x = ((x % n) + n) % n; y = Math.max(0, Math.min(n - 1, y));
            const key = z + '/' + x + '/' + y;
            if (cache.has(key)) { const value = cache.get(key); cache.delete(key); cache.set(key, value); return value; }
            const promise = schedule(async () => {
                if (abort.signal.aborted) throw new Error('Terrain viewer closed');
                const timeout = new AbortController();
                const cancel = () => timeout.abort();
                abort.signal.addEventListener('abort', cancel, {once: true});
                const timer = setTimeout(cancel, 20000);
                let response;
                try {
                    response = await fetch('https://elevation-tiles-prod.s3.amazonaws.com/terrarium/' + key + '.png', {signal: timeout.signal, credentials: 'omit'});
                    if (!response.ok) throw new Error('Terrain tile unavailable (' + response.status + ')');
                    response = await response.blob();
                } finally { clearTimeout(timer); abort.signal.removeEventListener('abort', cancel); }
                const image = await createImageBitmap(response, {colorSpaceConversion: 'none', premultiplyAlpha: 'none'});
                try {
                    if (image.width !== 256 || image.height !== 256) throw new Error('Invalid terrain tile dimensions');
                    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 256;
                    const context = canvas.getContext('2d', {willReadFrequently: true});
                    context.drawImage(image, 0, 0);
                    return context.getImageData(0, 0, 256, 256).data;
                } finally { image.close(); }
            });
            cache.set(key, promise);
            promise.then(() => { failed.delete(key); if (onStatus) onStatus(failed.size === 0); }, () => {
                if (cache.get(key) === promise) cache.delete(key);
                if (!abort.signal.aborted) { failed.add(key); if (onStatus) onStatus(false); }
            });
            if (cache.size > 192) cache.delete(cache.keys().next().value);
            return promise;
        }
        async function heights(x, y, z) {
            // Geographic meshes cover both poles. Reproject each sample into
            // Terrarium's Web Mercator grid, preserving pixel-centre semantics.
            const rect = scheme.tileXYToRectangle(x,y,z), sourceLevel = Math.min(z + 1, maxLevel);
            const n = 2 ** sourceLevel, pixels = n * 256, samples = [], required = new Map();
            const limit = Math.atan(Math.sinh(Math.PI));
            function pixelIndex(px,py) {
                px = ((px % pixels) + pixels) % pixels; py = Math.max(0,Math.min(pixels-1,py));
                const tx=Math.floor(px/256),ty=Math.floor(py/256),key=tx+','+ty;
                if(!required.has(key))required.set(key,{x:tx,y:ty});
                return {key,index:((py-ty*256)*256+px-tx*256)*4};
            }
            for(let row=0;row<size;row++)for(let col=0;col<size;col++) {
                const lon=rect.west+(rect.east-rect.west)*col/(size-1);
                const lat=Math.max(-limit,Math.min(limit,rect.north+(rect.south-rect.north)*row/(size-1)));
                const px=(lon+Math.PI)/(2*Math.PI)*pixels-.5;
                const py=(1-Math.asinh(Math.tan(lat))/Math.PI)/2*pixels-.5;
                const ix=Math.floor(px),iy=Math.floor(py),fx=px-ix,fy=py-iy;
                samples.push({fx,fy,indices:[pixelIndex(ix,iy),pixelIndex(ix+1,iy),pixelIndex(ix,iy+1),pixelIndex(ix+1,iy+1)]});
            }
            await Promise.all(Array.from(required.values()).map(async v=>{v.data=await tile(v.x,v.y,sourceLevel)}));
            function value(p){const data=required.get(p.key).data,i=p.index;return data[i]*256+data[i+1]+data[i+2]/256-32768}
            const output = new Float32Array(size * size);
            samples.forEach((p,i)=>{const a=p.indices.map(value);output[i]=(a[0]*(1-p.fx)+a[1]*p.fx)*(1-p.fy)+(a[2]*(1-p.fx)+a[3]*p.fx)*p.fy});
            return output;
        }
        const levelZeroError = Cesium.TerrainProvider.getEstimatedLevelZeroGeometricErrorForAHeightmap(scheme.ellipsoid, size, scheme.getNumberOfXTilesAtLevel(0));
        return {
            setStatusListener: fn => { onStatus = fn; if (fn) fn(failed.size === 0); },
            tilingScheme: scheme, availability, errorEvent, hasWaterMask: false, hasVertexNormals: false,
            credit: new Cesium.Credit('<a href="/map-sources" target="_blank" rel="noopener">Mapzen terrain · source attribution</a>', true),
            getLevelMaximumGeometricError: z => levelZeroError / 2 ** z,
            getTileDataAvailable: (x, y, z) => z >= 0 && z <= maxLevel && x >= 0 && y >= 0 && x < 2 ** (z+1) && y < 2 ** z,
            loadTileDataAvailability: () => undefined,
            requestTileGeometry: (x, y, z) => heights(x, y, z).then(buffer => new Cesium.HeightmapTerrainData({buffer, width: size, height: size, childTileMask: z < maxLevel ? 15 : 0})),
            destroy: () => { abort.abort(); cache.clear(); }
        };
    }
    function createImagery(Cesium, mode) {
        const osmCredit = new Cesium.Credit('© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors', true);
        if (mode === 'topographic') return new Cesium.UrlTemplateImageryProvider({
            url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', maximumLevel: 19, credit: osmCredit
        });
        const eoxCredit = new Cesium.Credit('<a href="https://cloudless.eox.at" target="_blank" rel="noopener">EOxCloudless</a> by <a href="https://eox.at" target="_blank" rel="noopener">EOX IT Services GmbH</a> (Contains modified Copernicus Sentinel data 2016)', true);
        const ignCredit = new Cesium.Credit('<a href="https://www.ign.es" target="_blank" rel="noopener">PNOA · IGN</a> 2022–2025 · <a href="https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf" target="_blank" rel="noopener">CC BY 4.0</a> scne.es', true);
        const eox = new Cesium.UrlTemplateImageryProvider({url: 'https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg', maximumLevel: 14, credit: eoxCredit});
        const ign = new Cesium.WebMapTileServiceImageryProvider({url: 'https://www.ign.es/wmts/pnoa-ma', layer: 'OI.OrthoimageCoverage', style: 'default', format: 'image/png', tileMatrixSetID: 'GoogleMapsCompatible', tilingScheme: eox.tilingScheme, maximumLevel: 19, credit: ignCredit});
        const regions = [Cesium.Rectangle.fromDegrees(-9.5,35.7,4.4,43.85), Cesium.Rectangle.fromDegrees(-18.3,27.5,-13.1,29.5)];
        const fallback = new Set(), parents = new Map();
        function national(x,y,z) {
            if (z < 12) return false;
            const p = Cesium.Rectangle.center(eox.tilingScheme.tileXYToRectangle(x,y,z));
            return regions.some(r => Cesium.Rectangle.contains(r,p));
        }
        function globalImage(x,y,z,request) {
            if (z <= 14) return eox.requestImage(x,y,z,request);
            const factor=2**(z-14), px=Math.floor(x/factor), py=Math.floor(y/factor), key=px+'/'+py;
            let image=parents.get(key);
            if (!image) {
                image=eox.requestImage(px,py,14,request);
                if (!image) return undefined;
                image=Promise.resolve(image); parents.set(key,image);
                image.catch(()=>{if(parents.get(key)===image)parents.delete(key)});
                if(parents.size>64)parents.delete(parents.keys().next().value);
            }
            return image.then(source=>{
                const canvas=document.createElement('canvas');canvas.width=canvas.height=256;
                canvas.getContext('2d').drawImage(source,(x%factor)*256/factor,(y%factor)*256/factor,256/factor,256/factor,0,0,256,256);
                return canvas;
            });
        }
        return {
            tileWidth:256,tileHeight:256,minimumLevel:0,maximumLevel:19,rectangle:eox.rectangle,tilingScheme:eox.tilingScheme,
            errorEvent:new Cesium.Event(),hasAlphaChannel:true,credit:undefined,tileDiscardPolicy:undefined,
            getTileCredits:(x,y,z)=>[national(x,y,z)&&!fallback.has(z+'/'+x+'/'+y)?ignCredit:eoxCredit],
            requestImage:(x,y,z,request)=>{
                if(!national(x,y,z))return globalImage(x,y,z,request);
                const result=ign.requestImage(x,y,z,request);
                if(!result)return undefined;
                return Promise.resolve(result).catch(error=>{
                    if(request && request.cancelled)throw error;
                    fallback.add(z+'/'+x+'/'+y);
                    // Use an unthrottled fallback; returning undefined inside a
                    // promise would otherwise produce an invalid imagery tile.
                    return globalImage(x,y,z,undefined);
                });
            },
            pickFeatures:()=>undefined
        };
    }
    function createGeocoder(Cesium, terrain) {
        const coordinates = new Cesium.CartographicGeocoderService();
        async function destination(lon,lat,requestedHeight) {
            const point=Cesium.Cartographic.fromDegrees(lon,lat);
            let height=10000;
            try { const result=await Cesium.sampleTerrain(terrain,12,[point]); if(Number.isFinite(result[0].height))height=result[0].height; } catch (_) {}
            return Cesium.Cartesian3.fromDegrees(lon,lat,Math.max(Number(requestedHeight)||0,height+5000));
        }
        return {
            credit: new Cesium.Credit('Search: <a href="https://photon.komoot.io">Photon</a> · © <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', true),
            geocode: async function(query, type) {
                if (type === Cesium.GeocodeType.AUTOCOMPLETE) return [];
                const local = await coordinates.geocode(query);
                if (local.length) {
                    for (const item of local) {
                        const p=Cesium.Cartographic.fromCartesian(item.destination);
                        item.destination=await destination(Cesium.Math.toDegrees(p.longitude),Cesium.Math.toDegrees(p.latitude),p.height);
                    }
                    return local;
                }
                const response = await fetch('/api/maps/geocode?q='+encodeURIComponent(query), {credentials:'omit'});
                if (!response.ok) throw new Error('Place search is unavailable. Try again or enter longitude, latitude.');
                const data = await response.json();
                return Promise.all((data.features||[]).map(async f=>{
                    const p=f.properties||{}, c=f.geometry.coordinates;
                    const name=[p.name,p.city,p.state,p.country].filter((x,i,a)=>x&&a.indexOf(x)===i).join(', ');
                    return {displayName:name||query, destination:await destination(c[0],c[1])};
                }));
            }
        };
    }
    global.MosaicMapSources = {createTerrain:createOpenTerrain, createImagery:createImagery, createGeocoder:createGeocoder};
})(window);
