/**
 * MosaicEarth GCP Studio — Geodetic CRS, CSV/ODM Parser & EXIF Utilities
 * License: MIT
 */

const WGS84_A = 6378137.0;
const WGS84_F = 1.0 / 298.257223563;
const WGS84_E2 = WGS84_F * (2.0 - WGS84_F);
const WGS84_EP2 = WGS84_E2 / (1.0 - WGS84_E2);
const UTM_K0 = 0.9996;

export const COMMON_CRS_CATALOG = [
  { code: "EPSG:4326", label: "EPSG:4326 — WGS 84 (Longitude / Latitude, deg)", kind: "geographic" },
  { code: "EPSG:25831", label: "EPSG:25831 — ETRS89 / UTM Zone 31N (Catalonia, Balearics, W. Europe)", kind: "utm", zone: 31, north: true },
  { code: "EPSG:25830", label: "EPSG:25830 — ETRS89 / UTM Zone 30N (Central Spain, UK, France W)", kind: "utm", zone: 30, north: true },
  { code: "EPSG:25829", label: "EPSG:25829 — ETRS89 / UTM Zone 29N (Galicia, Portugal, Ireland)", kind: "utm", zone: 29, north: true },
  { code: "EPSG:25832", label: "EPSG:25832 — ETRS89 / UTM Zone 32N (Central Europe, Italy, Germany)", kind: "utm", zone: 32, north: true },
  { code: "EPSG:25833", label: "EPSG:25833 — ETRS89 / UTM Zone 33N (E. Germany, Austria, Poland)", kind: "utm", zone: 33, north: true },
  { code: "EPSG:32631", label: "EPSG:32631 — WGS 84 / UTM Zone 31N", kind: "utm", zone: 31, north: true },
  { code: "EPSG:32630", label: "EPSG:32630 — WGS 84 / UTM Zone 30N", kind: "utm", zone: 30, north: true },
  { code: "EPSG:32632", label: "EPSG:32632 — WGS 84 / UTM Zone 32N", kind: "utm", zone: 32, north: true },
  { code: "EPSG:32633", label: "EPSG:32633 — WGS 84 / UTM Zone 33N", kind: "utm", zone: 33, north: true },
  { code: "EPSG:32610", label: "EPSG:32610 — WGS 84 / UTM Zone 10N (US West Coast)", kind: "utm", zone: 10, north: true },
  { code: "EPSG:32617", label: "EPSG:32617 — WGS 84 / UTM Zone 17N (US East / Toronto)", kind: "utm", zone: 17, north: true },
  { code: "EPSG:32618", label: "EPSG:32618 — WGS 84 / UTM Zone 18N (US Northeast / Colombia)", kind: "utm", zone: 18, north: true },
  { code: "EPSG:32719", label: "EPSG:32719 — WGS 84 / UTM Zone 19S (Chile / Argentina)", kind: "utm", zone: 19, north: false },
  { code: "EPSG:32755", label: "EPSG:32755 — WGS 84 / UTM Zone 55S (Australia East)", kind: "utm", zone: 55, north: false },
  { code: "EPSG:3857", label: "EPSG:3857 — WGS 84 / Pseudo-Mercator (Web Mercator, m)", kind: "webmercator" },
];

export function parseEpsgUtmInfo(crsStr) {
  const clean = String(crsStr || "").trim().toUpperCase();
  if (clean === "EPSG:4326" || clean === "WGS84") {
    return { kind: "geographic", code: "EPSG:4326" };
  }
  if (clean === "EPSG:3857") {
    return { kind: "webmercator", code: "EPSG:3857" };
  }
  const baseMatch = clean.match(/EPSG:(\d{4,5})/);
  if (baseMatch) {
    const codeNum = parseInt(baseMatch[1], 10);
    if (codeNum === 4326 || codeNum === 4258 || codeNum === 4979) {
      return { kind: "geographic", code: clean };
    }
    if (codeNum === 3857) {
      return { kind: "webmercator", code: clean };
    }
    // ETRS89 UTM 28N..38N: EPSG:25828..25838 (millimeter-identical ellipsoid parameters to WGS84 for map preview & Sim3)
    if (codeNum >= 25828 && codeNum <= 25838) {
      return { kind: "utm", zone: codeNum - 25800, north: true, code: clean };
    }
    // WGS84 UTM North: EPSG:32601..32660
    if (codeNum >= 32601 && codeNum <= 32660) {
      return { kind: "utm", zone: codeNum - 32600, north: true, code: clean };
    }
    // WGS84 UTM South: EPSG:32701..32760
    if (codeNum >= 32701 && codeNum <= 32760) {
      return { kind: "utm", zone: codeNum - 32700, north: false, code: clean };
    }
  }
  const wgsUtm = clean.match(/WGS\s*84\s+UTM\s+(\d{1,2})\s*([NS])/i);
  if (wgsUtm) {
    return { kind: "utm", zone: parseInt(wgsUtm[1], 10), north: wgsUtm[2].toUpperCase() === "N", code: clean };
  }
  return { kind: "custom", code: clean || "EPSG:4326" };
}

