(() => {
    const form = document.querySelector("[data-asset-upload]");
    if (!form) return;

    const project = form.elements.project_id.value;
    const origin = form.elements.source_origin.value;
    const input = form.elements.file;
    const base = "/api/v2/tasks/" + project + "/asset-uploads";
    const key = "mosaicearth:upload:" + project;
    const status = document.getElementById("asset-upload-status");
    const selection = document.getElementById("asset-upload-selection");
    const meter = document.getElementById("asset-upload-progress");
    const submit = form.querySelector("button[type=submit]");
    const pause = document.getElementById("asset-upload-pause");
    const cancel = document.getElementById("asset-upload-cancel");
    const result = document.getElementById("asset-upload-result");
    const retry = document.getElementById("asset-upload-retry");
    const model = /\.(ply|spz|splat|ksplat)$/i;
    let uploadId;
    let active = false;
    let paused = false;
    let request;
    let quoteReady = false;

    const describe = file => ({name: file.name, size: file.size});
    const mib = bytes => (bytes / (1024 ** 2)).toFixed(1) + " MiB";
    const message = text => { status.textContent = text; };

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
        message("Uploading " + mib(done) + " / " + mib(total) + " (" + Math.floor(100 * done / total) + "%)");
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
        const directImport = origin !== "gaussian" || model.test(files[0].name);
        submit.textContent = directImport ? "UPLOAD & IMPORT" : "UPLOAD & GENERATE GAUSSIAN SPLAT";
        if (form.elements.quality_preset) form.elements.quality_preset.disabled = directImport;
        const summary = files.map(f => f.name + " (" + mib(f.size) + ")").join(", ");
        selection.textContent = summary;
        if (directImport) {
            selection.textContent += " · Direct import · €0.00 additional charge";
            quoteReady = true;
        } else {
            try {
                const quote = await call(base + "/quote");
                if (input.files[0] !== files[0]) return;
                selection.textContent += " · COLMAP + OpenSplat · " + (form.elements.quality_preset?.value || "STANDARD") + " · PLY, SPZ and 3D Tiles · " + quote.label;
                quoteReady = quote.engine_available;
                if (!quote.engine_available) message(quote.local ? "Local COLMAP and OpenSplat are unavailable. Check the Desktop engine installation." : "Cloud training is currently unavailable.");
                else if (quote.local) message("Processing runs on this computer. No cloud credits are consumed.");
                else if (!quote.covered_by_plan) message("Upload the source, then confirm the quoted price in secure Checkout. Processing starts only after payment is verified.");
            } catch (error) {
                message(error.message);
            }
        }
        submit.disabled = !quoteReady;
    }

    async function watch(jobId, assetId) {
        if (!jobId) return;
        message("Job #" + jobId + " is queued.");
        meter.hidden = true;
        while (true) {
            try {
                const {job} = await call("/api/v2/jobs/status?job_id=" + jobId);
                const terminal = ["COMPLETED", "FAILED", "CANCELED"].includes(job.STATUS);
                const payment = job.PAYMENT_STATUS ? " · " + job.PAYMENT_STATUS.toLowerCase().replaceAll("_", " ") : "";
                message("Job #" + jobId + " · " + job.STATUS + " · " + (job.ERROR || job.STAGE || "") + payment);
                if (terminal) {
                    if (retry && ["FAILED", "CANCELED"].includes(job.STATUS) && savedUpload()) retry.hidden = false;
                    if (job.STATUS === "COMPLETED") {
                        result.textContent = "Open result";
                        result.href = "/Client/ExternalAssetDetail.aspx?project_id=" + project + "&asset_id=" + assetId;
                        result.hidden = false;
                    }
                    return;
                }
            } catch (error) {
                message("Job #" + jobId + " · " + error.message + " Retrying status.");
                if (error.status === 401 || error.status === 404) return;
            }
            await new Promise(resolve => setTimeout(resolve, 3000));
        }
    }

    input.addEventListener("change", selectionChanged);
    retry?.addEventListener("click", async () => {
        const saved = savedUpload();
        if (!saved) return;
        retry.disabled = true;
        try {
            const upload = await call(base + "/" + saved.id + "/retry", {method:"POST"});
            retry.hidden = true;
            if (!offerCheckout(upload)) await watch(upload.job_id, upload.asset_id);
        } catch (error) { message(error.message); }
        finally { retry.disabled = false; }
    });
    form.elements.quality_preset?.addEventListener("change", selectionChanged);
    function offerCheckout(upload) {
        if (upload.status !== "AWAITING_PAYMENT") return false;
        uploadId = upload.upload_id;
        cancel.hidden = false;
        message("Source upload complete. Payment is required before reconstruction. Your files are preserved.");
        if (upload.checkout_url) {
            result.textContent = "Continue to secure Checkout";
            result.href = upload.checkout_url;
            result.hidden = false;
        }
        return true;
    }
    form.addEventListener("submit", async event => {
        event.preventDefault();
        if (active || !quoteReady || !input.files.length) return;
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

        active = true;
        paused = false;
        pause.hidden = false;
        cancel.hidden = false;
        submit.disabled = true;
        input.disabled = true;
        const total = files.reduce((n, f) => n + f.size, 0);
        try {
            const upload = await call(endpoint, {
                method: "PUT", headers: {"Content-Type": "application/json"},
                body: JSON.stringify({origin, files: descriptors, details}),
            });
            if (upload.status === "AWAITING_PAYMENT") {
                offerCheckout(await call(endpoint + "/finish", {method:"POST"}));
                return;
            }
            if (upload.status === "QUEUED") {
                pause.hidden = cancel.hidden = true;
                await watch(upload.job_id, upload.asset_id);
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
                    const answer = await transmit(endpoint + "/files/" + index + "?offset=" + start, bytes, digest,
                        sent => reportBytes(previous + start + sent, total));
                    request = undefined;
                    offset = answer.offset;
                    reportBytes(previous + offset, total);
                }
                if (paused) return;
                previous += file.size;
            }
            pause.hidden = cancel.hidden = true;
            message("Upload complete. Registering the processing job.");
            const confirmed = await call(endpoint + "/finish", {method: "POST"});
            if (offerCheckout(confirmed)) return;
            await watch(confirmed.job_id, confirmed.asset_id);
        } catch (error) {
            if (error.status === 410) localStorage.removeItem(key);
            message(error.message + (paused ? " Select Upload to resume." : " You can retry from the last saved block."));
        } finally {
            active = false;
            input.disabled = false;
            submit.disabled = !quoteReady;
            pause.hidden = true;
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
            message("Upload canceled.");
        } catch (error) { message(error.message); }
    });

    selectionChanged();
    const previous = savedUpload();
    if (previous) {
        call(base + "/" + previous.id).then(upload => {
            if (upload.job_id) watch(upload.job_id, upload.asset_id);
            else if (upload.status === "AWAITING_PAYMENT") {
                message("Restoring secure Checkout. Your files are preserved.");
                call(base + "/" + previous.id + "/finish", {method:"POST"}).then(current => {
                    if (!offerCheckout(current)) watch(current.job_id, current.asset_id);
                }).catch(error=>message(error.message));
            }
            else if (upload.status === "UPLOADING") {
                uploadId = previous.id;
                cancel.hidden = false;
                message("An upload is saved. Select the same files and choose Upload to resume.");
            }
        }).catch(() => {});
    } else if (form.dataset.latestJob) {
        watch(Number(form.dataset.latestJob), Number(form.dataset.latestAsset));
    }
})();
