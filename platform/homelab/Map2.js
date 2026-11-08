Cesium.Ion.defaultAccessToken = key;

// Use a token-independent public base map. Cesium's implicit Ion imagery
// becomes a black globe when the token is missing or no longer valid.
var mosaicBaseMode = "satellite";
var mosaicBaseLayerInstance = null;
function mosaicEarthBaseLayer() {
    return MosaicCesiumRuntime.createBaseProvider(mosaicBaseMode);
}
function setMosaicBaseLayer(mode) {
    mosaicBaseMode = mode;
    if (viewer && viewer.imageryLayers) {
        mosaicBaseLayerInstance = MosaicCesiumRuntime.setBaseLayer(viewer, mosaicBaseMode, mosaicBaseLayerInstance);
    }
    var satelliteButton = document.getElementById("base-satellite");
    var topoButton = document.getElementById("base-topographic");
    if (satelliteButton) satelliteButton.classList.toggle("active", mode === "satellite");
    if (topoButton) topoButton.classList.toggle("active", mode === "topographic");
}

var mosaicAssetSearch = '';
var mosaicSearchTimer = null;
document.getElementById('mapAssetSearch').addEventListener('input', function () {
    var value = this.value.trim();
    window.clearTimeout(mosaicSearchTimer);
    mosaicSearchTimer = window.setTimeout(function () {
        mosaicAssetSearch = value;
        document.getElementById('mapSearchScope').textContent = value ? 'Searching all public assets by name or creator. Family and time filters still apply.' : "Browsing assets in this view. Use the globe's place search to go to a location.";
        mosaicFocusedAssetKey = ''; mosaicFocusedAssetUntil = 0; mosaicLastCardSignature = '';
        mosaicScheduleAssetQuery(true);
    }, 400);
});

var northMove = 0.00;
var southMove = 0.00;
var eastMove = 0.00;
var westMove = 0.00;
var heightMove = 0.00;
var latMove = 0.00;
var lonMove = 0.00;

function mosaicCreateViewer(options) {
    if (viewer && MosaicCesiumRuntime.alive(viewer)) return viewer;
    return MosaicCesiumRuntime.createViewer("cesiumContainer", options, key, {
        requestVertexNormals: true,
        requestWaterMask: true
    });
}

function configureMosaicEarthTerrain(targetViewer) {
    if (!targetViewer || !targetViewer.scene || !targetViewer.scene.globe) return;
    targetViewer.scene.globe.enableLighting = false;
    targetViewer.scene.globe.depthTestAgainstTerrain = !!key;
    targetViewer.shadows = false;
    if (targetViewer.clock) targetViewer.clock.shouldAnimate = false;
    mosaicBaseLayerInstance = targetViewer.imageryLayers && targetViewer.imageryLayers.length ? targetViewer.imageryLayers.get(0) : null;
    mosaicResetMarkerLayer(targetViewer);
    mosaicResetVisualRegistry(targetViewer);
    if (heightMove <= 0) targetViewer.camera.setView({
        // Show the complete globe with Europe as its visual centre.
        destination: Cesium.Cartesian3.fromDegrees((window.__MOSAIC_INITIAL_GLOBE_CONFIG && typeof window.__MOSAIC_INITIAL_GLOBE_CONFIG.lon === "number") ? window.__MOSAIC_INITIAL_GLOBE_CONFIG.lon : 10.0, (window.__MOSAIC_INITIAL_GLOBE_CONFIG && typeof window.__MOSAIC_INITIAL_GLOBE_CONFIG.lat === "number") ? window.__MOSAIC_INITIAL_GLOBE_CONFIG.lat : 50.0, (window.__MOSAIC_INITIAL_GLOBE_CONFIG && typeof window.__MOSAIC_INITIAL_GLOBE_CONFIG.height === "number" && window.__MOSAIC_INITIAL_GLOBE_CONFIG.height > 0) ? window.__MOSAIC_INITIAL_GLOBE_CONFIG.height : 12500000),
        orientation: { heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0 }
    });
}

var mosaicMarkerSource = null;
var mosaicMarkerViewer = null;

function mosaicMarkerImage(assetType) {
    return MosaicCesiumRuntime.markerImage(assetType);
}

function mosaicMarkerBillboard(assetType) {
    return {
        image: mosaicMarkerImage(assetType),
        width: 32,
        height: 36,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        heightReference: Cesium.HeightReference.CLAMP_TO_TERRAIN,
        eyeOffset: Cesium.Cartesian3.ZERO,
        pixelOffset: new Cesium.Cartesian2(0, -2),
        // Terrain depth varies while Cesium swaps LODs. Draw pins above that
        // depth and explicitly hide the far hemisphere in
        // mosaicUpdateMarkerVisibility instead.
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
        distanceDisplayCondition: new Cesium.DistanceDisplayCondition(900, Number.POSITIVE_INFINITY),
        scaleByDistance: new Cesium.NearFarScalar(500, 1, 12000000, .7),
        translucencyByDistance: new Cesium.NearFarScalar(500, 1, 16000000, .82)
    };
}

function mosaicClusterImage(count) {
    return MosaicCesiumRuntime.clusterImage(count);
}

function mosaicResetMarkerLayer(targetViewer) {
    mosaicMarkerViewer = targetViewer;
    mosaicMarkerSource = new Cesium.CustomDataSource("mosaicearth-markers");
    var clustering = mosaicMarkerSource.clustering;
    clustering.enabled = true;
    clustering.pixelRange = 44;
    clustering.minimumClusterSize = 3;
    clustering.clusterEvent.addEventListener(function (entities, cluster) {
        cluster.label.show = false;
        cluster.billboard.show = true;
        cluster.billboard.image = mosaicClusterImage(entities.length);
        cluster.billboard.width = 44;
        cluster.billboard.height = 48;
        cluster.billboard.verticalOrigin = Cesium.VerticalOrigin.BOTTOM;
        cluster.billboard.heightReference = Cesium.HeightReference.CLAMP_TO_TERRAIN;
        cluster.billboard.eyeOffset = Cesium.Cartesian3.ZERO;
        cluster.billboard.disableDepthTestDistance = Number.POSITIVE_INFINITY;
        cluster.billboard.distanceDisplayCondition = new Cesium.DistanceDisplayCondition(900, Number.POSITIVE_INFINITY);
    });
    targetViewer.dataSources.add(mosaicMarkerSource);
    targetViewer.camera.percentageChanged = .005;
    targetViewer.camera.changed.addEventListener(mosaicUpdateMarkerVisibility);
    targetViewer.camera.moveEnd.addEventListener(mosaicUpdateMarkerVisibility);
}

function mosaicUpdateMarkerVisibility() {
    if (!mosaicMarkerViewer || !mosaicMarkerSource || mosaicMarkerViewer.isDestroyed()) return;
    var cameraPosition = mosaicMarkerViewer.camera.positionWC;
    var occluder = new Cesium.EllipsoidalOccluder(Cesium.Ellipsoid.WGS84, cameraPosition);
    var time = mosaicMarkerViewer.clock ? mosaicMarkerViewer.clock.currentTime : Cesium.JulianDate.now();
    mosaicMarkerSource.entities.values.forEach(function (entity) {
        var position = entity.position && entity.position.getValue(time);
        var registryEntry = entity.mosaicAssetKey ? mosaicVisualRegistry[entity.mosaicAssetKey] : null;
        var remainsDiscovered = !registryEntry || registryEntry.discovered !== false;
        entity.show = !!position && remainsDiscovered && occluder.isPointVisible(position);
    });
}

function mosaicAddMarker(targetViewer, position, name, markerKey, assetType, deferVisibilityUpdate) {
    if (!targetViewer || !position) return null;
    if (mosaicMarkerViewer !== targetViewer || !mosaicMarkerSource) mosaicResetMarkerLayer(targetViewer);
    var id = "mosaic-marker-" + String(markerKey).replace(/[^a-zA-Z0-9_-]/g, "-");
    var existing = mosaicMarkerSource.entities.getById(id);
    if (existing) {
        existing.mosaicAssetKey = String(markerKey);
        return existing;
    }
    var entity = mosaicMarkerSource.entities.add({
        id: id,
        position: position,
        billboard: mosaicMarkerBillboard(assetType),
        name: String(name || "MosaicAsset").replace("<p>", "").replace("</p>", "")
    });
    entity.mosaicAssetKey = String(markerKey);
    if (!deferVisibilityUpdate) mosaicUpdateMarkerVisibility();
    return entity;
}

var mosaicVisualViewer = null;
var mosaicVisualRegistry = {};

function mosaicResetVisualRegistry(targetViewer) {
    mosaicVisualViewer = targetViewer;
    mosaicVisualRegistry = {};
    window.tilesets = {};
    targetViewer.camera.moveEnd.addEventListener(mosaicLoadNearbyVisuals);
}

function mosaicRegisterVisual(entry) {
    if (!entry || !entry.key || !isFinite(entry.latitude) || !isFinite(entry.longitude)) return;
    var existing = mosaicVisualRegistry[entry.key];
    if (existing && existing.viewer === mosaicVisualViewer && existing.url === entry.url && existing.kind === entry.kind) {
        Object.keys(entry).forEach(function (name) {
            if (["visual", "loaded", "loading", "lastVisibleAt", "loadGeneration", "focusOnReady", "focusPinnedUntil"].indexOf(name) < 0) existing[name] = entry[name];
        });
        existing.disposed = false;
        existing.discovered = true;
        existing.leftDiscoveryAt = 0;
        return existing;
    }
    if (existing) {
        existing.disposed = true;
        mosaicReleaseVisual(existing);
    }
    entry.viewer = mosaicVisualViewer;
    entry.disposed = false;
    entry.discovered = true;
    entry.loaded = false;
    entry.loading = false;
    entry.userVisible = entry.userVisible !== false;
    entry.lastVisibleAt = 0;
    entry.loadGeneration = 0;
    mosaicVisualRegistry[entry.key] = entry;
    if (window.MosaicAnnotations) MosaicAnnotations.register(entry.viewer,entry.key,{isVisible:function(){return entry.discovered!==false && entry.userVisible!==false;},getVisual:function(){return entry.visual;},allowSurfacePicking:function(){var a=mosaicAllAssetByKey[entry.key];return !!a && a.Subtype!=='Gaussian splat';},getMatrix:function(){return entry.visual && entry.visual.modelMatrix;}});
    return entry;
}