export function wgs84ToUtm(latDeg, lonDeg, forcedZone = null, forcedNorth = null) {
  const lat = Number(latDeg);
  const lon = Number(lonDeg);
  const zone = forcedZone || Math.max(1, Math.min(60, Math.floor((lon + 180.0) / 6.0) + 1));
  const north = forcedNorth !== null && forcedNorth !== undefined ? Boolean(forcedNorth) : lat >= 0.0;
  const phi = (lat * Math.PI) / 180.0;
  const lam = (lon * Math.PI) / 180.0;
  const lam0 = (((zone - 1) * 6 - 180 + 3) * Math.PI) / 180.0;

  const sinPhi = Math.sin(phi);
  const cosPhi = Math.cos(phi);
  const tanPhi = Math.tan(phi);

  const N = WGS84_A / Math.sqrt(1.0 - WGS84_E2 * sinPhi * sinPhi);
  const T = tanPhi * tanPhi;
  const C = WGS84_EP2 * cosPhi * cosPhi;
  const A = cosPhi * (lam - lam0);

  const e4 = WGS84_E2 * WGS84_E2;
  const e6 = e4 * WGS84_E2;
  const M =
    WGS84_A *
    ((1.0 - WGS84_E2 / 4.0 - (3.0 * e4) / 64.0 - (5.0 * e6) / 256.0) * phi -
      ((3.0 * WGS84_E2) / 8.0 + (3.0 * e4) / 32.0 + (45.0 * e6) / 1024.0) * Math.sin(2.0 * phi) +
      ((15.0 * e4) / 256.0 + (45.0 * e6) / 1024.0) * Math.sin(4.0 * phi) -
      ((35.0 * e6) / 3072.0) * Math.sin(6.0 * phi));

  const easting =
    500000.0 +
    UTM_K0 *
      N *
      (A +
        ((1.0 - T + C) * Math.pow(A, 3)) / 6.0 +
        ((5.0 - 18.0 * T + T * T + 72.0 * C - 58.0 * WGS84_EP2) * Math.pow(A, 5)) / 120.0);

  let northing =
    UTM_K0 *
    (M +
      N *
        tanPhi *
        ((A * A) / 2.0 +
          ((5.0 - T + 9.0 * C + 4.0 * C * C) * Math.pow(A, 4)) / 24.0 +
          ((61.0 - 58.0 * T + T * T + 600.0 * C - 330.0 * WGS84_EP2) * Math.pow(A, 6)) / 720.0));

  if (!north) {
    northing += 10000000.0;
  }
  return { easting, northing, zone, north };
}

