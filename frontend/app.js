"use strict";

const $ = (s) => document.querySelector(s);
const COLOURS = {
  "burned land": "rgb(230,85,13)", "new water": "rgb(33,113,181)", "water gone": "rgb(158,202,225)",
  "vegetation loss": "rgb(166,97,26)", "new building or bare ground": "rgb(203,24,29)",
};
const LAYER_NAMES = { before: "Before", after: "After", change: "Change", refined: "SAM outlines", unseen: "Not seen" };

const state = { bbox: null, run: null, overlays: {}, regionsLayer: null, drawing: null, health: {}, gen: 0, busy: false };

// fetch JSON; throws an Error with the server's reason on any failure
async function api(url, options) {
  let r;
  try { r = await fetch(url, options); } catch (e) { throw new Error("The server could not be reached."); }
  let j = null;
  try { j = await r.json(); } catch (e) { /* not JSON */ }
  if (!r.ok) throw new Error(j ? errorText(j) : `Request failed (${r.status}).`);
  return j;
}

// ---------- map ----------
const map = L.map("map", { zoomControl: true }).setView([22, 90], 5);
const satellite = L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { maxZoom: 18, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" });
const streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
  { maxZoom: 19, attribution: "© OpenStreetMap contributors" });
satellite.addTo(map);
L.control.layers({ "Satellite basemap": satellite, "Street map": streets }, {}, { position: "topright" }).addTo(map);
const areaRect = L.rectangle([[0, 0], [0, 0]], { color: "#f08c3a", weight: 2, fill: false, dashArray: "6 4" });

function setArea(bbox) {
  state.bbox = bbox.map((v) => +(+v).toFixed(5));
  const [w, s, e, n] = state.bbox;
  areaRect.setBounds([[s, w], [n, e]]).addTo(map);
  const km2 = (e - w) * 111.32 * Math.cos(((s + n) / 2) * Math.PI / 180) * (n - s) * 111.32;
  const max = state.health.max_area_km2 || 400;
  $("#area").textContent = `${km2.toFixed(0)} km²` + (km2 > max ? ` (too big, max ${max})` : "");
  $("#area").className = km2 > max ? "small conf-unsure" : "muted small";
  updateGo();
}

function updateGo() {
  $("#go").disabled = state.busy || !(state.bbox && $("#before").value && $("#after").value);
}

// Two clicks draw the area: first corner, then the opposite corner.
$("#draw").addEventListener("click", () => {
  state.drawing = [];
  document.body.classList.add("drawing");
  $("#status").textContent = "Click one corner of the area, then the opposite corner.";
});
map.on("click", (ev) => {
  if (!state.drawing) return;
  state.drawing.push(ev.latlng);
  if (state.drawing.length === 2) {
    const [a, b] = state.drawing;
    setArea([Math.min(a.lng, b.lng), Math.min(a.lat, b.lat), Math.max(a.lng, b.lng), Math.max(a.lat, b.lat)]);
    state.drawing = null;
    document.body.classList.remove("drawing");
    $("#status").textContent = "";
  }
});

// ---------- demos ----------
async function loadDemos() {
  let demos = [];
  try { demos = await api("/api/demos"); } catch (e) { setStatus(e.message, true); }
  for (const d of demos) $("#demo").add(new Option(d.title, d.id));
  $("#demo").addEventListener("change", () => {
    const d = demos.find((x) => x.id === $("#demo").value);
    $("#demo-story").textContent = d ? d.story : "";
    if (!d) return;
    $("#before").value = d.before;
    $("#after").value = d.after;
    state.windowDays = d.window_days;
    setArea(d.bbox);
    map.fitBounds([[d.bbox[1], d.bbox[0]], [d.bbox[3], d.bbox[2]]], { padding: [30, 30] });
  });
}
["#before", "#after"].forEach((s) => $(s).addEventListener("change", updateGo));