function mosaicVisualIsVisible(entry, rectangle) {
    if (!rectangle) return false;
    var north = Cesium.Math.toDegrees(rectangle.north), south = Cesium.Math.toDegrees(rectangle.south);
    var east = Cesium.Math.toDegrees(rectangle.east), west = Cesium.Math.toDegrees(rectangle.west);
    var longitudeVisible = west <= east ? entry.longitude >= west && entry.longitude <= east : entry.longitude >= west || entry.longitude <= east;
    return entry.latitude >= south && entry.latitude <= north && longitudeVisible;
}

function mosaicLoadNearbyVisuals() {
    if (!mosaicVisualViewer || mosaicVisualViewer.isDestroyed()) return;
    var height = Cesium.Cartographic.fromCartesian(mosaicVisualViewer.camera.positionWC).height;
    if (!isFinite(height)) return;
    var rectangle = mosaicVisualViewer.camera.computeViewRectangle();
    var centreLat = (northMove + southMove) / 2, centreLon = (eastMove + westMove) / 2;
    var now = Date.now();
    var entries = Object.keys(mosaicVisualRegistry).map(function (key) { return mosaicVisualRegistry[key]; });
    entries.forEach(function (entry) {
        var anchor = Cesium.Cartesian3.fromDegrees(entry.longitude, entry.latitude, Number(entry.altitude || entry.height || 0));
        var cameraDistance = Cesium.Cartesian3.distance(mosaicVisualViewer.camera.positionWC, anchor);
        var radius = entry.visual && entry.visual.boundingSphere ? Number(entry.visual.boundingSphere.radius || 0) : 0;
        var residencyDistance = Math.max(100000, radius * 50);
        var inGeographicView = mosaicVisualIsVisible(entry, rectangle);
        var nearCamera = cameraDistance <= residencyDistance;
        var pinned = Number(entry.focusPinnedUntil || 0) > now;
        // Discovery is the authoritative eligibility set. In particular, a
        // temporal-filter change must hide an asset immediately without
        // destroying the Cesium object that can be reused when the range is
        // widened again.
        var shouldRemainVisible = entry.discovered !== false && entry.userVisible !== false && (inGeographicView || nearCamera || pinned);
        if (shouldRemainVisible) entry.lastVisibleAt = now;
        // Cesium owns frustum, horizon and per-tile LOD culling. Catalogue
        // metadata must never hide a loaded renderer during an oblique orbit.
        if (entry.visual) entry.visual.show = shouldRemainVisible;
        if (!shouldRemainVisible && entry.loaded && entry.lastVisibleAt && now - entry.lastVisibleAt > 90000) mosaicReleaseVisual(entry);
        if (entry.discovered === false && !entry.loaded && !entry.loading && entry.leftDiscoveryAt && now - entry.leftDiscoveryAt > 90000) delete mosaicVisualRegistry[entry.key];
    });
    entries.filter(function (entry) {
        if (entry.discovered === false || height > 80000 || entry.userVisible === false || entry.loaded || entry.loading || entry.failed) return false;
        var anchor = Cesium.Cartesian3.fromDegrees(entry.longitude, entry.latitude, Number(entry.altitude || entry.height || 0));
        return mosaicVisualIsVisible(entry, rectangle) || Cesium.Cartesian3.distance(mosaicVisualViewer.camera.positionWC, anchor) <= 75000;
    })
        .sort(function (a, b) {
            var da = Math.pow(a.latitude - centreLat, 2) + Math.pow(a.longitude - centreLon, 2);
            var db = Math.pow(b.latitude - centreLat, 2) + Math.pow(b.longitude - centreLon, 2);
            return da - db;
        }).slice(0, 6).forEach(function (entry) { mosaicLoadVisual(entry, false); });
}

function mosaicReleaseVisual(entry) {
    if (!entry) return;
    entry.loadGeneration = Number(entry.loadGeneration || 0) + 1;
    if (!entry.visual || !entry.viewer || entry.viewer.isDestroyed()) {
        entry.visual = null; entry.loaded = false; entry.loading = false;
        return;
    }
    try {
        if (entry.kind === "external-manual-raster") entry.viewer.entities.remove(entry.visual);
        else if (entry.kind.indexOf("raster") >= 0) MosaicCesiumRuntime.removeImageryLayer(entry.viewer, entry.visual);
        else if (entry.kind === "external-glb" || entry.kind === "external-gltf") MosaicCesiumRuntime.removePrimitive(entry.viewer, entry.visual, "model");
        else if (entry.kind === "external-vector") MosaicCesiumRuntime.removeVectorAsset(entry.viewer, entry.vectorHandle || entry.visual);
        else MosaicCesiumRuntime.removePrimitive(entry.viewer, entry.visual, "tileset");
    } catch (ignore) { }
    entry.visual = null; entry.manualRasterHandle = null; entry.loaded = false; entry.loading = false;
}

function mosaicFocusVisual(entry) {
    if (!entry || !entry.viewer || entry.viewer.isDestroyed()) return;
    entry.focusPinnedUntil = Date.now() + 120000;
    mosaicPinFocusedAsset(entry.key, 120000);
    mosaicScheduleCardRanking(true);
    if (entry.visual) entry.visual.show = entry.userVisible !== false;
    if (entry.vectorHandle) {
        MosaicCesiumRuntime.focusVectorAsset(entry.viewer, entry.vectorHandle, .8);
        return;
    }
    if (entry.manualRasterHandle && entry.manualRasterHandle.focusRectangle) {
        MosaicCesiumRuntime.flyToRectangleTopDown(entry.viewer, entry.manualRasterHandle.focusRectangle, .8);
        return;
    }
    if (entry.visual && entry.visual.boundingSphere) {
        MosaicCesiumRuntime.flyToTopDown(entry.viewer, entry.visual, Math.max(entry.visual.boundingSphere.radius * 2.6, 180), .8);
        return;
    }
    entry.viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(entry.longitude, entry.latitude, 1800), orientation: { heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0 } });
}