export function utmToWgs84(easting, northing, zone, north = true) {
  const x = Number(easting) - 500000.0;
  const y = north ? Number(northing) : Number(northing) - 10000000.0;
  const M = y / UTM_K0;
  const e4 = WGS84_E2 * WGS84_E2;
  const e6 = e4 * WGS84_E2;
  const mu = M / (WGS84_A * (1.0 - WGS84_E2 / 4.0 - (3.0 * e4) / 64.0 - (5.0 * e6) / 256.0));
  const e1 = (1.0 - Math.sqrt(1.0 - WGS84_E2)) / (1.0 + Math.sqrt(1.0 - WGS84_E2));

  const phi1 =
    mu +
    ((3.0 * e1) / 2.0 - (27.0 * Math.pow(e1, 3)) / 32.0) * Math.sin(2.0 * mu) +
    ((21.0 * e1 * e1) / 16.0 - (55.0 * Math.pow(e1, 4)) / 32.0) * Math.sin(4.0 * mu) +
    ((151.0 * Math.pow(e1, 3)) / 96.0) * Math.sin(6.0 * mu) +
    ((1097.0 * Math.pow(e1, 4)) / 512.0) * Math.sin(8.0 * mu);

  const sin1 = Math.sin(phi1);
  const cos1 = Math.cos(phi1);
  const tan1 = Math.tan(phi1);
  const N1 = WGS84_A / Math.sqrt(1.0 - WGS84_E2 * sin1 * sin1);
  const T1 = tan1 * tan1;
  const C1 = WGS84_EP2 * cos1 * cos1;
  const R1 = (WGS84_A * (1.0 - WGS84_E2)) / Math.pow(1.0 - WGS84_E2 * sin1 * sin1, 1.5);
  const D = x / (N1 * UTM_K0);

  const phi =
    phi1 -
    ((N1 * tan1) / R1) *
      ((D * D) / 2.0 -
        ((5.0 + 3.0 * T1 + 10.0 * C1 - 4.0 * C1 * C1 - 9.0 * WGS84_EP2) * Math.pow(D, 4)) / 24.0 +
        ((61.0 + 90.0 * T1 + 298.0 * C1 + 45.0 * T1 * T1 - 252.0 * WGS84_EP2 - 3.0 * C1 * C1) * Math.pow(D, 6)) /
          720.0);

  const lam0 = (((zone - 1) * 6 - 180 + 3) * Math.PI) / 180.0;
  const lam =
    lam0 +
    (D -
      ((1.0 + 2.0 * T1 + C1) * Math.pow(D, 3)) / 6.0 +
      ((5.0 - 2.0 * C1 + 28.0 * T1 - 3.0 * C1 * C1 + 8.0 * WGS84_EP2 + 24.0 * T1 * T1) * Math.pow(D, 5)) / 120.0) /
      cos1;

  return {
    lat: (phi * 180.0) / Math.PI,
    lon: (lam * 180.0) / Math.PI,
  };
}

export function crsCoordsToWgs84(xOrLon, yOrLat, crsStr) {
  const x = Number(xOrLon);
  const y = Number(yOrLat);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  const info = parseEpsgUtmInfo(crsStr);
  if (info.kind === "geographic") {
    // Note: in OpenDroneMap gcp_list.txt with EPSG:4326, X = longitude, Y = latitude!
    if (Math.abs(x) <= 180 && Math.abs(y) <= 90) return { lat: y, lon: x };
    if (Math.abs(y) <= 180 && Math.abs(x) <= 90) return { lat: x, lon: y };
    return null;
  }
  if (info.kind === "webmercator") {
    const lon = (x / 20037508.342789244) * 180.0;
    let lat = (y / 20037508.342789244) * 180.0;
    lat = (180.0 / Math.PI) * (2.0 * Math.atan(Math.exp((lat * Math.PI) / 180.0)) - Math.PI / 2.0);
    return { lat, lon };
  }
  if (info.kind === "utm") {
    return utmToWgs84(x, y, info.zone, info.north);
  }
  if (Math.abs(x) <= 180 && Math.abs(y) <= 90) {
    return { lat: y, lon: x };
  }
  return null;
}

export function wgs84ToCrsCoords(latDeg, lonDeg, crsStr) {
  const lat = Number(latDeg);
  const lon = Number(lonDeg);
  const info = parseEpsgUtmInfo(crsStr);
  if (info.kind === "geographic") {
    return { x: Number(lon.toFixed(8)), y: Number(lat.toFixed(8)) };
  }
  if (info.kind === "webmercator") {
    const x = (lon * 20037508.342789244) / 180.0;
    const y =
      ((Math.log(Math.tan(((90.0 + lat) * Math.PI) / 360.0)) / (Math.PI / 180.0)) * 20037508.342789244) / 180.0;
    return { x: Number(x.toFixed(3)), y: Number(y.toFixed(3)) };
  }
  if (info.kind === "utm") {
    const u = wgs84ToUtm(lat, lon, info.zone, info.north);
    return { x: Number(u.easting.toFixed(3)), y: Number(u.northing.toFixed(3)) };
  }
  return { x: Number(lon.toFixed(8)), y: Number(lat.toFixed(8)) };
}

export function haversineMeters(lat1, lon1, lat2, lon2) {
  const R = 6371000.0;
  const p1 = (lat1 * Math.PI) / 180.0;
  const p2 = (lat2 * Math.PI) / 180.0;
  const dp = ((lat2 - lat1) * Math.PI) / 180.0;
  const dl = ((lon2 - lon1) * Math.PI) / 180.0;
  const a = Math.sin(dp * 0.5) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl * 0.5) ** 2;
  return 2.0 * R * Math.atan2(Math.sqrt(a), Math.sqrt(Math.max(0.0, 1.0 - a)));
}

