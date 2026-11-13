/**
 * MosaicEarth GCP Studio — Interactive Sub-Pixel Ground Control & Check Point Editor
 * Works both 100% standalone in the browser and natively connected to MosaicEarth Photogrammetry & 3DGS tasks.
 * License: MIT
 */

import {
  COMMON_CRS_CATALOG,
  parseEpsgUtmInfo,
  crsCoordsToWgs84,
  wgs84ToCrsCoords,
  haversineMeters,
  parseSurveyorCsvOrOdmTxt,
  exportOdmGcpListTxt,
  exportColmap3dgsGcpJson,
  extractJpegExifGps,
} from "./crs-utils.js";

const state = {
  crs: "EPSG:4326",
  gcps: [], // [{ id, label, role: 'gcp'|'check', x, y, z, lat, lon, observations: [{ image, px, py, image_width, image_height }] }]
  selectedGcpId: null,
  images: [], // [{ name, url, lat, lon, alt, width, height, bitmap }]
  activeImageName: null,
  pickMapMode: false,
  taskContext: null, // { taskId, mode: 'photogrammetry'|'gaussian', apiOrigin }
  view: {
    scale: 1,
    offsetX: 0,
    offsetY: 0,
    dragging: false,
    dragMoved: false,
    startX: 0,
    startY: 0,
    hoverPx: null,
    hoverPy: null,
  },
};

let map = null;
let gcpMarkersLayer = null;
let camMarkersLayer = null;
let activeImgObj = null;

const dom = {
  crsSelect: document.getElementById("crsSelect"),
  crsCustomInput: document.getElementById("crsCustomInput"),
  btnLoadImages: document.getElementById("btnLoadImages"),
  inpImages: document.getElementById("inpImages"),
  btnImportCsv: document.getElementById("btnImportCsv"),
  inpCsv: document.getElementById("inpCsv"),
  btnExportOdm: document.getElementById("btnExportOdm"),
  btnExport3dgs: document.getElementById("btnExport3dgs"),
  btnAttachTask: document.getElementById("btnAttachTask"),
  studioModeBadge: document.getElementById("studioModeBadge"),
  studioSubtitle: document.getElementById("studioSubtitle"),
  inpPlaceSearch: document.getElementById("inpPlaceSearch"),
  btnPlaceSearch: document.getElementById("btnPlaceSearch"),
  btnPickOnMap: document.getElementById("btnPickOnMap"),
  gcpCountHeading: document.getElementById("gcpCountHeading"),
  btnAddManualGcp: document.getElementById("btnAddManualGcp"),
  gcpTableScroll: document.getElementById("gcpTableScroll"),
  emptyGcpHint: document.getElementById("emptyGcpHint"),
  activeImageTitle: document.getElementById("activeImageTitle"),
  activeGcpPrompt: document.getElementById("activeGcpPrompt"),
  btnResetZoom: document.getElementById("btnResetZoom"),
  btnClearObsOnImage: document.getElementById("btnClearObsOnImage"),
  canvasStage: document.getElementById("canvasStage"),
  imageCanvas: document.getElementById("imageCanvas"),
  loupeBox: document.getElementById("loupeBox"),
  loupeCanvas: document.getElementById("loupeCanvas"),
  loupeCoords: document.getElementById("loupeCoords"),
  emptyCanvasHint: document.getElementById("emptyCanvasHint"),
  photoStripCount: document.getElementById("photoStripCount"),
  overallSummaryStats: document.getElementById("overallSummaryStats"),
  photoStripItems: document.getElementById("photoStripItems"),
  studioToast: document.getElementById("studioToast"),
};

function showToast(message, isError = false) {
  if (!dom.studioToast) return;
  dom.studioToast.textContent = message;
  dom.studioToast.style.borderColor = isError ? "#ef4444" : "#99CC00";
  dom.studioToast.style.color = isError ? "#fecaca" : "#ecfccb";
  dom.studioToast.style.display = "block";
  clearTimeout(showToast._timer);
  showToast._timer = setTimeout(() => {
    dom.studioToast.style.display = "none";
  }, 3800);
}

function initCrsSelector() {
  dom.crsSelect.innerHTML = "";
  for (const item of COMMON_CRS_CATALOG) {
    const opt = document.createElement("option");
    opt.value = item.code;
    opt.textContent = item.label;
    dom.crsSelect.appendChild(opt);
  }
  const customOpt = document.createElement("option");
  customOpt.value = "__CUSTOM__";
  customOpt.textContent = "Custom EPSG / PROJ...";
  dom.crsSelect.appendChild(customOpt);
  dom.crsSelect.value = state.crs;

  dom.crsSelect.addEventListener("change", () => {
    if (dom.crsSelect.value === "__CUSTOM__") {
      dom.crsCustomInput.style.display = "inline-block";
      dom.crsCustomInput.focus();
    } else {
      dom.crsCustomInput.style.display = "none";
      applyNewCrs(dom.crsSelect.value);
    }
  });

  dom.crsCustomInput.addEventListener("change", () => {
    const val = dom.crsCustomInput.value.trim();
    if (val) applyNewCrs(val);
  });
}

