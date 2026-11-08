Cesium.Ion.defaultAccessToken = key;

// Keep main navigation bar visible on MyMosaic viewer

var myMosaicPageParameters = new URLSearchParams(window.location.search);
var myMosaicSharedCamera = myMosaicPageParameters.get("share") === "1"
    ? MosaicCesiumRuntime.cameraStateFromUrl(window.location.href)
    : null;
var mosaicViewerBaseMode = myMosaicSharedCamera ? myMosaicSharedCamera.b : "satellite";
var mosaicViewerBaseLayer = null;
var externalMosaicAssets = [];
var externalMosaicVisualKeys = {};
var currentMosaicType = Number(tipo);
if (!isFinite(currentMosaicType) || currentMosaicType < 0 || currentMosaicType > 3) currentMosaicType = 0;
var myMosaicTemporalFilter = null;
var myMosaicRuntimeRegistry = {};
var myMosaicPendingExternalFocus = null;
var myMosaicLastCardItems = [];
var myMosaicLastCardsRequestId = 0;
var myMosaicLastViewportRequestId = 0;
var myMosaicVisualsRequested = false;
var myMosaicViewerListenersInstalled = false;
var myMosaicInitialFitDone = false;
var myMosaicCameraWasShared = !!myMosaicSharedCamera;

function myMosaicIsGaussian(item) {
    return /gaussian/i.test(String(item && (item.Subtype || item.AssetSubtype || item.SourceOrigin) || ''));
}

function myMosaicTypeId(item, key) {
    var direct = Number(item && (item.FileTypeId || item.FILE_TYPE_ID || item.TypeId || item.typeId));
    if (direct >= 1 && direct <= 3) return direct;
    var assetType = String(item && item.AssetType || "").toLowerCase();
    if (assetType === "pointcloud") return 1;
    if (assetType === "model3d") return 3;
    if (assetType === "orthophoto" || assetType === "vector") return 2;
    var text = String(key || "").toLowerCase();
    return text.indexOf("pointcloud") >= 0 ? 1 : text.indexOf("model") >= 0 || text.indexOf("tileset") >= 0 ? 3 : 2;
}

function myMosaicFamilyEligible(runtime) {
    return !currentMosaicType || Number(runtime && runtime.typeId) === Number(currentMosaicType);
}

function myMosaicTemporalEligible(item) {
    var extension = MosaicExtensionFilter.get('myMosaicExtension');
    return MosaicExtensionFilter.matches(item && item.Extensions, extension) && (!myMosaicTemporalFilter || myMosaicTemporalFilter.intersects(item || {}));
}

function registerMyMosaicRuntime(key, item, visual) {
    if (!key || !visual) return;
    if(window.MosaicAnnotations){var annotationType=myMosaicTypeId(item,key),annotationKey=key.indexOf("external-")===0?"external-"+annotationType+"-"+item.Id:"processed-"+annotationType+"-"+item.ID;MosaicAnnotations.register(viewer,annotationKey,{mapId:MYMAP_ID,allowSurfacePicking:!myMosaicIsGaussian(item),isVisible:function(){return visual.show!==false;},getVisual:function(){return visual;},getMatrix:function(){return visual.modelMatrix;}});}
    myMosaicRuntimeRegistry[key] = { item: item || {}, visual: visual, typeId: myMosaicTypeId(item, key) };
    visual.show = myMosaicTemporalEligible(item) && myMosaicFamilyEligible(myMosaicRuntimeRegistry[key]);
    if(myMosaicPendingExternalFocus && key.indexOf('external-')===0 && String(item.Id)===myMosaicPendingExternalFocus.id && Date.now()<myMosaicPendingExternalFocus.until){
        myMosaicPendingExternalFocus=null;
        focusMyMosaicVisual(visual);
    }
    if(myMosaicIsGaussian(item))scheduleMyMosaicInitialFit();
}

function focusMyMosaicVisual(visual){
    if(!visual)return false;
    if(visual.boundingSphere){MosaicCesiumRuntime.flyToTopDown(viewer,visual,0,.8);return true;}
    if(visual.imageryProvider && visual.imageryProvider.rectangle){MosaicCesiumRuntime.flyToRectangleTopDown(viewer,visual.imageryProvider.rectangle,.8);return true;}
    if(visual.entities){MosaicCesiumRuntime.focusVectorDataSource(viewer,visual,.8);return true;}
    return false;
}
function focusMyMosaicExternalAsset(event,id){
    if(event && event.target && event.target.closest && event.target.closest('a,button'))return;
    // An explicit locate action wins over the delayed initial overview.
    myMosaicInitialFitDone=true;
    if(myMosaicFitTimer){window.clearTimeout(myMosaicFitTimer);myMosaicFitTimer=null;}
    mosaicSetMobileDrawer(false);myMosaicPendingExternalFocus=null;
    var asset=externalMosaicAssets.find(function(item){return Number(item.Id)===Number(id);});
    if(!asset)return;
    var runtime=myMosaicRuntimeRegistry['external-tileset-'+id]||myMosaicRuntimeRegistry['external-model-'+id]||myMosaicRuntimeRegistry['external-raster-'+id]||myMosaicRuntimeRegistry['external-vector-'+id];
    if(runtime && focusMyMosaicVisual(runtime.visual))return;
    myMosaicPendingExternalFocus={id:String(id),until:Date.now()+30000};
    viewer.camera.flyTo({destination:Cesium.Cartesian3.fromDegrees(Number(asset.Longitude),Number(asset.Latitude),1800),orientation:{heading:0,pitch:MosaicCesiumRuntime.topDownOffset(0).pitch,roll:0}});
}

function updateMyMosaicShareUrl() {
    if (!document.getElementById("inputShare")) return;
    var base = $("#hdnUrl").val() + "MyMap.aspx?id=" + $("#hdnMapId").val() + "&share=1&lon=" + lonMove + "&lat=" + latMove + "&height=" + heightMove + "&type=" + currentMosaicType;
    base = myMosaicTemporalFilter ? myMosaicTemporalFilter.decorateUrl(base) : base;
    var extension = MosaicExtensionFilter.get('myMosaicExtension');
    if (extension) base += '&extension=' + encodeURIComponent(extension);
    $("#inputShare").val(MosaicCesiumRuntime.urlWithCameraState(base, viewer, mosaicViewerBaseMode));
}

function applyMyMosaicTemporalFilter() {
    Object.keys(myMosaicRuntimeRegistry).forEach(function (key) {
        var runtime = myMosaicRuntimeRegistry[key];
        if (runtime && runtime.visual) runtime.visual.show = myMosaicTemporalEligible(runtime.item) && myMosaicFamilyEligible(runtime);
    });
    myMosaicCardsSignature = "";
    renderMyMosaicCards(myMosaicLastCardItems, myMosaicLastCardsRequestId, myMosaicLastViewportRequestId);
    updateMyMosaicMarkerVisibility();
    updateMyMosaicShareUrl();
}
function mosaicMapImagery() {
    return MosaicCesiumRuntime.createBaseProvider(mosaicViewerBaseMode);
}
function setMyMosaicBaseLayer(mode) {
    mosaicViewerBaseMode = mode;
    if (!viewer || !viewer.imageryLayers) return;
    mosaicViewerBaseLayer = MosaicCesiumRuntime.setBaseLayer(viewer, mosaicViewerBaseMode, mosaicViewerBaseLayer);
    document.querySelectorAll('.mymosaic-map-controls [data-base]').forEach(function(button){button.classList.toggle('active',button.getAttribute('data-base')===mode);});
}
function mosaicCreateMyViewer(options) {
    if (viewer && MosaicCesiumRuntime.alive(viewer)) return viewer;
    return MosaicCesiumRuntime.createViewer("cesiumContainer", options, key, {
        requestVertexNormals: true,
        requestWaterMask: true
    });
}
function configureMosaicTerrain(targetViewer) {
    if (!targetViewer || !targetViewer.scene || !targetViewer.scene.globe) return;
    targetViewer.scene.globe.enableLighting = false;
    targetViewer.scene.globe.depthTestAgainstTerrain = !!key;
    targetViewer.shadows = false;
    if (targetViewer.clock) targetViewer.clock.shouldAnimate = false;
    mosaicViewerBaseLayer = targetViewer.imageryLayers && targetViewer.imageryLayers.length ? targetViewer.imageryLayers.get(0) : null;
    document.querySelectorAll('.mymosaic-map-controls [data-base]').forEach(function(button){button.classList.toggle('active',button.getAttribute('data-base')===mosaicViewerBaseMode);});
    externalMosaicVisualKeys = {};
    myMosaicRuntimeRegistry = {};
    resetMyMosaicMarkers(targetViewer);
}