/**
 * Parses either:
 *   A) An existing OpenDroneMap `gcp_list.txt` (CRS line + `X Y Z px py image_name [label]`)
 *   B) A surveyor CSV / TXT table (`Label, Easting/Lon, Northing/Lat, Elevation` or `X, Y, Z`)
 */
export function parseSurveyorCsvOrOdmTxt(rawText, defaultCrs = "EPSG:4326") {
  const lines = String(rawText || "")
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith("#"));

  if (!lines.length) {
    return { crs: defaultCrs, gcps: [] };
  }

  // Case A: OpenDroneMap gcp_list.txt detection
  const firstUpper = lines[0].toUpperCase();
  if (
    lines.length >= 2 &&
    (firstUpper.startsWith("EPSG:") || firstUpper.startsWith("+PROJ=") || firstUpper.startsWith("WGS84"))
  ) {
    const crs = lines[0];
    const byKey = new Map();
    let autoIdx = 1;
    for (let i = 1; i < lines.length; i++) {
      const parts = lines[i].split(/\s+/);
      if (parts.length >= 6) {
        const x = Number(parts[0]);
        const y = Number(parts[1]);
        const z = Number(parts[2]);
        const px = Number(parts[3]);
        const py = Number(parts[4]);
        const imgName = parts[5];
        const label = parts[6] || `GCP_${String(autoIdx++).padStart(2, "0")}`;
        if ([x, y, z, px, py].every(Number.isFinite)) {
          const key = `${label}::${x.toFixed(4)}::${y.toFixed(4)}`;
          if (!byKey.has(key)) {
            const wgs = crsCoordsToWgs84(x, y, crs);
            byKey.set(key, {
              id: `gcp_${byKey.size + 1}`,
              label,
              role: label.toUpperCase().startsWith("CHK") || label.toUpperCase().includes("CHECK") ? "check" : "gcp",
              x,
              y,
              z,
              lat: wgs ? wgs.lat : null,
              lon: wgs ? wgs.lon : null,
              observations: [],
            });
          }
          byKey.get(key).observations.push({ image: imgName, px, py });
        }
      }
    }
    if (byKey.size > 0) {
      return { crs, gcps: Array.from(byKey.values()) };
    }
  }

  // Case B: Surveyor CSV / semicolon / tab / whitespace table
  let crs = defaultCrs;
  const gcps = [];
  let startIdx = 0;

  const splitRow = (row) => {
    if (row.includes(";")) return row.split(";").map((s) => s.trim());
    if (row.includes(",")) return row.split(",").map((s) => s.trim());
    if (row.includes("\t")) return row.split("\t").map((s) => s.trim());
    return row.split(/\s+/).map((s) => s.trim());
  };

  const firstTokens = splitRow(lines[0]);
  const hasHeader = firstTokens.some((tok) => /[A-Za-z]/.test(tok) && !Number.isFinite(Number(tok)));
  let colMap = { label: 0, x: 1, y: 2, z: 3, role: -1 };

  if (hasHeader && lines.length > 1) {
    // Check if first row is actually a data row like `GCP01, 431200.5, 4582100.2, 125.4`
    const numCount = firstTokens.filter((t) => t !== "" && Number.isFinite(Number(t))).length;
    if (numCount < 2) {
      startIdx = 1;
      const hdr = firstTokens.map((h) => h.toLowerCase());
      const findCol = (patterns, fallback) => {
        const idx = hdr.findIndex((h) => patterns.some((p) => h.includes(p)));
        return idx >= 0 ? idx : fallback;
      };
      colMap = {
        label: findCol(["id", "name", "label", "point", "nom", "code", "pt"], -1),
        x: findCol(["east", "lon", "lng", "x"], 1),
        y: findCol(["north", "lat", "y"], 2),
        z: findCol(["elev", "alt", "height", "z", "cota", "orthometric"], 3),
        role: findCol(["role", "type", "kind"], -1),
      };
      // Notice: if surveyor CSV has `Lat, Lon` order in geographic mode, `x` (`lon`) and `y` (`lat`) are mapped properly!
    }
  }

  for (let i = startIdx; i < lines.length; i++) {
    const cols = splitRow(lines[i]);
    if (cols.length < 2) continue;
    let label = "";
    let xVal, yVal, zVal = 0.0;
    let role = "gcp";

    if (startIdx === 1) {
      label = colMap.label >= 0 && cols[colMap.label] ? cols[colMap.label] : `GCP_${String(gcps.length + 1).padStart(2, "0")}`;
      xVal = Number(String(cols[colMap.x] ?? "").replace(",", "."));
      yVal = Number(String(cols[colMap.y] ?? "").replace(",", "."));
      zVal = cols[colMap.z] !== undefined && cols[colMap.z] !== "" ? Number(String(cols[colMap.z]).replace(",", ".")) : 0.0;
      if (colMap.role >= 0 && cols[colMap.role]) {
        const rLow = cols[colMap.role].toLowerCase();
        if (rLow.includes("check") || rLow === "chk" || rLow === "qa") role = "check";
      }
    } else {
      // Heuristic: if col[0] is non-numeric label -> `Label, X, Y[, Z]`; else `X, Y[, Z]`
      const c0Num = Number(cols[0]);
      if (!Number.isFinite(c0Num) && cols.length >= 3) {
        label = cols[0];
        xVal = Number(cols[1]);
        yVal = Number(cols[2]);
        zVal = cols.length >= 4 && Number.isFinite(Number(cols[3])) ? Number(cols[3]) : 0.0;
      } else {
        label = `GCP_${String(gcps.length + 1).padStart(2, "0")}`;
        xVal = c0Num;
        yVal = Number(cols[1]);
        zVal = cols.length >= 3 && Number.isFinite(Number(cols[2])) ? Number(cols[2]) : 0.0;
      }
      // If geographic (EPSG:4326) and first number looks like Latitude (e.g. 41.8) while second is small Longitude (e.g. 2.1), keep X=lon, Y=lat consistent if unambiguous, otherwise trust X=Lon/East, Y=Lat/North
      if (parseEpsgUtmInfo(crs).kind === "geographic" && Math.abs(xVal) > 35 && Math.abs(xVal) <= 90 && Math.abs(yVal) <= 35) {
        // Likely Lat, Lon order in geographic CSV (e.g. 41.8205, 1.8676)
        const tmp = xVal;
        xVal = yVal;
        yVal = tmp;
      }
    }

    if (!Number.isFinite(xVal) || !Number.isFinite(yVal)) continue;
    if (!Number.isFinite(zVal)) zVal = 0.0;
    if (label.toUpperCase().startsWith("CHK") || label.toUpperCase().includes("CHECK")) {
      role = "check";
    }
    const wgs = crsCoordsToWgs84(xVal, yVal, crs);
    gcps.push({
      id: `gcp_${gcps.length + 1}`,
      label: label.replace(/\s+/g, "_"),
      role,
      x: xVal,
      y: yVal,
      z: zVal,
      lat: wgs ? wgs.lat : null,
      lon: wgs ? wgs.lon : null,
      observations: [],
    });
  }

  return { crs, gcps };
}