function mosaicLoadVisual(entry, focus) {
    if (!entry || entry.viewer !== mosaicVisualViewer || !entry.viewer || entry.viewer.isDestroyed()) return;
    if (focus) entry.focusOnReady = true;
    if (entry.loaded) { if (focus) mosaicFocusVisual(entry); return; }
    if (entry.loading) return;
    entry.loading = true;
    var loadGeneration = Number(entry.loadGeneration || 0) + 1;
    entry.loadGeneration = loadGeneration;

    if (entry.kind === "processed-raster" || entry.kind === "external-raster") {
        var rasterPromise = entry.kind === "processed-raster"
            ? MosaicCesiumRuntime.loadTmsLayer(entry.viewer, entry.url, { maximumLevel: 20 })
            : Promise.resolve(entry.viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({ url: entry.url, maximumLevel: 22 })));
        rasterPromise.then(function (layer) {
            if (entry.disposed || entry.loadGeneration !== loadGeneration || !entry.viewer || entry.viewer.isDestroyed()) {
                if (entry.viewer && !entry.viewer.isDestroyed()) MosaicCesiumRuntime.removeImageryLayer(entry.viewer, layer);
                return;
            }
            entry.visual = layer;
            entry.visual.show = entry.userVisible !== false;
            entry.loading = false; entry.loaded = true; entry.lastVisibleAt = Date.now();
            if (entry.focusOnReady) mosaicFocusVisual(entry);
        }).catch(function (error) {
            if (entry.loadGeneration !== loadGeneration) return;
            entry.loading = false; entry.failed = true;
            console.error("MosaicEarth imagery asset failed", entry.key, error);
        });
        return;
    }

    if (entry.kind === "external-manual-raster") {
        MosaicCesiumRuntime.loadManualRaster(entry.viewer, entry.url, {
            latitude: entry.latitude,
            longitude: entry.longitude,
            heading: entry.heading,
            scale: entry.scale
        }).then(function (handle) {
            if (entry.disposed || entry.loadGeneration !== loadGeneration || !entry.viewer || entry.viewer.isDestroyed()) {
                if (entry.viewer && !entry.viewer.isDestroyed() && handle && handle.visual) entry.viewer.entities.remove(handle.visual);
                return;
            }
            entry.manualRasterHandle = handle;
            entry.visual = handle.visual;
            entry.visual.show = entry.userVisible !== false;
            entry.loading = false;
            entry.loaded = true;
            entry.lastVisibleAt = Date.now();
            if (entry.focusOnReady) mosaicFocusVisual(entry);
        }).catch(function (error) {
            if (entry.loadGeneration !== loadGeneration) return;
            entry.loading = false;
            entry.failed = true;
            console.error("MosaicEarth manually placed raster failed", entry.key, error);
        });
        return;
    }

    if (entry.kind === "external-glb" || entry.kind === "external-gltf") {
        var modelPosition = Cesium.Cartesian3.fromDegrees(entry.longitude, entry.latitude, Number(entry.altitude || 0));
        MosaicCesiumRuntime.addModel(entry.viewer, entry.url, { modelMatrix:Cesium.Matrix4.IDENTITY, minimumPixelSize:0 }).then(function (model) {
            if (entry.disposed || entry.loadGeneration !== loadGeneration || !entry.viewer || entry.viewer.isDestroyed()) {
                if (entry.viewer && !entry.viewer.isDestroyed()) MosaicCesiumRuntime.removePrimitive(entry.viewer, model, "model");
                return;
            }
            entry.visual = model;
            var localRotation = Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(Number(entry.heading || 0))));
            Cesium.Matrix4.multiplyByUniformScale(localRotation, Number(entry.scale || 1), localRotation);
            Cesium.Matrix4.multiply(localRotation, Cesium.Matrix4.fromTranslation(Cesium.Cartesian3.negate(Cesium.Cartesian3.clone(model.boundingSphere.center), new Cesium.Cartesian3())), localRotation);
            model.modelMatrix = Cesium.Matrix4.multiply(Cesium.Transforms.eastNorthUpToFixedFrame(modelPosition), localRotation, new Cesium.Matrix4());
            entry.loading = false;
            entry.loaded = true;
            entry.lastVisibleAt = Date.now();
            if (entry.focusOnReady) mosaicFocusVisual(entry);
        }).catch(function (error) {
            if (entry.loadGeneration !== loadGeneration) return;
            entry.loading = false;
            entry.failed = true;
            console.error("MosaicEarth glTF asset failed", entry.key, error);
        });
        return;
    }

    if (entry.kind === "external-vector") {
        MosaicCesiumRuntime.loadVectorAsset(entry.viewer, entry.url, { assetKey:entry.key, sourceUrl:entry.url }).then(function (handle) {
            if (entry.disposed || !entry.viewer || entry.viewer.isDestroyed()) return;
            entry.vectorHandle = handle;
            entry.visual = handle.visual;
            entry.loading = false;
            entry.loaded = true;
            entry.lastVisibleAt = Date.now();
            if (entry.focusOnReady) MosaicCesiumRuntime.focusVectorAsset(entry.viewer, handle, .8);
        }).catch(function (error) {
            entry.loading = false;
            entry.failed = true;
            console.error("MosaicEarth vector asset failed", entry.key, error);
            MosaicCesiumRuntime.showViewerError(entry.viewer, "A vector MosaicAsset could not be displayed", "This layer was disabled while the rest of the map remains available.", false);
            window.setTimeout(function(){ MosaicCesiumRuntime.hideViewerError(entry.viewer); }, 5000);
        });
        return;
    }

    MosaicCesiumRuntime.loadTileset(entry.viewer, entry.url, {
        maximumScreenSpaceError: entry.kind === "external-pointcloud" ? 8 : 16,
        enableCollision: false
    }).then(function (tile) {
        if (entry.disposed || entry.loadGeneration !== loadGeneration || !entry.viewer || entry.viewer.isDestroyed()) {
            if (entry.viewer && !entry.viewer.isDestroyed()) MosaicCesiumRuntime.removePrimitive(entry.viewer, tile, "tileset");
            return;
        }
        entry.visual = tile;
        window.tilesets["tileset" + entry.id] = tile;
        if (entry.kind === "external-pointcloud" || entry.kind === "external-tileset") {
            var localAnchor = Cesium.Cartesian3.clone(tile.boundingSphere.center);
            var originFrame = Cesium.Transforms.eastNorthUpToFixedFrame(tile.boundingSphere.center);
            var target = Cesium.Cartesian3.fromDegrees(entry.longitude, entry.latitude, Number(entry.altitude || 0));
            var targetFrame = Cesium.Transforms.eastNorthUpToFixedFrame(target);
            var localRotation = Cesium.Matrix4.fromRotationTranslation(Cesium.Matrix3.fromRotationZ(Cesium.Math.toRadians(Number(entry.heading || 0))));
            Cesium.Matrix4.multiplyByUniformScale(localRotation, Number(entry.scale || 1), localRotation);
            if (entry.georeferenceMode !== "embedded") {
                Cesium.Matrix4.multiply(localRotation, Cesium.Matrix4.fromTranslation(Cesium.Cartesian3.negate(localAnchor, new Cesium.Cartesian3())), localRotation);
            }
            var transform = Cesium.Matrix4.multiply(targetFrame, localRotation, new Cesium.Matrix4());
            tile.modelMatrix = entry.georeferenceMode !== "embedded" ? transform : Cesium.Matrix4.multiply(transform, Cesium.Matrix4.inverse(originFrame, new Cesium.Matrix4()), new Cesium.Matrix4());
            if (entry.assetType === 1 && tile.pointCloudShading) {
                tile.pointCloudShading.attenuation = true;
                tile.pointCloudShading.maximumAttenuation = 10.0; tile.pointCloudShading.geometricErrorScale = 2.2; tile.pointCloudShading.baseResolution = 0.22;
            }
        } else {
            MosaicCesiumRuntime.applyProcessedTilesetHeight(tile, entry.height);
            if (entry.assetType === 1) tile.pointCloudShading.attenuation = true;
        }
        entry.loading = false;
        entry.loaded = true;
        if (entry.focusOnReady) mosaicFocusVisual(entry);
    }).catch(function (error) {
        if (entry.loadGeneration !== loadGeneration) return;
        entry.loading = false;
        entry.failed = true;
        console.error("MosaicEarth 3D Tiles asset failed", entry.key, error);
    });
}

