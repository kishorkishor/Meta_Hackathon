"use strict";

const $ = (s) => document.querySelector(s);
const COLOURS = {
  "burned land": "rgb(230,85,13)", "new water": "rgb(33,113,181)", "water gone": "rgb(158,202,225)",
  "vegetation loss": "rgb(166,97,26)", "new building or bare ground": "rgb(203,24,29)",
};
const LAYER_NAMES = { before: "Before", after: "After", change: "Change", refined: "SAM outlines",
                      agreement: "SAM vs rules", unseen: "Not seen" };
const OVERLAYS = ["change", "refined", "agreement"];   // the layers the opacity slider controls
const AGREEMENT = { "SAM and rules agree": "rgb(46,204,113)", "only the rules": "rgb(241,196,15)",
                    "only SAM": "rgb(155,89,182)" };

const state = { bbox: null, run: null, overlays: {}, regionsLayer: null, drawing: null, health: {}, gen: 0, busy: false,
                lang: "en", languages: [] };

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
// Starts a comparison of the current area and dates; resolves with the finished analysis, or null.
async function runCompare(planId = null) {
  const [west, south, east, north] = state.bbox;
  const body = { west, south, east, north, before: $("#before").value, after: $("#after").value };
  if (state.windowDays) body.window_days = state.windowDays;
  if (planId) body.plan_id = planId;
  const gen = ++state.gen;   // any older run still polling, or report still loading, is now stale
  state.run = null;
  state.view = null;
  $("#answers").innerHTML = "";
  state.busy = true;
  updateGo();
  $("#results").hidden = true;
  clearOverlays();
  setStatus("Starting…");
  try {
    const j = await api("/api/analyze", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return await poll(j.id, gen);
  } catch (e) {
    if (gen === state.gen) setStatus(e.message, true);
    return null;
  } finally {
    if (gen === state.gen) { state.busy = false; updateGo(); }
  }
}
$("#go").addEventListener("click", () => runCompare());

// ---------- ask in plain words ----------
$("#plain-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#plain").value.trim();
  if (q.length < 4 || state.busy) return;
  const gen = ++state.gen;
  state.busy = true;
  updateGo();
  $("#plain-go").disabled = true;
  $("#plan").hidden = false;
  $("#plan").textContent = "Muse Spark is finding the place and the dates, and checking for clear images…";
  let p = null;
  try {
    p = await api("/api/plan", { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify({ question: q, language: state.lang }) });
  } catch (e) {
    if (gen === state.gen) $("#plan").textContent = e.message;
  } finally {
    if (gen === state.gen) { state.busy = false; updateGo(); }
    $("#plain-go").disabled = !state.health.meta;
  }
  if (!p || gen !== state.gen) return;
  renderPlan(p);
  if (!p.ok) return;
  $("#demo").value = "";
  $("#demo-story").textContent = "";
  $("#before").value = p.before;
  $("#after").value = p.after;
  state.windowDays = p.window_days;
  setArea(p.bbox);
  map.fitBounds([[p.bbox[1], p.bbox[0]], [p.bbox[3], p.bbox[2]]], { padding: [30, 30] });
  const a = await runCompare(p.id);
  if (a && state.run && state.run.id === a.id) writeReport();   // the report answers the question
});

function el(tag, text, cls) {
  const e = document.createElement(tag);
  if (text != null) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

function renderPlan(p) {
  const box = $("#plan");
  box.innerHTML = "";
  if (!p.ok) {
    box.append(el("p", "Muse Spark could not plan this: " + (p.reason || "no reason given")));
  } else {
    box.append(el("div", p.place, "place"));
    const kinds = p.looking_for && p.looking_for.length ? `, looking for ${p.looking_for.join(", ")}` : "";
    box.append(el("p", `Before ${p.before}, after ${p.after} (±${p.window_days} days)${kinds}.`, "small"));
    if (p.why) box.append(el("p", p.why, "small"));
  }
  if (p.sources && p.sources.length) {
    const s = el("p", "Sources: ", "small sources");
    p.sources.forEach((src, i) => {
      const a = el("a", src.title || src.url);
      a.href = src.url; a.target = "_blank"; a.rel = "noopener noreferrer";
      if (i) s.append(" · ");
      s.append(a);
    });
    box.append(s);
  }
  if (p.steps && p.steps.length) {
    const d = el("details");
    d.append(el("summary", `How Muse Spark got there (${p.steps.length} steps)`));
    const ol = el("ol");
    p.steps.forEach((st) => ol.append(el("li", st.text)));
    d.append(ol);
    box.append(d);
  }
  box.hidden = false;
}

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
    if (gen !== state.gen) return null;
    const a = await api(`/api/analyze/${id}`);
    if (gen !== state.gen) return null;
    if (a.status === "done") { show(a); return a; }
    if (a.status === "failed" || a.status === "no_imagery") { setStatus(a.message, true); return null; }
    setStatus(`${a.progress || a.status}… ${Math.round((Date.now() - t0) / 1000)} s`);
    await new Promise((res) => setTimeout(res, 1500));
  }
}