function restoreMyMosaicInitialCamera(targetViewer) {
    if (!myMosaicSharedCamera) return false;
    var restored = MosaicCesiumRuntime.restoreCameraState(targetViewer, myMosaicSharedCamera);
    if (restored) {
        myMosaicSharedCamera = null;
        window.mosaicEarthFocused = true;
    }
    return restored;
}

var myMosaicMarkerSource = null;
var myMosaicMarkerViewer = null;

function myMosaicMarkerBillboard() {
    return {
        image: MosaicCesiumRuntime.markerImage(0),
        width: 32,
        height: 36,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_TERRAIN,
        eyeOffset: Cesium.Cartesian3.ZERO,
        pixelOffset: new Cesium.Cartesian2(0, -2),
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(250, Number.POSITIVE_INFINITY),
        scaleByDistance: new Cesium.NearFarScalar(500, 1, 12000000, .7),
        translucencyByDistance: new Cesium.NearFarScalar(500, 1, 16000000, .82)
    };
}

function myMosaicClusterImage(count) {
    return MosaicCesiumRuntime.clusterImage(count);
}

function resetMyMosaicMarkers(targetViewer) {
    myMosaicMarkerViewer = targetViewer;
    myMosaicMarkerSource = new Cesium.CustomDataSource("mymosaic-markers");
    var clustering = myMosaicMarkerSource.clustering;
    clustering.enabled = true;
    clustering.pixelRange = 44;
    clustering.minimumClusterSize = 3;
    clustering.clusterEvent.addEventListener(function (entities, cluster) {
        cluster.label.show = false;
        cluster.billboard.show = true;
        cluster.billboard.image = myMosaicClusterImage(entities.length);
        cluster.billboard.width = 44;
        cluster.billboard.height = 48;
        cluster.billboard.verticalOrigin = Cesium.VerticalOrigin.BOTTOM;
        cluster.billboard.heightReference = Cesium.HeightReference.CLAMP_TO_TERRAIN;
        cluster.billboard.eyeOffset = Cesium.Cartesian3.ZERO;
        cluster.billboard.disableDepthTestDistance = Number.POSITIVE_INFINITY;
        cluster.billboard.distanceDisplayCondition = new Cesium.DistanceDisplayCondition(250, Number.POSITIVE_INFINITY);
    });
    targetViewer.dataSources.add(myMosaicMarkerSource);
    targetViewer.camera.percentageChanged = .005;
    targetViewer.camera.changed.addEventListener(updateMyMosaicMarkerVisibility);
    targetViewer.camera.moveEnd.addEventListener(updateMyMosaicMarkerVisibility);
}

function updateMyMosaicMarkerVisibility() {
    if (!myMosaicMarkerViewer || !myMosaicMarkerSource || myMosaicMarkerViewer.isDestroyed()) return;
    var occluder = new Cesium.EllipsoidalOccluder(Cesium.Ellipsoid.WGS84, myMosaicMarkerViewer.camera.positionWC);
    var time = myMosaicMarkerViewer.clock ? myMosaicMarkerViewer.clock.currentTime : Cesium.JulianDate.now();
    myMosaicMarkerSource.entities.values.forEach(function (entity) {
        var position = entity.position && entity.position.getValue(time);
        entity.show = !!position && occluder.isPointVisible(position) && myMosaicTemporalEligible(entity.mosaicTemporalItem) && (!currentMosaicType || Number(entity.mosaicTypeId) === Number(currentMosaicType));
    });
}

function addMyMosaicMarker(position, name, markerKey, temporalItem, typeId) {
    if (!position || !myMosaicMarkerSource) return null;
    var id = "mymosaic-marker-" + String(markerKey).replace(/[^a-zA-Z0-9_-]/g, "-");
    var existing = myMosaicMarkerSource.entities.getById(id);
    if (existing) {
        existing.mosaicTemporalItem = temporalItem || existing.mosaicTemporalItem;
        existing.mosaicTypeId = Number(typeId || myMosaicTypeId(temporalItem, markerKey));
        return existing;
    }
    var marker = myMosaicMarkerSource.entities.add({
        id: id,
        position: position,
        billboard: myMosaicMarkerBillboard(),
        name: String(name || "MosaicAsset").replace("<p>", "").replace("</p>", "")
    });
    marker.mosaicTemporalItem = temporalItem || {};
    marker.mosaicTypeId = Number(typeId || myMosaicTypeId(temporalItem, markerKey));
    updateMyMosaicMarkerVisibility();
    scheduleMyMosaicInitialFit();
    return marker;
}

function addMyMosaicMarkerAt(longitude, latitude, name, markerKey, temporalItem, typeId) {
    return addMyMosaicMarker(Cesium.Cartesian3.fromDegrees(Number(longitude), Number(latitude), 0), name, markerKey, temporalItem, typeId);
}

var northMove = 0.00;
var southMove = 0.00;
var eastMove = 0.00;
var westMove = 0.00;
var heightMove = 0.00;
var latMove = 0.00;
var lonMove = 0.00;
var myMosaicCardsRequestId = 0;
var myMosaicCardsViewportRequestId = 0;
var myMosaicCardsSignature = "";

function myMosaicText(value) { return $('<div>').text(value == null ? '' : String(value)).html().replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }

function myMosaicCardKey(item, index) {
    return item.PublishId ? "published-" + item.PublishId : "processed-" + myMosaicTypeId(item) + "-" + String(item.ID || item.Id || index);
}

function myMosaicAssetCard(obj) {
    var typeId = myMosaicTypeId(obj);
    var typeName = typeId === 1 ? "POINT CLOUD" : typeId === 3 ? "3D MODEL" : "ORTHOPHOTO";
    var download = typeId === 1 ? "LAS · ZIP" : typeId === 3 ? "OBJ · ZIP" : "GEOTIFF · TIF";
    var action = typeId === 2 && obj.LATITUD != null && obj.LONGITUD != null
        ? `zoomCesiumOrthophoto(${String(obj.LATITUD).replace(',', '.')},${String(obj.LONGITUD).replace(',', '.')})`
        : `zoomCesium(${obj.ID},${typeId})`;
    return `<article class="mosaic-asset-card mosaic-card-v2 mymosaic-view-card" onclick="mosaicSetMobileDrawer(false);${action}">
        <div class="mosaic-card-preview"><span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span><img class="mosaic-preview-image" src="${myMosaicText(obj.UrlOrthoPhoto || obj.UrlPreview || '')}" alt="" loading="lazy" decoding="async" onerror="this.hidden=true" />
            <span class="mosaic-card-type-icon" title="${typeName}">${obj.IconType || ''}</span>
            <span class="mosaic-card-origin is-processed">PROCESSED</span>
            <a class="mosaic-card-details" href="${obj.UrlMarketplace || '#'}" onclick="event.stopPropagation()">${obj.IsPrivate ? 'Private details' : 'View details'}</a>
        </div>
        <div class="mosaic-card-body"><h3 class="mosaic-card-title"><button type="button" class="mosaic-locate" onclick="event.stopPropagation();mosaicSetMobileDrawer(false);${action}">${myMosaicText(obj.NAME || obj.NOMBRE)}<span class="visually-hidden"> — Locate asset</span></button></h3>
            <p class="mosaic-card-creator">${myMosaicText(obj.CreatorDisplay || 'MosaicEarth creator')}</p>
            <p class="mosaic-card-date"><span>${obj.DateLabel || 'Captured'}:</span><strong>${obj.CaptureDateDisplay || '—'}</strong></p>
            <button type="button" data-annotation-asset="processed-${typeId}-${obj.ID}" data-annotation-map="${MYMAP_ID}" aria-pressed="false">Annotations</button><div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>${download}</strong></span>${obj.IsPrivate ? '<strong class="mosaic-private-state">PRIVATE</strong>' : ''}</div>
        </div></article>`;
}

