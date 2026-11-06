"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector("meta[name=workspace-token]").content;
let selected = "";
let viewer;
let drawn = "";
let rasterSurface;

async function post(path, body, filename) {
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

async function refresh() {
    const projects = await fetch("/api/projects").then(r => r.json());
    $("projects").replaceChildren(...projects.map(project => {
        const button = document.createElement("button");
        button.textContent = project.name;
        button.onclick = () => { selected = project.id; refresh().catch(showError); };
        return button;
    }));
    const project = projects.find(p => p.id === selected);
    $("actions").hidden = !project;
    if (!project) return;
    if (project.mode !== "raster" && drawn && viewer) {
        if (rasterSurface) viewer.scene.primitives.remove(rasterSurface);
        rasterSurface = undefined;
        $("rasterPreview").hidden = true;
        drawn = "";
    }
    $("title").textContent = project.name;
    $("state").textContent = project.state + (project.error ? " · " + project.error : "");
    $("files").replaceChildren();
    if (!project.result) return;
    const path = "/results/" + project.id + "/";
    const files = ["job.log"];
    if (project.state === "complete") {
        if (project.mode === "odx") files.push("all.zip");
        if (project.mode === "gaussian") files.push("output/model.ply", "output/model.spz",
            "output/model.splat", "output/gaussian-3dtiles.zip", "output/benchmark.json",
            "output/opensplat.log", "colmap.log");
        if (project.mode === "raster") await displayRaster(path, project.result);
    }
    for (const file of files) {
        const link = document.createElement("a");
        link.href = path + file;
        link.textContent = file;
        link.download = "";
        $("files").append(link, " ");
    }
}

function showError(error) { $("state").textContent = error.message; }

$("create").onsubmit = async (event) => {
    event.preventDefault();
    try {
        selected = (await post("/api/projects", JSON.stringify({name: $("name").value}))).id;
        $("name").value = "";
        await refresh();
    } catch (error) { showError(error); }
};

async function upload(operation, files) {
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
    $(engine).onclick = () => post("/api/" + selected + "/run-" + engine).then(refresh).catch(showError);
}
$("cloudLogin").onsubmit = async (event) => {
    event.preventDefault();
    try {
        const data = await post("/api/cloud/sign-in", JSON.stringify({
            email: $("cloudEmail").value, password: $("cloudPassword").value
        }));
        $("cloudState").textContent = "Signed in as " + data.user;
        $("cloudLogout").hidden = false;
        $("syncProject").disabled = false;
    } catch (error) { $("cloudState").textContent = error.message; }
    finally { $("cloudPassword").value = ""; }
};
$("cloudLogout").onclick = async () => {
    await post("/api/cloud/sign-out");
    $("cloudState").textContent = "";
    $("cloudLogout").hidden = true;
    $("syncProject").disabled = true;
};
$("syncProject").onclick = async () => {
    try {
        const data = await post("/api/" + selected + "/sync");
        $("cloudState").textContent = "Cloud project #" + data.cloud_id;
    } catch (error) { $("cloudState").textContent = error.message; }
};
if (window.Cesium) {
    viewer = new Cesium.Viewer("viewer", {baseLayer: false, baseLayerPicker: false, geocoder: false,
        animation: false, timeline: false, sceneMode: Cesium.SceneMode.SCENE3D,
        sceneModePicker: false, navigationHelpButton: false, fullscreenElement: $("viewer"),
        skyBox: false, skyAtmosphere: false, contextOptions: {webgl: {preserveDrawingBuffer: true}}});
    viewer.scene.globe.show = false;
}
refresh().catch(showError);
setInterval(() => refresh().catch(showError), 3000);