function clearOverlays() {
  Object.values(state.overlays).forEach((o) => map.removeLayer(o));
  state.overlays = {};
  if (state.regionsLayer) map.removeLayer(state.regionsLayer);
  if (state.missedLayer) map.removeLayer(state.missedLayer);
  if (state.stopsLayer) map.removeLayer(state.stopsLayer);
}

function show(a) {
  state.run = a;
  clearOverlays();
  const bounds = a.bounds;
  const stamp = Date.now();
  for (const name of a.layers) {
    state.overlays[name] = L.imageOverlay(`/api/analyze/${a.id}/layer/${name}?v=${stamp}`, bounds,
      { opacity: OVERLAYS.includes(name) ? $("#opacity").value / 100 : 1, interactive: false,
        className: "pixelated" });
  }
  const main = state.view === "agreement" && a.layers.includes("agreement") ? "agreement"
    : a.layers.includes("refined") ? "refined" : "change";
  const on = new Set(["after", main, "unseen"]);
  $("#layers").innerHTML = "";
  for (const name of ["before", "after", "change", "refined", "agreement", "unseen"]) {
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
    style: (f) => edgeStyle(f, a),
    onEachFeature: (f, layer) => layer.on("click", () => openRegion(f)),
  }).addTo(map);
  map.fitBounds(bounds, { padding: [20, 20] });

  $("#legend").innerHTML = Object.entries(COLOURS)
    .map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("") +
    `<span style="--c:rgba(120,120,120,.7)">not seen</span>` +
    (a.layers.includes("agreement") ? `<span class="sub">SAM vs rules layer</span>` +
      Object.entries(AGREEMENT).map(([k, c]) => `<span style="--c:${c}">${k}</span>`).join("") : "") +
    (a.visual && a.visual.checked ? `<span class="sub">Patch edges: dashed red = Muse Spark disagrees, ` +
      `dotted grey = unclear</span>` : "");
  renderReport(a);
  renderLook(a);
  renderWitness(a);
  renderField(a);
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
  [...OVERLAYS, "unseen"].forEach((n) => state.overlays[n] && map.hasLayer(state.overlays[n]) && state.overlays[n].bringToFront());
}

$("#opacity").addEventListener("input", () => {
  OVERLAYS.forEach((n) => state.overlays[n] && state.overlays[n].setOpacity($("#opacity").value / 100));
});

function renderReport(a) {
  $("#question-echo").hidden = !a.question;
  $("#question-echo").textContent = a.question ? `You asked: ${a.question}` : "";
  $("#report").textContent = a.report || "";
  const written = a.report_source === "muse-spark" ? (a.report_language || "en") : "en";
  $("#report").lang = written;
  $("#report-source").textContent = a.report_source === "muse-spark"
    ? "Muse Spark" + (written !== "en" ? ` · ${lang(written).native}` : "") : "automatic summary";
  const meta = state.health.meta;
  $("#write").textContent = a.report_source === "muse-spark"
    ? (written === state.lang ? "Ask Muse Spark to write it again" : `Write it again in ${lang(state.lang).native}`)
    : "Ask Muse Spark for the report" + (state.lang !== "en" ? ` in ${lang(state.lang).native}` : "");
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
      (check ? ` · Muse Spark: <span class="verdict-${check.verdict}">${check.verdict}</span>` +
        (check.verdict !== "agrees" && check.looks_like ? ` (looks like ${check.looks_like})` : "") : "") +
      (p.sam_witness ? ` · SAM: ${p.sam_witness.verdict} (${Math.round(p.sam_witness.share * 100)}%)` : "") + "</div>";
    li.addEventListener("click", () => openRegion(f));
    list.append(li);
  }
}