function showMyMosaicCardsLoading() {
    myMosaicCardsSignature = "";
    myMosaicCardsViewportRequestId++;
    var container = document.getElementById("items");
    if (!container) return;
    container.innerHTML = `<div class="mymosaic-cards-loading" role="status" aria-live="polite">
        <span class="mymosaic-cards-spinner" aria-hidden="true"></span>
        <strong>Loading MosaicAssets</strong>
    </div>`;
}

function renderMyMosaicCards(items, cardsRequestId, viewportRequestId) {
    if (cardsRequestId !== myMosaicCardsRequestId || viewportRequestId !== myMosaicCardsViewportRequestId) return;
    myMosaicLastCardItems = items || [];
    myMosaicLastCardsRequestId = cardsRequestId;
    myMosaicLastViewportRequestId = viewportRequestId;

    var uniqueItems = [];
    var seen = {};
    (items || []).forEach(function (item, index) {
        var key = myMosaicCardKey(item, index);
        if (seen[key]) return;
        seen[key] = true;
        if (myMosaicTemporalEligible(item)) uniqueItems.push(item);
    });

    var signature = String(currentMosaicType) + ":" + uniqueItems.map(function (item, index) {
        return myMosaicCardKey(item, index);
    }).join("|");

    if (signature !== myMosaicCardsSignature) {
        var container = $("#items");
        container.empty();
        uniqueItems.forEach(function (item) {
            container.append(myMosaicAssetCard(item));
        });
        myMosaicCardsSignature = signature;
    }

    renderExternalMosaicAssets();
}

var viewer = null;

if (window.MosaicTemporalFilter) {
    myMosaicTemporalFilter = window.MosaicTemporalFilter.mount({
        containerId: "myMosaicTemporalFilter",
        ariaLabel: "Filter this Mosaic by acquisition date",
        onChange: applyMyMosaicTemporalFilter
    });
}

var myMosaicExtensionControl = document.getElementById('myMosaicExtension');
if (myMosaicExtensionControl) {
    MosaicExtensionFilter.set(myMosaicExtensionControl, myMosaicPageParameters.get('extension') || '');
    myMosaicExtensionControl.addEventListener('change', applyMyMosaicTemporalFilter);
}

if (Number(tipo) > 0) {
    latMove = parseFloat(latMarket.replace(',', '.'));
    lonMove = parseFloat(lonMarket.replace(',', '.'));
    heightMove = parseFloat(heightMarket.replace(',', '.'));
    loadCesium(tipo, latMarket.replace(',', '.'), lonMarket.replace(',', '.'), heightMarket.replace(',', '.'));
} else {
    loadCesium(currentMosaicType, null, null);
}
setTimeout(loadExternalMosaicAssets, 0);

function loadExternalMosaicAssets() {
    $.ajax({
        type: "POST", dataType: "json", contentType: "application/json; charset=utf-8",
        url: MYLIBRARY.getUrl_page() + "/loadExternalAssets", data: JSON.stringify({ MYMAP_ID: MYMAP_ID }),
        success: function (response) {
            externalMosaicAssets = $.parseJSON(response.d) || [];
            renderExternalMosaicAssets();
        }
    });
}

