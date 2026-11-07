"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector("meta[name=workspace-token]").content;
let selected = "";
let viewer;
let drawn = "";
let rasterSurface;
let localTiles;
let displayedProject = "";
let loadingGaussian = "";

async function post(path, body, filename) {
    $("notice").hidden = true;
    const headers = {"X-Mosaic-Workspace": token};
    if (filename) headers["X-File-Name"] = encodeURIComponent(filename);
    const response = await fetch(path, {method: "POST", body, headers});
    const data = await response.json();
    if (!response.ok) throw Error(data.error);
    return data;
}

async function displayRaster(path, revision) {
    if (!viewer || drawn === revision) return;
    const response = await fetch(path + "bounds.json");
    if (!response.ok) return;
    const bounds = await response.json();
    const rectangle = Cesium.Rectangle.fromDegrees(...bounds);
    if (!selected || !path.includes("/" + selected + "/")) return;
    drawn = revision;
    if (rasterSurface) viewer.scene.primitives.remove(rasterSurface);
    const image = path + "preview.png";
    rasterSurface = viewer.scene.primitives.add(new Cesium.Primitive({
        geometryInstances: new Cesium.GeometryInstance({geometry: new Cesium.RectangleGeometry({
            rectangle, height: 0, vertexFormat: Cesium.EllipsoidSurfaceAppearance.VERTEX_FORMAT
        })}),
        appearance: new Cesium.EllipsoidSurfaceAppearance({aboveGround: true,
            material: Cesium.Material.fromType("Image", {image})}),
        asynchronous: false
    }));
    const center = Cesium.Rectangle.center(rectangle);
    const height = Math.max(100, 3 * Cesium.BoundingSphere.fromRectangle3D(rectangle).radius);
    viewer.camera.setView({destination: Cesium.Cartesian3.fromRadians(center.longitude, center.latitude, height),
        orientation: {heading: 0, pitch: -Cesium.Math.PI_OVER_TWO, roll: 0}});
    viewer.scene.requestRender();
    viewer.render();
    $("viewer").dataset.rasterLayer = "loaded";
    $("rasterPreview").src = image;
    $("rasterPreview").hidden = false;
}

async function displayGaussian(project) {
    if (!viewer || drawn === project.result || loadingGaussian === project.result) return;
    const revision = project.result;
    const pid = project.id;
    loadingGaussian = revision;
    try {
    const tiles = await Cesium.Cesium3DTileset.fromUrl("/project-files/" + pid + "/" + revision + "/output/tileset.json", {maximumScreenSpaceError: 2});
    if (selected !== pid) { tiles.destroy(); return; }
    if (localTiles) viewer.scene.primitives.remove(localTiles);
    if (rasterSurface) viewer.scene.primitives.remove(rasterSurface);
    rasterSurface = undefined;
    // A local SfM frame is displayed above the ellipsoid for camera interaction;
    // this is a visual frame only and does not assert geographic coordinates.
    tiles.modelMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(Cesium.Cartesian3.fromDegrees(0, 0, 1000));
    localTiles = viewer.scene.primitives.add(tiles);
    drawn = revision;
    await viewer.zoomTo(tiles, new Cesium.HeadingPitchRange(0, -0.35, Math.max(2, tiles.boundingSphere.radius * 2.5)));
    $("viewer").dataset.gaussianLayer = "loaded";
    viewer.scene.requestRender();
    } finally { if (loadingGaussian === revision) loadingGaussian = ""; }
}

async function refresh() {
    await refreshTransfers();
    const projects = await fetch("/api/projects").then(r => r.json());
    $("projects").replaceChildren(...projects.map(project => {
        const button = document.createElement("button");
        button.textContent = project.name;
        button.setAttribute("aria-current", project.id === selected ? "true" : "false");
        button.onclick = () => { selected = project.id; refresh().catch(showError); };
        return button;
    }));
    const project = projects.find(p => p.id === selected);
    $("actions").hidden = !project;
    if (!project) return;
    if (!["raster", "odx", "gaussian"].includes(project.mode) && drawn && viewer) {
        if (rasterSurface) viewer.scene.primitives.remove(rasterSurface);
        rasterSurface = undefined;
        $("rasterPreview").hidden = true;
        drawn = "";
    }
    if (project.mode !== "gaussian" && localTiles && viewer) {
        viewer.scene.primitives.remove(localTiles); localTiles = undefined; drawn = "";
    }
    $("title").textContent = project.name;
    if (displayedProject !== project.id) { $("processingControls").open = project.state !== "complete"; $("allFiles").replaceChildren(); displayedProject = project.id; }
    $("viewerFrame").textContent = project.mode === "gaussian" ? "Local reconstruction frame · no geographic location assigned." : "";
    $("state").textContent = project.state + (project.error ? " · " + project.error : "");
    for (const id of ["odx", "gaussian", "uploadPhotos", "uploadRaster"]) $(id).disabled = project.state === "running";
    $("files").replaceChildren();
    if (!project.result) return;
    const path = "/results/" + project.id + "/";
    const files = ["job.log"];
    if (project.state === "complete") {
        if (project.mode === "odx") files.push("all.zip");
        if (project.mode === "gaussian") files.push("output/model.ply", "output/model.spz",
            "output/model.splat", "output/gaussian-3dtiles.zip", "output/benchmark.json",
            "output/opensplat.log", "colmap.log");
        if (project.mode === "raster" || project.mode === "odx") await displayRaster(path, project.result);
        if (project.mode === "gaussian") await displayGaussian(project);
    }
    for (const file of files) {
        const link = document.createElement("a");
        link.href = path + file;
        link.textContent = file;
        link.download = "";
        $("files").append(link, " ");
    }
}