function openRegion(f) {
  const p = f.properties;
  map.fitBounds(L.geoJSON(f).getBounds(), { padding: [80, 80], maxZoom: 15 });
  $("#crop img").src = `/api/analyze/${state.run.id}/crop/${p.id}`;
  const c = state.run.checks && state.run.checks[p.id];
  $("#crop-note").hidden = !c;
  $("#crop-note").textContent = c ? `Muse Spark ${c.verdict}: ${c.what_you_see} ${c.reason}`.trim() : "";
  $("#crop").hidden = false;
}

// patch edges: the change colour, dashed red where Muse Spark disagrees, dotted grey where it could not tell
function edgeStyle(f, a) {
  const c = a.checks && a.checks[f.properties.id];
  if (c && c.verdict === "disagrees") return { color: "#e0574f", weight: 2.5, dashArray: "6 4", fill: false };
  if (c && c.verdict === "unclear") return { color: "#c7ccd4", weight: 2, dashArray: "2 4", fill: false };
  return { color: COLOURS[f.properties.kind], weight: c ? 2 : 1.5, fill: false };
}

// ---------- Muse Spark visual check of every big patch ----------
function renderLook(a) {
  const v = a.visual || {};
  const meta = state.health.meta;
  $("#look").disabled = !meta;
  $("#look").title = meta ? "" : "Set MODEL_API_KEY to use Muse Spark";
  $("#look").textContent = !v.checked ? "Have Muse Spark look at every big patch"
    : v.remaining > 0 ? `Look at the next ${Math.min(20, v.remaining)} patches` : "All big patches looked at";
  if (v.checked && !v.remaining) $("#look").disabled = true;
  const list = $("#flagged");
  list.innerHTML = "";
  if (!v.checked) { $("#look-summary").textContent = ""; return; }
  const n = v.counts, ha = (x) => Number(x).toLocaleString("en-US", { maximumFractionDigits: 1 });
  $("#look-summary").textContent =
    `Looked at ${v.checked} of ${v.eligible} patches of 2 ha or more: ${n.agrees} agree (${ha(v.ha.agrees)} ha), ` +
    `${n.disagrees} disagree (${ha(v.ha.disagrees)} ha), ${n.unclear} unclear (${ha(v.ha.unclear)} ha).`;
  const byId = Object.fromEntries(a.regions.features.map((f) => [f.properties.id, f]));
  for (const x of v.flagged || []) {
    const li = el("li");
    li.append(el("b", `#${x.id} `), `${x.kind}, ${x.area_ha} ha · `, el("span", x.verdict, `verdict-${x.verdict}`),
              x.looks_like ? `, looks like ${x.looks_like}` : "",
              el("div", [x.what_you_see, x.reason].filter(Boolean).join(" "), "meta"));
    li.addEventListener("click", () => byId[x.id] ? openRegion(byId[x.id]) : map.setView([x.center[1], x.center[0]], 15));
    list.append(li);
  }
}

$("#look").addEventListener("click", async () => {
  const run = state.run, gen = state.gen;
  if (!run || !state.health.meta) return;
  const current = () => gen === state.gen && state.run && state.run.id === run.id;
  $("#look").disabled = true;
  $("#look-summary").textContent = "Muse Spark is looking at the before and after picture of each big patch…";
  try {
    const j = await api(`/api/analyze/${run.id}/check`, { method: "POST" });
    if (current()) show(j);
  } catch (e) {
    if (current()) { $("#look-summary").textContent = e.message; $("#look").disabled = false; }
  }
});
$("#crop-close").addEventListener("click", () => { $("#crop").hidden = true; });