function renderExternalMosaicAssets() {
    if (!viewer || !externalMosaicAssets) return;
    document.querySelectorAll('.mymosaic-filter-empty').forEach(function (item) { item.remove(); });
    document.querySelectorAll('.external-mymosaic-card').forEach(function (card) { card.remove(); });
    externalMosaicAssets.forEach(function (asset) {
                var typeId = asset.AssetType === 'pointcloud' ? 1 : (asset.AssetType === 'model3d' ? 3 : 2);
                if (Number(currentMosaicType) !== 0 && typeId !== Number(currentMosaicType)) return;
                if (asset.Latitude == null || asset.Longitude == null) return;
                var temporalEligible = myMosaicTemporalEligible(asset);
                var position = Cesium.Cartesian3.fromDegrees(Number(asset.Longitude), Number(asset.Latitude), Number(asset.Altitude || 0));
                var visualKey = typeId + ":" + asset.Id;
                if (!externalMosaicVisualKeys[visualKey]) {
                    externalMosaicVisualKeys[visualKey] = true;
                    var assetViewer = viewer;
                    if ((asset.DisplayKind === "model-glb" || asset.DisplayKind === "model-gltf") && asset.ViewerUrl) {
                        MosaicCesiumRuntime.addModel(assetViewer, asset.ViewerUrl, {modelMatrix:Cesium.Matrix4.IDENTITY,minimumPixelSize:0}).then(function(model){
                            if (assetViewer !== viewer || !MosaicCesiumRuntime.alive(assetViewer)) return;
                            var local=Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(Number(asset.Heading||0))));
                            Cesium.Matrix4.multiplyByUniformScale(local,Number(asset.Scale||1),local);
                            Cesium.Matrix4.multiply(local,Cesium.Matrix4.fromTranslation(Cesium.Cartesian3.negate(Cesium.Cartesian3.clone(model.boundingSphere.center),new Cesium.Cartesian3())),local);
                            model.modelMatrix=Cesium.Matrix4.multiply(Cesium.Transforms.eastNorthUpToFixedFrame(position),local,new Cesium.Matrix4());
                            registerMyMosaicRuntime("external-model-" + asset.Id, asset, model);
                        }).catch(function(error){ console.error("MyMosaic glTF asset failed", asset.Id, error); });
                    }
                    if (asset.DisplayKind === 'raster-tiles' && asset.ViewerUrl) {
                        var boundsUrl = new URL(asset.ViewerUrl, window.location.href);
                        boundsUrl.pathname = '/ExternalAssetFile.ashx';
                        ['z','x','y'].forEach(function(k){boundsUrl.searchParams.delete(k);});
                        boundsUrl.searchParams.set('mode','raster_bounds');
                        fetch(boundsUrl.href,{credentials:'same-origin'}).then(function(response){if(!response.ok)throw new Error('Raster bounds unavailable');return response.json();}).then(function(bounds){
                            if(assetViewer!==viewer||!MosaicCesiumRuntime.alive(assetViewer))return;
                            var rectangle=Cesium.Rectangle.fromDegrees(bounds.west,bounds.south,bounds.east,bounds.north);
                            var externalLayer=assetViewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({url:asset.ViewerUrl,rectangle:rectangle,minimumLevel:bounds.min_zoom||0,maximumLevel:bounds.max_zoom||22}));
                            registerMyMosaicRuntime('external-raster-'+asset.Id,asset,externalLayer);
                        }).catch(function(error){console.error('MyMosaic raster layer failed',asset.Id,error);MosaicCesiumRuntime.showViewerError(assetViewer,error.message);});
                    }
                    if (asset.DisplayKind === 'raster-manual-image' && asset.ViewerUrl) {
                        MosaicCesiumRuntime.loadManualRaster(assetViewer, asset.ViewerUrl, {
                            latitude:Number(asset.Latitude),
                            longitude:Number(asset.Longitude),
                            heading:Number(asset.Heading || 0),
                            scale:Number(asset.Scale || 1)
                        }).then(function(handle){
                            if(assetViewer===viewer&&MosaicCesiumRuntime.alive(assetViewer)){
                                registerMyMosaicRuntime("external-manual-raster-" + asset.Id, asset, handle.visual);
                                scheduleMyMosaicInitialFit();
                            }
                        }).catch(function(error){console.error("MyMosaic manually placed raster failed",asset.Id,error);});
                    }
                    if ((asset.DisplayKind === 'pointcloud-3dtiles' || asset.DisplayKind === 'model-3dtiles') && asset.ViewerUrl) {
                        MosaicCesiumRuntime.loadTileset(assetViewer, asset.ViewerUrl, {maximumScreenSpaceError:8,enableCollision:false}).then(function(tile){
                            if (assetViewer !== viewer || !MosaicCesiumRuntime.alive(assetViewer)) return;
                            var localAnchor=Cesium.Cartesian3.clone(tile.boundingSphere.center);
                            // Generated tiles already have an Earth-fixed root,
                            // including imports that originally used manual placement.
                            // Apply a placement delta around that root; re-anchoring
                            // the bounding centre rotates/translates native geometry.
                            var rootOrigin=tile.root&&tile.root.transform?Cesium.Matrix4.getTranslation(tile.root.transform,new Cesium.Cartesian3()):null;
                            var earthFixedRoot=!!rootOrigin&&Cesium.Cartesian3.magnitude(rootOrigin)>1e6;
                            var worldPlaced=earthFixedRoot||asset.GeoreferenceMode==='embedded';
                            var sourceFrame=Cesium.Transforms.eastNorthUpToFixedFrame(earthFixedRoot?rootOrigin:localAnchor);
                            var targetFrame=Cesium.Transforms.eastNorthUpToFixedFrame(position);
                            var local=Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(Number(asset.Heading||0))));
                            Cesium.Matrix4.multiplyByUniformScale(local,Number(asset.Scale||1),local);
                            if(!worldPlaced)Cesium.Matrix4.multiply(local,Cesium.Matrix4.fromTranslation(Cesium.Cartesian3.negate(localAnchor,new Cesium.Cartesian3())),local);
                            var targetTransform=Cesium.Matrix4.multiply(targetFrame,local,new Cesium.Matrix4());
                            tile.modelMatrix=!worldPlaced?targetTransform:Cesium.Matrix4.multiply(targetTransform,Cesium.Matrix4.inverse(sourceFrame,new Cesium.Matrix4()),new Cesium.Matrix4());
                            registerMyMosaicRuntime("external-tileset-" + asset.Id, asset, tile);
                        }).catch(function(error){ console.error("MyMosaic 3D Tiles asset failed", asset.Id, error); });
                    }
                    if (asset.DisplayKind === 'vector-geojson' && asset.ViewerUrl) {
                        MosaicCesiumRuntime.loadVectorAsset(assetViewer,asset.ViewerUrl,{assetKey:"external-vector-"+asset.Id,sourceUrl:asset.ViewerUrl}).then(function(handle){if(assetViewer===viewer&&MosaicCesiumRuntime.alive(assetViewer)){registerMyMosaicRuntime("external-vector-" + asset.Id, asset, handle.visual);scheduleMyMosaicInitialFit();}}).catch(function(error){console.error("MyMosaic vector asset failed",asset.Id,error);MosaicCesiumRuntime.showViewerError(assetViewer,"A vector MosaicAsset could not be displayed","This layer was disabled while the rest of the map remains available.",false);window.setTimeout(function(){MosaicCesiumRuntime.hideViewerError(assetViewer);},5000);});
                    }
                    if (asset.DisplayKind !== 'vector-geojson' && asset.DisplayKind !== 'raster-manual-image') addMyMosaicMarkerAt(asset.Longitude, asset.Latitude, asset.Name, "external-" + typeId + "-" + asset.Id, asset);
                }
                if (!temporalEligible) return;
                $("#items").append('<article class="mosaic-asset-card mosaic-card-v2 mymosaic-view-card external-mymosaic-card" onclick="focusMyMosaicExternalAsset(event,' + Number(asset.Id) + ')"><div class="mosaic-card-preview" style="background-image:url(\'' + (asset.PreviewUrl||'') + '\')"><span class="mosaic-card-type-icon">' + (asset.IconType||'') + '</span><span class="mosaic-card-origin ' + (asset.SourceOrigin==='processed'?'is-processed':'is-imported') + '">' + (asset.SourceOrigin==='processed'?'PROCESSED':asset.SourceOrigin==='gaussian'?'GENERATED':'IMPORTED') + '</span><span class="preview-provenance">Location preview</span><a class="mosaic-card-details" href="' + asset.DetailUrl + '" onclick="event.stopPropagation()">View details</a></div><div class="mosaic-card-body"><h3 class="mosaic-card-title"><button type="button" class="mosaic-locate" onclick="event.stopPropagation();this.closest(&quot;article&quot;).click()">' + myMosaicText(asset.Name) + '<span class="visually-hidden"> — Locate asset</span></button></h3><p class="mosaic-card-creator">MosaicEarth creator</p><p class="mosaic-card-date"><span>' + (asset.DateLabel || 'Captured') + ':</span><strong>' + (asset.CaptureDateDisplay || '—') + '</strong></p><button type="button" data-annotation-asset="external-' + typeId + '-' + asset.Id + '" data-annotation-map="' + MYMAP_ID + '" aria-pressed="false">Annotations</button><div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>' + myMosaicText(asset.Format.toUpperCase()) + '</strong></span>' + (asset.IsPrivate?'<strong class="mosaic-private-state">PRIVATE</strong>':'') + '</div></div></article>');
    });
    showMyMosaicFilterEmptyState();
}

function showMyMosaicFilterEmptyState() {
    if (MosaicExtensionFilter.get('myMosaicExtension') && !document.querySelector('#items .mymosaic-view-card, #items .mymosaic-cards-loading')) {
        $('#items').append('<div class="map-empty-state mymosaic-filter-empty" role="status"><strong>No assets match this view and filters</strong><span>Choose All extensions, broaden the family or time filter, or move the map.</span></div>');
    }
}

function setMyMosaicActiveFamily(typeId) {
    ["all-assets-menu", "orthophoto-menu", "3d-menu", "pointcloud-menu"].forEach(function (id) {
        var item = document.getElementById(id);
        if (item) { item.classList.remove("active-icon-menu"); item.setAttribute("aria-pressed", "false"); }
    });
    var activeId = Number(typeId) === 1 ? "pointcloud-menu" : Number(typeId) === 2 ? "orthophoto-menu" : Number(typeId) === 3 ? "3d-menu" : "all-assets-menu";
    var active = document.getElementById(activeId);
    if (active) { active.classList.add("active-icon-menu"); active.setAttribute("aria-pressed", "true"); }
}

