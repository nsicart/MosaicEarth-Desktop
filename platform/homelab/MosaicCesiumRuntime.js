(function (global) {
    "use strict";

    if (!global.Cesium) throw new Error("MosaicCesiumRuntime requires CesiumJS to be loaded first.");

    var Cesium = global.Cesium;
    var markerImages = Object.create(null);

    // Render at 4x the displayed dimensions. Cesium rasterizes billboard
    // images into its atlas, so a small SVG or canvas loses detail on HiDPI.
    function mapMarkerImage(typeId, count) {
        var clustered = typeof count === "number";
        var label = clustered ? (count > 999 ? "999+" : String(Math.max(1, Math.floor(count)))) : "";
        var colors = { 1: "#35b99b", 2: "#99cc00", 3: "#5f9ee8", 0: "#99cc00" };
        var color = clustered ? "#1f8a62" : (colors[Number(typeId)] || colors[0]);
        var cacheKey = clustered ? "cluster-" + label : "asset-" + color;
        if (markerImages[cacheKey]) return markerImages[cacheKey];
        var canvas = global.document.createElement("canvas");
        var width = clustered ? 44 : 32, height = clustered ? 48 : 36;
        canvas.width = width * 4; canvas.height = height * 4;
        var context = canvas.getContext("2d");
        context.scale(4, 4);
        context.imageSmoothingEnabled = true;
        context.imageSmoothingQuality = "high";
        context.shadowColor = "rgba(0,0,0,.4)";
        context.shadowBlur = 3; context.shadowOffsetY = 1;
        context.beginPath();
        if (clustered) {
            context.moveTo(17, 37); context.lineTo(22, 45); context.lineTo(27, 37);
            context.arc(22, 21, 18, .95, 2.19, true);
        } else {
            context.moveTo(16, 34);
            context.bezierCurveTo(12, 30, 3.5, 23, 3.5, 15);
            context.bezierCurveTo(3.5, 8.1, 9.1, 2.5, 16, 2.5);
            context.bezierCurveTo(22.9, 2.5, 28.5, 8.1, 28.5, 15);
            context.bezierCurveTo(28.5, 23, 20, 30, 16, 34);
        }
        context.closePath();
        context.fillStyle = color; context.fill();
        context.shadowColor = "transparent";
        context.lineWidth = 1.8; context.strokeStyle = "#f4fff9"; context.stroke();
        context.fillStyle = "#f4fff9";
        if (clustered) {
            context.font = "700 " + (label.length > 3 ? 12 : label.length > 2 ? 15 : 18) + "px Arial, sans-serif";
            context.textAlign = "center"; context.textBaseline = "middle";
            context.fillText(label, 22, 21);
        } else {
            context.beginPath(); context.arc(16, 15, 5.7, 0, Math.PI * 2); context.fill();
            context.lineWidth = 1.2; context.strokeStyle = "#173e31"; context.stroke();
            context.beginPath(); context.arc(16, 15, 2.4, 0, Math.PI * 2);
            context.fillStyle = color; context.fill();
        }
        markerImages[cacheKey] = canvas;
        return canvas;
    }
    var viewerRegistry = typeof global.WeakMap === "function" ? new global.WeakMap() : null;
    var creationRegistry = typeof global.WeakMap === "function" ? new global.WeakMap() : null;
    var counters = {
        viewersCreated: 0,
        viewersDestroyed: 0,
        tilesetsCreated: 0,
        tilesetsReleased: 0,
        imageryLayersCreated: 0,
        imageryLayersReleased: 0,
        modelsCreated: 0,
        modelsReleased: 0
    };

    function copyOptions(options) {
        var result = {};
        Object.keys(options || {}).forEach(function (key) { result[key] = options[key]; });
        return result;
    }

    function alive(viewer) {
        return !!viewer && typeof viewer.isDestroyed === "function" && !viewer.isDestroyed();
    }

    function resolveContainer(container) {
        return typeof container === "string" ? global.document.getElementById(container) : container;
    }

    function currentViewer(container) {
        var host = resolveContainer(container);
        if (!host) return null;
        var viewer = viewerRegistry ? viewerRegistry.get(host) : host._mosaicCesiumViewer;
        return alive(viewer) ? viewer : null;
    }

    function setRegisteredViewer(host, viewer) {
        if (!host) return;
        if (viewerRegistry) {
            if (viewer) viewerRegistry.set(host, viewer);
            else viewerRegistry.delete(host);
        } else host._mosaicCesiumViewer = viewer || null;
    }

    function setCreating(host, value) {
        if (!host) return;
        if (creationRegistry) {
            if (value) creationRegistry.set(host, true);
            else creationRegistry.delete(host);
        } else host._mosaicCesiumCreating = !!value;
    }

    function isCreating(host) {
        return !!host && (creationRegistry ? creationRegistry.get(host) === true : host._mosaicCesiumCreating === true);
    }

    function viewerDiagnostics(viewer, phase, error) {
        var canvas = alive(viewer) && viewer.scene ? viewer.scene.canvas : null;
        var gl = null;
        try { gl = canvas && (canvas.getContext("webgl2") || canvas.getContext("webgl")); } catch (ignore) { }
        var snapshot = diagnostics();
        snapshot.phase = phase || "unknown";
        snapshot.url = global.location ? global.location.href : "";
        snapshot.userAgent = global.navigator ? global.navigator.userAgent : "";
        snapshot.webglContextLost = !!(gl && typeof gl.isContextLost === "function" && gl.isContextLost());
        snapshot.errorName = error && error.name ? error.name : "";
        snapshot.errorMessage = error && error.message ? error.message : String(error || "");
        return snapshot;
    }

    function errorHost(target) {
        if (alive(target)) return target.container;
        return resolveContainer(target);
    }

    function hideViewerError(target) {
        var host = errorHost(target);
        if (!host) return;
        var panel = host.querySelector(":scope > .mosaic-cesium-error");
        if (panel && panel.parentNode) panel.parentNode.removeChild(panel);
    }

    function showViewerError(target, title, message, retry) {
        var host = errorHost(target);
        if (!host) return null;
        hideViewerError(host);
        if (!host.style.position || host.style.position === "static") host.style.position = "relative";
        var panel = global.document.createElement("section");
        panel.className = "mosaic-cesium-error";
        panel.setAttribute("role", "alert");
        panel.style.cssText = "position:absolute;z-index:1000;inset:0;display:flex;align-items:center;justify-content:center;padding:24px;background:rgba(5,9,6,.9);color:#f4f7f5;font:15px/1.5 Arial,sans-serif;text-align:center;";
        var card = global.document.createElement("div");
        card.style.cssText = "width:min(460px,100%);padding:24px;border:1px solid #4b5c50;border-radius:14px;background:#101611;box-shadow:0 18px 48px rgba(0,0,0,.4);";
        var heading = global.document.createElement("h2");
        heading.textContent = title || "The 3D viewer could not start";
        heading.style.cssText = "margin:0 0 10px;color:#a8d833;font-size:20px;";
        var copy = global.document.createElement("p");
        copy.textContent = message || "MosaicEarth could not initialize the map. Please try again.";
        copy.style.cssText = "margin:0;color:#c8d1cb;";
        card.appendChild(heading);
        card.appendChild(copy);
        if (retry !== false) {
            var button = global.document.createElement("button");
            button.type = "button";
            button.textContent = "TRY AGAIN";
            button.style.cssText = "margin-top:18px;min-width:140px;padding:11px 18px;border:0;border-radius:24px;background:#a8d833;color:#11170f;font-weight:700;cursor:pointer;";
            button.addEventListener("click", function () { global.location.reload(); });
            card.appendChild(button);
        }
        panel.appendChild(card);
        host.appendChild(panel);
        return panel;
    }

    function cleanupFailedContainer(host) {
        if (!host) return;
        Array.prototype.forEach.call(host.querySelectorAll("canvas"), function (canvas) {
            try {
                var gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
                var extension = gl && gl.getExtension("WEBGL_lose_context");
                if (extension) extension.loseContext();
            } catch (ignore) { }
        });
        while (host.firstChild) host.removeChild(host.firstChild);
    }

    function createBaseProvider(mode) {
        return global.MosaicMapSources.createImagery(Cesium, mode);
    }

    function prepareViewerOptions(options) {
        var prepared = copyOptions(options);
        if (!Object.prototype.hasOwnProperty.call(prepared, "showRenderLoopErrors")) prepared.showRenderLoopErrors = false;
        if (!Object.prototype.hasOwnProperty.call(prepared, "navigationInstructionsInitiallyVisible")) prepared.navigationInstructionsInitiallyVisible = false;
        prepared.infoBox = false;
        prepared.selectionIndicator = false;
        // CesiumJS is open source. Our basemaps, terrain and place search do not
        // use ion or the SDK's demo token, even if an old database has a key.
        Cesium.Ion.defaultAccessToken = "";
        Cesium.CreditDisplay.cesiumCredit = new Cesium.Credit('<a href="https://cesium.com/platform/cesiumjs/" target="_blank" rel="noopener">CesiumJS</a>', true);
        prepared.terrainProvider = global.MosaicMapSources.createTerrain(Cesium);
        if (prepared.geocoder !== false) prepared.geocoder = [global.MosaicMapSources.createGeocoder(Cesium, prepared.terrainProvider)];
        if (Object.prototype.hasOwnProperty.call(prepared, "imageryProvider")) {
            prepared.baseLayer = prepared.imageryProvider === false ? false : new Cesium.ImageryLayer(prepared.imageryProvider);
            delete prepared.imageryProvider;
        } else if (!Object.prototype.hasOwnProperty.call(prepared, "baseLayer")) {
            prepared.baseLayer = new Cesium.ImageryLayer(createBaseProvider("satellite"));
        }
        delete prepared.terrain;
        return prepared;
    }

    function applyOpen3DTerrainAndCleanAttribution(viewer) {
        if (!alive(viewer) || viewer._mosaicMapSourcesAttached) return;
        viewer._mosaicMapSourcesAttached = true;
        viewer._mosaicOpenTerrain = viewer.terrainProvider;
        viewer._mosaicTerrainReady = Promise.resolve(viewer.terrainProvider);
        if (viewer.geocoder) {
            var geocoder = viewer.geocoder.viewModel;
            geocoder.autoComplete = false;
            // Our service already samples terrain and supplies a safe camera
            // altitude. Cesium's default callback discards that altitude.
            geocoder.destinationFound = function (model, destination) {
                if (!alive(viewer)) return;
                viewer.camera.flyTo({destination: destination, duration: 1.5,
                    complete: function () { if (alive(viewer)) model.complete.raiseEvent(); }});
            };
        }
        var cc = viewer.cesiumWidget && viewer.cesiumWidget.creditContainer;
        if (cc) {
            // Keep Cesium's own credit nodes and popup. Never remove provider
            // attribution or hide credits on mobile.
            cc.style.cssText = "position:absolute;bottom:4px;left:8px;z-index:5;font:11px/1.4 sans-serif;color:#e2e8e4;background:rgba(10,14,12,.85);padding:3px 8px;border-radius:5px;width:fit-content;max-width:calc(100% - 16px);";
            var measure = function () {
                if (!alive(viewer)) return;
                var rect = cc.getBoundingClientRect();
                global.document.documentElement.style.setProperty('--mosaic-credit-height', Math.ceil(rect.height) + 'px');
                global.document.documentElement.style.setProperty('--mosaic-credit-bottom', Math.max(0, Math.ceil(global.innerHeight - rect.bottom)) + 'px');
            };
            if (global.ResizeObserver) {
                viewer._mosaicCreditObserver = new global.ResizeObserver(measure);
                viewer._mosaicCreditObserver.observe(cc);
            }
            measure();
        }
        viewer._mosaicOpenTerrain.setStatusListener(function (healthy) {
            if (!alive(viewer)) return;
            var notice = viewer.container.querySelector('.mosaic-terrain-warning');
            if (healthy) { if (notice) notice.remove(); return; }
            if (notice) return;
            notice = global.document.createElement('div');
            notice.className = 'mosaic-terrain-warning';
            notice.setAttribute('role', 'alert');
            notice.style.cssText = 'position:absolute;top:54px;left:12px;right:12px;z-index:6;padding:8px 12px;background:#352c16;color:#fff4ce;border-radius:6px;font:13px/1.4 sans-serif;pointer-events:none;';
            notice.textContent = 'Some 3D terrain could not load. Check your connection and reload to retry.';
            viewer.container.appendChild(notice);
        });
    }

    function observeViewerSize(viewer) {
        if (!alive(viewer) || !viewer.container) return null;
        var frame = 0;
        var resize = function () {
            if (!alive(viewer)) return;
            if (frame) global.cancelAnimationFrame(frame);
            frame = global.requestAnimationFrame(function () {
                frame = 0;
                if (!alive(viewer)) return;
                viewer.resize();
                viewer.scene.requestRender();
            });
        };
        if (typeof global.ResizeObserver === "function") {
            viewer._mosaicResizeObserver = new global.ResizeObserver(resize);
            viewer._mosaicResizeObserver.observe(viewer.container);
        } else {
            global.addEventListener("resize", resize);
            viewer._mosaicWindowResize = resize;
        }
        resize();
        return resize;
    }

    function attachPrecisionWheelZoom(viewer) {
        if (!alive(viewer) || !viewer.scene || !viewer.scene.canvas) return;
        var ctrl = viewer.scene.screenSpaceCameraController;
        if (ctrl) {
            ctrl.enableCollisionDetection = true;
            ctrl.inertiaZoom = 0.0;
            ctrl.inertiaSpin = 0.25;
            ctrl.inertiaTranslate = 0.25;
            ctrl._zoomFactor = 1.25;
            ctrl.wheelZoomFactor = 1.25;
            ctrl.minimumZoomDistance = 1.0;
            ctrl.zoomEventTypes = [
                Cesium.CameraEventType.RIGHT_DRAG,
                Cesium.CameraEventType.PINCH
            ];
        }
        var canvas = viewer.scene.canvas;
        if (canvas._mosaicPrecisionWheelAttached) return;
        canvas._mosaicPrecisionWheelAttached = true;
        canvas.addEventListener("wheel", function (e) {
            if (!alive(viewer)) return;
            e.preventDefault();
            e.stopPropagation();
            var cam = viewer.camera;
            var dist = 120.0;
            try {
                var centerPx = new Cesium.Cartesian2(canvas.clientWidth * 0.5, canvas.clientHeight * 0.5);
                var pickPos = viewer.scene.pickPosition(centerPx);
                if (!Cesium.defined(pickPos) && viewer.scene.globe) {
                    var ray = cam.getPickRay(centerPx);
                    if (ray) pickPos = viewer.scene.globe.pick(ray, viewer.scene);
                }
                if (Cesium.defined(pickPos)) {
                    dist = Cesium.Cartesian3.distance(cam.positionWC, pickPos);
                } else if (cam.positionCartographic) {
                    dist = Math.max(5.0, Math.abs(cam.positionCartographic.height));
                }
            } catch (ignore) {
                if (cam.positionCartographic) {
                    dist = Math.max(5.0, Math.abs(cam.positionCartographic.height));
                }
            }
            // Smooth 5.5% step per wheel notch, clamped for micro-control close to assets
            var rawNotches = Math.sign(e.deltaY) * Math.min(2.0, Math.max(0.35, Math.abs(e.deltaY) / 100.0));
            var step = Math.max(0.5, dist * 0.055) * Math.abs(rawNotches);
            if (rawNotches < 0) {
                cam.zoomIn(step);
            } else if (rawNotches > 0) {
                cam.zoomOut(step);
            }
            viewer.scene.requestRender();
        }, { passive: false });
    }

    function configureViewer(viewer, ionToken) {
        if (!alive(viewer)) return viewer;
        viewer.scene.globe.enableLighting = false;
        viewer.scene.globe.depthTestAgainstTerrain = false;
        viewer.scene.globe.showGroundAtmosphere = true;
        viewer.shadows = false;
        if (viewer.clock) viewer.clock.shouldAnimate = false;
        attachPrecisionWheelZoom(viewer);
        applyOpen3DTerrainAndCleanAttribution(viewer, ionToken);
        observeViewerSize(viewer);
        attachViewerLifecycle(viewer);
        return viewer;
    }

    function createViewer(container, options, ionToken, terrainOptions) {
        var host = resolveContainer(container);
        if (!host) throw new Error("Cesium viewer container was not found.");
        var existing = currentViewer(host);
        if (existing) return existing;
        if (isCreating(host)) {
            var raceError = new Error("A Cesium viewer is already being initialized for this container.");
            console.error("[MosaicEarth Cesium] duplicate initialization prevented", viewerDiagnostics(null, "duplicate-create", raceError));
            showViewerError(host, "The map is still starting", "MosaicEarth prevented a duplicate viewer from opening. Please try again if the map does not appear.");
            throw raceError;
        }
        setCreating(host, true);
        var lastError = null;
        try {
            for (var attempt = 1; attempt <= 2; attempt += 1) {
                var viewer = null;
                try {
                    cleanupFailedContainer(host);
                    var prepared = prepareViewerOptions(options, ionToken, terrainOptions);
                    viewer = new Cesium.Viewer(host, prepared);
                    if (prepared.terrain) {
                        var terrain = prepared.terrain;
                        viewer._mosaicTerrainReady = terrain.ready ? Promise.resolve(terrain.provider) : new Promise(function (resolve) {
                            var removeReady = terrain.readyEvent.addEventListener(function (provider) {
                                removeReady(); removeError(); resolve(provider);
                            });
                            var removeError = terrain.errorEvent.addEventListener(function () {
                                removeReady(); removeError(); resolve(null);
                            });
                        });
                    }
                    counters.viewersCreated += 1;
                    setRegisteredViewer(host, viewer);
                    if (global.MosaicAnnotations) global.MosaicAnnotations.attach(viewer);
                    configureViewer(viewer, ionToken);
                    viewer._mosaicInitializationAttempt = attempt;
                    console.info("[MosaicEarth Cesium] viewer initialized", viewerDiagnostics(viewer, "created-attempt-" + attempt));
                    return viewer;
                } catch (error) {
                    lastError = error;
                    console.error("[MosaicEarth Cesium] viewer initialization failed", viewerDiagnostics(viewer, "create-attempt-" + attempt, error));
                    if (alive(viewer)) destroyViewer(viewer);
                    else cleanupFailedContainer(host);
                }
            }
            showViewerError(host, "The 3D viewer could not start", "MosaicEarth retried once, but the browser could not create the WebGL view. Close other 3D tabs or try again.");
            throw lastError || new Error("Cesium viewer initialization failed.");
        } finally {
            setCreating(host, false);
        }
    }

    function destroyViewer(viewer) {
        if (!alive(viewer)) return;
        if (viewer._mosaicTerrainAbort) viewer._mosaicTerrainAbort.abort();
        if (viewer._mosaicOpenTerrain) viewer._mosaicOpenTerrain.destroy();
        if (viewer._mosaicCreditObserver) viewer._mosaicCreditObserver.disconnect();
        var host = viewer.container;
        if (viewer._mosaicAnnotations) viewer._mosaicAnnotations.destroy();
        if (viewer._mosaicResizeObserver) viewer._mosaicResizeObserver.disconnect();
        if (viewer._mosaicWindowResize) global.removeEventListener("resize", viewer._mosaicWindowResize);
        if (viewer._mosaicContextCanvas && viewer._mosaicContextLost) viewer._mosaicContextCanvas.removeEventListener("webglcontextlost", viewer._mosaicContextLost, false);
        if (viewer._mosaicContextCanvas && viewer._mosaicContextRestored) viewer._mosaicContextCanvas.removeEventListener("webglcontextrestored", viewer._mosaicContextRestored, false);
        if (viewer._mosaicRenderErrorRemove) viewer._mosaicRenderErrorRemove();
        if (viewer._mosaicPageHide) global.removeEventListener("pagehide", viewer._mosaicPageHide);
        hideViewerError(viewer);
        try {
            viewer.destroy();
        } finally {
            setRegisteredViewer(host, null);
            counters.viewersDestroyed += 1;
        }
    }

    function attachViewerLifecycle(viewer) {
        if (!alive(viewer) || viewer._mosaicLifecycleAttached) return viewer;
        viewer._mosaicLifecycleAttached = true;
        var canvas = viewer.scene && viewer.scene.canvas;
        if (canvas) {
            viewer._mosaicContextCanvas = canvas;
            viewer._mosaicContextLost = function (event) {
                event.preventDefault();
                console.error("[MosaicEarth Cesium] WebGL context lost", viewerDiagnostics(viewer, "webgl-context-lost"));
                showViewerError(viewer, "The 3D view was interrupted", "The browser temporarily lost the graphics context. MosaicEarth will resume it automatically when possible.", false);
            };
            viewer._mosaicContextRestored = function () {
                console.info("[MosaicEarth Cesium] WebGL context restored", viewerDiagnostics(viewer, "webgl-context-restored"));
                hideViewerError(viewer);
                if (alive(viewer)) {
                    viewer.useDefaultRenderLoop = true;
                    viewer.scene.requestRender();
                }
            };
            canvas.addEventListener("webglcontextlost", viewer._mosaicContextLost, false);
            canvas.addEventListener("webglcontextrestored", viewer._mosaicContextRestored, false);
        }
        if (viewer.scene && viewer.scene.renderError && typeof viewer.scene.renderError.addEventListener === "function") {
            viewer._mosaicRenderErrorRemove = viewer.scene.renderError.addEventListener(function (scene, error) {
                var message = error && error.message ? error.message : String(error || "");
                console.error("[MosaicEarth Cesium] render error", viewerDiagnostics(viewer, "render-error", error), error);
                var vectorClusterFailure = /_billboard|_point|EntityCluster|cluster/i.test(message);
                var isolated = vectorClusterFailure ? isolateLatestVectorAsset(viewer, error) : null;
                if (isolated) {
                    viewer.useDefaultRenderLoop = true;
                    scene.requestRender();
                    showViewerError(viewer, "A vector layer was hidden", "One vector MosaicAsset could not be rendered safely. The rest of the map is still available.", false);
                    global.setTimeout(function () { hideViewerError(viewer); }, 5000);
                    return;
                }
                showViewerError(viewer, "The 3D view stopped unexpectedly", "MosaicEarth protected the rest of the page. Try reloading the viewer.");
            });
        }
        viewer._mosaicPageHide = function (event) { if (!event.persisted && alive(viewer)) destroyViewer(viewer); };
        global.addEventListener("pagehide", viewer._mosaicPageHide);
        return viewer;
    }

    function setBaseLayer(viewer, mode, previousLayer) {
        if (!alive(viewer)) return null;
        var prev = previousLayer || viewer._mosaicBaseImageryLayer || (viewer.imageryLayers && viewer.imageryLayers.length ? viewer.imageryLayers.get(0) : null);
        var next = viewer.imageryLayers.addImageryProvider(createBaseProvider(mode), 0);
        if (prev && viewer.imageryLayers.contains(prev)) {
            viewer.imageryLayers.remove(prev, true);
        }
        viewer._mosaicBaseImageryLayer = next;
        viewer.scene.requestRender();
        return next;
    }

    function finiteVector(value) {
        return value && isFinite(value.x) && isFinite(value.y) && isFinite(value.z);
    }

    function captureCameraState(viewer, baseMode) {
        if (!alive(viewer) || !finiteVector(viewer.camera.positionWC) ||
            !finiteVector(viewer.camera.directionWC) || !finiteVector(viewer.camera.upWC)) return null;
        var position = viewer.camera.positionWC;
        var direction = viewer.camera.directionWC;
        var up = viewer.camera.upWC;
        return {
            v: 1,
            p: [position.x, position.y, position.z],
            d: [direction.x, direction.y, direction.z],
            u: [up.x, up.y, up.z],
            b: baseMode === "topographic" ? "topographic" : "satellite"
        };
    }

    function encodeCameraState(state) {
        if (!state) return "";
        try {
            return global.btoa(JSON.stringify(state)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
        } catch (ignore) {
            return "";
        }
    }

    function decodeCameraState(encoded) {
        if (!encoded || encoded.length > 2048) return null;
        try {
            var normal = encoded.replace(/-/g, "+").replace(/_/g, "/");
            while (normal.length % 4) normal += "=";
            var state = JSON.parse(global.atob(normal));
            if (!state || state.v !== 1 || !Array.isArray(state.p) || !Array.isArray(state.d) || !Array.isArray(state.u)) return null;
            if (state.p.length !== 3 || state.d.length !== 3 || state.u.length !== 3) return null;
            var values = state.p.concat(state.d, state.u);
            if (values.some(function (value) { return typeof value !== "number" || !isFinite(value); })) return null;
            state.b = state.b === "topographic" ? "topographic" : "satellite";
            return state;
        } catch (ignore) {
            return null;
        }
    }

    function cameraStateFromUrl(source) {
        try {
            var url = new global.URL(source || global.location.href, global.location.href);
            return decodeCameraState(url.searchParams.get("cam"));
        } catch (ignore) {
            return null;
        }
    }

    function restoreCameraState(viewer, source) {
        if (!alive(viewer)) return false;
        var state = typeof source === "string" || !source ? cameraStateFromUrl(source) : source;
        if (!state) return false;
        viewer.camera.setView({
            destination: new Cesium.Cartesian3(state.p[0], state.p[1], state.p[2]),
            orientation: {
                direction: new Cesium.Cartesian3(state.d[0], state.d[1], state.d[2]),
                up: new Cesium.Cartesian3(state.u[0], state.u[1], state.u[2])
            }
        });
        viewer.scene.requestRender();
        return true;
    }

    function urlWithCameraState(url, viewer, baseMode) {
        var state = captureCameraState(viewer, baseMode);
        if (!state) return url;
        try {
            var target = new global.URL(url, global.location.href);
            target.searchParams.set("cam", encodeCameraState(state));
            return target.href;
        } catch (ignore) {
            return url;
        }
    }

    function loadTileset(viewer, url, options) {
        return Cesium.Cesium3DTileset.fromUrl(url, options || {}).then(function (tileset) {
            if (!alive(viewer)) {
                if (tileset && typeof tileset.destroy === "function" && !tileset.isDestroyed()) tileset.destroy();
                throw new Error("Cesium viewer was replaced before the 3D Tiles asset finished loading.");
            }
            tileset._mosaicViewer = viewer;
            if (tileset.pointCloudShading) {
                tileset.pointCloudShading.attenuation = true;
                // Large 10-pixel discs obscure neighbouring returns and cause
                // excessive overdraw on dense clouds. Preserve every point and
                // its LOD selection while limiting its display footprint.
                tileset.pointCloudShading.maximumAttenuation = 3.0;
                tileset.pointCloudShading.geometricErrorScale = 2.2;
                tileset.pointCloudShading.baseResolution = 0.22;
                tileset.pointCloudShading.eyeDomeLighting = true;
                tileset.pointCloudShading.eyeDomeLightingStrength = 0.5;
                tileset.pointCloudShading.eyeDomeLightingRadius = 1.4;
            }
            if (tileset.boundingSphere && tileset.boundingSphere.center) {
                tileset._mosaicOriginalCenter = Cesium.Cartesian3.clone(tileset.boundingSphere.center);
            }
            viewer.scene.primitives.add(tileset);
            retainTerrainOverlay(viewer);
            tileset._mosaicTerrainOverlayRetained = true;
            counters.tilesetsCreated += 1;
            return tileset;
        });
    }

    function _setTilesetHeightOffsetMeters(tileset, offsetMeters) {
        if (!tileset || (typeof tileset.isDestroyed === "function" && tileset.isDestroyed())) return tileset;
        var baseCenter = tileset._mosaicOriginalCenter || (tileset.boundingSphere && tileset.boundingSphere.center);
        if (!baseCenter) return tileset;
        if (!tileset._mosaicOriginalCenter) {
            tileset._mosaicOriginalCenter = Cesium.Cartesian3.clone(baseCenter);
        }
        var cartographic = Cesium.Cartographic.fromCartesian(tileset._mosaicOriginalCenter);
        if (!cartographic) return tileset;
        var surface = Cesium.Cartesian3.fromRadians(cartographic.longitude, cartographic.latitude, 0);
        var offset = Cesium.Cartesian3.fromRadians(cartographic.longitude, cartographic.latitude, offsetMeters);
        var translation = Cesium.Cartesian3.subtract(offset, surface, new Cesium.Cartesian3());
        tileset.modelMatrix = Cesium.Matrix4.fromTranslation(translation);
        tileset._mosaicAppliedHeightOffset = offsetMeters;
        if (tileset._mosaicViewer && alive(tileset._mosaicViewer) && tileset._mosaicViewer.scene) {
            tileset._mosaicViewer.scene.requestRender();
        }
        return tileset;
    }

    function applyProcessedTilesetHeight(tileset, height) {
        if (!tileset || !tileset.boundingSphere || !tileset.boundingSphere.center) return tileset;
        if (!tileset._mosaicOriginalCenter) {
            tileset._mosaicOriginalCenter = Cesium.Cartesian3.clone(tileset.boundingSphere.center);
        }
        var resolvedHeight = Number(height);
        if (!isFinite(resolvedHeight)) resolvedHeight = 0;

        var extras = (tileset.asset && tileset.asset.extras) ||
            (tileset._extras) ||
            (tileset._root && tileset._root._header && tileset._root._header.extras) ||
            {};
        tileset._mosaicGeodesicExtras = extras;

        if (Math.abs(resolvedHeight) > 0.01) {
            tileset._mosaicHeightMode = "MANUAL_OVERRIDE";
            return _setTilesetHeightOffsetMeters(tileset, resolvedHeight);
        }

        // If the tileset's vertical geodesy has been verified/transformed (e.g. APPLIED_PROJ_GRID via
        // es_ign_egm08-rednap.tif / egm96_15.gtx or ALREADY_ELLIPSOIDAL RTK), preserve its exact
        // metric WGS84 ellipsoidal elevation with zero distortion.
        if (
            extras &&
            (extras.geodesicHeightVerified === true ||
                extras.verticalTransformApplied === true ||
                extras.verticalTransformStatus === "APPLIED_PROJ_GRID" ||
                extras.verticalTransformStatus === "ALREADY_ELLIPSOIDAL")
        ) {
            tileset._mosaicHeightMode = "GEODESIC_EXACT";
            return _setTilesetHeightOffsetMeters(tileset, 0);
        }

        // An unknown vertical datum is not permission to move real geometry
        // to a sampled terrain surface. Keep the source transform unchanged;
        // only an explicit user offset above may alter its elevation.
        tileset._mosaicHeightMode = "NATIVE_UNVERIFIED";
        return _setTilesetHeightOffsetMeters(tileset, 0);
    }

    function loadMvtTileset(viewer, urlTemplate, options) {
        if (!Cesium.MVTDataProvider || typeof Cesium.MVTDataProvider.fromUrl !== "function") {
            return Promise.reject(new Error("This CesiumJS build does not include the experimental MVT adapter."));
        }
        return Cesium.MVTDataProvider.fromUrl(urlTemplate, options || {}).then(function (provider) {
            if (!alive(viewer)) {
                if (provider && typeof provider.destroy === "function" && !provider.isDestroyed()) provider.destroy();
                throw new Error("Cesium viewer was replaced before the MVT layer finished loading.");
            }
            var primitive = provider.tileset || provider;
            viewer.scene.primitives.add(primitive);
            retainTerrainOverlay(viewer);
            counters.tilesetsCreated += 1;
            return { provider: provider, primitive: primitive };
        });
    }

    function retainTerrainOverlay(viewer) {
        if (!alive(viewer) || !viewer.scene || !viewer.scene.globe) return;
        var count = Number(viewer._mosaicTerrainOverlayCount || 0);
        if (count === 0) {
            viewer._mosaicPreviousDepthTest = viewer.scene.globe.depthTestAgainstTerrain;
        }
        viewer._mosaicTerrainOverlayCount = count + 1;

        // Surveyed geometry and MVT overlays can lie below the coarser world
        // terrain (notably dry reservoirs). Terrain LOD refinement must not
        // progressively bury them. Preserve their actual coordinates and keep
        // terrain relief; restore depth testing when the last overlay leaves.
        viewer.scene.globe.depthTestAgainstTerrain = false;
        viewer.scene.requestRender();
    }

    function releaseTerrainOverlay(viewer) {
        if (!alive(viewer) || !viewer.scene || !viewer.scene.globe) return;
        var count = Math.max(0, Number(viewer._mosaicTerrainOverlayCount || 0) - 1);
        viewer._mosaicTerrainOverlayCount = count;
        if (count === 0) {
            viewer.scene.globe.depthTestAgainstTerrain = !!viewer._mosaicPreviousDepthTest;
            delete viewer._mosaicPreviousDepthTest;
        }
        viewer.scene.requestRender();
    }

    function siblingAssetUrl(sourceUrl, relativePath) {
        var target = new global.URL(sourceUrl, global.location.href);
        var slash = target.pathname.lastIndexOf("/");
        target.pathname = target.pathname.substring(0, slash + 1) + String(relativePath || "").replace(/^\/+/, "");
        // URL serialisation may escape the placeholders required by the MVT
        // provider. Restore only these known tokens, never arbitrary input.
        return target.href
            .replace(/%7Bz%7D/ig, "{z}")
            .replace(/%7Bx%7D/ig, "{x}")
            .replace(/%7By%7D/ig, "{y}");
    }

    function fetchVectorManifest(sourceUrl) {
        var manifestUrl = siblingAssetUrl(sourceUrl, "viewer-manifest.json");
        // Private MosaicAssets use the application session for their protected
        // derivatives. Same-origin keeps that session without forwarding it to
        // any third-party URL.
        return global.fetch(manifestUrl, { credentials: "same-origin", cache: "no-store" }).then(function (response) {
            if (response.status === 404) return null; // Canonical GeoJSON needs no optional tiled manifest.
            if (!response.ok) throw new Error("Vector viewer manifest returned HTTP " + response.status + ".");
            return response.json();
        });
    }

    function loadManualRaster(viewer, manifestUrl, placement) {
        var state = placement || {};
        return global.fetch(manifestUrl, { credentials: "same-origin", cache: "no-store" }).then(function (response) {
            if (!response.ok) throw new Error("Raster placement manifest returned HTTP " + response.status + ".");
            return response.json();
        }).then(function (manifest) {
            if (!alive(viewer)) throw new Error("Cesium viewer was replaced before the raster preview loaded.");
            if (!manifest || manifest.activeRenderer !== "manual-raster" || !manifest.image) throw new Error("The manual raster manifest is invalid.");
            var imageUrl = siblingAssetUrl(manifestUrl, manifest.image);
            var entity = viewer.entities.add({
                rectangle: {
                    material: new Cesium.ImageMaterialProperty({ image: imageUrl, transparent: true }),
                    outline: true,
                    outlineColor: Cesium.Color.fromCssColorString("#99cc00"),
                    outlineWidth: 2
                }
            });
            var handle = { kind: "manual-raster", visual: entity, focusTarget: entity, manifest: manifest, focusRectangle: null };
            handle.updatePlacement = function (next) {
                state = next || state || {};
                var latitude = Number(state.latitude || 0);
                var longitude = Number(state.longitude || 0);
                var scale = Math.max(0.01, Number(state.scale || 1));
                var aspect = Math.max(0.01, Number(manifest.aspectRatio || 1));
                var widthMeters = Math.max(1, Number(manifest.defaultWidthMeters || 250) * scale);
                var heightMeters = widthMeters / aspect;
                var latitudeDelta = heightMeters / 110574;
                var longitudeDivisor = 111320 * Math.max(0.05, Math.cos(Cesium.Math.toRadians(latitude)));
                var longitudeDelta = widthMeters / longitudeDivisor;
                var rectangle = Cesium.Rectangle.fromDegrees(longitude - longitudeDelta / 2, latitude - latitudeDelta / 2, longitude + longitudeDelta / 2, latitude + latitudeDelta / 2);
                var rotation = Cesium.Math.toRadians(Number(state.heading || 0));
                entity.rectangle.coordinates = rectangle;
                entity.rectangle.rotation = rotation;
                entity.rectangle.stRotation = rotation;
                handle.focusRectangle = rectangle;
                if (viewer.scene) viewer.scene.requestRender();
                return rectangle;
            };
            handle.updatePlacement(state);
            return handle;
        });
    }

    async function prepareVectorDataSource(viewer, source) {
        // Very short ground-polyline segments produce long shader spikes in
        // Cesium 1.144. Keep every XY vertex and feature; render these 2D lines
        // on sampled terrain instead. This display offset is never a datum fix
        // and never applies to source geometry with native 3D heights.
        var lines = [], samples = [], time = Cesium.JulianDate.now();
        source.entities.values.forEach(function (entity) {
            var line = entity.polyline;
            if (!line || !line.clampToGround || !line.clampToGround.getValue(time)) return;
            var positions = line.positions && line.positions.getValue(time);
            if (!positions || positions.length < 2) return;
            for (var i = 1; i < positions.length; i++) {
                if (Cesium.Cartesian3.distance(positions[i - 1], positions[i]) > 1) return;
            }
            var points = positions.map(function (point) { return Cesium.Cartographic.fromCartesian(point); });
            if (points.some(function (point) { return Math.abs(point.height) > 0.001; })) return;
            lines.push({line: line, points: points});
            samples = samples.concat(points);
        });
        if (!samples.length) return source;
        var timer;
        await Promise.race([(async function () {
            if (viewer._mosaicTerrainReady) await viewer._mosaicTerrainReady;
            if (!alive(viewer)) throw new Error('The vector viewer was closed.');
            var provider = viewer.terrainProvider;
            if (provider.availability) await Cesium.sampleTerrainMostDetailed(provider, samples);
            else await Cesium.sampleTerrain(provider, 12, samples);
        })(), new Promise(function (_, reject) {
            timer = global.setTimeout(function () { reject(new Error('Terrain for this vector layer could not be loaded. Please retry.')); }, 30000);
        })]).finally(function () { global.clearTimeout(timer); });
        if (!alive(viewer)) throw new Error('The vector viewer was closed.');
        if (!samples.every(function (point) { return Number.isFinite(point.height); })) throw new Error('Terrain heights are unavailable for this vector layer.');
        lines.forEach(function (item) {
            item.line.clampToGround = false;
            item.line.arcType = Cesium.ArcType.NONE;
            item.line.positions = item.points.map(function (point) {
                return Cesium.Cartesian3.fromRadians(point.longitude, point.latitude, point.height + 0.3);
            });
        });
        source._mosaicSampledGroundLines = lines.length;
        source._mosaicSampledGroundVertices = samples.length;
        return source;
    }

    function loadGeoJsonVector(viewer, sourceUrl, metadata) {
        return Cesium.GeoJsonDataSource.load(sourceUrl, {
            clampToGround: true,
            stroke: Cesium.Color.fromCssColorString("#8fcb00"),
            fill: Cesium.Color.fromCssColorString("#8fcb00").withAlpha(0.24),
            strokeWidth: 3,
            markerColor: Cesium.Color.fromCssColorString("#8fcb00"),
            markerSize: 18
        }).then(async function (source) {
            await prepareVectorDataSource(viewer, source);
            styleVectorDataSource(source, metadata);
            addVectorDataSource(viewer, source, metadata);
            return { kind: "geojson", visual: source, focusTarget: source, manifest: null };
        });
    }

    function loadVectorAsset(viewer, sourceUrl, metadata) {
        var resolvedMetadata = metadata || {};
        function fallback(manifest, primaryError) {
            var fallbackUrl = manifest && manifest.fallbackDerivative
                ? siblingAssetUrl(sourceUrl, manifest.fallbackDerivative)
                : sourceUrl;
            return loadGeoJsonVector(viewer, fallbackUrl, resolvedMetadata).then(function (handle) {
                handle.manifest = manifest || null;
                handle.fallback = !!(manifest && manifest.activeRenderer !== "geojson-entities-safe");
                if (handle.fallback) {
                    console.warn("[MosaicEarth Cesium] tiled vector renderer unavailable; simplified preview enabled", {
                        assetKey: resolvedMetadata.assetKey || "unknown",
                        reason: primaryError && primaryError.message ? primaryError.message : String(primaryError || "")
                    });
                }
                return handle;
            });
        }

        return fetchVectorManifest(sourceUrl).then(function (manifest) {
            if (manifest && manifest.activeRenderer === "vector-raster-tiles" && manifest.raster && manifest.raster.urlTemplate) {
                var rasterBounds = manifest.raster.bounds || manifest.bounds;
                var rasterExtent = rasterBounds
                    ? Cesium.Rectangle.fromDegrees(Number(rasterBounds.west), Number(rasterBounds.south), Number(rasterBounds.east), Number(rasterBounds.north))
                    : undefined;
                var rasterUrl = siblingAssetUrl(sourceUrl, manifest.raster.urlTemplate);
                var rasterProvider = new Cesium.UrlTemplateImageryProvider({
                    url: rasterUrl,
                    rectangle: rasterExtent,
                    minimumLevel: Number(manifest.raster.minimumLevel || 0),
                    maximumLevel: Number(manifest.raster.maximumLevel || 22),
                    credit: "MosaicEarth optimized vector preview"
                });
                if (!alive(viewer)) throw new Error("Cesium viewer was replaced before the vector imagery layer loaded.");
                var layer = viewer.imageryLayers.addImageryProvider(rasterProvider);
                counters.imageryLayersCreated += 1;
                return Promise.resolve(layer).then(function (layer) {
                    return { kind: "vector-raster", visual: layer, focusTarget: layer, focusRectangle: rasterExtent, manifest: manifest };
                }).catch(function (error) {
                    return fallback(manifest, error);
                });
            }
            if (!manifest || manifest.activeRenderer !== "mvt" || !manifest.mvt || !manifest.mvt.urlTemplate) {
                return fallback(manifest, null);
            }
            var bounds = manifest.mvt.bounds || manifest.bounds;
            var extent = bounds
                ? Cesium.Rectangle.fromDegrees(Number(bounds.west), Number(bounds.south), Number(bounds.east), Number(bounds.north))
                : undefined;
            var mvtUrl = siblingAssetUrl(sourceUrl, manifest.mvt.urlTemplate);
            return loadMvtTileset(viewer, mvtUrl, {
                minZoom: Number(manifest.mvt.minZoom || 0),
                maxZoom: Number(manifest.mvt.maxZoom || 14),
                extent: extent
            }).then(function (loaded) {
                var primitive = loaded.primitive;
                if (primitive && Cesium.Cesium3DTileStyle) {
                    primitive.style = new Cesium.Cesium3DTileStyle({
                        color: "color('#9fda00', 0.96)",
                        pointSize: "12.0"
                    });
                }
                return { kind: "mvt", visual: primitive, focusTarget: primitive, focusRectangle: extent, provider: loaded.provider, manifest: manifest, terrainOverlayRetained: true };
            }).catch(function (error) {
                return fallback(manifest, error);
            });
        }).catch(function (manifestError) {
            // Legacy vector assets predate viewer-manifest.json. They remain
            // fully supported through the existing direct GeoJSON path.
            return fallback(null, manifestError);
        });
    }

    function removeVectorAsset(viewer, handle) {
        if (!handle) return false;
        if (!handle.kind && handle.entities) return removeRegisteredVectorAsset(viewer, handle);
        if (handle.kind === "geojson") return removeRegisteredVectorAsset(viewer, handle.visual);
        if (handle.kind === "vector-raster") return removeImageryLayer(viewer, handle.visual);
        if (handle.kind === "mvt") {
            if (handle.terrainOverlayRetained) {
                releaseTerrainOverlay(viewer);
                handle.terrainOverlayRetained = false;
            }
            return removePrimitive(viewer, handle.visual, "tileset");
        }
        return false;
    }

    function loadTmsLayer(viewer, url, options, index) {
        return Cesium.TileMapServiceImageryProvider.fromUrl(url, options || {}).then(function (provider) {
            if (!alive(viewer)) throw new Error("Cesium viewer was replaced before the imagery layer finished loading.");
            var layer = typeof index === "number"
                ? viewer.imageryLayers.addImageryProvider(provider, index)
                : viewer.imageryLayers.addImageryProvider(provider);
            counters.imageryLayersCreated += 1;
            return layer;
        });
    }

    function addModel(viewer, url, options) {
        var modelOptions = copyOptions(options);
        modelOptions.url = url;
        return Cesium.Model.fromGltfAsync(modelOptions).then(function (model) {
            if (!alive(viewer)) {
                if (model && typeof model.destroy === "function" && !model.isDestroyed()) model.destroy();
                throw new Error("Cesium viewer was replaced before the model finished loading.");
            }
            viewer.scene.primitives.add(model);
            retainTerrainOverlay(viewer);
            model._mosaicTerrainOverlayRetained = true;
            counters.modelsCreated += 1;
            return model;
        });
    }

    function registerVectorAsset(viewer, dataSource, metadata) {
        if (!alive(viewer) || !dataSource) return dataSource;
        viewer._mosaicVectorAssets = viewer._mosaicVectorAssets || [];
        dataSource._mosaicVectorMetadata = metadata || {};
        viewer._mosaicVectorAssets.push(dataSource);
        return dataSource;
    }

    function addVectorDataSource(viewer, dataSource, metadata) {
        if (!alive(viewer)) throw new Error("Cesium viewer was replaced before the vector layer finished loading.");
        if (!dataSource || !dataSource.entities) throw new Error("The vector layer did not produce a valid Cesium DataSource.");
        if (dataSource.clustering) dataSource.clustering.enabled = false;
        viewer.dataSources.add(dataSource);
        registerVectorAsset(viewer, dataSource, metadata);
        return dataSource;
    }

    function removeRegisteredVectorAsset(viewer, dataSource) {
        if (!alive(viewer) || !dataSource) return false;
        var removed = viewer.dataSources && viewer.dataSources.contains(dataSource)
            ? viewer.dataSources.remove(dataSource, true)
            : false;
        if (viewer._mosaicVectorAssets) {
            viewer._mosaicVectorAssets = viewer._mosaicVectorAssets.filter(function (item) { return item !== dataSource; });
        }
        return removed;
    }

    function isolateLatestVectorAsset(viewer, error) {
        if (!alive(viewer) || !viewer._mosaicVectorAssets || !viewer._mosaicVectorAssets.length) return null;
        var dataSource = viewer._mosaicVectorAssets[viewer._mosaicVectorAssets.length - 1];
        var metadata = dataSource._mosaicVectorMetadata || {};
        console.error("[MosaicEarth Cesium] isolated vector asset", {
            assetKey: metadata.assetKey || "unknown",
            sourceUrl: metadata.sourceUrl || "",
            reason: error && error.message ? error.message : String(error || "")
        });
        removeRegisteredVectorAsset(viewer, dataSource);
        return metadata.assetKey || "vector-layer";
    }

    function topDownPitch() {
        return -(Math.PI / 2 - 0.0001);
    }

    function topDownOffset(range, heading) {
        return new Cesium.HeadingPitchRange(Number(heading || 0), topDownPitch(), Math.max(Number(range || 0), 0));
    }

    function rangeToFitSphere(viewer, sphere, requestedRange) {
        var range = Math.max(Number(requestedRange || 0), 0);
        var frustum = viewer.camera && viewer.camera.frustum;
        if (!sphere || !(sphere.radius > 0) || !frustum || !(frustum.fovy > 0) || !(frustum.aspectRatio > 0)) return range;
        // Fixed radius multipliers crop geometry in portrait viewports. Fit the
        // sphere inside the narrower field of view, with room around the asset.
        var verticalHalf = frustum.fovy / 2;
        var horizontalHalf = Math.atan(Math.tan(verticalHalf) * frustum.aspectRatio);
        var halfAngle = Math.min(verticalHalf, horizontalHalf);
        return Math.max(range, sphere.radius / Math.sin(halfAngle) * 1.1);
    }

    function flyToTopDown(viewer, target, range, duration) {
        if (!alive(viewer) || !target) return Promise.resolve(false);
        if(Array.isArray(target) && target.length===1)target=target[0];
        var options = { offset: topDownOffset(rangeToFitSphere(viewer, target.boundingSphere, range), 0), duration: typeof duration === "number" ? duration : 0.8 };
        try {
            var sourceCamera = target.extras && target.extras.mosaicearthCamera;
            var validVector = function (v) { return Array.isArray(v) && v.length === 3 && v.every(function (n) { return typeof n === 'number' && isFinite(n) && Math.abs(n) <= 1e8; }); };
            if (sourceCamera && sourceCamera.version === 1 && sourceCamera.space === 'root-z-up' &&
                validVector(sourceCamera.position) && validVector(sourceCamera.direction) && validVector(sourceCamera.up) && target.root) {
                var modelFrame = Cesium.Matrix4.multiply(target.modelMatrix || Cesium.Matrix4.IDENTITY, target.root.transform || Cesium.Matrix4.IDENTITY, new Cesium.Matrix4());
                var eye = Cesium.Matrix4.multiplyByPoint(modelFrame, Cesium.Cartesian3.fromArray(sourceCamera.position), new Cesium.Cartesian3());
                var forward = Cesium.Matrix4.multiplyByPointAsVector(modelFrame, Cesium.Cartesian3.fromArray(sourceCamera.direction), new Cesium.Cartesian3());
                var up = Cesium.Matrix4.multiplyByPointAsVector(modelFrame, Cesium.Cartesian3.fromArray(sourceCamera.up), new Cesium.Cartesian3());
                Cesium.Cartesian3.normalize(forward, forward);
                Cesium.Cartesian3.normalize(up, up);
                // Display the reconstruction from a calibrated view rather than
                // imposing a geographic top-down view on an arbitrary SfM frame.
                viewer.camera.frustum.near = 0.01;
                return new Promise(function(resolve) {
                    viewer.camera.flyTo({destination: eye, orientation: {direction: forward, up: up}, duration: options.duration,
                        complete: function () { resolve(true); }, cancel: function () { resolve(false); }});
                });
            }
            // Camera metadata frames covered splats, without narrowing the real
            // culling bounds. The root transform includes current placement/scale.
            var focus = target.extras && target.extras.mosaicearthCapture;
            if (focus && focus.version === 1 && focus.space === 'root-z-up' &&
                Array.isArray(focus.center) && focus.center.length === 3 && focus.center.every(function (v) { return typeof v === 'number' && isFinite(v) && Math.abs(v) <= 1e8; }) &&
                typeof focus.radius === 'number' && isFinite(focus.radius) && focus.radius > 0 && focus.radius <= 1e7 && target.root && target.root.computedTransform) {
                var local = new Cesium.BoundingSphere(Cesium.Cartesian3.fromArray(focus.center), focus.radius);
                var placement = Cesium.Matrix4.multiply(target.modelMatrix || Cesium.Matrix4.IDENTITY, target.root.transform || Cesium.Matrix4.IDENTITY, new Cesium.Matrix4());
                var capture = Cesium.BoundingSphere.transform(local, placement, new Cesium.BoundingSphere());
                return new Promise(function (resolve) {
                    viewer.camera.flyToBoundingSphere(capture, {offset: topDownOffset(rangeToFitSphere(viewer, capture, Math.max(capture.radius * 2.6, .5)), 0), duration: options.duration,
                        complete: function () { resolve(true); }, cancel: function () { resolve(false); }});
                });
            }
            return Promise.resolve(viewer.flyTo(target, options));
        } catch (error) {
            console.error("[MosaicEarth Cesium] top-down framing failed", viewerDiagnostics(viewer, "top-down-frame", error));
            return Promise.resolve(false);
        }
    }

    async function flyToRectangleTopDown(viewer, rectangle, duration) {
        if (!alive(viewer) || !rectangle) return false;
        var sequence = (viewer._mosaicRectangleFlight || 0) + 1;
        viewer._mosaicRectangleFlight = sequence;
        var cancelled = false;
        var canvas = viewer.scene.canvas;
        var cancel = function () { cancelled = true; };
        canvas.addEventListener('pointerdown', cancel);
        canvas.addEventListener('wheel', cancel);
        function bounded(promise) {
            var timer;
            return Promise.race([promise, new Promise(function (_, reject) {
                timer = global.setTimeout(function () { reject(new Error('Terrain sampling timed out')); }, 10000);
            })]).finally(function () { global.clearTimeout(timer); });
        }
        try {
            // Rectangle framing alone measures height from the ellipsoid and
            // can put the camera *inside* elevated terrain for small rasters.
            // Raise only the camera; never change a master or its datum.
            if (viewer._mosaicTerrainReady) await bounded(viewer._mosaicTerrainReady).catch(function () { });
            if (!alive(viewer) || cancelled || viewer._mosaicRectangleFlight !== sequence) return false;
            var positions = [Cesium.Rectangle.center(rectangle), Cesium.Rectangle.southwest(rectangle),
                Cesium.Rectangle.southeast(rectangle), Cesium.Rectangle.northwest(rectangle), Cesium.Rectangle.northeast(rectangle)];
            var heights = positions.map(function (p) { return viewer.scene.globe.getHeight(p); }).filter(Number.isFinite);
            var provider = viewer.terrainProvider;
            try {
                var sampled = await bounded(provider.availability
                    ? Cesium.sampleTerrainMostDetailed(provider, positions)
                    : Cesium.sampleTerrain(provider, 12, positions));
                heights = heights.concat(sampled.map(function (p) { return p.height; }).filter(Number.isFinite));
            } catch (error) {
                console.warn('[MosaicEarth Cesium] using loaded terrain for camera framing', error.message);
            }
            if (!alive(viewer) || cancelled || viewer._mosaicRectangleFlight !== sequence) return false;
            var destination = viewer.camera.getRectangleCameraCoordinates(rectangle, new Cesium.Cartesian3());
            var cartographic = Cesium.Cartographic.fromCartesian(destination, Cesium.Ellipsoid.WGS84);
            var ground = heights.length ? Math.max.apply(null, heights) : 0;
            var clearance = Math.max(Number(cartographic.height || 0) * 1.18, heights.length ? 120 : 1200);
            cartographic.height = ground + clearance;
            viewer._mosaicRectangleFraming = {groundHeight: ground, clearance: clearance, terrainSamples: heights.length};
            destination = Cesium.Ellipsoid.WGS84.cartographicToCartesian(cartographic, new Cesium.Cartesian3());
            return new Promise(function (resolve) {
                viewer.camera.flyTo({
                    destination: destination,
                    orientation: { heading: 0, pitch: topDownPitch(), roll: 0 },
                    duration: typeof duration === "number" ? duration : 0.8,
                    complete: function () { resolve(true); },
                    cancel: function () { resolve(false); }
                });
            });
        } catch (error) {
            console.error("[MosaicEarth Cesium] extent framing failed", viewerDiagnostics(viewer, "extent-frame", error));
            return false;
        } finally {
            canvas.removeEventListener('pointerdown', cancel);
            canvas.removeEventListener('wheel', cancel);
        }
    }

    function focusVectorDataSource(viewer, source, duration) {
        if (!alive(viewer) || !source) return Promise.resolve(false);
        // Ground primitive bounding spheres can cover whole terrain tiles; a
        // zero-height vector can also frame the camera below the real ground.
        // Use the actual feature extent with the terrain-aware raster framing.
        var positions = [];
        var time = viewer.clock.currentTime;
        source.entities.values.forEach(function (entity) {
            var line = entity.polyline && entity.polyline.positions.getValue(time);
            if (line) line.forEach(function (position) { positions.push(position); });
            var polygon = entity.polygon && entity.polygon.hierarchy.getValue(time);
            if (polygon) polygon.positions.forEach(function (position) { positions.push(position); });
            var point = entity.position && entity.position.getValue(time);
            if (point) positions.push(point);
        });
        if (!positions.length) return flyToTopDown(viewer, source, 0, duration);
        var rectangle = Cesium.Rectangle.fromCartesianArray(positions);
        var padding = Cesium.Math.toRadians(0.00005);
        if (rectangle.width < padding) { rectangle.west -= padding; rectangle.east += padding; }
        if (rectangle.height < padding) { rectangle.south -= padding; rectangle.north += padding; }
        return flyToRectangleTopDown(viewer, rectangle, duration);
    }

    function focusVectorAsset(viewer, handle, duration) {
        if (!handle) return Promise.resolve(false);
        return handle.focusRectangle
            ? flyToRectangleTopDown(viewer, handle.focusRectangle, duration)
            : handle.kind === "geojson"
                ? focusVectorDataSource(viewer, handle.focusTarget || handle.visual, duration)
                : flyToTopDown(viewer, handle.focusTarget || handle.visual, 0, duration);
    }

    function removeImageryLayer(viewer, layer) {
        if (!alive(viewer) || !layer || !viewer.imageryLayers.contains(layer)) return false;
        var removed = viewer.imageryLayers.remove(layer, true);
        if (removed) counters.imageryLayersReleased += 1;
        return removed;
    }

    function removePrimitive(viewer, primitive, kind) {
        if (!alive(viewer) || !primitive || !viewer.scene.primitives.contains(primitive)) return false;
        var terrainOverlayRetained = primitive._mosaicTerrainOverlayRetained;
        var removed = viewer.scene.primitives.remove(primitive);
        if (removed) {
            if (terrainOverlayRetained) releaseTerrainOverlay(viewer);
            if (kind === "model") counters.modelsReleased += 1;
            else counters.tilesetsReleased += 1;
        }
        return removed;
    }

    function getFeatureMetadata(feature) {
        if (!feature || typeof feature.getPropertyIds !== "function") return null;
        var metadata = {};
        feature.getPropertyIds().forEach(function (name) {
            metadata[name] = feature.getProperty(name);
        });
        return metadata;
    }

    function enableMetadataPicking(viewer, onPicked) {
        if (!alive(viewer) || typeof onPicked !== "function") return null;
        var handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
        handler.setInputAction(function (movement) {
            if (!alive(viewer)) return;
            var feature = viewer.scene.pick(movement.position);
            var metadata = getFeatureMetadata(feature);
            if (!metadata || Object.keys(metadata).length === 0) return;
            onPicked(metadata, feature, movement.position);
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
        return {
            destroy: function () {
                if (!handler.isDestroyed()) handler.destroy();
            }
        };
    }

    function enableFeatureMetadataInspector(viewer, container) {
        if (!alive(viewer)) return null;
        var host = typeof container === "string" ? document.getElementById(container) : container;
        if (!host) host = viewer.container;
        var panel = document.createElement("section");
        panel.className = "mosaic-feature-metadata";
        panel.setAttribute("aria-live", "polite");
        panel.hidden = true;
        panel.style.cssText = "position:absolute;z-index:14;left:16px;bottom:16px;width:min(360px,calc(100% - 32px));max-height:45%;overflow:auto;padding:14px 16px;border:1px solid #405045;border-radius:12px;background:rgba(7,12,9,.94);color:#f5f7f5;box-shadow:0 12px 30px rgba(0,0,0,.34);font:14px/1.4 Arial,sans-serif;";
        var heading = document.createElement("div");
        heading.textContent = "Feature data";
        heading.style.cssText = "margin:0 32px 10px 0;color:#9de000;font-weight:700;letter-spacing:.02em;";
        var close = document.createElement("button");
        close.type = "button";
        close.setAttribute("aria-label", "Close feature data");
        close.textContent = "×";
        close.style.cssText = "position:absolute;right:8px;top:7px;width:30px;height:30px;border:0;background:transparent;color:#fff;font-size:24px;line-height:28px;cursor:pointer;";
        var list = document.createElement("dl");
        list.style.cssText = "display:grid;grid-template-columns:minmax(90px,.8fr) minmax(0,1.2fr);gap:7px 12px;margin:0;";
        panel.appendChild(heading);
        panel.appendChild(close);
        panel.appendChild(list);
        host.appendChild(panel);
        close.addEventListener("click", function () { panel.hidden = true; });
        var picking = enableMetadataPicking(viewer, function (metadata) {
            list.replaceChildren();
            Object.keys(metadata).sort().slice(0, 60).forEach(function (name) {
                var term = document.createElement("dt");
                var description = document.createElement("dd");
                var value = metadata[name];
                term.textContent = name;
                term.style.cssText = "margin:0;color:#9aa69e;overflow-wrap:anywhere;";
                if (value && typeof value === "object") {
                    try { value = JSON.stringify(value); } catch (ignore) { value = String(value); }
                }
                description.textContent = value == null ? "—" : String(value);
                description.style.cssText = "margin:0;color:#fff;overflow-wrap:anywhere;";
                list.appendChild(term);
                list.appendChild(description);
            });
            panel.hidden = false;
        });
        return {
            destroy: function () {
                if (picking) picking.destroy();
                if (panel.parentNode) panel.parentNode.removeChild(panel);
            }
        };
    }

    function styleVectorDataSource(dataSource, options) {
        if (!dataSource || !dataSource.entities) return dataSource;
        var resolved = options || {};
        var pointColor = resolved.pointColor || Cesium.Color.fromCssColorString("#a4df00");
        var showPointLabels = resolved.showPointLabels === true;
        var pointCount = 0;
        var pointSize = Math.max(10, Number(resolved.pointSize || 18));
        var pointScaleByDistance = new Cesium.NearFarScalar(50.0, 1.15, 2500000.0, 0.78);
        var pointPinBuilder = null;

        function createPointBillboard() {
            var image = resolved.pointImage || null;
            if (!image && Cesium.PinBuilder) {
                pointPinBuilder = pointPinBuilder || new Cesium.PinBuilder();
                image = pointPinBuilder.fromColor(pointColor, pointSize);
            }
            return new Cesium.BillboardGraphics({
                show: true,
                image: image || "/Resources/location.svg?v=4",
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                scaleByDistance: pointScaleByDistance
            });
        }

        dataSource.entities.values.forEach(function (entity) {
            var hasSurfaceOrLine = !!(
                entity.polygon || entity.polyline || entity.corridor || entity.rectangle ||
                entity.ellipse || entity.wall || entity.polylineVolume
            );

            // A polygon/line Placemark is geometry, not a map pin. Some source
            // formats carry a representative position as well; suppressing its
            // generated billboard avoids a second marker floating above every
            // feature while preserving the real geometry and its properties.
            if (hasSurfaceOrLine) {
                if (entity.billboard) entity.billboard.show = false;
                if (entity.point) entity.point.show = false;
                if (entity.label) entity.label.show = false;
                return;
            }

            if (!entity.position) return;
            pointCount += 1;
            // CesiumJS 1.144 intermittently fails to render PointGraphics that
            // use CLAMP_TO_GROUND, even though the entity and its position are
            // valid. Ground-clamped BillboardGraphics use Cesium's separate,
            // stable billboard path and remain visible over real terrain.
            // GeoJsonDataSource already creates a marker billboard for Point and
            // MultiPoint features; preserve it so every normalized vector format
            // (GPKG, KML/KMZ, GPX and GeoJSON) follows the same code path.
            if (!entity.billboard) entity.billboard = createPointBillboard();
            entity.billboard.show = true;
            entity.billboard.verticalOrigin = Cesium.VerticalOrigin.BOTTOM;
            entity.billboard.heightReference = Cesium.HeightReference.CLAMP_TO_GROUND;
            entity.billboard.disableDepthTestDistance = Number.POSITIVE_INFINITY;
            entity.billboard.scaleByDistance = pointScaleByDistance;
            if (entity.point) entity.point.show = false;
            if (entity.label && !showPointLabels) entity.label.show = false;
        });

        // EntityCluster in CesiumJS 1.144 can dereference an undefined entity id
        // while a large DataSource is stabilising. MosaicEarth deliberately keeps
        // per-feature clustering disabled here. Global discovery pins are clustered
        // separately in a controlled CustomDataSource whose entities all have ids.
        if (dataSource.clustering) dataSource.clustering.enabled = false;
        dataSource._mosaicVectorPointCount = pointCount;
        dataSource._mosaicVectorPointRenderer = pointCount ? "billboard-ground-clamped" : "none";
        return dataSource;
    }

    function createTerrainTileFetcher(signal) {
        var queue = [], active = 0;
        function pump() {
            if (signal.aborted) {
                queue.splice(0).forEach(function (item) { item.resolve(null); });
                return;
            }
            while (active < 6 && queue.length) {
                (function (item) {
                    active++;
                    global.fetch(item.url, {credentials: "same-origin", signal: signal})
                        .then(function (response) { return response.ok ? response.json() : null; })
                        .catch(function () { return null; })
                        .then(item.resolve)
                        .finally(function () { active--; pump(); });
                })(queue.shift());
            }
        }
        signal.addEventListener("abort", pump, {once: true});
        return function (url) {
            return new Promise(function (resolve) { queue.push({url: url, resolve: resolve}); pump(); });
        };
    }

    function applyLocalDemTerrain(viewer, gridUrlOrMode) {
        if (!alive(viewer)) return Promise.resolve(false);
        if (viewer._mosaicTerrainAbort) viewer._mosaicTerrainAbort.abort();
        var controller = new AbortController();
        viewer._mosaicTerrainAbort = controller;
        var request = (viewer._mosaicTerrainRequest || 0) + 1;
        viewer._mosaicTerrainRequest = request;
        if (!viewer._mosaicDefaultTerrainProvider) {
            viewer._mosaicDefaultTerrainProvider = viewer.terrainProvider;
        }
        if (!gridUrlOrMode || gridUrlOrMode === "default" || gridUrlOrMode === "off") {
            if (viewer._mosaicDefaultTerrainProvider) {
                viewer.terrainProvider = viewer._mosaicDefaultTerrainProvider;
            }
            viewer._mosaicActiveLocalTerrainUrl = null;
            if (viewer.scene) viewer.scene.requestRender();
            return Promise.resolve(true);
        }
        return global.fetch(gridUrlOrMode, { credentials: "same-origin", signal: controller.signal })
            .then(function (resp) { return resp.json(); })
            .then(function (grid) {
                if (!alive(viewer) || viewer._mosaicTerrainRequest !== request || !grid || !Array.isArray(grid.heights)) return false;
                var westRad = Cesium.Math.toRadians(Number(grid.west));
                var southRad = Cesium.Math.toRadians(Number(grid.south));
                var eastRad = Cesium.Math.toRadians(Number(grid.east));
                var northRad = Cesium.Math.toRadians(Number(grid.north));
                var demSpanLon = Math.max(1e-9, eastRad - westRad);
                var demSpanLat = Math.max(1e-9, northRad - southRad);
                var gw = Math.max(2, Number(grid.width) || 65);
                var gh = Math.max(2, Number(grid.height) || 65);
                var heights = grid.heights;
                var rootValidMask = Array.isArray(grid.valid_mask) ? grid.valid_mask : null;
                var geoidOffsetM = Number(grid.geoid_undulation_m || 0.0);
                var pyramid = Array.isArray(grid.pyramid) ? grid.pyramid : [];
                var maxPyrZ = Number(grid.max_pyramid_level || (pyramid.length > 0 ? pyramid.length - 1 : 0));
                var tileW = 65;
                var tileH = 65;
                var baseElevM = Number(grid.min_elevation_m || 0.0) + geoidOffsetM;
                var baseGlobalTp = viewer._mosaicDefaultTerrainProvider || null;
                var pendingSubTileFetches = {};
                var fetchTerrainTile = createTerrainTileFetcher(controller.signal);

                // Pre-sample a 17x17 global terrain reference collar around the DEM bbox (+50% margin)
                // so boundary tiles and NoData vertices seamlessly match the surrounding global relief!
                var collarW = 17;
                var collarH = 17;
                var collarWest = westRad - demSpanLon * 0.5;
                var collarEast = eastRad + demSpanLon * 0.5;
                var collarSouth = southRad - demSpanLat * 0.5;
                var collarNorth = northRad + demSpanLat * 0.5;
                var globalCollarHeights = new Float32Array(collarW * collarH);
                for (var ci = 0; ci < collarW * collarH; ci++) {
                    globalCollarHeights[ci] = baseElevM;
                }
                try {
                    var collarCartos = [];
                    for (var cr = 0; cr < collarH; cr++) {
                        var cLat = collarNorth - (cr / (collarH - 1)) * (collarNorth - collarSouth);
                        for (var cc = 0; cc < collarW; cc++) {
                            var cLon = collarWest + (cc / (collarW - 1)) * (collarEast - collarWest);
                            var carto = new Cesium.Cartographic(cLon, cLat, 0.0);
                            if (viewer.scene && viewer.scene.globe && typeof viewer.scene.globe.getHeight === "function") {
                                var ghGlobe = viewer.scene.globe.getHeight(carto);
                                if (Number.isFinite(ghGlobe)) {
                                    globalCollarHeights[cr * collarW + cc] = ghGlobe;
                                }
                            }
                            collarCartos.push(carto);
                        }
                    }
                    if (baseGlobalTp && typeof Cesium.sampleTerrain === "function") {
                        Cesium.sampleTerrain(baseGlobalTp, 11, collarCartos).then(function (sampled) {
                            if (!Array.isArray(sampled)) return;
                            for (var si = 0; si < sampled.length && si < globalCollarHeights.length; si++) {
                                if (sampled[si] && Number.isFinite(sampled[si].height)) {
                                    globalCollarHeights[si] = sampled[si].height;
                                }
                            }
                            if (alive(viewer) && viewer.scene) viewer.scene.requestRender();
                        }).catch(function () {});
                    }
                } catch (e) {}

                function bilinearArraySample(arr, w, h, uLocal, vLocal) {
                    var fx = Math.max(0, Math.min(w - 1, uLocal * (w - 1)));
                    var fy = Math.max(0, Math.min(h - 1, vLocal * (h - 1)));
                    var x0 = Math.max(0, Math.min(w - 1, Math.floor(fx)));
                    var y0 = Math.max(0, Math.min(h - 1, Math.floor(fy)));
                    var x1 = Math.min(w - 1, x0 + 1);
                    var y1 = Math.min(h - 1, y0 + 1);
                    var tx = fx - x0;
                    var ty = fy - y0;
                    var h00 = Number(arr[y0 * w + x0] || 0);
                    var h10 = Number(arr[y0 * w + x1] || 0);
                    var h01 = Number(arr[y1 * w + x0] || 0);
                    var h11 = Number(arr[y1 * w + x1] || 0);
                    return (1 - tx) * (1 - ty) * h00 + tx * (1 - ty) * h10 + (1 - tx) * ty * h01 + tx * ty * h11;
                }

                function sampleGlobalCollarBilinear(lonRad, latRad) {
                    var uC = Math.max(0.0, Math.min(1.0, (lonRad - collarWest) / Math.max(1e-9, collarEast - collarWest)));
                    var vC = Math.max(0.0, Math.min(1.0, (collarNorth - latRad) / Math.max(1e-9, collarNorth - collarSouth)));
                    return bilinearArraySample(globalCollarHeights, collarW, collarH, uC, vC);
                }

                function triggerOnDemandSubTileFetch(zLvl, txIdx, tyIdx, pyrEntry) {
                    var key = zLvl + "_" + txIdx + "_" + tyIdx;
                    if (pyrEntry && pyrEntry.tiles && pyrEntry.tiles[txIdx + "_" + tyIdx]) {
                        return Promise.resolve(pyrEntry.tiles[txIdx + "_" + tyIdx]);
                    }
                    if (pendingSubTileFetches[key]) return pendingSubTileFetches[key];
                    var tileUrl = gridUrlOrMode.indexOf("mode=terrain_grid") >= 0
                        ? gridUrlOrMode.replace("mode=terrain_grid", "mode=terrain_tile&z=" + zLvl + "&x=" + txIdx + "&y=" + tyIdx)
                        : gridUrlOrMode.replace(/elevation_grid\.json.*$/, "tiles/" + zLvl + "_" + txIdx + "_" + tyIdx + ".json");
                    var p = fetchTerrainTile(tileUrl)
                        .then(function (tDoc) {
                            if (!controller.signal.aborted && tDoc && Array.isArray(tDoc.heights) && pyrEntry) {
                                pyrEntry.tiles = pyrEntry.tiles || {};
                                pyrEntry.tiles[txIdx + "_" + tyIdx] = tDoc;
                                if (alive(viewer) && viewer.scene) viewer.scene.requestRender();
                            }
                            return tDoc;
                        })
                        .catch(function () { return null; });
                    pendingSubTileFetches[key] = p;
                    return p;
                }

                function sampleDemBilinear(lonRad, latRad, cesiumLevel) {
                    var uRaw = (lonRad - westRad) / demSpanLon;
                    var vRaw = (northRad - latRad) / demSpanLat;
                    var globalBgH = sampleGlobalCollarBilinear(lonRad, latRad);
                    var edgeDist = Math.max(0.0, -uRaw, uRaw - 1.0, -vRaw, vRaw - 1.0);
                    if (edgeDist > 0.25) {
                        return globalBgH;
                    }
                    var u = Math.max(0.0, Math.min(1.0, uRaw));
                    var v = Math.max(0.0, Math.min(1.0, vRaw));
                    var hVal = baseElevM;
                    var validWeight = 1.0;

                    if (pyramid.length > 0) {
                        var targetZ = Math.min(maxPyrZ, Math.max(0, (Number(cesiumLevel) || 15) - 13));
                        var selectedTile = null;
                        var selectedZ = targetZ;
                        for (var zTry = targetZ; zTry >= 0; zTry--) {
                            var pEntry = pyramid[zTry];
                            if (!pEntry) continue;
                            var nSplit = Math.max(1, Number(pEntry.tiles_per_axis) || (1 << zTry));
                            var txIdx = Math.min(nSplit - 1, Math.max(0, Math.floor(u * nSplit)));
                            var tyIdx = Math.min(nSplit - 1, Math.max(0, Math.floor(v * nSplit)));
                            if (pEntry.tiles && pEntry.tiles[txIdx + "_" + tyIdx] && Array.isArray(pEntry.tiles[txIdx + "_" + tyIdx].heights)) {
                                selectedTile = pEntry.tiles[txIdx + "_" + tyIdx];
                                selectedZ = zTry;
                                break;
                            } else if (zTry === targetZ && zTry > 2) {
                                triggerOnDemandSubTileFetch(zTry, txIdx, tyIdx, pEntry);
                            }
                        }
                        if (selectedTile) {
                            var nSp = 1 << selectedZ;
                            var txS = Math.min(nSp - 1, Math.max(0, Math.floor(u * nSp)));
                            var tyS = Math.min(nSp - 1, Math.max(0, Math.floor(v * nSp)));
                            var uSub = Math.max(0.0, Math.min(1.0, (u * nSp) - txS));
                            var vSub = Math.max(0.0, Math.min(1.0, (v * nSp) - tyS));
                            var sw = Math.max(2, Number(selectedTile.width) || 65);
                            var sh = Math.max(2, Number(selectedTile.height) || 65);
                            hVal = bilinearArraySample(selectedTile.heights, sw, sh, uSub, vSub) + geoidOffsetM;
                            if (Array.isArray(selectedTile.valid_mask)) {
                                validWeight = bilinearArraySample(selectedTile.valid_mask, sw, sh, uSub, vSub);
                            }
                        } else {
                            hVal = bilinearArraySample(heights, gw, gh, u, v) + geoidOffsetM;
                            if (rootValidMask) {
                                validWeight = bilinearArraySample(rootValidMask, gw, gh, u, v);
                            }
                        }
                    } else {
                        hVal = bilinearArraySample(heights, gw, gh, u, v) + geoidOffsetM;
                        if (rootValidMask) {
                            validWeight = bilinearArraySample(rootValidMask, gw, gh, u, v);
                        }
                    }

                    // Respect NoData mask: blend smoothly to global terrain height where valid_mask == 0
                    if (validWeight < 0.999) {
                        hVal = globalBgH + (hVal - globalBgH) * Math.max(0.0, Math.min(1.0, validWeight));
                    }
                    // Smooth C1 Hermite transition across the DEM boundary [0..0.25] into global terrain
                    if (edgeDist > 0.0) {
                        var t = Math.max(0.0, Math.min(1.0, edgeDist / 0.25));
                        var smoothStep = t * t * (3.0 - 2.0 * t);
                        return hVal * (1.0 - smoothStep) + globalBgH * smoothStep;
                    }
                    return hVal;
                }

                if (typeof Cesium.CustomHeightmapTerrainProvider === "function") {
                    var customTp = new Cesium.CustomHeightmapTerrainProvider({
                        tilingScheme: baseGlobalTp ? baseGlobalTp.tilingScheme : new Cesium.GeographicTilingScheme(),
                        credit: baseGlobalTp ? baseGlobalTp.credit : undefined,
                        width: tileW,
                        height: tileH,
                        callback: function (x, y, level) {
                            var out = new Float32Array(tileW * tileH);
                            var tilingScheme = customTp.tilingScheme;
                            var rect = tilingScheme.tileXYToRectangle(x, y, level);
                            var projection = tilingScheme.projection;
                            var northY = projection.project(new Cesium.Cartographic(0, rect.north)).y;
                            var southY = projection.project(new Cesium.Cartographic(0, rect.south)).y;
                            for (var r = 0; r < tileH; r++) {
                                var latR = projection.unproject(new Cesium.Cartesian3(0, northY - (r / (tileH - 1)) * (northY - southY), 0)).latitude;
                                for (var c = 0; c < tileW; c++) {
                                    var lonR = rect.west + (c / (tileW - 1)) * (rect.east - rect.west);
                                    out[r * tileW + c] = sampleDemBilinear(lonR, latR, level);
                                }
                            }
                            return out;
                        }
                    });
                    // Composite Terrain Provider:
                    // 1. Delegate tiles completely outside the local DEM bbox directly to baseGlobalTp.
                    // 2. Delegate low-zoom regional tiles (where the tile span is > 2.5x the local DEM span)
                    //    directly to baseGlobalTp so a small 300m DEM NEVER flattens a 10km regional tile!
                    // 3. Await any needed on-demand high-zoom sub-tiles (z >= 3) before generating the heightmap
                    //    so the very first render of a close-up tile already uses full z=3..maxPyrZ resolution!
                    if (typeof customTp.requestTileGeometry === "function") {
                        var origLocalRequestTileGeometry = customTp.requestTileGeometry.bind(customTp);
                        customTp.requestTileGeometry = function (x, y, level, request) {
                            try {
                                var rect = customTp.tilingScheme.tileXYToRectangle(x, y, level);
                                var intersectsLocalDem = !(
                                    rect.east < westRad ||
                                    rect.west > eastRad ||
                                    rect.north < southRad ||
                                    rect.south > northRad
                                );
                                if (!intersectsLocalDem) {
                                    if (baseGlobalTp && typeof baseGlobalTp.requestTileGeometry === "function") {
                                        return baseGlobalTp.requestTileGeometry(x, y, level, request);
                                    }
                                }
                                var tileSpanLon = rect.east - rect.west;
                                var tileSpanLat = rect.north - rect.south;
                                if (tileSpanLon > demSpanLon * 2.5 || tileSpanLat > demSpanLat * 2.5) {
                                    if (baseGlobalTp && typeof baseGlobalTp.requestTileGeometry === "function") {
                                        return baseGlobalTp.requestTileGeometry(x, y, level, request);
                                    }
                                }
                                var targetZ = Math.min(maxPyrZ, Math.max(0, (Number(level) || 15) - 13));
                                if (intersectsLocalDem && targetZ >= 3 && pyramid[targetZ]) {
                                    var pEntry = pyramid[targetZ];
                                    var nSp = Math.max(1, Number(pEntry.tiles_per_axis) || (1 << targetZ));
                                    var u0 = Math.max(0.0, Math.min(0.999999, (rect.west - westRad) / demSpanLon));
                                    var u1 = Math.max(0.0, Math.min(0.999999, (rect.east - westRad) / demSpanLon));
                                    var v0 = Math.max(0.0, Math.min(0.999999, (northRad - rect.north) / demSpanLat));
                                    var v1 = Math.max(0.0, Math.min(0.999999, (northRad - rect.south) / demSpanLat));
                                    var tx0 = Math.max(0, Math.floor(Math.min(u0, u1) * nSp));
                                    var tx1 = Math.min(nSp - 1, Math.floor(Math.max(u0, u1) * nSp));
                                    var ty0 = Math.max(0, Math.floor(Math.min(v0, v1) * nSp));
                                    var ty1 = Math.min(nSp - 1, Math.floor(Math.max(v0, v1) * nSp));
                                    var neededPromises = [];
                                    for (var tyI = ty0; tyI <= ty1; tyI++) {
                                        for (var txI = tx0; txI <= tx1; txI++) {
                                            if (!pEntry.tiles || !pEntry.tiles[txI + "_" + tyI]) {
                                                neededPromises.push(triggerOnDemandSubTileFetch(targetZ, txI, tyI, pEntry));
                                            }
                                        }
                                    }
                                    if (neededPromises.length > 0) {
                                        return Promise.all(neededPromises).then(function () {
                                            return origLocalRequestTileGeometry(x, y, level, request);
                                        });
                                    }
                                }
                            } catch (e) {}
                            return origLocalRequestTileGeometry(x, y, level, request);
                        };
                        customTp.getTileDataAvailable = function (x, y, level) {
                            try {
                                var rect = customTp.tilingScheme.tileXYToRectangle(x, y, level);
                                var intersectsLocalDem = !(
                                    rect.east < westRad ||
                                    rect.west > eastRad ||
                                    rect.north < southRad ||
                                    rect.south > northRad
                                );
                                if (intersectsLocalDem && level <= 22) {
                                    return true;
                                }
                                if (baseGlobalTp && typeof baseGlobalTp.getTileDataAvailable === "function") {
                                    return baseGlobalTp.getTileDataAvailable(x, y, level);
                                }
                            } catch (e) {}
                            return level <= 18;
                        };
                    }
                    viewer.terrainProvider = customTp;
                    viewer._mosaicActiveLocalTerrainUrl = gridUrlOrMode;
                    viewer._mosaicLocalTerrainPyramidLevels = maxPyrZ + 1;
                    viewer._mosaicLocalTerrainComposite = Boolean(baseGlobalTp);
                    if (viewer.scene) viewer.scene.requestRender();
                    return true;
                }
                return false;
            })
            .catch(function () { return false; });
    }

    async function loadXyzRaster(viewer, url, boundsUrl) {
        var response = await fetch(boundsUrl, {credentials: 'same-origin'});
        if (!response.ok) throw new Error('The raster bounds could not be loaded.');
        var bounds = await response.json();
        var coordinates = [bounds.west, bounds.south, bounds.east, bounds.north];
        if (!coordinates.every(Number.isFinite)) throw new Error('The raster bounds are invalid.');
        var rectangle = Cesium.Rectangle.fromDegrees.apply(null, coordinates);
        var layer = viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({
            url: url, rectangle: rectangle, maximumLevel: Number.isInteger(bounds.max_zoom) ? bounds.max_zoom : 20,
            minimumLevel: Number.isInteger(bounds.min_zoom) ? bounds.min_zoom : 0
        }));
        await flyToRectangleTopDown(viewer, rectangle, 0.8);
        return layer;
    }

    function diagnostics() {
        var snapshot = {};
        Object.keys(counters).forEach(function (key) { snapshot[key] = counters[key]; });
        snapshot.activeViewers = counters.viewersCreated - counters.viewersDestroyed;
        snapshot.activeTilesets = counters.tilesetsCreated - counters.tilesetsReleased;
        snapshot.activeImageryLayers = counters.imageryLayersCreated - counters.imageryLayersReleased;
        snapshot.activeModels = counters.modelsCreated - counters.modelsReleased;
        snapshot.cesiumVersion = Cesium.VERSION;
        return snapshot;
    }

    global.MosaicCesiumRuntime = {
        markerImage: function (typeId) { return mapMarkerImage(typeId); },
        clusterImage: function (count) { return mapMarkerImage(0, count); },
        alive: alive,
        currentViewer: currentViewer,
        createBaseProvider: createBaseProvider,
        createViewer: createViewer,
        configureViewer: configureViewer,
        destroyViewer: destroyViewer,
        setBaseLayer: setBaseLayer,
        switchBaseImagery: setBaseLayer,
        captureCameraState: captureCameraState,
        encodeCameraState: encodeCameraState,
        decodeCameraState: decodeCameraState,
        cameraStateFromUrl: cameraStateFromUrl,
        restoreCameraState: restoreCameraState,
        urlWithCameraState: urlWithCameraState,
        loadTileset: loadTileset,
        applyProcessedTilesetHeight: applyProcessedTilesetHeight,
        loadMvtTileset: loadMvtTileset,
        loadVectorAsset: loadVectorAsset,
        loadManualRaster: loadManualRaster,
        loadTmsLayer: loadTmsLayer,
        loadXyzRaster: loadXyzRaster,
        addModel: addModel,
        addVectorDataSource: addVectorDataSource,
        removeVectorDataSource: removeRegisteredVectorAsset,
        removeVectorAsset: removeVectorAsset,
        topDownOffset: topDownOffset,
        flyToTopDown: flyToTopDown,
        flyToRectangleTopDown: flyToRectangleTopDown,
        focusVectorAsset: focusVectorAsset,
        focusVectorDataSource: focusVectorDataSource,
        showViewerError: showViewerError,
        hideViewerError: hideViewerError,
        removeImageryLayer: removeImageryLayer,
        removePrimitive: removePrimitive,
        getFeatureMetadata: getFeatureMetadata,
        styleVectorDataSource: styleVectorDataSource,
        prepareVectorDataSource: prepareVectorDataSource,
        enableMetadataPicking: enableMetadataPicking,
        enableFeatureMetadataInspector: enableFeatureMetadataInspector,
        applyLocalDemTerrain: applyLocalDemTerrain,
        diagnostics: diagnostics
    };
})(window);
