(() => {
    const form = document.querySelector("[data-asset-upload]");
    if (!form) return;

    const project = form.elements.project_id.value;
    const origin = form.elements.source_origin.value;
    const input = form.elements.file;
    const base = "/api/v2/tasks/" + project + "/asset-uploads";
    const key = "mosaicearth:upload:" + project;
    const myearthUrl = form.dataset.myearthUrl || ("/en/Projects?open_project=" + project);
    const status = document.getElementById("asset-upload-status");
    const selection = document.getElementById("asset-upload-selection");
    const meter = document.getElementById("asset-upload-progress");
    const submit = form.querySelector("button[type=submit]");
    const pause = document.getElementById("asset-upload-pause");
    const cancel = document.getElementById("asset-upload-cancel");
    const result = document.getElementById("asset-upload-result");
    const retry = document.getElementById("asset-upload-retry");
    const dropzone = document.getElementById("unifiedDropzone");
    const fieldsWrap = document.getElementById("unifiedUploadFieldsWrap");
    const georefBadge = document.getElementById("georefStatusBadge");
    const latInput = document.getElementById("upload-lat") || form.elements.lat;
    const lonInput = document.getElementById("upload-lon") || form.elements.lon;
    const placeSearchInput = document.getElementById("mapPlaceSearchInput");
    const placeSearchBtn = document.getElementById("btnMapPlaceSearch");
    const gcpStudioBtn = document.getElementById("btnOpenGaussianGcpStudio");
    const gcpBadge = document.getElementById("gaussianGcpBadge");

    const model = /\.(ply|spz|splat|ksplat)$/i;
    const telemetryExt = /\.(srt|gpx|csv|jgw|tfw|pgw|wld|prj)$/i;
    let uploadId;
    let active = false;
    let processingLocked = false;
    let paused = false;
    let request;
    let quoteReady = false;
    let hasDetectedGeoref = false;
    let gcpCount = Number(form.dataset.gcpCount || 0);
    let locMap = null;
    let locMarker = null;
    let gcpStudioPopup = null;

    const describe = file => ({name: file.name, size: file.size});
    const mib = bytes => (bytes / (1024 ** 2)).toFixed(1) + " MiB";
    const message = text => { if (status) status.textContent = text; };

    window.addEventListener("beforeunload", event => {
        if (!active) return;
        event.preventDefault();
        event.returnValue = "";
    });

    function setGeorefBadge(text, tone) {
        if (!georefBadge) return;
        georefBadge.textContent = text;
        if (tone === "ok") {
            georefBadge.style.background = "rgba(153,204,0,0.18)";
            georefBadge.style.borderColor = "#99CC00";
            georefBadge.style.color = "#d9f99d";
        } else if (tone === "warn") {
            georefBadge.style.background = "rgba(245,158,11,0.18)";
            georefBadge.style.borderColor = "#f59e0b";
            georefBadge.style.color = "#fde68a";
        } else {
            georefBadge.style.background = "#18241d";
            georefBadge.style.borderColor = "#3c5244";
            georefBadge.style.color = "#cbd5e1";
        }
    }

    function parseCoord(val, limit) {
        if (val == null || String(val).trim() === "") return null;
        const n = Number(String(val).trim().replace(",", "."));
        if (!Number.isFinite(n) || Math.abs(n) > limit) return null;
        return n;
    }

    function placeMapPin(lat, lon, fly = true) {
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
        if (latInput) latInput.value = Number(lat).toFixed(6);
        if (lonInput) lonInput.value = Number(lon).toFixed(6);
        if (!locMap || !window.L) return;
        if (!locMarker) {
            locMarker = window.L.marker([lat, lon], {draggable: true}).addTo(locMap);
            locMarker.on("dragend", () => {
                const pos = locMarker.getLatLng();
                if (latInput) latInput.value = pos.lat.toFixed(6);
                if (lonInput) lonInput.value = pos.lng.toFixed(6);
                setGeorefBadge("✓ Initial approximate location placed on map", "ok");
            });
        } else {
            locMarker.setLatLng([lat, lon]);
        }
        if (fly) locMap.setView([lat, lon], Math.max(locMap.getZoom(), 16));
    }

    function initLocationMap() {
        const host = document.getElementById("uploadLocationMap");
        if (!host) return;
        if (!window.L) {
            setTimeout(initLocationMap, 120);
            return;
        }
        if (locMap) return;
        const initLat = parseCoord(latInput?.value, 90);
        const initLon = parseCoord(lonInput?.value, 180);
        const hasInit = initLat !== null && initLon !== null && (Math.abs(initLat) > 1e-5 || Math.abs(initLon) > 1e-5);
        locMap = window.L.map(host, {
            center: hasInit ? [initLat, initLon] : [30.0, 5.0],
            zoom: hasInit ? 16 : 2,
            zoomControl: true,
        });
        const sat = window.L.tileLayer(
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            {maxZoom: 20, attribution: "Tiles &copy; Esri"}
        );
        const osm = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 20,
            attribution: "&copy; OpenStreetMap",
        });
        sat.addTo(locMap);
        window.L.control.layers({"Satellite": sat, "Streets": osm}, null, {position: "bottomleft"}).addTo(locMap);
        if (hasInit) {
            placeMapPin(initLat, initLon, false);
        }
        locMap.on("click", ev => {
            if (active || processingLocked) return;
            placeMapPin(ev.latlng.lat, ev.latlng.lng, false);
            setGeorefBadge("✓ Initial approximate location set on map (refinable in MyEarth)", "ok");
        });
    }

    async function searchMapPlace() {
        const q = (placeSearchInput?.value || "").trim();
        if (!q || !locMap) return;
        try {
            const r = await fetch("https://nominatim.openstreetmap.org/search?format=json&limit=1&q=" + encodeURIComponent(q));
            const arr = await r.json();
            if (Array.isArray(arr) && arr[0]) {
                const lat = Number(arr[0].lat);
                const lon = Number(arr[0].lon);
                locMap.setView([lat, lon], 17);
                if (!hasDetectedGeoref) {
                    placeMapPin(lat, lon, false);
                    setGeorefBadge("✓ Initial approximate location set from search", "ok");
                }
            }
        } catch (_) {}
    }

    placeSearchBtn?.addEventListener("click", searchMapPlace);
    placeSearchInput?.addEventListener("keydown", ev => {
        if (ev.key === "Enter") {
            ev.preventDefault();
            searchMapPlace();
        }
    });
    latInput?.addEventListener("change", () => {
        const la = parseCoord(latInput.value, 90);
        const lo = parseCoord(lonInput?.value, 180);
        if (la !== null && lo !== null) placeMapPin(la, lo, true);
    });
    lonInput?.addEventListener("change", () => {
        const la = parseCoord(latInput?.value, 90);
        const lo = parseCoord(lonInput.value, 180);
        if (la !== null && lo !== null) placeMapPin(la, lo, true);
    });

    if (dropzone) {
        ["dragenter", "dragover"].forEach(evName => {
            dropzone.addEventListener(evName, ev => {
                ev.preventDefault();
                if (!active && !processingLocked) dropzone.classList.add("is-dragover");
            });
        });
        ["dragleave", "drop"].forEach(evName => {
            dropzone.addEventListener(evName, ev => {
                ev.preventDefault();
                dropzone.classList.remove("is-dragover");
            });
        });
        dropzone.addEventListener("drop", ev => {
            if (active || processingLocked) return;
            const dropped = ev.dataTransfer?.files;
            if (!dropped || !dropped.length) return;
            try {
                const dt = new DataTransfer();
                Array.from(dropped).forEach(f => dt.items.add(f));
                input.files = dt.files;
                selectionChanged();
            } catch (_) {}
        });
    }

    if (gcpStudioBtn) {
        gcpStudioBtn.addEventListener("click", () => {
            const url = "/GCPStudio.aspx?task_id=" + encodeURIComponent(project) + "&mode=gaussian";
            gcpStudioPopup = window.open(url, "mosaicearth_gcp_studio", "width=1420,height=900,resizable=yes,scrollbars=yes");
        });
        window.addEventListener("message", ev => {
            if (ev.origin !== window.location.origin || !ev.data) return;
            if (ev.data.type === "mosaicearth:gcp-studio-ready" && String(ev.data.taskId) === String(project)) {
                const imgFiles = Array.from(input.files || []).filter(f => /\.(jpe?g|png|webp)$/i.test(f.name));
                if (imgFiles.length && gcpStudioPopup && !gcpStudioPopup.closed) {
                    gcpStudioPopup.postMessage({type: "mosaicearth:local-images", files: imgFiles}, window.location.origin);
                }
            } else if (ev.data.type === "mosaicearth:gcp-attached" && String(ev.data.taskId) === String(project)) {
                gcpCount = Number(ev.data.gcpCount || 0);
                form.dataset.gcpCount = String(gcpCount);
                if (gcpBadge) {
                    gcpBadge.textContent = "✓ " + gcpCount + " Metric GCPs Attached";
                    gcpBadge.style.background = "rgba(153,204,0,0.18)";
                    gcpBadge.style.borderColor = "#99CC00";
                    gcpBadge.style.color = "#d9f99d";
                }
                if (ev.data.centroid && Number.isFinite(ev.data.centroid.lat) && Number.isFinite(ev.data.centroid.lon)) {
                    hasDetectedGeoref = true;
                    placeMapPin(ev.data.centroid.lat, ev.data.centroid.lon, true);
                    setGeorefBadge("✓ Metric GCP Control Attached (" + gcpCount + " GCPs · " + (ev.data.crs || "WGS84") + ")", "ok");
                }
            }
        });
    }

    async function looksLikePretrainedGaussianZip(file) {
        if (!/\.zip$/i.test(file.name)) return false;
        try {
            const tailStart = Math.max(0, file.size - 262144);
            const buf = await file.slice(tailStart, file.size).arrayBuffer();
            const ascii = new TextDecoder("latin1").decode(new Uint8Array(buf));
            if (/\.(splat|spz|ksplat)/i.test(ascii)) return true;
            if (/\.ply/i.test(ascii) && !/\.(jpe?g|png|mp4|mov|webm)/i.test(ascii)) return true;
        } catch (_) {}
        return false;
    }

    async function inspectSelectionGeoref(files) {
        if (!files.length) return;
        const primary = files.find(f => telemetryExt.test(f.name)) || files[0];
        if (!primary) return;
        try {
            const fd = new FormData();
            const maxInspect = 20 * 1024 * 1024;
            const sliceBlob = primary.size > maxInspect ? primary.slice(0, maxInspect) : primary;
            fd.append("file", sliceBlob, primary.name);
            fd.append("filename", primary.name);
            const resp = await fetch("/api/v2/uploads/inspect-georef", {
                method: "POST",
                body: fd,
                credentials: "same-origin",
            });
            if (!resp.ok) return;
            const info = await resp.json();
            if (input.files[0] !== files[0]) return;
            if (info && info.georeferenced && Number.isFinite(info.lat) && Number.isFinite(info.lon)) {
                hasDetectedGeoref = true;
                placeMapPin(Number(info.lat), Number(info.lon), true);
                setGeorefBadge("✓ Georeferenced automatically (" + (info.source || info.crs || "WGS84") + ")", "ok");
            } else if (gcpCount >= 3) {
                hasDetectedGeoref = true;
                setGeorefBadge("✓ Georeferenced via " + gcpCount + " attached GCPs", "ok");
            } else {
                hasDetectedGeoref = false;
                const curLat = parseCoord(latInput?.value, 90);
                const curLon = parseCoord(lonInput?.value, 180);
                if (curLat !== null && curLon !== null) {
                    setGeorefBadge("✓ Using selected map pin (" + curLat.toFixed(4) + ", " + curLon.toFixed(4) + ")", "ok");
                } else {
                    setGeorefBadge("📍 Unreferenced — Click on the map below to set initial approximate location", "warn");
                }
            }
        } catch (_) {}
    }

    function setFormLocked(locked) {
        processingLocked = locked;
        if (fieldsWrap) fieldsWrap.classList.toggle("me-upload-state-locked", locked);
        input.disabled = locked || active;
        if (submit) submit.disabled = locked || active || !quoteReady;
    }

    function savedUpload() {
        try { return JSON.parse(localStorage.getItem(key)); }
        catch (_) { return null; }
    }

    async function jsonResponse(response) {
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw Object.assign(new Error(
            typeof data.detail === "string" ? data.detail : "Request failed (" + response.status + ")."
        ), {status: response.status});
        return data;
    }

    async function call(path, options) {
        const signal = AbortSignal.timeout(30000);
        return jsonResponse(await fetch(path, {...options, signal, cache: "no-store"}));
    }

    function reportBytes(done, total) {
        meter.hidden = false;
        meter.max = total;
        meter.value = done;
        const pct = total > 0 ? Math.floor((100 * done) / total) : 100;
        message("Uploading " + mib(done) + " / " + mib(total) + " (" + pct + "%) — keep this tab open until upload reaches 100%.");
    }

    function transmit(path, bytes, digest, onProgress) {
        return new Promise((resolve, reject) => {
            request = new XMLHttpRequest();
            request.open("PUT", path);
            request.timeout = 45000;
            request.setRequestHeader("X-Content-SHA256", digest);
            request.upload.onprogress = event => onProgress(event.loaded);
            request.onload = () => {
                let data;
                try { data = JSON.parse(request.responseText); } catch (_) { data = {}; }
                if (request.status >= 200 && request.status < 300) resolve(data);
                else reject(new Error(data.detail || "Upload failed (" + request.status + ")."));
            };
            request.onerror = request.ontimeout = () => reject(new Error("Connection interrupted."));
            request.onabort = () => reject(new Error("Upload paused."));
            request.send(bytes);
        });
    }

    async function selectionChanged() {
        const files = Array.from(input.files);
        quoteReady = false;
        submit.disabled = true;
        if (!files.length) {
            selection.textContent = "";
            submit.textContent = "SELECT FILES TO CONTINUE";
            if (form.elements.quality_preset) form.elements.quality_preset.disabled = false;
            return;
        }
        const nonTelem = files.filter(f => !telemetryExt.test(f.name));
        const mainFile = nonTelem[0] || files[0];
        const pretrainedZip = origin === "gaussian" && files.length === 1 && await looksLikePretrainedGaussianZip(mainFile);
        const directImport = origin !== "gaussian" || model.test(mainFile.name) || pretrainedZip;
        submit.textContent = directImport ? "UPLOAD & IMPORT" : "UPLOAD & GENERATE GAUSSIAN SPLAT";
        if (form.elements.quality_preset) form.elements.quality_preset.disabled = directImport;
        const summary = files.map(f => f.name + " (" + mib(f.size) + ")").join(", ");
        selection.textContent = summary;
        inspectSelectionGeoref(files);
        if (directImport) {
            selection.textContent += " · Direct import · €0.00 additional charge";
            quoteReady = true;
        } else {
            try {
                const quote = await call(base + "/quote");
                if (input.files[0] !== files[0]) return;
                selection.textContent += " · COLMAP + OpenSplat · Maximum Quality · PLY, SPZ and 3D Tiles · " + quote.label;
                quoteReady = quote.engine_available;
                if (!quote.engine_available) message(quote.local ? "Local COLMAP and OpenSplat are unavailable. Check the Desktop engine installation." : "Cloud training is currently unavailable.");
                else if (quote.local) message("Processing runs on this computer at Maximum Quality. No cloud credits are consumed.");
                else if (!quote.covered_by_plan) message("Upload the source, then confirm the quoted price in secure Checkout. Processing starts only after payment is verified.");
            } catch (error) {
                message(error.message);
            }
        }
        submit.disabled = !quoteReady;
    }

    async function watch(jobIdOrIds, assetId) {
        const ids = (Array.isArray(jobIdOrIds) ? jobIdOrIds : [jobIdOrIds]).map(Number).filter(n => n > 0);
        if (!ids.length) return;
        setFormLocked(true);
        meter.hidden = false;
        meter.max = 100;
        meter.value = 5;
        message("Job #" + ids[0] + (ids.length > 1 ? " (+" + (ids.length - 1) + " more)" : "") + " queued in background...");
        while (true) {
            try {
                const statuses = await Promise.all(ids.map(id => call("/api/v2/jobs/status?job_id=" + id)));
                const jobs = statuses.map(s => s.job || {});
                const failedJob = jobs.find(j => ["FAILED", "CANCELED"].includes(j.STATUS));
                const allCompleted = jobs.every(j => j.STATUS === "COMPLETED");
                const avgPct = Math.round(jobs.reduce((acc, j) => acc + Number(j.PROGRESS || (j.STATUS === "COMPLETED" ? 100 : 10)), 0) / jobs.length);
                meter.hidden = false;
                meter.max = 100;
                meter.value = allCompleted ? 100 : Math.max(5, Math.min(99, avgPct));
                const lead = jobs.find(j => !["COMPLETED", "FAILED", "CANCELED"].includes(j.STATUS)) || jobs[0];
                const payment = lead.PAYMENT_STATUS ? " · " + lead.PAYMENT_STATUS.toLowerCase().replaceAll("_", " ") : "";
                message("Job #" + (lead.ID || ids[0]) + " · " + lead.STATUS + " (" + meter.value + "%) · " + (lead.ERROR || lead.STAGE || "") + payment);

                if (failedJob) {
                    setFormLocked(false);
                    if (retry && savedUpload()) retry.hidden = false;
                    return;
                }
                if (allCompleted) {
                    localStorage.removeItem(key);
                    meter.value = 100;
                    message("Job #" + ids[0] + " · COMPLETED · 100% — Returning to MyEarth...");
                    if (result) {
                        result.textContent = "Open result";
                        result.href = myearthUrl;
                        result.hidden = false;
                    }
                    setTimeout(() => {
                        window.location.href = myearthUrl;
                    }, 550);
                    return;
                }
            } catch (error) {
                message("Job #" + ids[0] + " · " + error.message + " Retrying status.");
                if (error.status === 401 || error.status === 404) {
                    setFormLocked(false);
                    return;
                }
            }
            await new Promise(resolve => setTimeout(resolve, 2500));
        }
    }

    input.addEventListener("change", selectionChanged);
    retry?.addEventListener("click", async () => {
        const saved = savedUpload();
        if (!saved) return;
        retry.disabled = true;
        try {
            const upload = await call(base + "/" + saved.id + "/retry", {method: "POST"});
            retry.hidden = true;
            if (!offerCheckout(upload)) await watch(upload.job_ids || upload.job_id, upload.asset_id);
        } catch (error) { message(error.message); }
        finally { retry.disabled = false; }
    });
    form.elements.quality_preset?.addEventListener("change", selectionChanged);

    function offerCheckout(upload) {
        if (upload.status !== "AWAITING_PAYMENT") return false;
        uploadId = upload.upload_id;
        cancel.hidden = false;
        setFormLocked(false);
        message("Source upload complete (100%). Payment is required before reconstruction. Your files are preserved.");
        if (upload.checkout_url) {
            result.textContent = "Continue to secure Checkout";
            result.href = upload.checkout_url;
            result.hidden = false;
        }
        return true;
    }

    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (active || processingLocked || !quoteReady || !input.files.length) return;
        const curLat = parseCoord(latInput?.value, 90);
        const curLon = parseCoord(lonInput?.value, 180);
        if (!hasDetectedGeoref && gcpCount < 3 && (curLat === null || curLon === null)) {
            setGeorefBadge("📍 Click on the map below to set an initial approximate location before uploading!", "warn");
            message("Unreferenced file detected: please click anywhere on the map above to place its approximate initial location (you can refine X, Y, Z later in MyEarth).");
            document.getElementById("uploadLocationMap")?.scrollIntoView({behavior: "smooth", block: "center"});
            return;
        }

        const files = Array.from(input.files);
        const descriptors = files.map(describe);
        const fingerprint = JSON.stringify(files.map(f => [f.name, f.size, f.lastModified]));
        const old = savedUpload();
        uploadId = old && old.fingerprint === fingerprint ? old.id : crypto.randomUUID();
        localStorage.setItem(key, JSON.stringify({id: uploadId, fingerprint}));
        const endpoint = base + "/" + uploadId;
        const details = Object.fromEntries(new FormData(form));
        delete details.file;
        delete details.project_id;
        delete details.source_origin;
        if (origin === "gaussian") details.quality_preset = "HIGH_QUALITY";

        active = true;
        paused = false;
        pause.hidden = false;
        cancel.hidden = false;
        submit.disabled = true;
        input.disabled = true;
        if (fieldsWrap) fieldsWrap.classList.add("me-upload-state-locked");
        const total = files.reduce((n, f) => n + f.size, 0);
        try {
            const upload = await call(endpoint, {
                method: "PUT",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({origin, files: descriptors, details}),
            });
            if (upload.status === "AWAITING_PAYMENT") {
                active = false;
                offerCheckout(await call(endpoint + "/finish", {method: "POST"}));
                return;
            }
            if (upload.status === "QUEUED") {
                active = false;
                pause.hidden = cancel.hidden = true;
                await watch(upload.job_ids || upload.job_id, upload.asset_id);
                return;
            }
            let previous = 0;
            for (let index = 0; index < files.length; index++) {
                const file = files[index];
                let offset = upload.offsets[index];
                while (offset < file.size && !paused) {
                    const bytes = await file.slice(offset, offset + upload.chunk_bytes).arrayBuffer();
                    const sha = await crypto.subtle.digest("SHA-256", bytes);
                    const digest = Array.from(new Uint8Array(sha), b => b.toString(16).padStart(2, "0")).join("");
                    if (paused) break;
                    const start = offset;
                    const answer = await transmit(
                        endpoint + "/files/" + index + "?offset=" + start,
                        bytes,
                        digest,
                        sent => reportBytes(previous + start + sent, total)
                    );
                    request = undefined;
                    offset = answer.offset;
                    reportBytes(previous + offset, total);
                }
                if (paused) return;
                previous += file.size;
            }
            active = false;
            pause.hidden = cancel.hidden = true;
            message("Upload complete (100%). Registering the processing job...");
            const confirmed = await call(endpoint + "/finish", {method: "POST"});
            if (offerCheckout(confirmed)) return;
            await watch(confirmed.job_ids || confirmed.job_id, confirmed.asset_id);
        } catch (error) {
            if (error.status === 410) localStorage.removeItem(key);
            message(error.message + (paused ? " Select Upload to resume." : " You can retry from the last saved block."));
            setFormLocked(false);
        } finally {
            active = false;
            pause.hidden = true;
            if (!processingLocked) {
                input.disabled = false;
                submit.disabled = !quoteReady;
                if (fieldsWrap) fieldsWrap.classList.remove("me-upload-state-locked");
            }
        }
    });

    pause.addEventListener("click", () => {
        paused = true;
        if (request) request.abort();
        message("Upload paused. Select Upload to resume.");
    });
    cancel.addEventListener("click", async () => {
        paused = true;
        if (request) request.abort();
        try {
            await call(base + "/" + uploadId, {method: "DELETE"});
            localStorage.removeItem(key);
            cancel.hidden = true;
            setFormLocked(false);
            message("Upload canceled.");
        } catch (error) { message(error.message); }
    });

    initLocationMap();
    selectionChanged();
    const previous = savedUpload();
    if (previous) {
        call(base + "/" + previous.id).then(upload => {
            if (upload.job_ids || upload.job_id) watch(upload.job_ids || upload.job_id, upload.asset_id);
            else if (upload.status === "AWAITING_PAYMENT") {
                message("Restoring secure Checkout. Your files are preserved.");
                call(base + "/" + previous.id + "/finish", {method: "POST"}).then(current => {
                    if (!offerCheckout(current)) watch(current.job_ids || current.job_id, current.asset_id);
                }).catch(error => message(error.message));
            } else if (upload.status === "UPLOADING") {
                uploadId = previous.id;
                cancel.hidden = false;
                message("An upload is saved. Select the same files and choose Upload to resume.");
            }
        }).catch(() => {});
    } else if (Number(form.dataset.latestJob) > 0) {
        call("/api/v2/jobs/status?job_id=" + Number(form.dataset.latestJob)).then(({job}) => {
            if (job && !["COMPLETED", "FAILED", "CANCELED"].includes(job.STATUS)) {
                watch(Number(form.dataset.latestJob), Number(form.dataset.latestAsset));
            }
        }).catch(() => {});
    }
})();