function mosaicSortValue(value) {
    if (!value || value === "—" || value === "-") return 0;
    var dotNet = String(value).match(/Date\((\d+)/);
    if (dotNet) return Number(dotNet[1]);
    var display = String(value).match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
    if (display) return new Date(Number(display[3]), Number(display[2]) - 1, Number(display[1])).getTime();
    var parsed = new Date(value).getTime();
    return isNaN(parsed) ? 0 : parsed;
}

function sortVisibleAssetCards() {
    var container = document.getElementById("items");
    if (!container) return;
    Array.prototype.slice.call(container.querySelectorAll(".mosaic-asset-card")).sort(function (a, b) {
        return Number(b.getAttribute("data-sort-date") || 0) - Number(a.getAttribute("data-sort-date") || 0);
    }).forEach(function (card) { container.appendChild(card); });
}

function mosaicGlobalAssetCard(obj, typeId) {
    var assetKey = "processed-" + typeId + "-" + (obj.PublishId || obj.ID || obj.ProjectId || obj.NAME);
    if (document.querySelector('[data-asset-key="' + assetKey + '"]')) return "";
    var typeName = typeId == 1 ? "Point cloud" : (typeId == 2 ? "Map or layer" : "3D model");
    var download = typeId == 1 ? "LAS · ZIP" : (typeId == 2 ? "GEOTIFF · TIF" : "OBJ · ZIP");
    var price = Number(obj.Credits || 0) === 0 ? "FREE" : obj.Credits + " credits";
    var preview = obj.UrlOrthoPhoto || "";
    var dateLabel = obj.CaptureDate ? "Captured" : "Published";
    var dateDisplay = obj.CaptureDateDisplay && obj.CaptureDateDisplay !== "-" ? obj.CaptureDateDisplay : mosaicDateDisplay(obj.PublishDate);
    var sortDate = mosaicSortValue(obj.CaptureDate || obj.PublishDate || dateDisplay);
    return `<article class="mosaic-asset-card mosaic-card-v2 global-asset-card" data-asset-key="${assetKey}" data-sort-date="${sortDate}" onclick="${typeId == 2 ? `zoomCesiumOrthophoto(${String(obj.LATITUD || '0').replace(',', '.')},${String(obj.LONGITUD || '0').replace(',', '.')})` : `zoomCesium(${obj.ID})`}">
        <div class="mosaic-card-preview" style="background-image:url('${preview}')">
            <span class="mosaic-card-type-icon" aria-label="${typeName}" title="${typeName}">${obj.IconType || ''}</span>
            <span class="mosaic-card-origin is-processed">Processed</span>
            <a class="mosaic-card-details" href="${obj.UrlMarketplace}" onclick="event.stopPropagation()">View details</a>
        </div>
        <div class="mosaic-card-body">
            <h3 class="mosaic-card-title">${mosaicEscapeHtml(obj.NAME)}</h3><p class="mosaic-card-creator">${mosaicEscapeHtml(obj.CreatorDisplay || 'MosaicEarth creator')}</p>
            <p class="mosaic-card-date"><span>${dateLabel}:</span><strong>${dateDisplay}</strong></p>
            <div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>${download}</strong></span><strong class="mosaic-card-price">${price}</strong></div>
        </div>
    </article>`;
}

function mosaicDateDisplay(value) {
    if (!value) return "—";
    var match = String(value).match(/Date\((\d+)/);
    var date = match ? new Date(Number(match[1])) : new Date(value);
    if (isNaN(date.getTime())) return "—";
    return String(date.getDate()).padStart(2, "0") + "/" + String(date.getMonth() + 1).padStart(2, "0") + "/" + date.getFullYear();
}

var externalPublicTilesets = {};
var externalVisualsViewer = null;
var externalVisualsType = 0;
var externalVisualIds = {};
var mosaicCardRequestId = 0;
function appendPublicExternalAssets(typeId, requestId) {
    $.ajax({
        type: "POST", dataType: "json", contentType: "application/json; charset=utf-8",
        url: MYLIBRARY.getUrl_page() + "/loadPublicExternalAssets",
        data: JSON.stringify({ type_id: String(typeId) }),
        success: function (response) {
            if (requestId && requestId !== mosaicCardRequestId) return;
            var assets = $.parseJSON(response.d) || [];
            if (assets.length) $("#items .map-empty-state").remove();
            var rectangle = viewer && viewer.camera ? viewer.camera.computeViewRectangle() : null;
            if (externalVisualsViewer !== viewer || externalVisualsType !== typeId) {
                externalVisualIds = {};
                externalPublicTilesets = {};
            }
            externalVisualsViewer = viewer; externalVisualsType = typeId;
            assets.forEach(function (asset) {
                var span = .012, lat = Number(asset.Latitude), lon = Number(asset.Longitude);
                var visualKey = String(asset.Id);
                if (!externalVisualIds[visualKey]) {
                    externalVisualIds[visualKey] = true;
                    mosaicAddMarker(viewer, Cesium.Cartesian3.fromDegrees(lon, lat, 120), asset.Name, "external-" + typeId + "-" + asset.Id, typeId);
                    var kind = asset.DisplayKind === "raster-tiles" ? "external-raster" : asset.DisplayKind === "raster-manual-image" ? "external-manual-raster" : asset.DisplayKind === "vector-geojson" ? "external-vector" : (asset.DisplayKind === "model-glb" || asset.DisplayKind === "model-gltf") ? "external-gltf" : asset.DisplayKind === "pointcloud-3dtiles" ? "external-pointcloud" : "external-tileset";
                    if (asset.ViewerUrl) mosaicRegisterVisual({key:"external-"+typeId+"-"+asset.Id,kind:kind,assetType:typeId,id:"external-"+asset.Id,latitude:lat,longitude:lon,altitude:asset.Altitude,heading:asset.Heading,scale:asset.Scale,georeferenceMode:asset.GeoreferenceMode,url:asset.ViewerUrl,name:asset.Name});
                }
                if (rectangle) {
                    var north = Cesium.Math.toDegrees(rectangle.north), south = Cesium.Math.toDegrees(rectangle.south), east = Cesium.Math.toDegrees(rectangle.east), west = Cesium.Math.toDegrees(rectangle.west);
                    var longitudeVisible = west <= east ? lon >= west && lon <= east : lon >= west || lon <= east;
                    if (lat < south || lat > north || !longitudeVisible) return;
                }
                var preview = asset.PreviewUrl || "/map-preview.svg";
                var price = Number(asset.PriceCredits || 0) === 0 ? "FREE" : asset.PriceCredits + " credits";
                var typeName = typeId == 1 ? "Point cloud" : (typeId == 2 ? "Map or layer" : "3D model");
                var icon = asset.IconType || "";
                if (!document.querySelector('[data-asset-key="external-' + asset.Id + '"]')) $("#items").append(`<article class="mosaic-asset-card mosaic-card-v2 global-asset-card" data-asset-key="external-${asset.Id}" data-sort-date="${mosaicSortValue(asset.DateDisplay)}" onclick="zoomExternalAsset(${asset.Id},${lat},${lon})"><div class="mosaic-card-preview" style="background-image:url('${preview}')"><span class="mosaic-card-type-icon" aria-label="${typeName}" title="${typeName}">${icon}</span><span class="mosaic-card-origin is-imported">Imported</span><a class="mosaic-card-details" href="${asset.DetailUrl}" onclick="event.stopPropagation()">View details</a></div><div class="mosaic-card-body"><h3 class="mosaic-card-title">${mosaicEscapeHtml(asset.Name)}</h3><p class="mosaic-card-creator">${mosaicEscapeHtml(asset.CreatorDisplay || 'MosaicEarth creator')}</p><p class="mosaic-card-date"><span>${asset.DateLabel || 'Created'}:</span><strong>${asset.DateDisplay || '—'}</strong></p><div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>${mosaicEscapeHtml(asset.DownloadFormat || asset.Format)}</strong></span><strong class="mosaic-card-price">${price}</strong></div></div></article>`);
            });
            sortVisibleAssetCards();
            mosaicLoadNearbyVisuals();
        }
    });
}

function zoomExternalAsset(assetId, latitude, longitude) {
    var entry = mosaicVisualRegistry["external-" + externalVisualsType + "-" + assetId];
    if (entry && entry.loaded) { mosaicFocusVisual(entry); return; }
    // Always acknowledge the card click immediately. A heavy model may still be
    // streaming (or its source may be temporarily unavailable), but the camera
    // must still reach the exact catalogue location instead of appearing inert.
    viewer.camera.flyTo({ destination:Cesium.Cartesian3.fromDegrees(longitude, latitude, 1800), orientation:{heading:0, pitch:MosaicCesiumRuntime.topDownOffset(0).pitch, roll:0}, duration:.8 });
    if (entry) { entry.focusOnReady = true; entry.focusPinnedUntil = Date.now() + 120000; mosaicLoadVisual(entry, true); }
}

var mosaicAllAssets = [];
var mosaicAllAssetByKey = {};
var mosaicAssetQueryTimer = null;
var mosaicAssetQueryGeneration = 0;
var mosaicAssetQueryRequest = null;
var mosaicAssetQuerySeen = {};
var mosaicAssetQueryStage = null;
var mosaicActiveFamilies = "";
var mosaicCardPresence = {};
var mosaicCardRankingTimer = null;
var mosaicCardLastRenderAt = 0;
var mosaicLastCardSignature = "";
var mosaicFocusedAssetKey = "";
var mosaicFocusedAssetUntil = 0;
var mosaicRankedCandidateCount = 0;
var mosaicQaHeadingDegrees = 0;
var mosaicTemporalFilter = null;

var mosaicCardScratchDirection = new Cesium.Cartesian3();
var mosaicCardScratchWindow = new Cesium.Cartesian2();

function mosaicPinFocusedAsset(keyValue, durationMs) {
    mosaicFocusedAssetKey = String(keyValue || "");
    mosaicFocusedAssetUntil = Date.now() + Number(durationMs || 120000);
    var state = mosaicCardPresence[mosaicFocusedAssetKey] || {};
    state.visible = true;
    state.lastRelevantAt = Date.now();
    mosaicCardPresence[mosaicFocusedAssetKey] = state;
}

function mosaicFocusedAssetIsPinned(keyValue, now) {
    var entry = mosaicVisualRegistry[keyValue];
    return (mosaicFocusedAssetKey === keyValue && mosaicFocusedAssetUntil > now) || !!(entry && Number(entry.focusPinnedUntil || 0) > now);
}

function mosaicAssetBoundingSphere(asset) {
    var entry = mosaicVisualRegistry[asset.Key];
    if (entry && entry.visual && entry.visual.boundingSphere) return entry.visual.boundingSphere;
    if (asset._mosaicFallbackSphere) return asset._mosaicFallbackSphere;
    var altitude = Number(asset.Altitude || asset.Height || 0);
    var centre = Cesium.Cartesian3.fromDegrees(Number(asset.Longitude), Number(asset.Latitude), isFinite(altitude) ? altitude : 0);
    var radius = Math.max(80, Number(asset.EstimatedRadius || 0), Math.abs(Number(asset.Scale || 1)) * 80);
    asset._mosaicFallbackSphere = new Cesium.BoundingSphere(centre, radius);
    return asset._mosaicFallbackSphere;
}

function mosaicCardFrameContext() {
    if (!viewer || viewer.isDestroyed() || !viewer.scene || !viewer.camera) return null;
    var scene = viewer.scene, camera = viewer.camera, canvas = scene.canvas;
    var width = Math.max(1, canvas.clientWidth || canvas.width || 1), height = Math.max(1, canvas.clientHeight || canvas.height || 1);
    var cameraHeight = camera.positionCartographic ? Number(camera.positionCartographic.height || 0) : 0;
    var surfaceNormal = Cesium.Ellipsoid.WGS84.geodeticSurfaceNormal(camera.positionWC, new Cesium.Cartesian3());
    var downness = Math.max(0, -Cesium.Cartesian3.dot(camera.directionWC, surfaceNormal));
    return {
        scene: scene,
        camera: camera,
        width: width,
        height: height,
        cameraHeight: cameraHeight,
        distanceBudget: Math.min(26000000, Math.max(30000, cameraHeight * (3 + (1 - downness) * 8))),
        cullingVolume: camera.frustum.computeCullingVolume(camera.positionWC, camera.directionWC, camera.upWC),
        occluder: new Cesium.EllipsoidalOccluder(Cesium.Ellipsoid.WGS84, camera.positionWC)
    };
}

function mosaicScoreAssetForCards(asset, context, now) {
    var sphere = mosaicAssetBoundingSphere(asset);
    var focused = mosaicFocusedAssetIsPinned(asset.Key, now);
    var distance = Cesium.Cartesian3.distance(context.camera.positionWC, sphere.center);
    var distanceBudget = Math.max(context.distanceBudget, sphere.radius * 160);
    if (!focused && distance - sphere.radius > distanceBudget) return null;

    Cesium.Cartesian3.subtract(sphere.center, context.camera.positionWC, mosaicCardScratchDirection);
    if (Cesium.Cartesian3.magnitudeSquared(mosaicCardScratchDirection) <= 0) return null;
    Cesium.Cartesian3.normalize(mosaicCardScratchDirection, mosaicCardScratchDirection);
    var directionDot = Cesium.Cartesian3.dot(context.camera.directionWC, mosaicCardScratchDirection);
    var angularRadius = distance > sphere.radius ? Math.asin(Math.min(1, sphere.radius / distance)) : Math.PI / 2;
    if (!focused && directionDot < -Math.sin(angularRadius) - .03) return null;

    var horizonVisible = context.occluder.isBoundingSphereVisible
        ? context.occluder.isBoundingSphereVisible(sphere)
        : context.occluder.isPointVisible(sphere.center);
    if (!focused && !horizonVisible) return null;

    var frustumVisibility = context.cullingVolume.computeVisibility(sphere);
    var windowPosition = Cesium.SceneTransforms.worldToWindowCoordinates(context.scene, sphere.center, mosaicCardScratchWindow);
    var projectedRadius = 0;
    if (distance > 0 && context.camera.frustum && isFinite(context.camera.frustum.fovy)) {
        projectedRadius = sphere.radius / distance * context.height / (2 * Math.tan(context.camera.frustum.fovy / 2));
    }
    projectedRadius = Math.min(Math.max(projectedRadius, 0), Math.max(context.width, context.height));
    var viewportMargin = Math.max(36, projectedRadius, Math.min(context.width, context.height) * .08);
    var projectedNearViewport = !!windowPosition && isFinite(windowPosition.x) && isFinite(windowPosition.y) &&
        windowPosition.x >= -viewportMargin && windowPosition.x <= context.width + viewportMargin &&
        windowPosition.y >= -viewportMargin && windowPosition.y <= context.height + viewportMargin;
    if (!focused && frustumVisibility === Cesium.Intersect.OUTSIDE && !projectedNearViewport) return null;

    var screenProximity = 0;
    if (windowPosition && isFinite(windowPosition.x) && isFinite(windowPosition.y)) {
        var dx = (windowPosition.x - context.width / 2) / (context.width / 2);
        var dy = (windowPosition.y - context.height / 2) / (context.height / 2);
        screenProximity = 1 - Math.min(1, Math.sqrt(dx * dx + dy * dy) / Math.SQRT2);
    }
    var frustumScore = frustumVisibility === Cesium.Intersect.INSIDE ? 420 : frustumVisibility === Cesium.Intersect.INTERSECTING ? 340 : 120;
    var distanceScore = 190 * Math.max(0, 1 - Math.max(0, distance - sphere.radius) / distanceBudget);
    var screenScore = 260 * Math.max(0, screenProximity);
    var sizeScore = Math.min(120, Math.log(1 + projectedRadius) / Math.LN2 * 18);
    var directionScore = 90 * Math.max(0, Math.min(1, (directionDot + .08) / 1.08));
    return {
        asset: asset,
        focused: focused,
        score: frustumScore + distanceScore + screenScore + sizeScore + directionScore + (focused ? 5000 : 0),
        distance: distance,
        frustumVisibility: frustumVisibility,
        screenProximity: screenProximity
    };
}

function mosaicRankAssetsForCamera() {
    if (mosaicAssetSearch) {
        mosaicRankedCandidateCount = mosaicAllAssets.length;
        return mosaicAllAssets.slice(0, 120);
    }
    var context = mosaicCardFrameContext();
    if (!context) return [];
    var now = Date.now(), ranked = [];
    mosaicAllAssets.forEach(function (asset) {
        var result = mosaicScoreAssetForCards(asset, context, now);
        var state = mosaicCardPresence[asset.Key] || { visible:false, smoothedScore:0, lastRelevantAt:0, rank:9999 };
        var rawScore = result ? result.score : 0;
        state.smoothedScore = state.smoothedScore ? state.smoothedScore * .58 + rawScore * .42 : rawScore;
        if (result) state.lastRelevantAt = now;
        var entryThreshold = 300, exitThreshold = 235;
        var keepDuringMargin = state.visible && now - Number(state.lastRelevantAt || 0) < 750;
        var include = !!(result && (result.focused || rawScore >= entryThreshold || (state.visible && rawScore >= exitThreshold))) || keepDuringMargin;
        if (include) {
            state.visible = true;
            ranked.push({ asset:asset, score:(result && result.focused ? result.score : state.smoothedScore) + (state.rank < 9999 ? 18 : 0), previousRank:state.rank });
        } else state.visible = false;
        mosaicCardPresence[asset.Key] = state;
    });
    ranked.sort(function (a, b) {
        var scoreDifference = b.score - a.score;
        if (Math.abs(scoreDifference) >= 18) return scoreDifference;
        if (a.previousRank !== b.previousRank) return a.previousRank - b.previousRank;
        return mosaicSortValue(b.asset.SortDate || b.asset.DateDisplay) - mosaicSortValue(a.asset.SortDate || a.asset.DateDisplay);
    });
    mosaicRankedCandidateCount = ranked.length;
    ranked.slice(0, 120).forEach(function (item, index) { mosaicCardPresence[item.asset.Key].rank = index; });
    return ranked.slice(0, 120).map(function (item) { return item.asset; });
}

function mosaicScheduleCardRanking(immediate) {
    if (mosaicCardRankingTimer) window.clearTimeout(mosaicCardRankingTimer);
    var elapsed = Date.now() - mosaicCardLastRenderAt;
    var delay = immediate ? 0 : Math.max(0, 140 - elapsed);
    mosaicCardRankingTimer = window.setTimeout(function () {
        mosaicCardRankingTimer = null;
        mosaicRenderAllAssetCards();
    }, delay);
}

// Deterministic local-only camera presets for regression testing. These
// controls never appear on staging or production hosts.
function mosaicInstallLocalCardQaControls() {
    if (window.location.hostname !== "localhost" || window.location.search.indexOf("mosaic_card_qa=1") < 0 || document.getElementById("mosaic-card-qa")) return;
    var panel = document.createElement("div");
    panel.id = "mosaic-card-qa";
    panel.setAttribute("aria-label", "Card relevance QA controls");
    panel.style.cssText = "position:fixed;right:16px;top:150px;z-index:9999;display:flex;gap:4px;padding:6px;background:#101713;border:1px solid #8fcb00";
    [0, 30, 45, 60, 82].forEach(function (tilt) {
        var button = document.createElement("button");
        button.type = "button"; button.textContent = tilt === 82 ? "Horizon" : String(tilt) + "°";
        button.setAttribute("data-qa-tilt", String(tilt));
        button.onclick = function () { mosaicQaSetCamera(tilt, mosaicQaHeadingDegrees); };
        panel.appendChild(button);
    });
    var orbitButton = document.createElement("button");
    orbitButton.type = "button"; orbitButton.textContent = "Orbit +90°"; orbitButton.setAttribute("data-qa-orbit", "90");
    orbitButton.onclick = function () { mosaicQaHeadingDegrees = (mosaicQaHeadingDegrees + 90) % 360; mosaicQaSetCamera(45, mosaicQaHeadingDegrees); };
    panel.appendChild(orbitButton); document.body.appendChild(panel);
}

function mosaicQaSetCamera(tiltDegrees, headingDegrees) {
    if (!viewer || viewer.isDestroyed()) return;
    var asset = mosaicAllAssetByKey[mosaicFocusedAssetKey] || mosaicAllAssets[0];
    if (!asset) return;
    mosaicPinFocusedAsset(asset.Key, 120000);
    var sphere = mosaicAssetBoundingSphere(asset);
    var range = Math.max(2200, Number(sphere.radius || 0) * 8);
    viewer.camera.lookAt(sphere.center, new Cesium.HeadingPitchRange(Cesium.Math.toRadians(Number(headingDegrees || 0)), Cesium.Math.toRadians(-90 + Number(tiltDegrees || 0)), range));
    mosaicScheduleCardRanking(true);
}

function mosaicFinalizeAssetDiscoverySet() {
    var now = Date.now();
    Object.keys(mosaicVisualRegistry || {}).forEach(function (keyValue) {
        var entry = mosaicVisualRegistry[keyValue];
        if (!entry || mosaicAssetQuerySeen[keyValue]) return;
        entry.discovered = false;
        if (!entry.leftDiscoveryAt) entry.leftDiscoveryAt = now;
        // Keep clustered discovery entities stable for the lifetime of the Viewer.
        // Cesium 1.144 can retain a transient cluster visualizer item while an
        // entity is removed between frames. Visibility is updated instead, which
        // avoids the undefined EntityCluster id state without touching Cesium core.
        if (mosaicMarkerSource && mosaicMarkerSource.entities) {
            var markerId = "mosaic-marker-" + String(keyValue).replace(/[^a-zA-Z0-9_-]/g, "-");
            var marker = mosaicMarkerSource.entities.getById(markerId);
            if (marker) marker.show = false;
        }
    });
}

function mosaicEscapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
        return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character];
    });
}