// ---------- SAM 3.1 second witness ----------
function renderWitness(a) {
  const w = a.witness || {};
  const meta = state.health.meta;
  $("#witness").disabled = !meta;
  $("#witness").title = meta ? "" : "Set MODEL_API_KEY to use SAM 3.1";
  $("#witness").textContent = w.calls ? "Ask SAM 3.1 again" : "Ask SAM 3.1 for a second opinion";
  const list = $("#missed");
  list.innerHTML = "";
  if (state.missedLayer) map.removeLayer(state.missedLayer);
  if (!w.calls) {
    $("#witness-summary").textContent = w.note || "";
    return;
  }
  const ha = (x) => Number(x).toLocaleString("en-US", { maximumFractionDigits: 1 });
  const pct = w.agree_share == null ? "" : ` (${Math.round(w.agree_share * 100)}% of what the rules found)`;
  $("#witness-summary").textContent =
    `SAM also sees ${ha(w.both_ha)} ha of the change${pct}. It did not find ${ha(w.only_rules_ha)} ha that the ` +
    `rules marked, and saw ${ha(w.only_sam_ha)} ha of change the rules did not.` +
    (w.failed_concepts && w.failed_concepts.length ? ` SAM failed for: ${w.failed_concepts.join(", ")}.` : "");
  state.missedLayer = L.layerGroup().addTo(map);
  for (const m of w.only_sam_places || []) {
    const [lon, lat] = m.center;
    L.circleMarker([lat, lon], { radius: 7, color: AGREEMENT["only SAM"], weight: 2, fill: false })
      .bindTooltip(`Only SAM: ${m.kind}, ${m.area_ha} ha`).addTo(state.missedLayer);
    const li = el("li");
    li.append(el("b", "Only SAM saw: "), `${m.kind}, ${m.area_ha} ha`,
              el("div", `${lat.toFixed(4)}, ${lon.toFixed(4)} · check by eye`, "meta"));
    li.addEventListener("click", () => map.setView([lat, lon], 15));
    list.append(li);
  }
}

$("#witness").addEventListener("click", async () => {
  const run = state.run, gen = state.gen;
  if (!run || !state.health.meta) return;
  const current = () => gen === state.gen && state.run && state.run.id === run.id;
  $("#witness").disabled = true;
  $("#witness-summary").textContent = "SAM 3.1 is looking at the before and after images…";
  try {
    const j = await api(`/api/analyze/${run.id}/witness`, { method: "POST" });
    if (current()) { state.view = "agreement"; show(j); }
  } catch (e) {
    if (current()) { $("#witness-summary").textContent = e.message; $("#witness").disabled = false; }
  }
});

// ---------- field checklist ----------
function renderField(a) {
  const fl = a.checklist || {};
  const meta = state.health.meta;
  const inLang = state.lang !== "en" ? ` in ${lang(state.lang).native}` : "";
  $("#field").textContent = meta ? (fl.stops ? `Have Muse Spark make it again${inLang}`
    : `Have Muse Spark make a field checklist${inLang}`) : "Make a field checklist (plain version)";
  $("#field").disabled = false;
  const list = $("#stops");
  list.innerHTML = "";
  if (state.stopsLayer) map.removeLayer(state.stopsLayer);
  $("#field-links").hidden = !fl.stops;
  if (!fl.stops) { $("#field-summary").textContent = ""; return; }
  $("#field-print").href = `/field/${a.id}`;
  $("#field-kml").href = `/api/analyze/${a.id}/field.kml`;
  $("#field-geojson").href = `/api/analyze/${a.id}/field.geojson`;
  $("#field-summary").textContent = [fl.intro, fl.note,
    fl.source === "muse-spark" ? (fl.language && fl.language !== "en" ? `Written in ${lang(fl.language).native}.` : "")
      : "Plain version, written without Muse Spark.",
    a.checklist_stale ? "New checks came in after this list was made; make it again to use them." : ""]
    .filter(Boolean).join(" ");
  if (!fl.stops.length) list.append(el("li", "No changed patch of 1 ha or more, so there is nothing to visit.", "muted"));
  state.stopsLayer = L.layerGroup().addTo(map);
  const labels = fl.labels || {};
  const kind = (k) => labels["kind_" + k.replace(/ /g, "_")] || k;
  for (const s of fl.stops) {
    const [lon, lat] = s.point;
    L.marker([lat, lon], { icon: L.divIcon({ className: "stop-pin", html: String(s.rank), iconSize: [24, 24] }) })
      .bindTooltip(`${s.rank}. ${kind(s.kind)}, ${s.area_ha} ha`).addTo(state.stopsLayer);
    const li = el("li");
    li.append(el("b", `${s.rank}. `), `${kind(s.kind)}, ${s.area_ha} ha`,
              el("div", `${lat.toFixed(5)}, ${lon.toFixed(5)}` +
                 (s.km_from_previous != null ? ` · ${s.km_from_previous} km from stop ${s.rank - 1}` : ""), "meta"),
              el("div", `${labels.look_for || "Look for"}: ${s.look_for}`, "meta"),
              el("div", `${labels.why || "Why"}: ${s.why}`, "meta"));
    li.addEventListener("click", () => map.setView([lat, lon], 15));
    list.append(li);
  }
}