// ---------- run ----------
$("#go").addEventListener("click", async () => {
  const [west, south, east, north] = state.bbox;
  const body = { west, south, east, north, before: $("#before").value, after: $("#after").value };
  if (state.windowDays) body.window_days = state.windowDays;
  const gen = ++state.gen;   // any older run still polling, or report still loading, is now stale
  state.run = null;
  $("#answers").innerHTML = "";
  state.busy = true;
  updateGo();
  $("#results").hidden = true;
  clearOverlays();
  setStatus("Starting…");
  try {
    const j = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    await poll(j.id, gen);
  } catch (e) {
    if (gen === state.gen) setStatus(e.message, true);
  } finally {
    if (gen === state.gen) { state.busy = false; updateGo(); }
  }
});

function errorText(j) {
  if (typeof j.detail === "string") return j.detail;
  if (Array.isArray(j.detail)) return j.detail.map((d) => d.msg).join("; ");
  return "Something went wrong.";
}

function setStatus(text, bad = false) {
  $("#status").textContent = text;
  $("#status").className = "small" + (bad ? " conf-unsure" : "");
}

async function poll(id, gen) {
  const t0 = Date.now();
  for (;;) {
    if (gen !== state.gen) return;
    const a = await api(`/api/analyze/${id}`);
    if (gen !== state.gen) return;
    if (a.status === "done") { show(a); return; }
    if (a.status === "failed" || a.status === "no_imagery") { setStatus(a.message, true); return; }
    setStatus(`${a.progress || a.status}… ${Math.round((Date.now() - t0) / 1000)} s`);
    await new Promise((res) => setTimeout(res, 1500));
  }
}

function clearOverlays() {
  Object.values(state.overlays).forEach((o) => map.removeLayer(o));
  state.overlays = {};
  if (state.regionsLayer) map.removeLayer(state.regionsLayer);
}

function show(a) {
  state.run = a;
  clearOverlays();
  const bounds = a.bounds;
  const stamp = Date.now();
  for (const name of a.layers) {
    state.overlays[name] = L.imageOverlay(`/api/analyze/${a.id}/layer/${name}?v=${stamp}`, bounds,
      { opacity: name === "change" || name === "refined" ? $("#opacity").value / 100 : 1, interactive: false,
        className: "pixelated" });
  }
  const on = new Set(["after", a.layers.includes("refined") ? "refined" : "change", "unseen"]);
  $("#layers").innerHTML = "";
  for (const name of ["before", "after", "change", "refined", "unseen"]) {
    if (!a.layers.includes(name)) continue;
    const id = `layer-${name}`;
    const lab = document.createElement("label");
    lab.innerHTML = `<input type="checkbox" id="${id}"> ${LAYER_NAMES[name]}`;
    const box = lab.querySelector("input");
    box.checked = on.has(name);
    box.addEventListener("change", () => toggle(name, box.checked));
    $("#layers").append(lab);
    toggle(name, box.checked);
  }
  state.regionsLayer = L.geoJSON(a.regions, {
    style: (f) => ({ color: COLOURS[f.properties.kind], weight: 1.5, fill: false }),
    onEachFeature: (f, layer) => layer.on("click", () => openRegion(f)),
  }).addTo(map);
  map.fitBounds(bounds, { padding: [20, 20] });

  $("#legend").innerHTML = Object.entries(COLOURS)
    .map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("") +
    `<span style="--c:rgba(120,120,120,.7)">not seen</span>`;
  renderReport(a);
  renderRegions(a);
  $("#results").hidden = false;
  const used = (s) => (s || []).map((x) => x.date).sort();
  const b = used(a.scenes_before), f = used(a.scenes_after);
  setStatus(`Compared ${b[0] || "?"}${b.length > 1 ? " to " + b[b.length - 1] : ""} with ` +
            `${f[0] || "?"}${f.length > 1 ? " to " + f[f.length - 1] : ""}. ${a.message || ""}`);
}