/**
 * Generates standard OpenDroneMap `gcp_list.txt` text.
 * Note: Only `role !== 'check'` control points are written to `gcp_list.txt` by default unless `includeCheckPoints` is set.
 */
export function exportOdmGcpListTxt(crs, gcps, { includeCheckPoints = false } = {}) {
  const header = String(crs || "EPSG:4326").trim();
  const rows = [header];
  for (const g of gcps || []) {
    if (!includeCheckPoints && g.role === "check") continue;
    for (const obs of g.observations || []) {
      if (!obs.image || !Number.isFinite(obs.px) || !Number.isFinite(obs.py)) continue;
      const safeLabel = String(g.label || "GCP").replace(/\s+/g, "_");
      rows.push(
        `${Number(g.x).toFixed(6)} ${Number(g.y).toFixed(6)} ${Number(g.z || 0).toFixed(4)} ${Math.round(obs.px)} ${Math.round(obs.py)} ${obs.image} ${safeLabel}`
      );
    }
  }
  return rows.join("\n") + "\n";
}

/**
 * Generates structured JSON for COLMAP / 3D Gaussian Splatting metric Sim(3) triangulation & QA Check Points.
 */
export function exportColmap3dgsGcpJson(crs, gcps) {
  return JSON.stringify(
    {
      schema: "mosaicearth.gcp.v1",
      crs: String(crs || "EPSG:4326").trim(),
      generatedAt: new Date().toISOString(),
      points: (gcps || []).map((g) => {
        const wgs = g.lat != null && g.lon != null ? { lat: g.lat, lon: g.lon } : crsCoordsToWgs84(g.x, g.y, crs);
        return {
          label: g.label,
          role: g.role || "gcp",
          x: Number(g.x),
          y: Number(g.y),
          z: Number(g.z || 0),
          lat: wgs ? Number(wgs.lat.toFixed(8)) : null,
          lon: wgs ? Number(wgs.lon.toFixed(8)) : null,
          observations: (g.observations || []).map((o) => ({
            image: o.image,
            px: Number(o.px.toFixed(2)),
            py: Number(o.py.toFixed(2)),
            image_width: o.image_width || null,
            image_height: o.image_height || null,
          })),
        };
      }),
    },
    null,
    2
  );
}