function syncCrsDropdownValue(crsStr) {
  const clean = String(crsStr || "EPSG:4326").trim();
  state.crs = clean;
  const match = COMMON_CRS_CATALOG.find((c) => c.code.toUpperCase() === clean.toUpperCase());
  if (match) {
    dom.crsSelect.value = match.code;
    dom.crsCustomInput.style.display = "none";
  } else {
    dom.crsSelect.value = "__CUSTOM__";
    dom.crsCustomInput.style.display = "inline-block";
    dom.crsCustomInput.value = clean;
  }
}

function applyNewCrs(newCrs) {
  const prevInfo = parseEpsgUtmInfo(state.crs);
  state.crs = newCrs;
  // Reproject existing GCPs that have valid WGS84 lat/lon into the newly selected CRS
  for (const g of state.gcps) {
    if (g.lat != null && g.lon != null) {
      const proj = wgs84ToCrsCoords(g.lat, g.lon, state.crs);
      g.x = proj.x;
      g.y = proj.y;
    } else if (prevInfo.kind !== "custom") {
      const wgs = crsCoordsToWgs84(g.x, g.y, state.crs);
      if (wgs) {
        g.lat = wgs.lat;
        g.lon = wgs.lon;
      }
    }
  }
  renderGcpList();
  refreshMapMarkers(false);
}

function initMap() {
  if (!window.L) return;
  map = window.L.map("gcpMap", {
    zoomControl: false,
    attributionControl: true,
  }).setView([38.5, 1.5], 3);

  window.L.control.zoom({ position: "bottomright" }).addTo(map);

  const esriSat = window.L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 21,
      maxNativeZoom: 19,
      attribution: "Tiles &copy; Esri World Imagery",
    }
  );
  const osmStreets = window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 21,
    maxNativeZoom: 19,
    attribution: "&copy; OpenStreetMap",
  });

  esriSat.addTo(map);
  window.L.control
    .layers({ "Satellite (Esri)": esriSat, "OpenStreetMap": osmStreets }, null, { position: "bottomleft" })
    .addTo(map);

  camMarkersLayer = window.L.layerGroup().addTo(map);
  gcpMarkersLayer = window.L.layerGroup().addTo(map);

  map.on("click", (ev) => {
    if (!state.pickMapMode) return;
    const { lat, lng } = ev.latlng;
    addGcpFromLatLon(lat, lng);
    setPickMapMode(false);
  });
}

function setPickMapMode(enabled) {
  state.pickMapMode = Boolean(enabled);
  dom.btnPickOnMap.classList.toggle("is-active", state.pickMapMode);
  dom.btnPickOnMap.textContent = state.pickMapMode ? "Click Satellite Map Now (Esc to cancel)" : "+ Click Map to Add GCP";
  if (map && map.getContainer()) {
    map.getContainer().style.cursor = state.pickMapMode ? "crosshair" : "";
  }
}

function addGcpFromLatLon(lat, lon, customLabel = null) {
  const proj = wgs84ToCrsCoords(lat, lon, state.crs);
  const idx = state.gcps.length + 1;
  const gcp = {
    id: `gcp_${Date.now()}_${idx}`,
    label: customLabel || `GCP_${String(idx).padStart(2, "0")}`,
    role: "gcp",
    x: proj.x,
    y: proj.y,
    z: estimateInitialElevation(),
    lat,
    lon,
    observations: [],
  };
  state.gcps.push(gcp);
  state.selectedGcpId = gcp.id;
  renderAll();
  showToast(`Added ${gcp.label} at ${lat.toFixed(6)}, ${lon.toFixed(6)} — now click its pixel in 3+ photos.`);
}

function estimateInitialElevation() {
  const alts = state.images.map((im) => im.alt).filter((a) => Number.isFinite(a) && a !== 0);
  if (!alts.length) return 0.0;
  // Rough ground estimate if drone EXIF altitude is present
  return Number(Math.min(...alts).toFixed(2));
}