function createMyMosaicStandardViewer() {
    var isNew = !viewer || !MosaicCesiumRuntime.alive(viewer);
    var next = mosaicCreateMyViewer({
        imageryProvider: mosaicMapImagery(), timeline:false, animation:false,
        fullscreenButton:false, homeButton:false, infoBox:false,
        sceneModePicker:false, baseLayerPicker:false, navigationHelpButton:false,
        projectionPicker:false, scene3DOnly:true
    });
    if (isNew) configureMosaicTerrain(next);
    return next;
}

function loadAllMyMosaicCards(cardsRequestId) {
    if (!viewer || viewer.isDestroyed()) return;
    var rectangle = viewer.camera.computeViewRectangle();
    if (!rectangle) return;
    var viewportRequestId = ++myMosaicCardsViewportRequestId;
    var bounds = {
        north: Cesium.Math.toDegrees(rectangle.north).toString(),
        south: Cesium.Math.toDegrees(rectangle.south).toString(),
        east: Cesium.Math.toDegrees(rectangle.east).toString(),
        west: Cesium.Math.toDegrees(rectangle.west).toString()
    };
    var remaining = 3, combined = [];
    [1, 2, 3].forEach(function (typeId) {
        $.ajax({
            type:"POST", dataType:"json", contentType:"application/json; charset=utf-8",
            url:MYLIBRARY.getUrl_page()+"/loadItems",
            data:JSON.stringify({north:bounds.north,south:bounds.south,east:bounds.east,west:bounds.west,type_id:String(typeId),MYMAP_ID:MYMAP_ID})
        }).done(function (response) {
            combined = combined.concat($.parseJSON(response.d) || []);
        }).always(function () {
            remaining -= 1;
            if (remaining === 0) renderMyMosaicCards(combined, cardsRequestId, viewportRequestId);
        });
    });
}

function loadAllMyMosaicVisuals() {
    var targetViewer = viewer;
    function endpoint(name, onItem) {
        $.ajax({
            type:"POST", dataType:"json", contentType:"application/json; charset=utf-8",
            url:MYLIBRARY.getUrl_page()+"/"+name, data:JSON.stringify({MYMAP_ID:MYMAP_ID})
        }).done(function (response) {
            if (targetViewer !== viewer || !MosaicCesiumRuntime.alive(targetViewer)) return;
            ($.parseJSON(response.d) || []).forEach(onItem);
        });
    }
    endpoint("loadOrthophoto", function (item) {
        MosaicCesiumRuntime.loadTmsLayer(targetViewer,item.URL_CESIUM,{maximumLevel:20,credit:"MosaicEarth"}).then(function(layer){
            if(targetViewer!==viewer)return;registerMyMosaicRuntime("processed-raster-"+item.ID,item,layer);scheduleMyMosaicInitialFit();
        }).catch(function(error){console.error("MyMosaic orthophoto failed",item.ID,error);});
        addMyMosaicMarkerAt(item.MED_LON,item.MED_LAT,item.NOMBRE,"processed-2-"+item.ID,item);
    });
    endpoint("loadPointcloud", function (item) {
        MosaicCesiumRuntime.loadTileset(targetViewer,item.URL_CESIUM,{maximumScreenSpaceError:8,enableCollision:false}).then(function(tile){
            if(targetViewer!==viewer)return;
            window.tilesets=window.tilesets||{};window.tilesets["tileset"+item.ID]=tile;
            var location=Cesium.Cartographic.fromCartesian(tile.boundingSphere.center);
            MosaicCesiumRuntime.applyProcessedTilesetHeight(tile,item.HEIGHT);
            if(tile.pointCloudShading)tile.pointCloudShading.attenuation=true;
            registerMyMosaicRuntime("processed-pointcloud-"+item.ID,item,tile);
            addMyMosaicMarker(Cesium.Cartesian3.fromRadians(location.longitude,location.latitude,0),item.NOMBRE,"processed-1-"+item.ID,item);
            scheduleMyMosaicInitialFit();
        }).catch(function(error){console.error("MyMosaic point cloud failed",item.ID,error);});
    });
    endpoint("load3DModels", function (item) {
        MosaicCesiumRuntime.loadTileset(targetViewer,item.URL_CESIUM,{maximumScreenSpaceError:16,enableCollision:false}).then(function(tile){
            if(targetViewer!==viewer)return;
            window.tilesets=window.tilesets||{};window.tilesets["tileset"+item.ID]=tile;
            var location=Cesium.Cartographic.fromCartesian(tile.boundingSphere.center);
            MosaicCesiumRuntime.applyProcessedTilesetHeight(tile,item.HEIGHT);
            registerMyMosaicRuntime("processed-model-"+item.ID,item,tile);
            addMyMosaicMarker(Cesium.Cartesian3.fromRadians(location.longitude,location.latitude,0),item.NOMBRE,"processed-3-"+item.ID,item);
            scheduleMyMosaicInitialFit();
        }).catch(function(error){console.error("MyMosaic 3D model failed",item.ID,error);});
    });
}

function loadMyMosaicCards(cardsRequestId) {
    if (!viewer || viewer.isDestroyed()) return;
    if (!currentMosaicType) { loadAllMyMosaicCards(cardsRequestId); return; }
    var rectangle=viewer.camera.computeViewRectangle();
    if(!rectangle)return;
    var viewportRequestId=++myMosaicCardsViewportRequestId;
    $.ajax({
        type:"POST",dataType:"json",contentType:"application/json; charset=utf-8",
        url:MYLIBRARY.getUrl_page()+"/loadItems",
        data:JSON.stringify({north:String(Cesium.Math.toDegrees(rectangle.north)),south:String(Cesium.Math.toDegrees(rectangle.south)),east:String(Cesium.Math.toDegrees(rectangle.east)),west:String(Cesium.Math.toDegrees(rectangle.west)),type_id:String(currentMosaicType),MYMAP_ID:MYMAP_ID})
    }).done(function(response){renderMyMosaicCards($.parseJSON(response.d)||[],cardsRequestId,viewportRequestId);});
}

var myMosaicFitTimer = null;
function scheduleMyMosaicInitialFit() {
    if (myMosaicCameraWasShared || myMosaicInitialFitDone || !viewer || viewer.isDestroyed()) return;
    if (myMosaicFitTimer) window.clearTimeout(myMosaicFitTimer);
    myMosaicFitTimer = window.setTimeout(function(){
        myMosaicFitTimer = null;
        if(!viewer||viewer.isDestroyed()||myMosaicCameraWasShared||myMosaicInitialFitDone)return;
        var markerEntities = myMosaicMarkerSource ? myMosaicMarkerSource.entities.values.slice() : [];
        var now = viewer.clock ? viewer.clock.currentTime : Cesium.JulianDate.now();
        var pts = [];
        markerEntities.forEach(function(e){
            var p = e && e.position && e.position.getValue ? e.position.getValue(now) : null;
            if (p) pts.push(p);
        });
        if (pts.length > 0) {
            myMosaicInitialFitDone = true;
            var sphere = Cesium.BoundingSphere.fromPoints(pts);
            var overviewRange = Math.max((sphere.radius || 0) * 2.8, 2400);
            viewer.camera.flyToBoundingSphere(sphere, {
                offset: MosaicCesiumRuntime.topDownOffset(overviewRange, 0),
                duration: 0.9
            });
            return;
        }
        var targets = [];
        Object.keys(myMosaicRuntimeRegistry).forEach(function(key){
            var visual = myMosaicRuntimeRegistry[key] && myMosaicRuntimeRegistry[key].visual;
            if (visual && visual.entities && visual.entities.values) targets = targets.concat(visual.entities.values);
            else if (visual && visual.rectangle) targets.push(visual);
        });
        if (!targets.length) return;
        myMosaicInitialFitDone = true;
        MosaicCesiumRuntime.flyToTopDown(viewer,targets,2400,.9);
    },280);
}