function mosaicAllAssetPreview(asset) {
    if (asset.PreviewUrl) return asset.PreviewUrl;
    var span = .012, lat = Number(asset.Latitude), lon = Number(asset.Longitude);
    return "/map-preview.svg";
}

function mosaicAllAssetCard(asset) {
    var price = Number(asset.PriceCredits || 0) === 0 ? "FREE" : asset.PriceCredits + " credits";
    var originClass = asset.Source === "imported" ? "is-imported" : "is-processed";
    var originText = asset.Source === "gaussian" ? "Generated" : asset.Source === "imported" ? "Imported" : "Processed";
    return '<article class="mosaic-asset-card mosaic-card-v2 global-asset-card all-asset-card type-' + asset.FileTypeId + '" data-asset-key="' + mosaicEscapeHtml(asset.Key) + '" data-sort-date="' + mosaicSortValue(asset.SortDate || asset.DateDisplay) + '" onclick="mosaicFocusAllAsset(event,\'' + asset.Key + '\')">' +
        '<div class="mosaic-card-preview"><span class="mosaic-preview-fallback" aria-hidden="true">Preview unavailable</span><img class="mosaic-preview-image" src="' + mosaicEscapeHtml(mosaicAllAssetPreview(asset)) + '" alt="" loading="lazy" decoding="async" onerror="this.hidden=true" />' +
        (!asset.PreviewUrl ? '<span class="preview-provenance">Location preview</span>' : '') +
        '<span class="mosaic-card-type-icon" aria-label="' + mosaicEscapeHtml(asset.Family) + '" title="' + mosaicEscapeHtml(asset.Family) + '">' + (asset.IconType || '') + '</span>' +
        '<button type="button" class="mosaic-card-visibility is-visible" data-visibility-key="' + mosaicEscapeHtml(asset.Key) + '" onclick="mosaicToggleAssetVisibility(event,\'' + asset.Key + '\')" aria-pressed="true" aria-label="Hide this asset"><span aria-hidden="true">&#9673;</span><em>Visible</em></button>' +
        '<span class="mosaic-card-origin ' + originClass + '">' + originText + '</span><a class="mosaic-card-details" href="' + mosaicEscapeHtml(asset.DetailUrl) + '" onclick="event.stopPropagation()">View details</a></div>' +
        '<div class="mosaic-card-body"><p class="mosaic-card-family">' + mosaicEscapeHtml(asset.Family) + ' · ' + mosaicEscapeHtml(asset.Subtype) + '</p><h3 class="mosaic-card-title"><button type="button" class="mosaic-locate" onclick="event.stopPropagation();mosaicFocusAllAsset(null,\'' + asset.Key + '\')">' + mosaicEscapeHtml(asset.Name) + '<span class="visually-hidden"> — Locate asset</span></button></h3><p class="mosaic-card-creator">' + mosaicEscapeHtml(asset.Creator || "MosaicEarth creator") + '</p>' +
        '<p class="mosaic-card-date"><span>' + mosaicEscapeHtml(asset.DateLabel || "Published") + ':</span><strong>' + mosaicEscapeHtml(asset.DateDisplay || "—") + '</strong></p>' +
        '<button type="button" data-annotation-asset="' + mosaicEscapeHtml(asset.Key) + '" aria-pressed="false">Annotations</button><div class="mosaic-card-footer"><span class="mosaic-card-download"><span>Download</span><strong>' + mosaicEscapeHtml(asset.DownloadFormat || "Original") + '</strong></span><strong class="mosaic-card-price">' + price + '</strong></div></div></article>';
}