function refreshMapMarkers(fitBounds = false) {
  if (!map || !window.L) return;
  camMarkersLayer.clearLayers();
  gcpMarkersLayer.clearLayers();
  const boundsPts = [];

  for (const im of state.images) {
    if (Number.isFinite(im.lat) && Number.isFinite(im.lon) && (im.lat !== 0 || im.lon !== 0)) {
      boundsPts.push([im.lat, im.lon]);
      const isCurrent = im.name === state.activeImageName;
      const circle = window.L.circleMarker([im.lat, im.lon], {
        radius: isCurrent ? 6 : 4,
        color: isCurrent ? "#ffffff" : "#38bdf8",
        fillColor: isCurrent ? "#99CC00" : "#0284c7",
        fillOpacity: 0.9,
        weight: isCurrent ? 2 : 1,
      });
      circle.bindTooltip(`📷 ${im.name}`, { direction: "top" });
      circle.on("click", () => selectImage(im.name));
      camMarkersLayer.addLayer(circle);
    }
  }

  for (const g of state.gcps) {
    const wgs = g.lat != null && g.lon != null ? { lat: g.lat, lon: g.lon } : crsCoordsToWgs84(g.x, g.y, state.crs);
    if (!wgs) continue;
    g.lat = wgs.lat;
    g.lon = wgs.lon;
    boundsPts.push([wgs.lat, wgs.lon]);
    const isSel = g.id === state.selectedGcpId;
    const color = g.role === "check" ? "#38bdf8" : "#99CC00";
    const icon = window.L.divIcon({
      className: "gcp-map-pin",
      html: `<div style="width:${isSel ? 22 : 18}px;height:${isSel ? 22 : 18}px;border-radius:50%;background:${
        isSel ? color : "#111814"
      };border:2px solid ${color};color:${
        isSel ? "#090f0b" : "#fff"
      };font-size:10px;font-weight:800;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 8px rgba(0,0,0,0.65);">&#8982;</div>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    const marker = window.L.marker([wgs.lat, wgs.lon], { icon, draggable: true });
    marker.bindTooltip(`${g.label} (${g.observations.length} obs)`, { direction: "top" });
    marker.on("click", () => {
      state.selectedGcpId = g.id;
      renderAll();
    });
    marker.on("dragend", () => {
      const pos = marker.getLatLng();
      g.lat = pos.lat;
      g.lon = pos.lng;
      const proj = wgs84ToCrsCoords(g.lat, g.lon, state.crs);
      g.x = proj.x;
      g.y = proj.y;
      state.selectedGcpId = g.id;
      renderAll();
    });
    gcpMarkersLayer.addLayer(marker);
  }

  if (fitBounds && boundsPts.length > 0) {
    if (boundsPts.length === 1) {
      map.setView(boundsPts[0], 18);
    } else {
      map.fitBounds(boundsPts, { padding: [28, 28], maxZoom: 19 });
    }
  }
}

function getSelectedGcp() {
  return state.gcps.find((g) => g.id === state.selectedGcpId) || null;
}

function renderGcpList() {
  const info = parseEpsgUtmInfo(state.crs);
  const xLabel = info.kind === "geographic" ? "Longitude (X)" : "Easting / X (m)";
  const yLabel = info.kind === "geographic" ? "Latitude (Y)" : "Northing / Y (m)";

  dom.gcpCountHeading.textContent = `Ground Control & Check Points (${state.gcps.length})`;
  dom.gcpTableScroll.innerHTML = "";

  if (!state.gcps.length) {
    dom.gcpTableScroll.appendChild(dom.emptyGcpHint);
    dom.emptyGcpHint.style.display = "block";
    updateSummaryCounters();
    return;
  }

  for (const g of state.gcps) {
    const isSel = g.id === state.selectedGcpId;
    const obsCount = g.observations.length;
    const ready = obsCount >= 3;

    const card = document.createElement("div");
    card.className = `gcp-card${isSel ? " is-selected" : ""}`;
    card.addEventListener("click", (ev) => {
      if (ev.target.tagName === "INPUT" || ev.target.tagName === "SELECT" || ev.target.tagName === "BUTTON") return;
      state.selectedGcpId = g.id;
      renderAll();
      if (g.lat != null && g.lon != null && map) {
        map.panTo([g.lat, g.lon]);
      }
    });

    const top = document.createElement("div");
    top.className = "gcp-card-top";

    const leftGrp = document.createElement("div");
    leftGrp.style.display = "flex";
    leftGrp.style.alignItems = "center";
    leftGrp.style.gap = "6px";

    const lblInput = document.createElement("input");
    lblInput.type = "text";
    lblInput.className = "gcp-label-input";
    lblInput.value = g.label;
    lblInput.addEventListener("change", () => {
      g.label = lblInput.value.trim().replace(/\s+/g, "_") || g.label;
      renderAll();
    });

    const roleSel = document.createElement("select");
    roleSel.className = "gcp-role-select";
    roleSel.innerHTML = `<option value="gcp">Control (GCP)</option><option value="check">Check Point (QA)</option>`;
    roleSel.value = g.role || "gcp";
    roleSel.addEventListener("change", () => {
      g.role = roleSel.value;
      renderAll();
    });

    leftGrp.appendChild(lblInput);
    leftGrp.appendChild(roleSel);

    const rightGrp = document.createElement("div");
    rightGrp.style.display = "flex";
    rightGrp.style.alignItems = "center";
    rightGrp.style.gap = "5px";

    const badge = document.createElement("span");
    badge.className = `obs-badge ${ready ? "obs-badge--ready" : "obs-badge--warn"}`;
    badge.textContent = `${obsCount} photo${obsCount === 1 ? "" : "s"}`;

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-studio";
    delBtn.style.cssText = "height:22px;padding:0 6px;font-size:11px;color:#fca5a5;";
    delBtn.textContent = "×";
    delBtn.title = "Delete point";
    delBtn.addEventListener("click", () => {
      state.gcps = state.gcps.filter((item) => item.id !== g.id);
      if (state.selectedGcpId === g.id) {
        state.selectedGcpId = state.gcps[0]?.id || null;
      }
      renderAll();
    });

    rightGrp.appendChild(badge);
    rightGrp.appendChild(delBtn);
    top.appendChild(leftGrp);
    top.appendChild(rightGrp);

    const grid = document.createElement("div");
    grid.className = "gcp-coords-grid";
    grid.innerHTML = `
      <div>
        <label>${xLabel}</label>
        <input type="number" step="any" data-field="x" value="${g.x}" />
      </div>
      <div>
        <label>${yLabel}</label>
        <input type="number" step="any" data-field="y" value="${g.y}" />
      </div>
      <div>
        <label>Elev Z (m)</label>
        <input type="number" step="any" data-field="z" value="${g.z ?? 0}" />
      </div>
    `;
    grid.querySelectorAll("input").forEach((inp) => {
      inp.addEventListener("change", () => {
        const f = inp.getAttribute("data-field");
        const val = Number(inp.value);
        if (Number.isFinite(val)) {
          g[f] = val;
          if (f === "x" || f === "y") {
            const wgs = crsCoordsToWgs84(g.x, g.y, state.crs);
            if (wgs) {
              g.lat = wgs.lat;
              g.lon = wgs.lon;
            }
          }
          refreshMapMarkers(false);
          renderPhotoStrip();
        }
      });
    });

    card.appendChild(top);
    card.appendChild(grid);
    dom.gcpTableScroll.appendChild(card);
  }

  updateSummaryCounters();
}

function updateSummaryCounters() {
  const validControl = state.gcps.filter((g) => g.role !== "check" && g.observations.length >= 2).length;
  const totalObs = state.gcps.reduce((acc, g) => acc + g.observations.length, 0);
  dom.overallSummaryStats.textContent = `${validControl} active GCPs (2+ obs) · ${totalObs} pixel observations`;

  const sel = getSelectedGcp();
  if (sel) {
    dom.activeGcpPrompt.textContent = `Tagging ${sel.label} (${sel.role === "check" ? "QA Check" : "Control"}) — click target pixel`;
  } else {
    dom.activeGcpPrompt.textContent = "Select or add a GCP on the left, then click its pixel target";
  }
}

function getSortedImagesForActiveGcp() {
  const sel = getSelectedGcp();
  if (!sel || sel.lat == null || sel.lon == null) {
    return [...state.images];
  }
  return [...state.images].sort((a, b) => {
    const aHas = Number.isFinite(a.lat) && Number.isFinite(a.lon) && (a.lat !== 0 || a.lon !== 0);
    const bHas = Number.isFinite(b.lat) && Number.isFinite(b.lon) && (b.lat !== 0 || b.lon !== 0);
    if (aHas && bHas) {
      return haversineMeters(sel.lat, sel.lon, a.lat, a.lon) - haversineMeters(sel.lat, sel.lon, b.lat, b.lon);
    }
    if (aHas) return -1;
    if (bHas) return 1;
    return a.name.localeCompare(b.name);
  });
}

function renderPhotoStrip() {
  const sorted = getSortedImagesForActiveGcp();
  const sel = getSelectedGcp();
  dom.photoStripCount.textContent = `Images (${state.images.length}) — ${
    sel && sel.lat != null ? `sorted by closest camera to ${sel.label}` : "click any photo to inspect"
  }`;
  dom.photoStripItems.innerHTML = "";

  for (const im of sorted) {
    const obsHere = state.gcps.filter((g) => g.observations.some((o) => o.image === im.name));
    const selMarked = sel && sel.observations.some((o) => o.image === im.name);
    let distLabel = "";
    if (sel && sel.lat != null && Number.isFinite(im.lat) && Number.isFinite(im.lon) && (im.lat !== 0 || im.lon !== 0)) {
      const d = haversineMeters(sel.lat, sel.lon, im.lat, im.lon);
      distLabel = d < 1000 ? `${Math.round(d)} m` : `${(d / 1000).toFixed(2)} km`;
    }

    const chip = document.createElement("div");
    chip.className = `photo-chip${im.name === state.activeImageName ? " is-active" : ""}${
      obsHere.length ? " has-obs" : ""
    }`;
    chip.innerHTML = `
      <div class="photo-chip-name" title="${im.name}">${selMarked ? "✓ " : ""}${im.name}</div>
      <div class="photo-chip-meta">
        <span>${obsHere.length} pin${obsHere.length === 1 ? "" : "s"}</span>
        <span style="color:#bef264;">${distLabel}</span>
      </div>
    `;
    chip.addEventListener("click", () => selectImage(im.name));
    dom.photoStripItems.appendChild(chip);
  }
}

function selectImage(imageName) {
  const found = state.images.find((im) => im.name === imageName);
  if (!found) return;
  state.activeImageName = found.name;
  dom.activeImageTitle.textContent = found.name;
  dom.emptyCanvasHint.style.display = "none";

  const img = new Image();
  img.crossOrigin = "anonymous";
  img.onload = () => {
    found.width = img.naturalWidth;
    found.height = img.naturalHeight;
    activeImgObj = img;
    fitImageToCanvas();
    drawCanvas();
  };
  img.onerror = () => {
    showToast(`Could not load image ${found.name}`, true);
  };
  img.src = found.url;

  renderPhotoStrip();
  refreshMapMarkers(false);
}

function resizeCanvasToStage() {
  const rect = dom.canvasStage.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  dom.imageCanvas.width = Math.max(1, Math.floor(rect.width * dpr));
  dom.imageCanvas.height = Math.max(1, Math.floor(rect.height * dpr));
}

function fitImageToCanvas() {
  resizeCanvasToStage();
  if (!activeImgObj) return;
  const cw = dom.imageCanvas.width;
  const ch = dom.imageCanvas.height;
  const scaleX = (cw * 0.94) / activeImgObj.naturalWidth;
  const scaleY = (ch * 0.94) / activeImgObj.naturalHeight;
  state.view.scale = Math.min(scaleX, scaleY);
  state.view.offsetX = (cw - activeImgObj.naturalWidth * state.view.scale) * 0.5;
  state.view.offsetY = (ch - activeImgObj.naturalHeight * state.view.scale) * 0.5;
}

function canvasClientToImagePx(clientX, clientY) {
  const rect = dom.imageCanvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const cx = (clientX - rect.left) * dpr;
  const cy = (clientY - rect.top) * dpr;
  const px = (cx - state.view.offsetX) / state.view.scale;
  const py = (cy - state.view.offsetY) / state.view.scale;
  return { cx, cy, px, py };
}

function drawCanvas() {
  const ctx = dom.imageCanvas.getContext("2d");
  ctx.clearRect(0, 0, dom.imageCanvas.width, dom.imageCanvas.height);
  if (!activeImgObj) return;

  ctx.save();
  ctx.imageSmoothingEnabled = state.view.scale < 2.5;
  ctx.translate(state.view.offsetX, state.view.offsetY);
  ctx.scale(state.view.scale, state.view.scale);
  ctx.drawImage(activeImgObj, 0, 0);
  ctx.restore();

  // Draw all GCP observations marked on this image
  for (const g of state.gcps) {
    const obs = g.observations.find((o) => o.image === state.activeImageName);
    if (!obs) continue;
    const sx = state.view.offsetX + obs.px * state.view.scale;
    const sy = state.view.offsetY + obs.py * state.view.scale;
    const isSel = g.id === state.selectedGcpId;
    const color = g.role === "check" ? "#38bdf8" : "#99CC00";

    ctx.save();
    ctx.strokeStyle = "#060907";
    ctx.lineWidth = 4;
    ctx.beginPath();
    ctx.moveTo(sx - 14, sy);
    ctx.lineTo(sx + 14, sy);
    ctx.moveTo(sx, sy - 14);
    ctx.lineTo(sx, sy + 14);
    ctx.stroke();

    ctx.strokeStyle = color;
    ctx.lineWidth = isSel ? 2.5 : 1.8;
    ctx.beginPath();
    ctx.moveTo(sx - 14, sy);
    ctx.lineTo(sx + 14, sy);
    ctx.moveTo(sx, sy - 14);
    ctx.lineTo(sx, sy + 14);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(sx, sy, isSel ? 8 : 5.5, 0, Math.PI * 2);
    ctx.stroke();

    ctx.font = "bold 12px Inter, sans-serif";
    const txt = g.label;
    const tw = ctx.measureText(txt).width;
    ctx.fillStyle = "rgba(8, 13, 10, 0.88)";
    ctx.fillRect(sx + 10, sy - 20, tw + 10, 18);
    ctx.strokeStyle = color;
    ctx.strokeRect(sx + 10, sy - 20, tw + 10, 18);
    ctx.fillStyle = "#ffffff";
    ctx.fillText(txt, sx + 15, sy - 7);
    ctx.restore();
  }
}

function drawLoupe(px, py) {
  if (!activeImgObj || px < 0 || py < 0 || px > activeImgObj.naturalWidth || py > activeImgObj.naturalHeight) {
    dom.loupeBox.style.display = "none";
    return;
  }
  dom.loupeBox.style.display = "block";
  const lctx = dom.loupeCanvas.getContext("2d");
  const lw = dom.loupeCanvas.width;
  const lh = dom.loupeCanvas.height;
  lctx.clearRect(0, 0, lw, lh);
  lctx.imageSmoothingEnabled = false;

  const patchRadius = 10; // 20x20 source pixels -> ~7.8x crisp pixel magnification
  lctx.drawImage(
    activeImgObj,
    px - patchRadius,
    py - patchRadius,
    patchRadius * 2,
    patchRadius * 2,
    0,
    0,
    lw,
    lh
  );

  // Sub-pixel crosshair
  lctx.strokeStyle = "#99CC00";
  lctx.lineWidth = 1.5;
  lctx.beginPath();
  lctx.moveTo(lw / 2, 0);
  lctx.lineTo(lw / 2, lh);
  lctx.moveTo(0, lh / 2);
  lctx.lineTo(lw, lh / 2);
  lctx.stroke();

  dom.loupeCoords.textContent = `px: ${px.toFixed(1)}, py: ${py.toFixed(1)}`;
}

function initCanvasInteractions() {
  window.addEventListener("resize", () => {
    if (activeImgObj) {
      resizeCanvasToStage();
      drawCanvas();
    }
  });

  dom.imageCanvas.addEventListener(
    "wheel",
    (ev) => {
      if (!activeImgObj) return;
      ev.preventDefault();
      const { cx, cy } = canvasClientToImagePx(ev.clientX, ev.clientY);
      const factor = ev.deltaY < 0 ? 1.22 : 1 / 1.22;
      const nextScale = Math.max(0.05, Math.min(40, state.view.scale * factor));
      state.view.offsetX = cx - ((cx - state.view.offsetX) / state.view.scale) * nextScale;
      state.view.offsetY = cy - ((cy - state.view.offsetY) / state.view.scale) * nextScale;
      state.view.scale = nextScale;
      drawCanvas();
    },
    { passive: false }
  );

  dom.imageCanvas.addEventListener("mousedown", (ev) => {
    if (!activeImgObj || ev.button !== 0) return;
    state.view.dragging = true;
    state.view.dragMoved = false;
    state.view.startX = ev.clientX;
    state.view.startY = ev.clientY;
  });

  window.addEventListener("mousemove", (ev) => {
    if (!activeImgObj) return;
    const { px, py } = canvasClientToImagePx(ev.clientX, ev.clientY);
    if (ev.target === dom.imageCanvas) {
      drawLoupe(px, py);
    } else {
      dom.loupeBox.style.display = "none";
    }
    if (state.view.dragging) {
      const dpr = window.devicePixelRatio || 1;
      const dx = (ev.clientX - state.view.startX) * dpr;
      const dy = (ev.clientY - state.view.startY) * dpr;
      if (Math.hypot(dx, dy) > 4) state.view.dragMoved = true;
      state.view.offsetX += dx;
      state.view.offsetY += dy;
      state.view.startX = ev.clientX;
      state.view.startY = ev.clientY;
      drawCanvas();
    }
  });

  window.addEventListener("mouseup", (ev) => {
    if (!state.view.dragging) return;
    const wasMoved = state.view.dragMoved;
    state.view.dragging = false;
    if (wasMoved || ev.target !== dom.imageCanvas || !activeImgObj) return;

    const { px, py } = canvasClientToImagePx(ev.clientX, ev.clientY);
    if (px < 0 || py < 0 || px > activeImgObj.naturalWidth || py > activeImgObj.naturalHeight) return;

    let sel = getSelectedGcp();
    if (!sel) {
      showToast("Select or create a Ground Control Point on the left first!", true);
      return;
    }
    const existing = sel.observations.find((o) => o.image === state.activeImageName);
    if (existing) {
      existing.px = Number(px.toFixed(2));
      existing.py = Number(py.toFixed(2));
      existing.image_width = activeImgObj.naturalWidth;
      existing.image_height = activeImgObj.naturalHeight;
    } else {
      sel.observations.push({
        image: state.activeImageName,
        px: Number(px.toFixed(2)),
        py: Number(py.toFixed(2)),
        image_width: activeImgObj.naturalWidth,
        image_height: activeImgObj.naturalHeight,
      });
    }
    renderGcpList();
    renderPhotoStrip();
    refreshMapMarkers(false);
    drawCanvas();
  });

  dom.btnResetZoom.addEventListener("click", () => {
    fitImageToCanvas();
    drawCanvas();
  });

  dom.btnClearObsOnImage.addEventListener("click", () => {
    const sel = getSelectedGcp();
    if (!sel || !state.activeImageName) return;
    sel.observations = sel.observations.filter((o) => o.image !== state.activeImageName);
    renderGcpList();
    renderPhotoStrip();
    refreshMapMarkers(false);
    drawCanvas();
  });
}

async function loadLocalImageFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  for (const file of files) {
    let gps = null;
    if (/\.jpe?g$/i.test(file.name)) {
      try {
        const sliceBuf = await file.slice(0, 131072).arrayBuffer();
        gps = extractJpegExifGps(sliceBuf);
      } catch (_e) {
        gps = null;
      }
    }
    const url = URL.createObjectURL(file);
    const existingIdx = state.images.findIndex((im) => im.name === file.name);
    const entry = {
      name: file.name,
      url,
      lat: gps ? gps.lat : null,
      lon: gps ? gps.lon : null,
      alt: gps ? gps.alt : null,
      width: null,
      height: null,
    };
    if (existingIdx >= 0) {
      state.images[existingIdx] = entry;
    } else {
      state.images.push(entry);
    }
  }
  if (!state.activeImageName && state.images.length) {
    selectImage(state.images[0].name);
  }
  renderPhotoStrip();
  refreshMapMarkers(true);
  showToast(`Loaded ${files.length} image${files.length === 1 ? "" : "s"}.`);
}

function downloadTextFile(filename, text, mime = "text/plain;charset=utf-8") {
  const blob = new Blob([text], { type: mime });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 500);
}

async function searchPlaceNominatim() {
  const q = dom.inpPlaceSearch.value.trim();
  if (!q || !map) return;
  const latLonMatch = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
  if (latLonMatch) {
    const lat = Number(latLonMatch[1]);
    const lon = Number(latLonMatch[2]);
    if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
      map.setView([lat, lon], 18);
      return;
    }
  }
  try {
    const resp = await fetch(
      `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`
    );
    const rows = await resp.json();
    if (Array.isArray(rows) && rows.length > 0) {
      map.setView([Number(rows[0].lat), Number(rows[0].lon)], 17);
    } else {
      showToast("Place not found.", true);
    }
  } catch (_err) {
    showToast("Geocoder temporarily unavailable.", true);
  }
}

async function initMosaicEarthTaskBridge() {
  const params = new URLSearchParams(window.location.search);
  const taskId = params.get("task_id") || params.get("project_id");
  const mode = (params.get("mode") || "photogrammetry").toLowerCase();
  if (!taskId) return;

  state.taskContext = { taskId, mode };
  dom.studioModeBadge.textContent =
    mode === "gaussian" ? `Task #${taskId} · 3D Gaussian Splatting` : `Task #${taskId} · Photogrammetry`;
  dom.btnAttachTask.style.display = "inline-flex";

  window.addEventListener("message", (ev) => {
    if (ev.origin !== window.location.origin || !ev.data) return;
    if (ev.data.type === "mosaicearth:local-images" && Array.isArray(ev.data.files) && ev.data.files.length) {
      loadLocalImageFiles(ev.data.files);
    }
  });

  try {
    const resp = await fetch(`/api/v2/tasks/${encodeURIComponent(taskId)}/gcp-studio-context`, {
      credentials: "same-origin",
    });
    if (!resp.ok) {
      showToast(`Could not load task #${taskId} context (HTTP ${resp.status}).`, true);
      return;
    }
    const data = await resp.json();
    if (data.title) {
      dom.studioSubtitle.textContent = `Connected to "${data.title}" (${
        mode === "gaussian" ? "Metric Sim(3) 3DGS Alignment" : "OpenDroneMap Bundle Adjustment"
      })`;
    }
    if (Array.isArray(data.images) && data.images.length) {
      state.images = data.images.map((im) => ({
        name: im.name,
        url: im.url,
        lat: im.lat ?? null,
        lon: im.lon ?? null,
        alt: im.alt ?? null,
        width: im.width ?? null,
        height: im.height ?? null,
      }));
      selectImage(state.images[0].name);
    } else if (window.opener && !window.opener.closed) {
      window.opener.postMessage({ type: "mosaicearth:gcp-studio-ready", taskId, mode }, window.location.origin);
    }
    if (data.gcp_json && Array.isArray(data.gcp_json.points) && data.gcp_json.points.length) {
      syncCrsDropdownValue(data.gcp_json.crs || "EPSG:4326");
      state.gcps = data.gcp_json.points.map((p, idx) => {
        const wgs =
          p.lat != null && p.lon != null
            ? { lat: Number(p.lat), lon: Number(p.lon) }
            : crsCoordsToWgs84(p.x, p.y, state.crs);
        return {
          id: `gcp_loaded_${idx + 1}`,
          label: p.label || `GCP_${String(idx + 1).padStart(2, "0")}`,
          role: p.role || "gcp",
          x: Number(p.x),
          y: Number(p.y),
          z: Number(p.z || 0),
          lat: wgs ? wgs.lat : null,
          lon: wgs ? wgs.lon : null,
          observations: Array.isArray(p.observations) ? p.observations : [],
        };
      });
      state.selectedGcpId = state.gcps[0]?.id || null;
    } else if (data.gcp_list_txt) {
      const parsed = parseSurveyorCsvOrOdmTxt(data.gcp_list_txt, state.crs);
      if (parsed.gcps.length) {
        syncCrsDropdownValue(parsed.crs);
        state.gcps = parsed.gcps;
        state.selectedGcpId = state.gcps[0].id;
      }
    } else if (Number.isFinite(data.lat) && Number.isFinite(data.lon) && (data.lat !== 0 || data.lon !== 0) && map) {
      map.setView([data.lat, data.lon], 17);
    }
    renderAll();
    refreshMapMarkers(true);
  } catch (err) {
    showToast(`Failed loading task images: ${err.message || err}`, true);
  }
}

async function saveAndAttachToMosaicEarthTask() {
  if (!state.taskContext) return;
  const controlWithObs = state.gcps.filter((g) => g.role !== "check" && g.observations.length >= 2);
  if (controlWithObs.length < 3) {
    showToast(
      "Mark at least 3 Ground Control Points in 2+ images each (3+ recommended) for metric 3D georeferencing!",
      true
    );
    return;
  }

  dom.btnAttachTask.disabled = true;
  const origLabel = dom.btnAttachTask.textContent;
  dom.btnAttachTask.textContent = "Saving GCPs...";

  try {
    const gcpListTxt = exportOdmGcpListTxt(state.crs, state.gcps, { includeCheckPoints: false });
    const gcpJsonStr = exportColmap3dgsGcpJson(state.crs, state.gcps);
    const resp = await fetch(`/api/v2/tasks/${encodeURIComponent(state.taskContext.taskId)}/gcp-control`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: state.taskContext.mode,
        crs: state.crs,
        gcp_list_txt: gcpListTxt,
        gcp_json: JSON.parse(gcpJsonStr),
      }),
    });
    const resData = await resp.json().catch(() => ({}));
    if (!resp.ok || resData.ok === false) {
      throw new Error(resData.error || `HTTP ${resp.status}`);
    }

    if (window.opener && !window.opener.closed) {
      window.opener.postMessage(
        {
          type: "mosaicearth:gcp-attached",
          taskId: state.taskContext.taskId,
          mode: state.taskContext.mode,
          gcpCount: controlWithObs.length,
          checkCount: state.gcps.filter((g) => g.role === "check").length,
          observationCount: state.gcps.reduce((a, g) => a + g.observations.length, 0),
          centroid: resData.centroid || null,
          crs: state.crs,
        },
        window.location.origin
      );
    }

    showToast(`Attached ${controlWithObs.length} GCPs to Task #${state.taskContext.taskId}! Returning to task...`);
    setTimeout(() => {
      if (window.opener && !window.opener.closed) {
        window.close();
      }
    }, 900);
  } catch (err) {
    showToast(`Save failed: ${err.message || err}`, true);
  } finally {
    dom.btnAttachTask.disabled = false;
    dom.btnAttachTask.textContent = origLabel;
  }
}