function showError(error) { $("notice").textContent = error.message; $("notice").hidden = false; }

$("create").onsubmit = async (event) => {
    event.preventDefault();
    try {
        selected = (await post("/api/projects", JSON.stringify({name: $("name").value}))).id;
        $("name").value = "";
        await refresh();
    } catch (error) { showError(error); }
};

async function upload(operation, files) {
    if (!files.length) throw Error("Select at least one file");
    const pid = selected;
    for (const file of files) await post("/api/" + pid + "/" + operation, file, file.name);
}

$("uploadPhotos").onclick = async () => {
    try {
        await upload("photo", $("photos").files);
        $("photos").value = "";
        $("state").textContent = "Photos imported";
    } catch (error) { showError(error); }
};
$("uploadRaster").onclick = async () => {
    try {
        if (!$("raster").files.length) throw Error("Select a GeoTIFF");
        await upload("raster", $("raster").files);
        await post("/api/" + selected + "/run-raster");
        await refresh();
    } catch (error) { showError(error); }
};
for (const engine of ["odx", "gaussian"]) {
    $(engine).onclick = () => post("/api/" + selected + "/run-" + engine, JSON.stringify({preset: $("odxPreset").value})).then(refresh).catch(showError);
}
$("cloudLogin").onsubmit = async (event) => {
    event.preventDefault();
    try {
        const data = await post("/api/cloud/sign-in", JSON.stringify({
            email: $("cloudEmail").value, password: $("cloudPassword").value
        }));
        $("cloudState").textContent = "Signed in as " + data.user;
        $("cloudLogout").hidden = false;
        $("cloudBrowse").hidden = false;
        $("cloudLogin").hidden = true;
        $("syncProject").disabled = false;
    } catch (error) { $("cloudState").textContent = error.message; }
    finally { $("cloudPassword").value = ""; }
};
$("cloudLogout").onclick = async () => {
    try { await post("/api/cloud/sign-out"); }
    catch (error) { $("cloudState").textContent = error.message; }
    finally {
        $("cloudLogout").hidden = true;
        $("cloudBrowse").hidden = true;
        $("cloudLogin").hidden = false;
        $("cloudCatalog").replaceChildren();
        $("syncProject").disabled = true;
    }
};
$("syncProject").onclick = async () => {
    try {
        await post("/api/" + selected + "/sync");
        $("cloudState").textContent = "Transfer started. You can pause it and resume without uploading the same chunks again.";
    } catch (error) { $("cloudState").textContent = error.message; }
};
async function refreshTransfers() {
    const states = await fetch("/api/transfers").then(r => r.json());
    $("transfers").replaceChildren(...Object.entries(states).map(([id, state]) => {
        const row = document.createElement("div");
        const label = document.createElement("p");
        label.textContent = state.status + " · " + state.message;
        row.append(label);
        if (state.total) {
            const progress = document.createElement("progress");
            progress.max = state.total; progress.value = state.done;
            progress.setAttribute("aria-label", "File transfer"); row.append(progress);
        }
        if (state.status === "running") {
            const pause = document.createElement("button"); pause.textContent = "Pause";
            pause.onclick = () => post("/api/transfers/pause", JSON.stringify({id})).catch(showError);
            row.append(pause);
        }
        if (state.result?.cloud_id) {
            const link = document.createElement("a");
            link.textContent = "Open MyEarth"; link.target = "_blank"; link.rel = "noopener noreferrer";
            link.href = "https://app.mosaicearth.app/en/Projects?open_project=" + state.result.cloud_id;
            row.append(link);
            const message = document.createElement("p");
            message.textContent = "Originals saved. Viewer preparation has its own status in MyEarth.";
            row.append(message);
        }
        return row;
    }));
}
$("cloudBrowse").onclick = async () => {
    try {
        $("cloudState").textContent = "Loading cloud projects…";
        const response = await fetch("/api/cloud/catalog");
        const data = await response.json();
        if (!response.ok) throw Error(data.error);
        $("cloudCatalog").replaceChildren();
        const entries = [
            ...data.projects.map(p => ({label: p.name + " · latest cloud files", body: {project_id: p.id}})),
            ...data.snapshots.filter(s => s.status === "COMPLETE").map(s => ({label: s.name + " · " + new Date(s.created_at * 1000).toLocaleString(), body: {snapshot_id: s.id}}))
        ];
        for (const item of entries) {
            const button = document.createElement("button"); button.textContent = "Download " + item.label;
            button.onclick = () => post("/api/cloud/download", JSON.stringify(item.body)).then(refresh).catch(showError);
            $("cloudCatalog").append(button);
        }
        for (const snapshot of data.snapshots.filter(s => ["UPLOADING", "FAILED"].includes(s.status))) {
            const remove = document.createElement("button");
            remove.textContent = "Remove unfinished upload · " + snapshot.name;
            remove.onclick = async () => {
                if (!confirm("Remove the unfinished cloud upload? Your local files are preserved.")) return;
                try { await post("/api/cloud/cancel-upload", JSON.stringify({snapshot_id: snapshot.id})); remove.remove(); }
                catch (error) { showError(error); }
            };
            $("cloudCatalog").append(remove);
        }
        $("cloudState").textContent = entries.length ? "Downloads create a local copy and preserve existing projects." : "No cloud files yet.";
    } catch (error) { $("cloudState").textContent = error.message; }
};
$("browseFiles").onclick = async () => {
    try {
        const pid = selected;
        const files = await fetch("/api/files/" + pid).then(r => r.json());
        $("allFiles").replaceChildren(...files.map(file => {
            const row = document.createElement("p"), link = document.createElement("a");
            link.href = "/project-files/" + pid + "/" + file.split("/").map(encodeURIComponent).join("/");
            link.textContent = file; link.download = ""; row.append(link); return row;
        }));
    } catch (error) { showError(error); }
};
async function checkInstallation() {
    const response = await fetch("/api/installation"); const data = await response.json();
    $("gaussian").textContent = "Train Gaussian on " + (data.gaussian_device === "cuda" ? "NVIDIA GPU" : "CPU");
    $("installationState").textContent = (data.docker ? "Docker ready. " : "Docker with Linux containers is required. ") +
        (Object.values(data.engines).filter(Boolean).length === 2 ? "Local engines installed. " : "Install the local engines before processing. ") +
        (data.account ? "Activated for " + data.account.email : data.commercial ? "Activate your purchasing account." : "Open-source edition · local use without sign-in.");
    $("activateInstallation").hidden = !!data.account;
    $("installEngines").disabled = !data.docker || !data.account;
    if (!data.docker || Object.values(data.engines).filter(Boolean).length !== 2) $("installation").open = true;
}
$("activateInstallation").onsubmit = async event => {
    event.preventDefault();
    try { await post("/api/installation/activate", JSON.stringify({email: $("activationEmail").value, password: $("activationPassword").value})); await checkInstallation(); }
    catch (error) { $("installationState").textContent = error.message; }
    finally { $("activationPassword").value = ""; }
};
$("installEngines").onclick = () => post("/api/installation/engines").then(refresh).catch(error => {$("installationState").textContent = error.message;});
$("checkInstallation").onclick = () => checkInstallation().catch(showError);
checkInstallation().catch(showError);
if (window.Cesium) {
    viewer = new Cesium.Viewer("viewer", {baseLayer: false, baseLayerPicker: false, geocoder: false,
        animation: false, timeline: false, sceneMode: Cesium.SceneMode.SCENE3D,
        sceneModePicker: false, navigationHelpButton: false, fullscreenElement: $("viewer"),
        skyBox: false, skyAtmosphere: false, contextOptions: {webgl: {preserveDrawingBuffer: true}}});
    viewer.scene.globe.show = false;
    viewer.homeButton.viewModel.command.beforeExecute.addEventListener(event => {
        event.cancel = true;
        if (localTiles) viewer.zoomTo(localTiles, new Cesium.HeadingPitchRange(0, -0.35, Math.max(2, localTiles.boundingSphere.radius * 2.5)));
        else { drawn = ""; refresh().catch(showError); }
    });
}
refresh().catch(showError);
setInterval(() => refresh().catch(showError), 3000);