function toggle(name, visible) {
  const o = state.overlays[name];
  if (!o) return;
  if (visible) o.addTo(map); else map.removeLayer(o);
  // keep change on top of the photos
  ["change", "refined", "unseen"].forEach((n) => state.overlays[n] && map.hasLayer(state.overlays[n]) && state.overlays[n].bringToFront());
}

$("#opacity").addEventListener("input", () => {
  ["change", "refined"].forEach((n) => state.overlays[n] && state.overlays[n].setOpacity($("#opacity").value / 100));
});

function renderReport(a) {
  $("#report").textContent = a.report || "";
  $("#report-source").textContent = a.report_source === "muse-spark" ? "Muse Spark" : "automatic summary";
  const meta = state.health.meta;
  $("#write").disabled = !meta;
  $("#write").title = meta ? $("#write").title : "Set MODEL_API_KEY to use Muse Spark and SAM 3.1";
  $("#ask").hidden = !(meta && a.report_source === "muse-spark");
}

function renderRegions(a) {
  const list = $("#regions");
  list.innerHTML = "";
  const feats = a.regions.features.slice(0, 40);
  if (!feats.length) list.innerHTML = '<li class="muted">No changed patches above the minimum size.</li>';
  for (const f of feats) {
    const p = f.properties;
    const li = document.createElement("li");
    const check = a.checks && a.checks[p.id];
    li.innerHTML = `<b>#${p.id}</b> ${p.kind}, ${p.area_ha} ha ` +
      `<span class="conf-${p.confidence}">${p.confidence}</span>` +
      `<div class="meta">${p.center[1].toFixed(4)}, ${p.center[0].toFixed(4)}` +
      (p.main_doubt ? ` · doubt: ${p.main_doubt}` : "") +
      (check ? ` · visual check: ${check.verdict}` : "") + "</div>";
    li.addEventListener("click", () => openRegion(f));
    list.append(li);
  }
}

function openRegion(f) {
  const p = f.properties;
  map.fitBounds(L.geoJSON(f).getBounds(), { padding: [80, 80], maxZoom: 15 });
  $("#crop img").src = `/api/analyze/${state.run.id}/crop/${p.id}`;
  $("#crop").hidden = false;
}
$("#crop-close").addEventListener("click", () => { $("#crop").hidden = true; });

// ---------- Muse Spark ----------
$("#write").addEventListener("click", async () => {
  const run = state.run, gen = state.gen;
  if (!run) return;
  const current = () => gen === state.gen && state.run && state.run.id === run.id;   // still the same area?
  const previous = $("#report").textContent;
  $("#write").disabled = true;
  $("#report").textContent = "Muse Spark is checking the largest patches and writing the report…";
  try {
    const j = await api(`/api/analyze/${run.id}/report`, { method: "POST" });
    if (current()) show(j);
  } catch (e) {
    if (current()) $("#report").textContent = `${e.message}\n\n${previous}`;
  } finally {
    if (current()) $("#write").disabled = !state.health.meta;
  }
});

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#question").value.trim();
  if (!q || !state.run) return;
  const runId = state.run.id;
  const box = document.createElement("div");
  box.className = "answer";
  box.innerHTML = `<b></b><span>Thinking…</span>`;
  box.querySelector("b").textContent = q;
  $("#answers").prepend(box);
  $("#question").value = "";
  try {
    const j = await api(`/api/analyze/${runId}/ask`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q }) });
    box.querySelector("span").textContent = j.answer;
  } catch (e) {
    box.querySelector("span").textContent = e.message;
  }
});

// ---------- theme ----------
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("wch-theme", t); } catch (e) { /* private mode */ }
}
$("#theme").addEventListener("click", () =>
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
try { const t = localStorage.getItem("wch-theme"); if (t) setTheme(t); } catch (e) { /* ignore */ }

(async () => {
  try { state.health = await api("/api/health"); } catch (e) { state.health = {}; }
  await loadDemos();
})();