function ensureMyMosaicViewer(cardsRequestId) {
    var isNew=!viewer||!MosaicCesiumRuntime.alive(viewer);
    viewer=createMyMosaicStandardViewer();
    if(isNew){
        viewer.scene.canvas.addEventListener('pointerdown',function(){myMosaicPendingExternalFocus=null;});
        restoreMyMosaicInitialCamera(viewer);
        if(!myMosaicViewerListenersInstalled){
            myMosaicViewerListenersInstalled=true;
            viewer.camera.moveEnd.addEventListener(function(){
                if(!viewer||viewer.isDestroyed())return;
                var rectangle=viewer.camera.computeViewRectangle(),cartographic=Cesium.Cartographic.fromCartesian(viewer.camera.positionWC);
                if(rectangle){northMove=Cesium.Math.toDegrees(rectangle.north);southMove=Cesium.Math.toDegrees(rectangle.south);eastMove=Cesium.Math.toDegrees(rectangle.east);westMove=Cesium.Math.toDegrees(rectangle.west);latMove=(northMove+southMove)/2;lonMove=(eastMove+westMove)/2;}
                heightMove=cartographic.height;loadMyMosaicCards(myMosaicCardsRequestId);updateMyMosaicShareUrl();
            });
        }
    }
    if(!myMosaicVisualsRequested){myMosaicVisualsRequested=true;loadAllMyMosaicVisuals();}
    applyMyMosaicTemporalFilter();
    renderExternalMosaicAssets();
    window.setTimeout(function(){loadMyMosaicCards(cardsRequestId);scheduleMyMosaicInitialFit();},0);
}