$("#field").addEventListener("click", async () => {
  const run = state.run, gen = state.gen;
  if (!run) return;
  const current = () => gen === state.gen && state.run && state.run.id === run.id;
  $("#field").disabled = true;
  $("#field-summary").textContent = state.health.meta
    ? "Muse Spark is choosing the stops and writing what to look for…" : "Making the list…";
  try {
    const j = await api(`/api/analyze/${run.id}/field?language=${state.lang}`, { method: "POST" });
    if (current()) show(j);
  } catch (e) {
    if (current()) { $("#field-summary").textContent = e.message; $("#field").disabled = false; }
  }
});

// ---------- Muse Spark ----------
$("#write").addEventListener("click", () => writeReport());

async function writeReport() {
  const run = state.run, gen = state.gen;
  if (!run || !state.health.meta) return;
  const current = () => gen === state.gen && state.run && state.run.id === run.id;   // still the same area?
  const previous = $("#report").textContent;
  $("#write").disabled = true;
  $("#report").textContent = "Muse Spark is checking the largest patches and writing the report…";
  try {
    const j = await api(`/api/analyze/${run.id}/report?language=${state.lang}`, { method: "POST" });
    if (current()) show(j);
  } catch (e) {
    if (current()) $("#report").textContent = `${e.message}\n\n${previous}`;
  } finally {
    if (current()) $("#write").disabled = !state.health.meta;
  }
}

$("#ask").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const q = $("#question").value.trim();
  if (!q || !state.run) return;
  const runId = state.run.id;
  const box = document.createElement("div");
  box.className = "answer";
  box.innerHTML = `<b dir="auto"></b><span dir="auto">Thinking…</span>`;
  box.querySelector("b").textContent = q;
  $("#answers").prepend(box);
  $("#question").value = "";
  try {
    const j = await api(`/api/analyze/${runId}/ask`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, language: state.lang }) });
    box.querySelector("span").textContent = j.answer;
    box.querySelector("span").lang = j.language || "en";
  } catch (e) {
    box.querySelector("span").textContent = e.message;
  }
});

// ---------- language ----------
function lang(code) {
  return state.languages.find((l) => l.code === code) || { code, name: code, native: code, voice: false };
}

async function loadLanguages() {
  try { state.languages = await api("/api/languages"); } catch (e) { state.languages = [lang("en")]; }
  let saved = "en";
  try { saved = localStorage.getItem("wch-lang") || "en"; } catch (e) { /* private mode */ }
  if (!state.languages.some((l) => l.code === saved)) saved = "en";
  for (const l of state.languages) {
    $("#lang").add(new Option(l.code === "en" ? l.native : `${l.native} (${l.name})`, l.code));
  }
  setLanguage(saved);
}

function setLanguage(code) {
  state.lang = code;
  $("#lang").value = code;
  try { localStorage.setItem("wch-lang", code); } catch (e) { /* private mode */ }
  const l = lang(code);
  const tip = l.voice ? `Speak your question${code !== "en" ? " in " + l.native : ""}`
    : `Speak your question. ${l.name} is not one of the languages Muse Voice Transcribe was tested on, ` +
      "so it may mishear; typing always works.";
  ["#plain-mic", "#ask-mic"].forEach((s) => { $(s).title = tip; });
  if (state.run && state.run.status === "done") { renderReport(state.run); renderField(state.run); }
}
$("#lang").addEventListener("change", () => setLanguage($("#lang").value));