function mosaicFocusAllAsset(event, keyValue) {
    if (event && event.target && event.target.closest && event.target.closest("a,button")) return;
    mosaicSetMobileDrawer(false);
    var entry = mosaicVisualRegistry[keyValue], asset = mosaicAllAssetByKey[keyValue];
    mosaicPinFocusedAsset(keyValue, 120000);
    mosaicScheduleCardRanking(true);
    if (entry) {
        entry.userVisible = true;
        // Locate immediately even if the preview is pending or unavailable.
        // A loaded visual still supplies its existing, more precise framing.
        if (!entry.loaded) mosaicFocusVisual(entry);
        mosaicLoadVisual(entry, true); mosaicSyncVisibilityButton(keyValue, true); return;
    }
    if (asset) viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(Number(asset.Longitude), Number(asset.Latitude), 1800), orientation: { heading: 0, pitch: MosaicCesiumRuntime.topDownOffset(0).pitch, roll: 0 } });
}

function mosaicSyncVisibilityButton(keyValue, visible) {
    var button = document.querySelector('[data-visibility-key="' + keyValue.replace(/"/g, '\\"') + '"]');
    if (!button) return;
    button.classList.toggle("is-visible", visible); button.classList.toggle("is-hidden", !visible);
    button.setAttribute("aria-pressed", visible ? "true" : "false"); button.setAttribute("aria-label", visible ? "Hide this asset" : "Show this asset");
    var label = button.querySelector("em"); if (label) label.textContent = visible ? "Visible" : "Hidden";
}

function mosaicToggleAssetVisibility(event, keyValue) {
    if (event) { event.preventDefault(); event.stopPropagation(); }
    var entry = mosaicVisualRegistry[keyValue];
    if (!entry) return;
    entry.userVisible = entry.userVisible === false;
    if (entry.visual) entry.visual.show = entry.userVisible;
    if (entry.userVisible) mosaicLoadNearbyVisuals();
    mosaicSyncVisibilityButton(keyValue, entry.userVisible);
}

function mosaicRenderAllAssetCards() {
    if (!viewer || viewer.isDestroyed()) return;
    mosaicCardLastRenderAt = Date.now();
    var limited = mosaicRankAssetsForCamera(), html = limited.map(mosaicAllAssetCard).join("");
    var itemsElement = document.getElementById("items");
    if (itemsElement) {
        itemsElement.setAttribute("data-camera-pitch-degrees", String(Math.round(Cesium.Math.toDegrees(viewer.camera.pitch) * 10) / 10));
        itemsElement.setAttribute("data-camera-heading-degrees", String(Math.round(Cesium.Math.toDegrees(viewer.camera.heading) * 10) / 10));
        itemsElement.setAttribute("data-ranked-candidate-count", String(mosaicRankedCandidateCount));
    }
    if (!html && document.querySelector("#items .ssr-asset-card") && !mosaicAssetSearch) return;
    if (!html) html = mosaicAssetSearch ? '<div class="map-empty-state"><strong>No public assets match this name and filters</strong><span>Try another asset or creator name, or broaden the family, extension and time filters.</span></div>' : '<div class="map-empty-state"><strong>No public assets match this view and filters</strong><span>Move or zoom the globe, or broaden the filters to discover more MosaicAssets.</span></div>';
    else if (mosaicRankedCandidateCount > limited.length) html = '<div class="map-results-summary">Showing the ' + limited.length + (mosaicAssetSearch ? ' matching assets. Refine the name or filters to narrow the result.</div>' : ' assets most relevant to this 3D view. Move closer to refine the result.</div>') + html;
    var signature = limited.map(function (asset) { return asset.Key; }).join("|") + "#" + mosaicRankedCandidateCount;
    if (signature !== mosaicLastCardSignature || !document.querySelector("#items .all-asset-card") && limited.length) {
        $("#items").html(html);
        mosaicLastCardSignature = signature;
    }
    limited.forEach(function (asset) { var entry = mosaicVisualRegistry[asset.Key]; mosaicSyncVisibilityButton(asset.Key, !entry || entry.userVisible !== false); });
}

function mosaicNormaliseLongitude(longitude) {
    var value = Number(longitude || 0);
    while (value > 180) value -= 360;
    while (value < -180) value += 360;
    return value;
}

function mosaicUnwrapLongitude(longitude, reference) {
    var value = mosaicNormaliseLongitude(longitude), centre = mosaicNormaliseLongitude(reference);
    while (value - centre > 180) value -= 360;
    while (value - centre < -180) value += 360;
    return value;
}

function mosaicCaptureViewBounds(targetViewer) {
    if (!targetViewer || targetViewer.isDestroyed() || !targetViewer.camera || !targetViewer.scene) return;
    var scene = targetViewer.scene, camera = targetViewer.camera, canvas = scene.canvas;
    var cameraCartographic = camera.positionCartographic || Cesium.Cartographic.fromCartesian(camera.positionWC);
    heightMove = Math.max(0, Number(cameraCartographic.height || 0));
    var referenceLongitude = Cesium.Math.toDegrees(cameraCartographic.longitude), samples = [];
    var xRatios = [.02, .25, .5, .75, .98], yRatios = [.02, .5, .98];
    yRatios.forEach(function (yRatio) {
        xRatios.forEach(function (xRatio) {
            var windowPosition = new Cesium.Cartesian2((canvas.clientWidth || canvas.width) * xRatio, (canvas.clientHeight || canvas.height) * yRatio);
            var ray = camera.getPickRay(windowPosition);
            var point = ray && scene.globe ? scene.globe.pick(ray, scene) : null;
            if (!point && ray) point = camera.pickEllipsoid(windowPosition, Cesium.Ellipsoid.WGS84);
            if (!point) return;
            var cartographic = Cesium.Cartographic.fromCartesian(point);
            samples.push({ latitude:Cesium.Math.toDegrees(cartographic.latitude), longitude:mosaicUnwrapLongitude(Cesium.Math.toDegrees(cartographic.longitude), referenceLongitude) });
        });
    });
    var subCamera = Cesium.Ellipsoid.WGS84.scaleToGeodeticSurface(camera.positionWC, new Cesium.Cartesian3());
    if (subCamera) {
        var subCartographic = Cesium.Cartographic.fromCartesian(subCamera);
        samples.push({ latitude:Cesium.Math.toDegrees(subCartographic.latitude), longitude:mosaicUnwrapLongitude(Cesium.Math.toDegrees(subCartographic.longitude), referenceLongitude) });
    }
    var focused = mosaicAllAssetByKey[mosaicFocusedAssetKey];
    if (focused && mosaicFocusedAssetIsPinned(mosaicFocusedAssetKey, Date.now())) samples.push({ latitude:Number(focused.Latitude), longitude:mosaicUnwrapLongitude(Number(focused.Longitude), referenceLongitude) });
    if (!samples.length) samples.push({ latitude:Cesium.Math.toDegrees(cameraCartographic.latitude), longitude:referenceLongitude });

    var latitudes = samples.map(function (point) { return point.latitude; });
    var longitudes = samples.map(function (point) { return point.longitude; });
    var minLatitude = Math.min.apply(Math, latitudes), maxLatitude = Math.max.apply(Math, latitudes);
    var minLongitude = Math.min.apply(Math, longitudes), maxLongitude = Math.max.apply(Math, longitudes);
    var earthRadius = Cesium.Ellipsoid.WGS84.maximumRadius;
    var horizonDegrees = Cesium.Math.toDegrees(Math.acos(Math.min(1, earthRadius / (earthRadius + heightMove))));
    var latitudePadding = Math.max(.25, (maxLatitude - minLatitude) * .12, samples.length < 4 ? horizonDegrees * 1.05 : horizonDegrees * .08);
    var longitudePadding = Math.max(.25, (maxLongitude - minLongitude) * .12, samples.length < 4 ? horizonDegrees * 1.05 : horizonDegrees * .08);
    northMove = Math.min(90, maxLatitude + latitudePadding);
    southMove = Math.max(-90, minLatitude - latitudePadding);
    var paddedWest = minLongitude - longitudePadding, paddedEast = maxLongitude + longitudePadding;
    if (paddedEast - paddedWest >= 359) { westMove = -180; eastMove = 180; }
    else { westMove = mosaicNormaliseLongitude(paddedWest); eastMove = mosaicNormaliseLongitude(paddedEast); }
    latMove = (northMove + southMove) / 2;
    lonMove = mosaicNormaliseLongitude((minLongitude + maxLongitude) / 2);
}

function mosaicApplyAssetPage(items, stage) {
    stage = stage || mosaicAssetQueryStage;
    if (!stage) return;
    (items || []).forEach(function (asset) {
        asset.Latitude = Number(asset.Latitude); asset.Longitude = Number(asset.Longitude); asset.FileTypeId = Number(asset.FileTypeId);
        if (!isFinite(asset.Latitude) || !isFinite(asset.Longitude) || !asset.Key) return;
        stage.seen[asset.Key] = true;
        if (!stage.byKey[asset.Key]) { stage.indexByKey[asset.Key] = stage.assets.length; stage.assets.push(asset); }
        else stage.assets[stage.indexByKey[asset.Key]] = asset;
        stage.byKey[asset.Key] = asset;
        mosaicAddMarker(viewer, Cesium.Cartesian3.fromDegrees(asset.Longitude, asset.Latitude, Number(asset.Altitude || 0)), asset.Name, asset.Key, asset.FileTypeId, true);
        mosaicRegisterVisual({
            key:asset.Key, kind:asset.ViewerKind, assetType:asset.FileTypeId, id:asset.Id,
            latitude:asset.Latitude, longitude:asset.Longitude, altitude:Number(asset.Altitude||0),
            height:Number(asset.Height||0), heading:Number(asset.Heading||0), scale:Number(asset.Scale||1),
            georeferenceMode:asset.GeoreferenceMode, url:asset.ViewerUrl, name:asset.Name
        });
    });
}

function mosaicCatalogueError() {
    $('#mapCatalogueStatus').html('Assets could not be refreshed. Previously loaded results may still be shown. <button type="button" class="btn btn-secondary" onclick="mosaicScheduleAssetQuery(true)">Retry</button>');
    if (!mosaicAllAssets.length && !document.querySelector('#items .ssr-asset-card')) $('#items').html('<div class="map-empty-state"><strong>The public assets could not be loaded</strong><span>Check your connection and use Retry above.</span></div>');
}

function mosaicQueryAssetsInView(cursor, generation) {
    if (!viewer || viewer.isDestroyed() || generation !== mosaicAssetQueryGeneration) return;
    var stage = mosaicAssetQueryStage;
    if (!stage || stage.generation !== generation) return;
    if (Number(cursor || 0) === 0 || !stage.bounds) {
        $('#mapCatalogueStatus').text('Loading assets…');
        mosaicCaptureViewBounds(viewer);
        stage.bounds = { north:northMove, south:southMove, east:eastMove, west:westMove, cameraHeight:heightMove };
    }
    var payload = {
        north:String(stage.bounds.north), south:String(stage.bounds.south), east:String(stage.bounds.east), west:String(stage.bounds.west),
        families:mosaicActiveFamilies, pageSize:240, cursor:Number(cursor || 0), cameraHeight:String(stage.bounds.cameraHeight || 0),
        capturedFromUtc:mosaicTemporalFilter ? mosaicTemporalFilter.request().capturedFromUtc : "",
        capturedToUtc:mosaicTemporalFilter ? mosaicTemporalFilter.request().capturedToUtc : ""
    };
    payload.query = mosaicAssetSearch;
    payload.extension = MosaicExtensionFilter.get('mapExtension');
    mosaicAssetQueryRequest = $.ajax({
        type:"POST", dataType:"json", contentType:"application/json; charset=utf-8",
        url:MYLIBRARY.getUrl_page()+"/filterPublicAssetIndex", data:JSON.stringify(payload), timeout:30000,
        success:function(response){
            if (generation !== mosaicAssetQueryGeneration) return;
            var result;
            try { result = $.parseJSON(response.d); if (!result || !Array.isArray(result.Items)) throw new Error(); }
            catch (invalid) { mosaicCatalogueError(); return; }
            // Build a complete replacement set off-screen. Existing cards stay
            // stable while pages arrive, avoiding empty/partial-list flicker.
            mosaicApplyAssetPage(result.Items, stage);
            if (result.HasMore && Number(result.Cursor) < 5000) window.setTimeout(function(){ mosaicQueryAssetsInView(result.Cursor, generation); }, 35);
            else {
                var pinnedAsset = mosaicAllAssetByKey[mosaicFocusedAssetKey];
                if (!mosaicAssetSearch && pinnedAsset && mosaicFocusedAssetIsPinned(mosaicFocusedAssetKey, Date.now()) && !stage.byKey[mosaicFocusedAssetKey]) {
                    stage.indexByKey[mosaicFocusedAssetKey] = stage.assets.length;
                    stage.assets.push(pinnedAsset); stage.byKey[mosaicFocusedAssetKey] = pinnedAsset; stage.seen[mosaicFocusedAssetKey] = true;
                }
                mosaicAllAssets = stage.assets;
                $('#mapCatalogueStatus').text('');
                mosaicAllAssetByKey = stage.byKey;
                mosaicAssetQuerySeen = stage.seen;
                mosaicAssetQueryStage = null;
                mosaicFinalizeAssetDiscoverySet();
                mosaicUpdateMarkerVisibility();
                mosaicScheduleCardRanking(true);
                mosaicLoadNearbyVisuals();
            }
        },
        error:function(xhr, status){
            if (status === "abort" || generation !== mosaicAssetQueryGeneration) return;
            mosaicCatalogueError();
        }
    });
}

function mosaicScheduleAssetQuery(immediate) {
    if (mosaicAssetSearch && !immediate) { mosaicScheduleCardRanking(false); return; }
    if (mosaicAssetQueryTimer) window.clearTimeout(mosaicAssetQueryTimer);
    if (mosaicAssetQueryRequest && mosaicAssetQueryRequest.readyState !== 4) mosaicAssetQueryRequest.abort();
    var generation = ++mosaicAssetQueryGeneration;
    mosaicAssetQueryStage = { generation:generation, assets:[], byKey:{}, seen:{}, indexByKey:{}, bounds:null };
    mosaicAssetQueryTimer = window.setTimeout(function(){ mosaicQueryAssetsInView(0, generation); }, immediate ? 0 : 180);
}

function loadAllAssets(latMarket, lonMarket, families, preservedCamera) {
    mosaicActiveFamilies = String(families || "");
    mosaicFocusedAssetKey = ""; mosaicFocusedAssetUntil = 0; mosaicCardPresence = {}; mosaicLastCardSignature = "";
    var isNewViewer = !viewer || !MosaicCesiumRuntime.alive(viewer);
    viewer = mosaicCreateViewer({ imageryProvider: mosaicEarthBaseLayer(), timeline:false, animation:false, fullscreenButton:false, homeButton:false, infoBox:false, sceneModePicker:false, baseLayerPicker:false, navigationHelpButton:false, projectionPicker:false, scene3DOnly:true });
    if (isNewViewer) {
        configureMosaicEarthTerrain(viewer);
        mosaicInstallLocalCardQaControls();
        viewer.camera.changed.addEventListener(function () { mosaicScheduleCardRanking(false); });
        viewer.camera.moveEnd.addEventListener(function () { mosaicCaptureViewBounds(viewer); mosaicScheduleCardRanking(true); mosaicLoadNearbyVisuals(); mosaicScheduleAssetQuery(false); });
    }
    mosaicAllAssets = []; mosaicAllAssetByKey = {};
    $("#items").html('<div class="map-empty-state"><strong>Loading the global asset network</strong><span>Preparing lightweight discovery markers…</span></div>');
    if (isNewViewer) {
        if (preservedCamera) viewer.camera.setView({ destination:preservedCamera.destination, orientation:{ direction:preservedCamera.direction, up:preservedCamera.up } });
        else if (latMarket != null && lonMarket != null) viewer.camera.setView({destination:Cesium.Cartesian3.fromDegrees(Number(lonMarket),Number(latMarket),2000),orientation:{heading:0,pitch:MosaicCesiumRuntime.topDownOffset(1).pitch,roll:0}});
        else if (heightMove > 0) viewer.camera.setView({destination:Cesium.Cartesian3.fromDegrees(Number(lonMove),Number(latMove),Number(heightMove)),orientation:{heading:0,pitch:MosaicCesiumRuntime.topDownOffset(1).pitch,roll:0}});
    }
    mosaicScheduleAssetQuery(true);
}

var viewer = null;

function mosaicTemporalFilterChanged() {
    // Time changes replace the discovery set while preserving the current
    // Cesium Viewer, camera, base map and every still-eligible runtime object.
    mosaicFocusedAssetKey = ""; mosaicFocusedAssetUntil = 0; mosaicCardPresence = {}; mosaicLastCardSignature = "";
    if (!viewer || viewer.isDestroyed()) return;
    mosaicScheduleAssetQuery(true);
}

mosaicTemporalFilter = MosaicTemporalFilter.mount({ containerId:"globalTemporalFilter", onChange:mosaicTemporalFilterChanged });
document.getElementById('mapExtension').addEventListener('change', mosaicTemporalFilterChanged);

if (tipo != "0") {
    loadCesium(tipo, latMarket.replace(',', '.'), lonMarket.replace(',', '.'));
} else {
    loadCesium(0, null, null);
}



function loadCesium(opcion, latMarket, lonMarket) {
    ["all-assets-menu", "orthophoto-menu", "3d-menu", "pointcloud-menu"].forEach(function(id){ var element=document.getElementById(id); if(element) { element.classList.remove("active-icon-menu"); element.setAttribute("aria-pressed", "false"); } });
    var activeMenu = opcion == 1 ? "pointcloud-menu" : opcion == 2 ? "orthophoto-menu" : opcion == 3 ? "3d-menu" : "all-assets-menu";
    var families = opcion == 1 ? "pointcloud" : opcion == 2 ? "orthophoto" : opcion == 3 ? "model3d" : "";
    document.getElementById(activeMenu).classList.add("active-icon-menu");
    document.getElementById(activeMenu).setAttribute("aria-pressed", "true");
    // All tabs share the same 3D-aware discovery and ranking path. The legacy
    // type-specific implementation below is intentionally unreachable until it
    // can be removed with the corresponding old WebMethods.
    loadAllAssets(latMarket, lonMarket, families, null);
    return;

    if (opcion == 2) {

        viewer = mosaicCreateViewer({
            imageryProvider: mosaicEarthBaseLayer(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            infoBox: false,
            sceneModePicker: false,
            baseLayerPicker: false,
            navigationHelpButton: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicEarthTerrain(viewer);

        var scene = viewer.scene;

        if (heightMove > 0.00) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMove, latMove, heightMove),
            });
        }

        const elemento = document.querySelector('#cesiumContainer');

        scene.camera.moveEnd.addEventListener(function () {
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

                loadItems(north, south, east, west, 2);
        });



        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id };
            var requestId = ++mosaicCardRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    if (requestId !== mosaicCardRequestId) return;
                    var json = $.parseJSON(response.d);
                    var items = json;
                    $("#items").empty();
                    if (items.length === 0) $("#items").html('<div class="map-empty-state"><strong>No public maps or layers in this view</strong><span>Move or zoom the map, or publish an asset from MyProjects.</span></div>');
                    for (i = 0; i < items.length; i++) {
                        //$("#items").append(tableTemplateData(items[i]["NAME"], items[i]["ID"]));
                        $("#items").append(mosaicGlobalAssetCard(items[i], type_id));
                    }
                    sortVisibleAssetCards();
                    appendPublicExternalAssets(type_id, requestId);
                },
                error: function (response) {
                    $("#items").html('<div class="map-empty-state"><strong>Assets could not be loaded</strong><span>Please retry or check the public-asset data.</span></div>');
                }
            });
        }

        // Populate the discovery list immediately; terrain loading must not gate catalogue data.
        loadItems(72, 30, 45, -25, 2);

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
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesiumOrthophoto(${obj.LATITUD.replace(',', '.')}, ${obj.LONGITUD.replace(',', '.')})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}" onclick="event.stopPropagation()">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${mosaicEscapeHtml(obj.NAME)}</p>
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

        var data = {};

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/loadOrthophoto",
            data: JSON.stringify(data),
            success: function (response) {
                var json = $.parseJSON(response.d);
                var items = json;
                for (i = 0; i < items.length; i++) {
                    loadOrthophoto(items[i]["MED_LAT"], items[i]["MED_LON"], items[i]["URL_CESIUM"], items[i]["NOMBRE"], items[i]["ID"], items[i]["MED_HEIGHT"]);
                }
            },
            error: function (response) {

            }
        });

        function loadOrthophoto(med_lat, med_lon, url_cesium, nombre, ID, altura) {
            window.tilesets = window.tilesets || {};
            const tilesetName = `tileset${ID}`;

            MosaicCesiumRuntime.loadTmsLayer(viewer, url_cesium, {
                maximumLevel: 20, // adjust to your maximum tile level
                credit: 'Your attribution here'
            }).then(function (layer) {
                window.tilesets[tilesetName] = layer;
            }).catch(function (error) {
                console.error("MosaicEarth orthophoto layer failed", ID, error);
            });

            mosaicAddMarker(viewer, Cesium.Cartesian3.fromDegrees(Number(med_lon), Number(med_lat), 120), nombre, "processed-2-" + ID, 2);

        }

        if (latMarket != null && lonMarket != null) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMarket, latMarket, 2000.0),
                orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }
            });
        }
    }

    if (opcion == 1) {



        // Initialize the Cesium Viewer in the HTML element with the `cesiumContainer` ID.
        viewer = mosaicCreateViewer({
            imageryProvider: mosaicEarthBaseLayer(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            sceneModePicker: false,
            baseLayerPicker: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicEarthTerrain(viewer);

        var scene = viewer.scene;

        const elemento = document.querySelector('#cesiumContainer');

        if (heightMove > 0.00) {
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

                loadItems(north, south, east, west, 1);
        });

        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id };
            var requestId = ++mosaicCardRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    if (requestId !== mosaicCardRequestId) return;
                    var json = $.parseJSON(response.d);
                    var items = json;
                    $("#items").empty();
                    if (items.length === 0) $("#items").html('<div class="map-empty-state"><strong>No public point clouds</strong><span>Publish one from MyProjects or import an external MosaicAsset.</span></div>');
                    for (i = 0; i < items.length; i++) {
                        //$("#items").append(tableTemplateData(items[i]["NAME"], items[i]["ID"]));
                        $("#items").append(mosaicGlobalAssetCard(items[i], type_id));
                    }
                    sortVisibleAssetCards();
                    appendPublicExternalAssets(type_id, requestId);
                },
                error: function (response) {
                    $("#items").html('<div class="map-empty-state"><strong>Point clouds could not be loaded</strong><span>Please retry or review public-asset data.</span></div>');
                }
            });

        }

        loadItems(72, 30, 45, -25, 1);

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
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesium(${obj.ID})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}" onclick="event.stopPropagation()">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${mosaicEscapeHtml(obj.NAME)}</p>
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

        var data = {};

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/loadPointcloud",
            data: JSON.stringify(data),
            success: function (response) {
                var json = $.parseJSON(response.d);
                var items = json;
                for (i = 0; i < items.length; i++) {
                    loadPointcloud(items[i]["HEIGHT"], items[i]["URL_CESIUM"], items[i]["NOMBRE"], items[i]["ID"], items[i]["MED_LAT"], items[i]["MED_LON"]);
                }
                mosaicLoadNearbyVisuals();
            },
            error: function (response) {

            }
        });

        function loadPointcloud(height, url_cesium, nombre, ID, med_lat, med_lon) {
            mosaicAddMarker(viewer, Cesium.Cartesian3.fromDegrees(Number(med_lon), Number(med_lat), 120), nombre, "processed-1-" + ID, 1);
            mosaicRegisterVisual({key:"processed-1-"+ID,kind:"processed-tileset",assetType:1,id:ID,latitude:Number(med_lat),longitude:Number(med_lon),height:Number(height||0),url:url_cesium,name:nombre});
        }

        if (latMarket != null && lonMarket != null) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMarket, latMarket, 2000.0),
                orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }
            });
        }

    }

    if (opcion == 3) {

        // Initialize the Cesium Viewer in the HTML element with the `cesiumContainer` ID.
        viewer = mosaicCreateViewer({
            imageryProvider: mosaicEarthBaseLayer(),
            timeline: false,
            animation: false,
            fullscreenButton: false,
            homeButton: false,
            sceneModePicker: false,
            baseLayerPicker: false,
            projectionPicker: false,
            scene3DOnly: true
        });
        configureMosaicEarthTerrain(viewer);

        var scene = viewer.scene;

        if (heightMove > 0.00) {
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

            loadItems(north, south, east, west, 3);
        });

        function loadItems(north, south, east, west, type_id) {

            var url = MYLIBRARY.getUrl_page();

            var data = { north, south, east, west, type_id };
            var requestId = ++mosaicCardRequestId;

            $.ajax({
                type: "POST",
                dataType: 'json',
                contentType: "application/json; charset=utf-8",
                url: url + "/loadItems",
                data: JSON.stringify(data),
                success: function (response) {
                    if (requestId !== mosaicCardRequestId) return;
                    var json = $.parseJSON(response.d);
                    var items = json;
                    $("#items").empty();
                    if (items.length === 0) $("#items").html('<div class="map-empty-state"><strong>No public 3D models</strong><span>Publish one from MyProjects or import an external MosaicAsset.</span></div>');
                    for (i = 0; i < items.length; i++) {
                        //$("#items").append(tableTemplateData(items[i]["NAME"], items[i]["ID"]));
                        $("#items").append(mosaicGlobalAssetCard(items[i], type_id));
                    }
                    sortVisibleAssetCards();
                    appendPublicExternalAssets(type_id, requestId);
                },
                error: function (response) {
                    $("#items").html('<div class="map-empty-state"><strong>3D models could not be loaded</strong><span>Please retry or review public-asset data.</span></div>');
                }
            });

        }

        loadItems(72, 30, 45, -25, 3);

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
            let displayCredits = obj.Credits == 0 ? 'FREE' : obj.Credits;
            return `<div class="box-product" onclick="zoomCesium(${obj.ID})">
                        <div class="box-product-image" style="background-image: url('${obj.UrlOrthoPhoto}');">
                            <div class="icon icon-top icon-map">
                                <img src="../assets/resources/icon-map.svg" />
                            </div>
                            <div class="icon icon-top icon-shop">
                                <a href="${obj.UrlMarketplace}" onclick="event.stopPropagation()">
                                    <img src="../assets/resources/icon feather-shopping-cart.svg" />
                                </a>
                            </div>
                            <div class="icon icon-menu">
                                ${obj.IconType}
                            </div>
                        </div>
                        <p class="product-title">${mosaicEscapeHtml(obj.NAME)}</p>
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

        var data = {};

        $.ajax({
            type: "POST",
            dataType: 'json',
            contentType: "application/json; charset=utf-8",
            url: url + "/load3DModels",
            data: JSON.stringify(data),
            success: function (response) {
                var json = $.parseJSON(response.d);
                var items = json;
                for (i = 0; i < items.length; i++) {
                    load3D(items[i]["HEIGHT"], items[i]["URL_CESIUM"], items[i]["NOMBRE"], items[i]["ID"], items[i]["MED_LAT"], items[i]["MED_LON"]);
                }
                mosaicLoadNearbyVisuals();
            },
            error: function (response) {

            }
        });

        function load3D(height, url_cesium, nombre, ID, med_lat, med_lon) {
            mosaicAddMarker(viewer, Cesium.Cartesian3.fromDegrees(Number(med_lon), Number(med_lat), 120), nombre, "processed-3-" + ID, 3);
            mosaicRegisterVisual({key:"processed-3-"+ID,kind:"processed-tileset",assetType:3,id:ID,latitude:Number(med_lat),longitude:Number(med_lon),height:Number(height||0),url:url_cesium,name:nombre});
        }

        if (latMarket != null && lonMarket != null) {
            viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lonMarket, latMarket, 2000.0),
                orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }
            });
        }
    }
    hideUnwantedItems();
}

function zoomCesium(ID) {
    var entry = Object.keys(mosaicVisualRegistry).map(function(key){return mosaicVisualRegistry[key];}).filter(function(item){return String(item.id)===String(ID);})[0];
    if (entry) { mosaicLoadVisual(entry, true); return; }
    var target = window.tilesets["tileset" + ID];
    if (!target) return;
    var range = target.boundingSphere ? Math.max(target.boundingSphere.radius * 2.5, 300) : 1200;
    viewer.flyTo(target, { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-90), range) });


}

function zoomCesiumOrthophoto(LATITUD, LONGITUD) {

    viewer.camera.flyTo({
        destination: Cesium.Cartesian3.fromDegrees(LONGITUD, LATITUD, 2000.0),
        orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 }
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
    //let items = document.querySelectorAll('.cesium-baseLayerPicker-item');

    //items.forEach(item => {
    //    let label = item.querySelector('.cesium-baseLayerPicker-itemLabel');

    //    if (label && label.textContent !== "Bing Maps Aerial" && label.textContent !== "Open­Street­Map") {
    //        item.style.display = "none"; // Esconde el item
    //    }
    //});

    //// Ocultar todos los títulos "Terrain"
    //let terrainTitles = document.querySelectorAll('.cesium-baseLayerPicker-sectionTitle');
    //terrainTitles.forEach(title => {
    //    if (title.textContent.trim() === "Terrain") {
    //        title.style.display = "none"; // Esconde el título
    //    }
    //});

    //// Ocultar la sección que tiene 'foreach: _terrainProviders' en data-bind
    //let terrainProviderSection = document.querySelector('div[data-bind="foreach: _terrainProviders"]');
    //if (terrainProviderSection) {
    //    terrainProviderSection.style.display = "none"; // Esconde la sección
    //}
}

// Llama a la función después de que Cesium haya terminado de cargar y crear el baseLayerPicker
setTimeout(hideUnwantedItems, 1000); // Puedes ajustar el tiempo si es necesario