/**
 * Lightweight browser/Node JPEG EXIF GPS reader (reads first 128 KB of a JPEG ArrayBuffer).
 */
export function extractJpegExifGps(arrayBuffer) {
  const view = new DataView(arrayBuffer);
  if (view.byteLength < 12 || view.getUint16(0, false) !== 0xffd8) return null;
  let offset = 2;
  while (offset + 4 < view.byteLength) {
    const marker = view.getUint16(offset, false);
    offset += 2;
    if ((marker & 0xff00) !== 0xff00) break;
    if (marker === 0xffda || marker === 0xffd9) break;
    const segLen = view.getUint16(offset, false);
    if (segLen < 2 || offset + segLen > view.byteLength) break;
    if (
      marker === 0xffe1 &&
      segLen >= 14 &&
      view.getUint32(offset + 2, false) === 0x45786966 &&
      view.getUint16(offset + 6, false) === 0x0000
    ) {
      const tiffStart = offset + 8;
      const little = view.getUint16(tiffStart, false) === 0x4949;
      const getU16 = (p) => view.getUint16(p, little);
      const getU32 = (p) => view.getUint32(p, little);
      if (getU16(tiffStart + 2) !== 0x002a) return null;
      const ifd0Off = tiffStart + getU32(tiffStart + 4);
      if (ifd0Off + 2 > view.byteLength) return null;
      const nEntries = getU16(ifd0Off);
      let gpsIfdPtr = 0;
      for (let i = 0; i < nEntries; i++) {
        const ep = ifd0Off + 2 + i * 12;
        if (ep + 12 > view.byteLength) break;
        if (getU16(ep) === 0x8825) {
          gpsIfdPtr = tiffStart + getU32(ep + 8);
          break;
        }
      }
      if (!gpsIfdPtr || gpsIfdPtr + 2 > view.byteLength) return null;
      const gpsCount = getU16(gpsIfdPtr);
      let latRef = "N",
        lonRef = "E",
        altRef = 0,
        latDeg = null,
        lonDeg = null,
        altM = 0.0;
      const readRationals = (valOff, count) => {
        const out = [];
        for (let k = 0; k < count; k++) {
          const p = valOff + k * 8;
          if (p + 8 > view.byteLength) return null;
          const num = getU32(p);
          const den = getU32(p + 4);
          out.push(den ? num / den : 0);
        }
        return out;
      };
      for (let j = 0; j < gpsCount; j++) {
        const gp = gpsIfdPtr + 2 + j * 12;
        if (gp + 12 > view.byteLength) break;
        const tag = getU16(gp);
        const cnt = getU32(gp + 4);
        if (tag === 1) {
          const ch = view.getUint8(gp + 8);
          if (ch === 83 || ch === 115) latRef = "S";
        } else if (tag === 2 && cnt === 3) {
          const r = readRationals(tiffStart + getU32(gp + 8), 3);
          if (r) latDeg = r[0] + r[1] / 60.0 + r[2] / 3600.0;
        } else if (tag === 3) {
          const ch = view.getUint8(gp + 8);
          if (ch === 87 || ch === 119) lonRef = "W";
        } else if (tag === 4 && cnt === 3) {
          const r = readRationals(tiffStart + getU32(gp + 8), 3);
          if (r) lonDeg = r[0] + r[1] / 60.0 + r[2] / 3600.0;
        } else if (tag === 5) {
          altRef = view.getUint8(gp + 8);
        } else if (tag === 6 && cnt === 1) {
          const r = readRationals(tiffStart + getU32(gp + 8), 1);
          if (r) altM = r[0];
        }
      }
      if (latDeg !== null && lonDeg !== null && (latDeg !== 0 || lonDeg !== 0)) {
        const lat = latRef === "S" ? -latDeg : latDeg;
        const lon = lonRef === "W" ? -lonDeg : lonDeg;
        const alt = altRef === 1 ? -altM : altM;
        if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
          return { lat, lon, alt };
        }
      }
      return null;
    }
    offset += segLen;
  }
  return null;
}