// ---------- voice questions (Muse Voice Transcribe) ----------
// The browser records in its own format; the service takes mono 16 bit WAV, so the sound is decoded,
// mixed down and resampled to 16 kHz here before it is sent.
async function toWav16k(blob) {
  const ctx = new AudioContext();
  let audio;
  try { audio = await ctx.decodeAudioData(await blob.arrayBuffer()); } finally { ctx.close(); }
  const rate = 16000;
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(audio.duration * rate)), rate);
  const src = off.createBufferSource();
  src.buffer = audio;
  src.connect(off.destination);
  src.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const v = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const tag = (at, t) => [...t].forEach((c, i) => v.setUint8(at + i, c.charCodeAt(0)));
  tag(0, "RIFF"); v.setUint32(4, 36 + pcm.length * 2, true); tag(8, "WAVE"); tag(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  tag(36, "data"); v.setUint32(40, pcm.length * 2, true);
  for (let i = 0; i < pcm.length; i++) {
    const x = Math.max(-1, Math.min(1, pcm[i]));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return new Blob([v], { type: "audio/wav" });
}

const voice = { rec: null, timer: null };
const MAX_VOICE_S = 60;

function voiceNote(note, text, bad = false) {
  note.hidden = !text;
  note.textContent = text || "";
  note.classList.toggle("bad", bad);
}

// One press starts, the next press stops (or a minute passes). The words go into the box to check before asking.
async function toggleVoice(btn, input, note, action) {
  if (voice.rec) { voice.rec.stop(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
  } catch (e) {
    voiceNote(note, "The microphone is blocked or missing. Allow it in the browser, or type the question.", true);
    return;
  }
  const rec = new MediaRecorder(stream);
  const chunks = [];
  const t0 = Date.now();
  rec.ondataavailable = (ev) => { if (ev.data.size) chunks.push(ev.data); };
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    clearInterval(voice.timer);
    voice.rec = null;
    btn.classList.remove("recording");
    btn.setAttribute("aria-label", "Speak your question");
    if ((Date.now() - t0) < 400) { voiceNote(note, "Too short. Press once, speak, then press again.", true); return; }
    voiceNote(note, "Muse Voice Transcribe is listening to the recording…");
    btn.disabled = true;
    try {
      const wav = await toWav16k(new Blob(chunks, { type: rec.mimeType }));
      const run = state.run ? `&run_id=${state.run.id}` : "";
      const j = await api(`/api/transcribe?language=${state.lang}${run}`,
                          { method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav });
      if (!j.text) { voiceNote(note, "Nothing was heard. Try again a little closer to the microphone.", true); return; }
      input.value = j.text;
      input.focus();
      voiceNote(note, `Heard ${j.seconds} s. Check the words, fix any name it misheard, then press ${action}.`);
    } catch (e) {
      voiceNote(note, e.message, true);
    } finally {
      btn.disabled = false;
    }
  };
  voice.rec = rec;
  rec.start();
  btn.classList.add("recording");
  btn.setAttribute("aria-label", "Stop recording");
  const tick = () => {
    const sec = Math.floor((Date.now() - t0) / 1000);
    voiceNote(note, `Listening… ${sec} s. Press the microphone again to stop.`);
    if (sec >= MAX_VOICE_S && voice.rec) voice.rec.stop();
  };
  tick();
  voice.timer = setInterval(tick, 250);
}
$("#plain-mic").addEventListener("click", () =>
  toggleVoice($("#plain-mic"), $("#plain"), $("#plain-voice"), "Find it and compare"));
$("#ask-mic").addEventListener("click", () =>
  toggleVoice($("#ask-mic"), $("#question"), $("#ask-voice"), "Ask"));

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
  const canRecord = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder &&
                       window.OfflineAudioContext);
  $("#plain-mic").hidden = $("#ask-mic").hidden = !(state.health.meta && canRecord);
  await loadLanguages();
  if (!state.health.meta) {
    $("#plain").disabled = $("#plain-go").disabled = true;
    $("#plain").placeholder = "Needs a Meta API key. Use an example or draw an area below.";
  }
  await loadDemos();
})();
