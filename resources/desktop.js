"use strict";

const $ = (id) => document.getElementById(id);
const token = document.querySelector("meta[name=workspace-token]").content;
let selected = "";
let viewer;
let drawn = "";

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
    drawn = revision;
    viewer.entities.removeAll();
    const rectangle = Cesium.Rectangle.fromDegrees(...bounds);
    const material = new Cesium.ImageMaterialProperty({image: path + "preview.png", transparent: true});
    viewer.entities.add({rectangle: {coordinates: rectangle, material}});
    viewer.camera.flyTo({destination: rectangle});
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
    $("title").textContent = project.name;
    $("state").textContent = project.state + (project.error ? " · " + project.error : "");
    $("files").replaceChildren();
    if (!project.result) return;
    const path = "/results/" + project.id + "/";
    const files = ["job.log"];
    if (project.state === "complete") {
        if (project.mode === "odx") files.push("all.zip");
        if (project.mode === "gaussian") files.push("output/model.ply", "output/metadata.json", "output/opensplat.log", "colmap.log");
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
        animation: false, timeline: false, sceneModePicker: false, navigationHelpButton: false});
}
refresh().catch(showError);
setInterval(() => refresh().catch(showError), 3000);