function loadCesium(opcion, latMarket, lonMarket, heightMarket) {
    currentMosaicType = Number(opcion);
    var cardsRequestId = ++myMosaicCardsRequestId;
    showMyMosaicCardsLoading();
    setMyMosaicActiveFamily(opcion);
    ensureMyMosaicViewer(cardsRequestId);
    return;

    if (opcion == 1) {
        document.getElementById("pointcloud-menu").classList.add("active-icon-menu");
        document.getElementById("orthophoto-menu").classList.remove("active-icon-menu");
        document.getElementById("3d-menu").classList.remove("active-icon-menu");
    } else if (opcion == 2) {
        document.getElementById("orthophoto-menu").classList.add("active-icon-menu");
        document.getElementById("3d-menu").classList.remove("active-icon-menu");
        document.getElementById("pointcloud-menu").classList.remove("active-icon-menu");
    } else if (opcion == 3) {
        document.getElementById("pointcloud-menu").classList.remove("active-icon-menu");
        document.getElementById("orthophoto-menu").classList.remove("active-icon-menu");
        document.getElementById("3d-menu").classList.add("active-icon-menu");
    }

    if (opcion == 2) {

        viewer = mosaicCreateMyViewer({
            imageryProvider: mosaicMapImagery(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            infoBox: false,
            sceneModePicker: false,
            //imageryProvider: Cesium.createWorldImagery({
            //    style: Cesium.IonWorldImageryStyle.AERIAL_WITH_LABELS
            //}),
            baseLayerPicker: false,
            navigationHelpButton: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicTerrain(viewer);

        var scene = viewer.scene;

        if (!restoreMyMosaicInitialCamera(viewer) && latMove != 0.00 && lonMove != 0.00) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMove, latMove, heightMove),
            });
        }

        const elemento = document.querySelector('#cesiumContainer');

        viewer.scene.globe.tileLoadProgressEvent.addEventListener(function () {
            if (viewer.scene.globe.tilesLoaded) {
                var rectangle = scene.camera.computeViewRectangle();
                var cartographic = Cesium.Cartographic.fromCartesian(scene.camera.positionWC);
                heightMove = cartographic.height;

                var north = Cesium.Math.toDegrees(rectangle.north);
                var south = Cesium.Math.toDegrees(rectangle.south);
                var east = Cesium.Math.toDegrees(rectangle.east);
                var west = Cesium.Math.toDegrees(rectangle.west);

                northMove = Cesium.Math.toDegrees(rectangle.north);
                southMove = Cesium.Math.toDegrees(rectangle.south);
                eastMove = Cesium.Math.toDegrees(rectangle.east);
                westMove = Cesium.Math.toDegrees(rectangle.west);

                latMove = (northMove + southMove) / 2;
                lonMove = (eastMove + westMove) / 2;

                console.log("North: " + northMove);
                console.log("South: " + southMove);
                console.log("East: " + eastMove);
                console.log("West: " + westMove);

                heightMarket = heightMove;
                latMarket = latMove;
                lonMarket = lonMove;

                loadItems(north, south, east, west, 2);

                updateMyMosaicShareUrl();
            }
        });



        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id, MYMAP_ID };
            var viewportRequestId = ++myMosaicCardsViewportRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    var json = $.parseJSON(response.d);
                    renderMyMosaicCards(json, cardsRequestId, viewportRequestId);
                },
                error: function (response) {

                }
            });
        }

        function slugify(phrase) {
            let str = removeAccent(phrase).toLowerCase();
            str = str.replace(/[^a-z0-9\s-]/g, ''); // Remove all non valid chars          
            str = str.replace(/\s+/g, ' ').trim(); // convert multiple spaces into one space  
            str = str.replace(/\s/g, '-'); // Replace spaces by dashes
            return str;
        }

        function removeAccent(str) {
            return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        }

        function tableTemplateDataCard(obj) {
            return myMosaicAssetCard(obj);
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesiumOrthophoto(${obj.LATITUD.replace(',', '.')}, ${obj.LONGITUD.replace(',', '.')})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${myMosaicText(obj.NAME)}</p>
                        <p class="product-author">${obj.CreatorDisplay}</p>
                        <div class="row m-0 p-0 product-box-info">
                            <div class="col-md-4 ps-2 pt-1">
                                <p class="product-price">${displayCredits}</p>
                                <!-- Condicion para no mostrar la palabra 'credits' si es FREE -->
                                <p>${displayCredits === 'FREE' ? '' : 'credits'}</p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-area.svg" class="me-1" />
                                    ${obj.AreaDisplay} ha
                                </p>
                                <p>
                                    <img src="../Resources/icon-gsd.svg" class="me-1" />                   
				                    ${obj.GPSAverageDisplay} m
                                </p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-gps.svg" class="me-1" />
                                    ${obj.GSDAverageDisplay}  cm/px                                   
                                </p>
                                <p>
                                    <img src="../Resources/icon-publish-date.svg" class="me-1" />
                                    ${obj.CaptureDateDisplay}
                                </p>
                            </div>
                        </div>
                    </div>`;
        }

        function tableTemplateData(Nombre, ID) {
            let carpeta = slugify(ID + "-" + Nombre);
            return `
                <div class="object" onclick="zoomCesium(${ID})">
                    <div class="object-preview">
                        <img style="height:180px; width:180px;" src="${window.MOSAICEARTH_ASSET_BASE}${carpeta}/procesado/odm_orthophoto/odm_orthophoto.webp" />
                    </div>
                    <div class="object-description">
                        <div class="title-description">
                            ${Nombre}
                        </div>
                        <div class="author-description">
                            AuthorName
                        </div>

                    </div>
                </div>
            `;
        }

        var url = MYLIBRARY.getUrl_page();

        var data = { MYMAP_ID };

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/loadOrthophoto",
            data: JSON.stringify(data),
            success: function (response) {
                if (cardsRequestId !== myMosaicCardsRequestId) return;
                var json = $.parseJSON(response.d);
                var items = json;
                for (var i = 0; i < items.length; i++) {
                    loadOrthophoto(items[i]);
                }
            },
            error: function (response) {

            }
        });

        function loadOrthophoto(item) {
            var med_lat = item.MED_LAT, med_lon = item.MED_LON, url_cesium = item.URL_CESIUM, nombre = item.NOMBRE, ID = item.ID;
            window.tilesets = window.tilesets || {};
            const tilesetName = `tileset${ID}`;

            MosaicCesiumRuntime.loadTmsLayer(viewer, url_cesium, {
                maximumLevel: 20, // adjust to your maximum tile level
                credit: 'Your attribution here'
            }).then(function (layer) {
                window.tilesets[tilesetName] = layer;
                registerMyMosaicRuntime("processed-raster-" + ID, item, layer);
            }).catch(function (error) {
                console.error("MyMosaic orthophoto failed", ID, error);
            });

            addMyMosaicMarkerAt(med_lon, med_lat, nombre, "processed-2-" + ID, item);
            if (!window.mosaicEarthFocused) {
                window.mosaicEarthFocused = true;
                viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(parseFloat(med_lon), parseFloat(med_lat), 1800000) });
            }

        }

    }

    if (opcion == 1) {



        // Initialize the Cesium Viewer in the HTML element with the `cesiumContainer` ID.
        viewer = mosaicCreateMyViewer({
            imageryProvider: mosaicMapImagery(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            sceneModePicker: false,
            baseLayerPicker: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicTerrain(viewer);

        var scene = viewer.scene;

        const elemento = document.querySelector('#cesiumContainer');

        if (!restoreMyMosaicInitialCamera(viewer) && latMove != 0.00 && lonMove != 0.00) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMove, latMove, heightMove),
            });
        }

        viewer.scene.globe.tileLoadProgressEvent.addEventListener(function () {
            if (viewer.scene.globe.tilesLoaded) {
                var rectangle = scene.camera.computeViewRectangle();
                var cartographic = Cesium.Cartographic.fromCartesian(scene.camera.positionWC);
                heightMove = cartographic.height;

                var north = Cesium.Math.toDegrees(rectangle.north);
                var south = Cesium.Math.toDegrees(rectangle.south);
                var east = Cesium.Math.toDegrees(rectangle.east);
                var west = Cesium.Math.toDegrees(rectangle.west);

                console.log("North: " + north);
                console.log("South: " + south);
                console.log("East: " + east);
                console.log("West: " + west);

                northMove = Cesium.Math.toDegrees(rectangle.north);
                southMove = Cesium.Math.toDegrees(rectangle.south);
                eastMove = Cesium.Math.toDegrees(rectangle.east);
                westMove = Cesium.Math.toDegrees(rectangle.west);

                latMove = (northMove + southMove) / 2;
                lonMove = (eastMove + westMove) / 2;

                heightMarket = heightMove;
                latMarket = latMove;
                lonMarket = lonMove;

                loadItems(north, south, east, west, 1);

                updateMyMosaicShareUrl();
            }
        });

        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id, MYMAP_ID };
            var viewportRequestId = ++myMosaicCardsViewportRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    var json = $.parseJSON(response.d);
                    renderMyMosaicCards(json, cardsRequestId, viewportRequestId);
                },
                error: function (response) {

                }
            });

        }

        function slugify(phrase) {
            let str = removeAccent(phrase).toLowerCase();
            str = str.replace(/[^a-z0-9\s-]/g, ''); // Remove all non valid chars          
            str = str.replace(/\s+/g, ' ').trim(); // convert multiple spaces into one space  
            str = str.replace(/\s/g, '-'); // Replace spaces by dashes
            return str;
        }

        function removeAccent(str) {
            return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        }

        function tableTemplateDataCard(obj) {
            return myMosaicAssetCard(obj);
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesium(${obj.ID})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${myMosaicText(obj.NAME)}</p>
                        <p class="product-author">${obj.CreatorDisplay}</p>
                        <div class="row m-0 p-0 product-box-info">
                            <div class="col-md-4 ps-2 pt-1">
                                <p class="product-price">${displayCredits}</p>
                                <!-- Condicion para no mostrar la palabra 'credits' si es FREE -->
                                <p>${displayCredits === 'FREE' ? '' : 'credits'}</p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-area.svg" class="me-1" />
                                    ${obj.AreaDisplay} ha
                                </p>
                                <p>
                                    <img src="../Resources/icon-gsd.svg" class="me-1" />                   
				                    ${obj.GPSAverageDisplay} m
                                </p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-gps.svg" class="me-1" />
                                    ${obj.GSDAverageDisplay}  cm/px                                   
                                </p>
                                <p>
                                    <img src="../Resources/icon-publish-date.svg" class="me-1" />
                                    ${obj.CaptureDateDisplay}
                                </p>
                            </div>
                        </div>
                    </div>`;
        }

        function tableTemplateData(Nombre, ID) {
            let carpeta = slugify(ID + "-" + Nombre);
            return `
                <div class="object" onclick="zoomCesium(${ID})">
                    <div class="object-preview">
                        <img style="height:180px; width:180px;" src="${window.MOSAICEARTH_ASSET_BASE}${carpeta}/procesado/odm_orthophoto/odm_orthophoto.webp" />
                    </div>
                    <div class="object-description">
                        <div class="title-description">
                            ${Nombre}
                        </div>
                        <div class="author-description">
                            AuthorName
                        </div>

                    </div>
                </div>
            `;
        }



        var url = MYLIBRARY.getUrl_page();

        var data = { MYMAP_ID };

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/loadPointcloud",
            data: JSON.stringify(data),
            success: function (response) {
                if (cardsRequestId !== myMosaicCardsRequestId) return;
                var json = $.parseJSON(response.d);
                var items = json;
                for (var i = 0; i < items.length; i++) {
                    loadPointcloud(items[i]);
                }
            },
            error: function (response) {

            }
        });

        function loadPointcloud(item) {
            var height = item.HEIGHT, url_cesium = item.URL_CESIUM, nombre = item.NOMBRE, ID = item.ID;
            window.tilesets = window.tilesets || {};
            const tilesetName = `tileset${ID}`;
            var assetViewer = viewer;
            MosaicCesiumRuntime.loadTileset(assetViewer, url_cesium, {maximumScreenSpaceError:8,enableCollision:false}).then(async (tile) => {
                if (assetViewer !== viewer || !MosaicCesiumRuntime.alive(assetViewer)) return;
                window.tilesets[tilesetName] = tile;


                var boundingSphere = tile.boundingSphere;
                var cartographic = Cesium.Cartographic.fromCartesian(boundingSphere.center);
                MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, height);
                registerMyMosaicRuntime("processed-pointcloud-" + ID, item, tile);

                tile.pointCloudShading.attenuation = true;
                addMyMosaicMarker(
                    Cesium.Cartesian3.fromRadians(cartographic.longitude, cartographic.latitude, 0),
                    nombre,
                    "processed-1-" + ID,
                    item
                );

            }).catch(function (error) { console.error("MyMosaic point cloud failed", ID, error); });

        }



    }

    if (opcion == 3) {

        // Initialize the Cesium Viewer in the HTML element with the `cesiumContainer` ID.
        viewer = mosaicCreateMyViewer({
            imageryProvider: mosaicMapImagery(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            sceneModePicker: false,
            baseLayerPicker: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicTerrain(viewer);

        var scene = viewer.scene;

        if (!restoreMyMosaicInitialCamera(viewer) && latMove != 0.00 && lonMove != 0.00) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMove, latMove, heightMove),
            });
        }

        scene.camera.moveEnd.addEventListener(function () {
            var rectangle = scene.camera.computeViewRectangle();
            var cartographic = Cesium.Cartographic.fromCartesian(scene.camera.positionWC);
            heightMove = cartographic.height;

            var north = Cesium.Math.toDegrees(rectangle.north);
            var south = Cesium.Math.toDegrees(rectangle.south);
            var east = Cesium.Math.toDegrees(rectangle.east);
            var west = Cesium.Math.toDegrees(rectangle.west);

            console.log("North: " + north);
            console.log("South: " + south);
            console.log("East: " + east);
            console.log("West: " + west);

            northMove = Cesium.Math.toDegrees(rectangle.north);
            southMove = Cesium.Math.toDegrees(rectangle.south);
            eastMove = Cesium.Math.toDegrees(rectangle.east);
            westMove = Cesium.Math.toDegrees(rectangle.west);

            latMove = (northMove + southMove) / 2;
            lonMove = (eastMove + westMove) / 2;

            heightMarket = heightMove;
            latMarket = latMove;
            lonMarket = lonMove;

            loadItems(north, south, east, west, 3);

            updateMyMosaicShareUrl();
        });

        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id, MYMAP_ID };
            var viewportRequestId = ++myMosaicCardsViewportRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    var json = $.parseJSON(response.d);
                    renderMyMosaicCards(json, cardsRequestId, viewportRequestId);
                },
                error: function (response) {

                }
            });

        }

        function slugify(phrase) {
            let str = removeAccent(phrase).toLowerCase();
            str = str.replace(/[^a-z0-9\s-]/g, ''); // Remove all non valid chars          
            str = str.replace(/\s+/g, ' ').trim(); // convert multiple spaces into one space  
            str = str.replace(/\s/g, '-'); // Replace spaces by dashes
            return str;
        }

        function removeAccent(str) {
            return str.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
        }

        function tableTemplateDataCard(obj) {
            return myMosaicAssetCard(obj);
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesium(${obj.ID})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${myMosaicText(obj.NAME)}</p>
                        <p class="product-author">${obj.CreatorDisplay}</p>
                        <div class="row m-0 p-0 product-box-info">
                            <div class="col-md-4 ps-2 pt-1">
                                <p class="product-price">${displayCredits}</p>
                                <!-- Condicion para no mostrar la palabra 'credits' si es FREE -->
                                <p>${displayCredits === 'FREE' ? '' : 'credits'}</p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-area.svg" class="me-1" />
                                    ${obj.AreaDisplay} ha
                                </p>
                                <p>
                                    <img src="../Resources/icon-gsd.svg" class="me-1" />                   
				                    ${obj.GPSAverageDisplay} m
                                </p>
                            </div>
                            <div class="col-md-4 ps-2 pt-2">
                                <p class="mb-2">
                                    <img src="../Resources/icon-gps.svg" class="me-1" />
                                    ${obj.GSDAverageDisplay}  cm/px                                   
                                </p>
                                <p>
                                    <img src="../Resources/icon-publish-date.svg" class="me-1" />
                                    ${obj.CaptureDateDisplay}
                                </p>
                            </div>
                        </div>
                    </div>`;
        }

        function tableTemplateData(Nombre, ID) {
            let carpeta = slugify(ID + "-" + Nombre);
            return `
                <div class="object" onclick="zoomCesium(${ID})">
                    <div class="object-preview">
                        <img style="height:200px; width:200px;" src="${window.MOSAICEARTH_ASSET_BASE}${carpeta}/procesado/odm_orthophoto/odm_orthophoto.webp" />
                    </div>
                    <div class="object-description">
                        <div class="title-description">
                            ${Nombre}
                        </div>
                        <div class="author-description">
                            AuthorName
                        </div>

                    </div>
                </div>
            `;
        }

        var url = MYLIBRARY.getUrl_page();

        var data = { MYMAP_ID };

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/load3DModels",
            data: JSON.stringify(data),
            success: function (response) {
                if (cardsRequestId !== myMosaicCardsRequestId) return;
                var json = $.parseJSON(response.d);
                var items = json;
                for (var i = 0; i < items.length; i++) {
                    load3D(items[i]);
                }
            },
            error: function (response) {

            }
        });

        function load3D(item) {
            var height = item.HEIGHT, url_cesium = item.URL_CESIUM, nombre = item.NOMBRE, ID = item.ID;
            window.tilesets = window.tilesets || {};
            const tilesetName = `tileset${ID}`;
            var assetViewer = viewer;
            MosaicCesiumRuntime.loadTileset(assetViewer, url_cesium, {maximumScreenSpaceError:16,enableCollision:false}).then(async (tile) => {
                if (assetViewer !== viewer || !MosaicCesiumRuntime.alive(assetViewer)) return;
                window.tilesets[tilesetName] = tile;


                var boundingSphere = tile.boundingSphere;
                var cartographic = Cesium.Cartographic.fromCartesian(boundingSphere.center);
                MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, height);
                registerMyMosaicRuntime("processed-model-" + ID, item, tile);

                addMyMosaicMarker(
                    Cesium.Cartesian3.fromRadians(cartographic.longitude, cartographic.latitude, 0),
                    nombre,
                    "processed-3-" + ID,
                    item
                );

            }).catch(function (error) { console.error("MyMosaic 3D model failed", ID, error); });
        }


    }
    setTimeout(renderExternalMosaicAssets, 0);
}

function zoomCesium(ID, typeId) {
    myMosaicPendingExternalFocus=null;

    const tilesetName = `tileset${ID}`;

    var runtimeKey = Number(typeId) === 1 ? "processed-pointcloud-" : "processed-model-";
    var runtime = typeId && myMosaicRuntimeRegistry[runtimeKey + ID];
    var target = runtime ? runtime.visual : window.tilesets && window.tilesets[tilesetName];
    if (!target) return;
    var range = target.boundingSphere ? Math.max(target.boundingSphere.radius * 2.5, 300) : 1200;
    MosaicCesiumRuntime.flyToTopDown(viewer, target, range);


}

function zoomCesiumOrthophoto(LATITUD, LONGITUD) {

    viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(LONGITUD, LATITUD, 2000.0),
        orientation: { heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0 }
    });

    //viewer.camera.flyTo({
    //    destination: Cesium.Cartesian3.fromDegrees(1.83707840412516, 42.3864883580154, 0), //Reemplaza longitud y latitud con los valores que necesites
    //    orientation: {
    //        heading: Cesium.Math.toRadians(0.0),
    //        pitch: Cesium.Math.toRadians(-90.0),
    //        roll: 0.0
    //    }
    //});
}

function hideUnwantedItems() {
    // Obtén todos los items del baseLayerPicker
    // let items = document.querySelectorAll('.cesium-baseLayerPicker-item');

    // items.forEach(item => {
        // let label = item.querySelector('.cesium-baseLayerPicker-itemLabel');

        // if (label && label.textContent !== "Bing Maps Aerial" && label.textContent !== "Open­Street­Map") {
            // item.style.display = "none"; // Esconde el item
        // }
    // });

    // // Ocultar todos los títulos "Terrain"
    // let terrainTitles = document.querySelectorAll('.cesium-baseLayerPicker-sectionTitle');
    // terrainTitles.forEach(title => {
        // if (title.textContent.trim() === "Terrain") {
            // title.style.display = "none"; // Esconde el título
        // }
    // });

    // // Ocultar la sección que tiene 'foreach: _terrainProviders' en data-bind
    // let terrainProviderSection = document.querySelector('div[data-bind="foreach: _terrainProviders"]');
    // if (terrainProviderSection) {
        // terrainProviderSection.style.display = "none"; // Esconde la sección
    // }
}

// Llama a la función después de que Cesium haya terminado de cargar y crear el baseLayerPicker
setTimeout(hideUnwantedItems, 1000); // Puedes ajustar el tiempo si es necesario