function renderAll() {
  renderGcpList();
  renderPhotoStrip();
  refreshMapMarkers(false);
  drawCanvas();
}

function wireToolbarEvents() {
  dom.btnLoadImages.addEventListener("click", () => dom.inpImages.click());
  dom.inpImages.addEventListener("change", () => {
    loadLocalImageFiles(dom.inpImages.files);
    dom.inpImages.value = "";
  });

  dom.btnImportCsv.addEventListener("click", () => dom.inpCsv.click());
  dom.inpCsv.addEventListener("change", async () => {
    const f = dom.inpCsv.files?.[0];
    dom.inpCsv.value = "";
    if (!f) return;
    const raw = await f.text();
    const parsed = parseSurveyorCsvOrOdmTxt(raw, state.crs);
    if (!parsed.gcps.length) {
      showToast("No valid coordinates found in CSV/TXT file.", true);
      return;
    }
    syncCrsDropdownValue(parsed.crs);
    state.gcps = [...state.gcps, ...parsed.gcps];
    state.selectedGcpId = parsed.gcps[0].id;
    renderAll();
    refreshMapMarkers(true);
    showToast(`Imported ${parsed.gcps.length} points (${parsed.crs}).`);
  });

  dom.btnAddManualGcp.addEventListener("click", () => {
    const center = map ? map.getCenter() : { lat: 41.8, lng: 1.8 };
    addGcpFromLatLon(center.lat, center.lng);
  });

  dom.btnPickOnMap.addEventListener("click", () => setPickMapMode(!state.pickMapMode));
  window.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && state.pickMapMode) setPickMapMode(false);
  });

  dom.btnPlaceSearch.addEventListener("click", searchPlaceNominatim);
  dom.inpPlaceSearch.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      searchPlaceNominatim();
    }
  });

  dom.btnExportOdm.addEventListener("click", () => {
    const txt = exportOdmGcpListTxt(state.crs, state.gcps, { includeCheckPoints: false });
    downloadTextFile("gcp_list.txt", txt);
  });

  dom.btnExport3dgs.addEventListener("click", () => {
    const js = exportColmap3dgsGcpJson(state.crs, state.gcps);
    downloadTextFile("gcp_observations_3dgs.json", js, "application/json;charset=utf-8");
  });

  dom.btnAttachTask.addEventListener("click", saveAndAttachToMosaicEarthTask);
}

initCrsSelector();
initMap();
initCanvasInteractions();
wireToolbarEvents();
renderAll();
initMosaicEarthTaskBridge();
