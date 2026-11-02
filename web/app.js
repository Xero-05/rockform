// Rockform hold generator in the browser. The Python hold generator runs in worker.js; this file draws the
// holds and the wall with three.js and handles the controls, including dragging holds bolt to bolt.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

const $ = (id) => document.getElementById(id);
const deg = THREE.MathUtils.degToRad;

// ---- worker calls -------------------------------------------------------------------------------------
const worker = new Worker(new URL("worker.js", import.meta.url), { type: "module" });
const pending = new Map();
let nextCall = 0;
worker.onmessage = ({ data }) => {
  if (data.ready) return started();
  if (data.failed) return failedToStart(data.failed);
  if (data.progress && data.id === undefined) { $("loading-text").textContent = data.progress; return; }
  const call = pending.get(data.id);
  if (!call) return;
  if (data.progress) { call.progress && call.progress(data.progress); return; }
  pending.delete(data.id);
  data.ok ? call.resolve(data.result) : call.reject(new Error(data.error));
};
function rpc(fn, args = [], progress = null) {
  const id = nextCall++;
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject, progress }); worker.postMessage({ id, fn, args }); });
}
let busyCount = 0;
async function busy(text, work) {
  busyCount++;
  $("status").textContent = text;
  try { return await work(); } finally { if (--busyCount === 0) $("status").textContent = "Runs in your browser · nothing is uploaded"; }
}
function download(name, bytes, type = "application/zip") {
  const url = URL.createObjectURL(new Blob([bytes], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ---- colours ------------------------------------------------------------------------------------------
const DIFFICULTY_STOPS = [[64, 178, 120], [150, 200, 90], [240, 196, 70], [236, 120, 60], [205, 52, 62]];
function difficultyColor(d) {
  const t = Math.min(Math.max(d / 10, 0), 1) * (DIFFICULTY_STOPS.length - 1);
  const i = Math.min(Math.floor(t), DIFFICULTY_STOPS.length - 2), f = t - i;
  const c = DIFFICULTY_STOPS[i].map((v, k) => Math.round(v + (DIFFICULTY_STOPS[i + 1][k] - v) * f));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
const WORDS = [[2, "Very easy"], [4, "Easy"], [6, "Moderate"], [8, "Hard"], [10.01, "Very hard"]];
const difficultyWord = (d) => WORDS.find(([limit]) => d < limit)[1];
const GRIP_STOPS = [[0, "#5f7073"], [.02, "#2c4fa3"], [.3, "#1fa3c9"], [.55, "#5ed17a"], [.75, "#f4d03f"], [.9, "#f0812e"], [1, "#d7263d"]]
  .map(([t, hex]) => [t, new THREE.Color(hex)]);
function gripColor(w, out) {
  w = Math.min(Math.max(w, 0), 1);
  for (let i = 1; i < GRIP_STOPS.length; i++) {
    if (w <= GRIP_STOPS[i][0]) {
      const [t0, c0] = GRIP_STOPS[i - 1], [t1, c1] = GRIP_STOPS[i];
      return out.copy(c0).lerp(c1, (w - t0) / (t1 - t0 || 1));
    }
  }
  return out.copy(GRIP_STOPS.at(-1)[1]);
}
const wallText = (a) => Math.abs(a) < .5 ? "Vertical" : a >= 89.5 ? "Roof" : `${Math.abs(a)}° ${a > 0 ? "overhang" : "slab"}`;
function pullText(a) {
  a = ((a + 180) % 360 + 360) % 360 - 180;
  if (Math.abs(a) < .5) return "Pulling straight down";
  if (Math.abs(Math.abs(a) - 180) < .5) return "Undercling (pulling up)";
  return `Pulling ${Math.abs(a).toFixed(0)}° ${a > 0 ? "right" : "left"} of down`;
}
const rotationText = (r) => Math.abs(r) < .5 ? "as designed" : Math.abs(Math.abs(r) - 180) < .5 ? "upside down" : `${Math.abs(r)}° ${r > 0 ? "anticlockwise" : "clockwise"}`;
const wrap = (a) => ((a + 180) % 360 + 360) % 360 - 180;

// ---- a 3D view ------------------------------------------------------------------------------------------
class View {
  constructor(host) {
    this.host = host;
    this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    host.prepend(this.renderer.domElement);
    this.labels = new CSS2DRenderer();
    Object.assign(this.labels.domElement.style, { position: "absolute", inset: "0", pointerEvents: "none" });
    host.appendChild(this.labels.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(30, 1, 5, 100000);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.addEventListener("change", () => this.render());
    this.scene.add(new THREE.HemisphereLight(0xe8f4f0, 0x29353a, 1.6));
    const sun = new THREE.DirectionalLight(0xffffff, 2.2);
    sun.position.set(.6, 1, 1.4);
    this.camera.add(sun);                       // light from the viewer, so the visible side is always lit
    this.scene.add(this.camera);
    new ResizeObserver(() => this.resize()).observe(host);
  }
  resize() {
    const { clientWidth: w, clientHeight: h } = this.host;
    if (!w || !h) return;
    this.renderer.setSize(w, h);
    this.labels.setSize(w, h);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.render();
  }
  frame(center, distance, offset = [.35, .25, 1]) {
    const d = new THREE.Vector3(...offset).normalize().multiplyScalar(distance);
    this.controls.target.copy(center);
    this.camera.position.copy(center).add(d);
    this.camera.up.set(0, 1, 0);
    this.controls.update();
  }
  render() {
    if (this.queued) return;
    this.queued = true;
    requestAnimationFrame(() => { this.queued = false; this.renderer.render(this.scene, this.camera); this.labels.render(this.scene, this.camera); });
  }
}
function holdGeometry(mesh, flatColors = false) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(mesh.vertices, 3));
  g.setIndex(new THREE.BufferAttribute(mesh.faces, 1));
  g.computeVertexNormals();
  return flatColors ? g.toNonIndexed() : g;   // per-face colours need each face's own vertices
}

// =========================================================================================================
// Hold view
// =========================================================================================================
const H = { view: new View($("hold-canvas")), hold: null, pull: [0, 0], group: new THREE.Group(), mesh: null, info: null, typeIndex: 0,
            sizeKey: "M", customSize: null, timer: 0, requestNo: 0, framedType: null };
H.view.scene.add(H.group);

function setupHoldControls(info) {
  H.info = info;
  const types = $("types");
  info.types.forEach((type, i) => {
    const b = document.createElement("button");
    b.innerHTML = `<img alt="" src="data:image/svg+xml;utf8,${encodeURIComponent(info.icons[type])}"><span>${type[0].toUpperCase() + type.slice(1)}</span>`;
    b.title = info.info[type];
    b.onclick = () => { H.typeIndex = i; markTypes(); generateHold(); };
    types.appendChild(b);
  });
  Object.keys(info.sizes).forEach((key) => {
    const b = document.createElement("button");
    b.textContent = key;
    b.title = `${key}: body × ${info.sizes[key]}. Finger-sized features (edge depth, pocket hole, pinch gap) keep their size.`;
    b.onclick = () => { H.sizeKey = key; H.customSize = null; markTypes(); generateHold(); };
    $("sizes").appendChild(b);
  });
  markTypes();
  for (const id of ["difficulty", "wall"]) $(id).addEventListener("input", () => { holdLabels(); clearTimeout(H.timer); H.timer = setTimeout(generateHold, 300); });
  $("rotation").addEventListener("input", () => { holdLabels(); clearTimeout(H.timer); H.timer = setTimeout(rotateHold, 150); });
  $("seed").addEventListener("change", generateHold);
  $("randomise").onclick = () => { $("seed").value = String(Math.floor(Math.random() * 1e6)); generateHold(); };
  $("describe").onclick = describeHold;
  $("description").addEventListener("keydown", (e) => e.key === "Enter" && describeHold());
  $("overlay").onchange = () => { $("grip-legend").hidden = $("overlay").value === "plain"; showPull(...H.pull); };
  $("hold-reset").onclick = () => frameHold();
  $("export-hold").onclick = async () => { const [name, bytes] = await busy("Exporting…", () => rpc("export_hold", [H.hold.id])); download(name, bytes); };
  const grabs = $("grabs");
  Object.entries(info.styles).forEach(([key, s]) => {
    const b = document.createElement("button");
    b.dataset.angle = s.angle;
    b.onclick = () => showPull(s.angle, 0);
    grabs.appendChild(b);
  });
  holdLabels();
}
function markTypes() {
  [...$("types").children].forEach((b, i) => b.classList.toggle("on", i === H.typeIndex));
  [...$("sizes").children].forEach((b) => b.classList.toggle("on", !H.customSize && b.textContent === H.sizeKey));
  $("type-info").textContent = H.info.info[H.info.types[H.typeIndex]];
}
function holdLabels() {
  const d = $("difficulty").value / 10;
  $("difficulty-value").textContent = `${d.toFixed(1)} · ${difficultyWord(d)}`;
  $("wall-value").textContent = wallText(+$("wall").value);
  const r = +$("rotation").value;
  $("rotation-value").textContent = r === 0 ? "Upright" : `${Math.abs(r)}° ${r > 0 ? "↺" : "↻"}`;
}
document.querySelectorAll(".presets").forEach((row) => row.querySelectorAll("button").forEach((b) => b.onclick = () => {
  const input = $(row.dataset.target);
  input.value = b.dataset.value;
  input.dispatchEvent(new Event("input"));
}));

async function generateHold() {
  const spec = { type: H.info.types[H.typeIndex], difficulty: $("difficulty").value / 10, seed: $("seed").value || "0",
                 wall_angle: +$("wall").value, size: H.customSize || H.info.sizes[H.sizeKey], rotation: +$("rotation").value };
  await runHold(() => rpc("generate_hold", [spec]));
}
async function describeHold() {
  const text = $("description").value.trim();
  if (text) await runHold(() => rpc("generate_hold", [{ description: text, seed: $("seed").value || "0", rotation: +$("rotation").value }]));
}
async function runHold(call, pull = null) {
  const no = ++H.requestNo;
  try {
    const hold = await busy("Generating the hold…", call);
    if (no !== H.requestNo) return;             // a newer request replaced this one
    showHold(hold, pull);
  } catch (error) { $("hold-message").textContent = `Could not generate this hold: ${error.message}`; }
}
async function rotateHold() {
  if (!H.hold) return;
  const no = ++H.requestNo;
  const hold = await busy("Re-rating for the new rotation…", () => rpc("rotate_hold", [H.hold.id, +$("rotation").value]));
  if (no === H.requestNo) showHold(hold, H.pull, true);
}
function showHold(hold, pull = null, sameShape = false, note = "") {
  const sameType = H.hold && H.hold.type === hold.type;
  H.hold = hold;
  // Controls follow the hold (e.g. after a description or an inspected route hold).
  H.typeIndex = H.info.types.indexOf(hold.type);
  $("difficulty").value = Math.round(hold.target * 10);
  $("wall").value = Math.round(hold.wall_angle);
  $("seed").value = hold.seed;
  const key = Object.keys(H.info.sizes).reduce((a, k) => Math.abs(Math.log(H.info.sizes[k] / hold.size)) < Math.abs(Math.log(H.info.sizes[a] / hold.size)) ? k : a);
  H.sizeKey = key;
  H.customSize = Math.abs(H.info.sizes[key] - hold.size) > 1e-6 ? hold.size : null;
  $("rotation").value = Math.round(hold.rotation);
  markTypes(); holdLabels();
  const mm = hold.extents.map((v) => v.toFixed(0)).join(" × "), inch = hold.extents.map((v) => (v / 25.4).toFixed(1)).join(" × ");
  $("size-info").textContent = `${mm} mm (${inch} in)` + (hold.size === 1 ? "" : ` · body × ${hold.size.toFixed(2)}`);
  $("reach").textContent = `A ${hold.type} with this variation reaches ${hold.achievable[0].toFixed(1)}–${hold.achievable[1].toFixed(1)} on a ${wallText(hold.wall_angle).toLowerCase()} wall.`;
  $("hold-message").textContent = [note, ...hold.notes.map((n) => `⚠ ${n}`)].filter(Boolean).join("\n");
  const pocket = hold.fingers ? `<br>${hold.fingers}-finger pocket, ${hold.pocket_depth.toFixed(0)} mm deep` : "";
  $("details").innerHTML = `<b>${hold.type[0].toUpperCase() + hold.type.slice(1)}</b>, variation ${hold.seed}${hold.drilled ? " · drilled" : ""}<br>` +
    `${mm} mm (${inch} in) · ${hold.volume_cm3.toFixed(0)} cm³<br>Holding area ${hold.holding_area.toFixed(0)} mm² straight down${pocket}`;
  $("export-hold").disabled = false;
  const { lo, hi } = { lo: hold.pull.min, hi: hold.pull.max };
  const turned = hold.rotation ? ` with the hold turned ${rotationText(hold.rotation)}` : "";
  $("range-text").innerHTML = `<b>${lo.toFixed(1)} – ${hi.toFixed(1)}</b> for pulls within ${hold.pull.cone_deg.toFixed(0)}° of straight down${turned} (dashed line in the chart).`;
  const arrows = { side_right: " →", side_left: " ←" };
  [...$("grabs").children].forEach((b, i) => {
    const name = Object.keys(H.info.styles)[i], d = hold.pull.styles[name];
    b.textContent = `${H.info.styles[name].name.split(" (")[0]}${arrows[name] || ""}   ${d.toFixed(1)}`;
    b.style.borderLeft = `5px solid ${difficultyColor(d)}`;
    b.title = `${H.info.styles[name].name}: difficulty ${d.toFixed(1)}, ${difficultyWord(d).toLowerCase()}`;
  });
  // Mesh: tilted with the wall and turned on it, as the climber sees it.
  if (!sameShape) {
    H.group.clear();
    H.geometry = holdGeometry(hold.mesh, true);
    H.geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(H.geometry.attributes.position.count * 3), 3));
    H.mesh = new THREE.Mesh(H.geometry, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: .75, metalness: 0 }));
    H.group.add(H.mesh);
    const size = Math.max(...hold.extents);
    const wall = new THREE.Mesh(new THREE.PlaneGeometry(size * 1.8, size * 1.8), new THREE.MeshBasicMaterial({ color: 0x9aa7a6, transparent: true, opacity: .16, side: THREE.DoubleSide }));
    wall.position.z = -.5;
    H.group.add(wall);
  }
  H.group.rotation.set(deg(hold.wall_angle), 0, deg(hold.rotation), "XYZ");
  if (!sameShape && (!sameType || H.framedType !== hold.type)) frameHold();
  showPull(...(pull || (sameType ? H.pull : [0, 0])));
}
function frameHold() {
  if (!H.hold) return;
  H.framedType = H.hold.type;
  H.group.updateMatrixWorld();
  const box = new THREE.Box3().setFromObject(H.mesh);
  H.view.frame(box.getCenter(new THREE.Vector3()), Math.max(...H.hold.extents) * 3.6, [.9, .55, 1.6]);
}
async function showPull(around, outward) {
  if (!H.hold) return;
  H.pull = [around, outward];
  const hold = H.hold, overlay = $("overlay").value;
  const g = await rpc("grip", [hold.id, around, outward, hold.rotation, overlay === "plain" ? "grip" : overlay]);
  if (hold !== H.hold) return;
  const colors = H.geometry.attributes.color, c = new THREE.Color(0xa9b8b2);
  for (let f = 0; f < g.weights.length; f++) {
    if (overlay !== "plain") gripColor(g.weights[f], c);
    for (let k = 0; k < 3; k++) colors.setXYZ(3 * f + k, c.r, c.g, c.b);
  }
  colors.needsUpdate = true;
  const d = g.difficulty, down = Math.abs(wrap(around)) < .5 && outward === 0;
  const away = outward ? `, ${outward.toFixed(0)}° from the wall` : "";
  const turned = hold.rotation ? ` · hold turned ${rotationText(hold.rotation)}` : "";
  $("hold-title").textContent = `${pullText(around)}${away}${turned}: difficulty ${d.toFixed(1)}`;
  $("big").textContent = d.toFixed(1);
  $("big").style.color = difficultyColor(d);
  const what = down ? "straight-down pull" : `${pullText(around).toLowerCase()}${away}`;
  const note = down && !hold.rotation ? "straight-down pull" : `${what}${turned} · as designed ${hold.difficulty.toFixed(1)}`;
  $("word").innerHTML = `${difficultyWord(d)}<br><span class="muted" style="font-size:12px;font-weight:400">${note}</span>`;
  const how = g.mode === "squeeze" ? " · scored as a squeeze" : g.mode === "hang" ? " · scored as a ledge: easier than squeezing for this pull" : "";
  $("holding-line").textContent = `Holding area for this pull: ${g.area.toFixed(0)} mm²${how}`;
  [...$("grabs").children].forEach((b) => b.classList.toggle("on", outward === 0 && Math.abs(wrap(around - +b.dataset.angle)) < .5));
  drawRangeBar(d, hold.pull.min, hold.pull.max);
  drawPullChart();
  H.view.render();
}
function fitCanvas(canvas) {
  const r = canvas.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  canvas.width = Math.round(r.width * dpr); canvas.height = Math.round(r.height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, r.width, r.height];
}
function drawRangeBar(d, lo, hi) {
  const [ctx, w] = fitCanvas($("range-bar"));
  const x0 = 8, x1 = w - 8, y = 12, x = (v) => x0 + (x1 - x0) * v / 10;
  ctx.fillStyle = "#26373e"; ctx.beginPath(); ctx.roundRect(x0, y - 3, x1 - x0, 6, 3); ctx.fill();
  ctx.fillStyle = "#7f9796"; ctx.font = "11px Segoe UI, sans-serif"; ctx.textAlign = "center";
  for (const k of [0, 5, 10]) ctx.fillText(String(k), x(k), y + 21);
  const grad = ctx.createLinearGradient(x0, 0, x1, 0);
  for (let k = 0; k <= 10; k++) grad.addColorStop(k / 10, difficultyColor(k));
  ctx.fillStyle = grad; ctx.beginPath(); ctx.roundRect(x(lo) - 3, y - 6, Math.max(x(hi) - x(lo), 0) + 6, 12, 6); ctx.fill();
  ctx.fillStyle = difficultyColor(d); ctx.strokeStyle = "#fff"; ctx.lineWidth = 2;
  ctx.beginPath(); ctx.arc(x(d), y, 6, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
}
// Difficulty for each pull: across = direction around the hold, up = away from the wall. Click a cell to show it.
const chart = $("pull-chart");
function chartLayout() {
  const p = H.hold.pull, r = chart.getBoundingClientRect();
  const plot = { x: 34, y: 8, w: r.width - 44, h: r.height - 40 };
  return { p, plot, cw: plot.w / p.around_deg.length, ch: plot.h / p.outward_deg.length };
}
function drawPullChart() {
  const [ctx] = fitCanvas(chart);
  const { p, plot, cw, ch } = chartLayout();
  p.difficulty.forEach((row, i) => row.forEach((d, j) => {
    ctx.fillStyle = difficultyColor(d);
    ctx.fillRect(plot.x + j * cw, plot.y + plot.h - (i + 1) * ch, cw + .5, ch + .5);
  }));
  const around = p.around_deg, out = p.outward_deg;
  const xy = (a, o) => [plot.x + (a - around[0]) / (around.at(-1) - around[0]) * (plot.w - cw) + cw / 2,
                        plot.y + plot.h - (o - out[0]) / (out.at(-1) - out[0]) * (plot.h - ch) - ch / 2];
  const cone = deg(p.cone_deg);
  ctx.setLineDash([5, 4]); ctx.strokeStyle = "rgba(255,255,255,.8)"; ctx.lineWidth = 1.5; ctx.beginPath();
  for (let k = 0; k <= 40; k++) {
    const s = -cone + 2 * cone * k / 40, o = THREE.MathUtils.radToDeg(Math.acos(Math.min(Math.max(Math.cos(cone) / Math.cos(s), -1), 1)));
    const [x, y] = xy(THREE.MathUtils.radToDeg(s), o);
    k ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
  }
  ctx.stroke(); ctx.setLineDash([]);
  ctx.font = "11px Segoe UI, sans-serif"; ctx.textAlign = "center";
  for (const [a, name] of [[-180, "Under"], [-90, "Side ←"], [0, "Down"], [90, "Side →"], [180, "Under"]]) {
    const [x] = xy(a, 0);
    ctx.strokeStyle = "rgba(255,255,255,.25)"; ctx.beginPath(); ctx.moveTo(x, plot.y); ctx.lineTo(x, plot.y + plot.h); ctx.stroke();
    ctx.fillStyle = "#98aaa9"; ctx.fillText(name, x, plot.y + plot.h + 13);
  }
  ctx.fillText("direction of pull around the hold", plot.x + plot.w / 2, plot.y + plot.h + 27);
  ctx.textAlign = "right";
  ctx.fillText(`${out.at(-1)}°`, 30, plot.y + ch / 2 + 4); ctx.fillText(`${out[0]}°`, 30, plot.y + plot.h - ch / 2 + 4);
  const [sx, sy] = xy(...H.pull);
  ctx.strokeStyle = "#fff"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(sx, sy, 5, 0, Math.PI * 2); ctx.stroke();
}
function chartCell(event) {
  if (!H.hold) return null;
  const { p, plot, cw, ch } = chartLayout(), r = chart.getBoundingClientRect();
  const j = Math.floor((event.clientX - r.left - plot.x) / cw), i = Math.floor((plot.y + plot.h - (event.clientY - r.top)) / ch);
  return i >= 0 && i < p.outward_deg.length && j >= 0 && j < p.around_deg.length ? [i, j] : null;
}
chart.addEventListener("mousemove", (e) => {
  const cell = chartCell(e), p = H.hold && H.hold.pull;
  chart.title = cell ? `${pullText(p.around_deg[cell[1]])}, ${p.outward_deg[cell[0]]}° from the wall: difficulty ${p.difficulty[cell[0]][cell[1]].toFixed(1)}\nClick to show its heat map` : "";
});
chart.addEventListener("click", (e) => { const cell = chartCell(e); if (cell) showPull(H.hold.pull.around_deg[cell[1]], H.hold.pull.outward_deg[cell[0]]); });

// =========================================================================================================
// Route view
// =========================================================================================================
const R = { view: new View($("route-canvas")), routes: new Map(), problems: new Map(), selected: null, history: [],
            wallGroup: new THREE.Group(), holdObjects: new Map(), lines: [], drag: null, framed: false };
R.view.scene.add(R.wallGroup);
const BOARD = 18;

function wallSpec() {
  return { width: +$("wall-width").value, height: +$("wall-height").value, angle: +$("wall-angle").value, bolt_spacing: +$("bolt-spacing").value };
}
function boltAxes(w) {
  const g = w.bolt_spacing, ni = Math.floor(w.width / g), nj = Math.floor(w.height / g);
  const us = [], vs = [];
  for (let i = 0; i < ni; i++) us.push(-w.width / 2 + (w.width - (ni - 1) * g) / 2 + i * g);
  for (let j = 0; j < nj; j++) vs.push((w.height - (nj - 1) * g) / 2 + j * g);
  return [us, vs];
}
function snap(u, v, w = R.wall) {
  const [us, vs] = boltAxes(w), near = (arr, x) => arr.reduce((a, b) => Math.abs(b - x) < Math.abs(a - x) ? b : a);
  return [near(us, u), near(vs, v)];
}
const key = (rid, index) => `${rid}:${index}`;
const findHold = (rid, index) => R.routes.get(rid).holds[index];

function setupRouteControls(info) {
  R.info = info;
  Object.keys(info.grades).forEach((g, i) => {
    const b = document.createElement("button");
    b.textContent = g;
    b.title = `${g}: hand holds about ${info.grades[g].difficulty.toFixed(1)} as placed, moves about ${info.grades[g].move_mm.toFixed(0)} mm`;
    b.onclick = () => { R.grade = g; [...$("grades").children].forEach((x) => x.classList.toggle("on", x === b)); };
    $("grades").appendChild(b);
    if (g === "V3") b.click();
  });
  $("route-random").onclick = () => { $("route-seed").value = String(Math.floor(Math.random() * 1e5)); };
  for (const id of ["wall-width", "wall-height", "bolt-spacing", "wall-angle"]) $(id).addEventListener("input", wallChanged);
  $("add-route").onclick = addRoute;
  $("remove-route").onclick = async () => { const last = [...R.routes.keys()].at(-1); if (!last) return; setProblems(await rpc("remove_route", [last])); R.routes.delete(last); routesChanged(); };
  $("clear-wall").onclick = async () => { await rpc("clear_routes"); R.routes.clear(); R.problems.clear(); R.history = []; routesChanged(); $("route-message").textContent = "Wall cleared."; };
  $("colour-by").onchange = () => { recolor(); R.view.render(); };
  $("labels").onchange = () => { R.lines.forEach((o) => o.isCSS2DObject && (o.visible = $("labels").checked)); drawRouteLines(); R.view.render(); };
  $("route-reset").onclick = frameWall;
  $("route-pick").onchange = showSummary;
  $("export-wall").onclick = async () => { const [name, bytes] = await busy("Exporting the wall…", () => rpc("export_wall")); download(name, bytes); };
  document.querySelectorAll(".adjust button[data-du], .adjust button[data-dv], .adjust button[data-turn]").forEach((b) => b.onclick = () =>
    nudge(+(b.dataset.du || 0), +(b.dataset.dv || 0), +(b.dataset.turn || 0)));
  $("face-pull").onclick = () => R.selected && adjust(R.selected, null, null, null, true);
  for (const id of ["pos-u", "pos-v"]) $(id).addEventListener("change", () => R.selected && adjust(R.selected, +$("pos-u").value, +$("pos-v").value));
  $("undo").onclick = undo;
  $("inspect").onclick = inspect;
  addEventListener("keydown", (e) => {
    if ($("view-route").hidden || e.target.matches("input, select, textarea")) return;
    const arrows = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, 1], ArrowDown: [0, -1] };
    if (e.ctrlKey && e.key.toLowerCase() === "z") { e.preventDefault(); undo(); }
    else if (e.ctrlKey && arrows[e.key]) { e.preventDefault(); nudge(...arrows[e.key], 0); }
    else if (!e.ctrlKey && (e.key === "q" || e.key === "e")) { e.preventDefault(); turnKey(e.key === "q" ? 15 : -15); }
  });
  wallChanged();
  frameWall();
  enableAdjusters();
}
function wallChanged() {
  $("wall-angle-value").textContent = wallText(+$("wall-angle").value);
  if (R.routes.size) $("route-message").textContent = "⚠ The wall changed: the routes on it were planned and rated for the old wall. Clear the wall and add them again.";
  R.wall = wallSpec();
  drawWall();
}
function drawWall() {
  const w = R.wall;
  R.wallGroup.clear();
  R.holdObjects.clear();
  R.lines = [];
  R.wallGroup.rotation.set(deg(w.angle), 0, 0);
  const board = new THREE.Mesh(new THREE.BoxGeometry(w.width, w.height, BOARD), new THREE.MeshStandardMaterial({ color: 0xc9a77c, roughness: .9 }));
  board.position.set(0, w.height / 2, -BOARD / 2);
  board.userData.board = true;
  R.wallGroup.add(board);
  const [us, vs] = boltAxes(w), pts = [];
  for (const u of us) for (const v of vs) pts.push(u, v, .6);
  const bolts = new THREE.Points(new THREE.BufferGeometry().setAttribute("position", new THREE.Float32BufferAttribute(pts, 3)),
    new THREE.PointsMaterial({ color: 0x3a2a1a, size: 4, sizeAttenuation: false }));
  R.wallGroup.add(bolts);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(w.width + 1200, w.height * .9), new THREE.MeshStandardMaterial({ color: 0x2d4147, transparent: true, opacity: .55 }));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, -1, w.height * .45 - 100);
  R.view.scene.children.filter((o) => o.userData.floor).forEach((o) => R.view.scene.remove(o));
  floor.userData.floor = true;
  R.view.scene.add(floor);
  for (const [rid, route] of R.routes) route.holds.forEach((h, i) => addHoldObject(rid, route, h, i));
  drawRouteLines();
  recolor();
  R.view.render();
}
function frameWall() {
  const w = R.wall;
  R.wallGroup.updateMatrixWorld();
  const centre = new THREE.Vector3(0, w.height / 2, 0).applyMatrix4(R.wallGroup.matrixWorld);
  R.view.frame(centre, w.height * 2.1, [.18, -.02, 1]);
}
function addHoldObject(rid, route, h, index) {
  const geometry = route.geometries[index] || (route.geometries[index] = holdGeometry(h.mesh));
  const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ roughness: .7, metalness: 0 }));
  mesh.userData = { rid, index };
  const group = new THREE.Group();
  group.add(mesh);
  place(group, h.u, h.v, h.spin);
  R.wallGroup.add(group);
  R.holdObjects.set(key(rid, index), { group, mesh });
}
const place = (group, u, v, spin) => { group.position.set(u, v, 0); group.rotation.set(0, 0, deg(spin)); };
const ROLE_LETTERS = { START: "S ", FINISH: "F " };
function drawRouteLines() {
  R.lines.forEach((o) => o.parent && o.parent.remove(o));
  R.lines = [];
  for (const [rid, route] of R.routes) {
    const pts = [];
    route.holds.forEach((h, i) => {
      if (h.role === "FOOT") return;
      const tip = new THREE.Vector3(h.u, h.v, h.size[2] + 15);
      pts.push(tip);
      if ($("labels").checked) {
        const el = document.createElement("div");
        el.className = "label3d";
        el.textContent = `${ROLE_LETTERS[h.role] || ""}${h.difficulty.toFixed(1)}`;
        const label = new CSS2DObject(el);
        label.position.copy(tip).add(new THREE.Vector3(18, 14, 0));
        R.wallGroup.add(label); R.lines.push(label);
      }
    });
    if (pts.length > 1) {
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: new THREE.Color(...route.color), transparent: true, opacity: .85 }));
      R.wallGroup.add(line); R.lines.push(line);
    }
  }
}
function recolor() {
  const mode = $("colour-by").value;
  for (const [k, { mesh }] of R.holdObjects) {
    const { rid, index } = mesh.userData, route = R.routes.get(rid), h = route.holds[index];
    const color = mode === "role" ? R.info.role_colors[h.role] : mode === "difficulty" ? difficultyColor(h.difficulty) : new THREE.Color(...route.color);
    mesh.material.color.set(color);
    const selected = R.selected && R.selected.rid === rid && R.selected.index === index;
    mesh.material.emissive.set(selected ? 0x5a6a66 : R.problems.has(k) ? 0x6a2e08 : 0x000000);
  }
  $("route-legend").textContent = mode === "role" ? "start (green) · hand (blue) · foot (yellow) · finish (red)"
    : mode === "difficulty" ? "difficulty as placed: easy (green) to hard (red)" : "";
}
function setProblems(list) {
  R.problems = new Map(list.map(([rid, index, message]) => [key(rid, index), message]));
}
function mergeRoute(update) {
  const route = R.routes.get(update.id);
  update.holds.forEach((h) => Object.assign(route.holds[h.index], h));
  route.summary = update.summary;
}

async function addRoute() {
  const spec = { wall: wallSpec(), grade: R.grade, seed: $("route-seed").value || "0", moves: +$("moves").value,
                 feet: $("feet").checked, bolt: +$("bolt").value };
  $("route-busy").hidden = false;
  setRouteButtons(false);
  $("route-message").textContent = `Planning a ${spec.grade} route…`;
  try {
    const out = await busy("Planning the route…", () => rpc("plan", [spec], (line) => { $("route-message").textContent = line.trim(); }));
    const route = out.route;
    route.geometries = [];
    R.routes.set(route.id, route);
    setProblems(out.problems);
    const notes = route.summary.notes || [];
    $("route-message").textContent = `Added ${route.name}: estimated ${route.summary.estimated_grade}, ${route.holds.length} holds.` + (notes.length ? `\n${notes.length} note(s): see the route card.` : "");
    route.holds.forEach((h, i) => addHoldObject(route.id, route, h, i));
    routesChanged(route.id);
  } catch (error) {
    $("route-message").textContent = `Could not plan this route: ${error.message}`;
  } finally {
    $("route-busy").hidden = true;
    setRouteButtons(true);
  }
}
function setRouteButtons(on) {
  $("add-route").disabled = !on;
  $("remove-route").disabled = !on || !R.routes.size;
  $("clear-wall").disabled = !on;
  $("export-wall").disabled = !on || !R.routes.size;
  R.locked = !on;
  enableAdjusters();
}
function routesChanged(pick = null) {
  if (R.selected && !R.routes.has(R.selected.rid)) R.selected = null;
  R.history = R.history.filter((s) => R.routes.has(s.rid));
  for (const k of [...R.holdObjects.keys()]) if (!R.routes.has(k.split(":")[0])) {
    const { group } = R.holdObjects.get(k); group.parent.remove(group); R.holdObjects.delete(k);
  }
  const keep = pick || $("route-pick").value;
  $("route-pick").innerHTML = [...R.routes].map(([id, r]) => `<option value="${id}">${r.name}</option>`).join("");
  if (R.routes.has(keep)) $("route-pick").value = keep;
  drawRouteLines(); recolor(); fillTable(); showSummary(); setRouteButtons(true);
  $("route-title").textContent = R.routes.size ? `${R.routes.size} route(s), ${[...R.routes.values()].reduce((n, r) => n + r.holds.length, 0)} holds on a ${wallText(R.wall.angle).toLowerCase()} wall`
    : "No routes yet: choose a grade and add a route";
  R.view.render();
}
const STYLE_NAMES = { down: "Down-pull", side_right: "Side-pull →", side_left: "Side-pull ←", undercling: "Undercling" };
function fillTable() {
  const body = $("hold-table").tBodies[0];
  body.innerHTML = "";
  for (const [rid, route] of R.routes) {
    const order = route.holds.map((h, i) => i).sort((a, b) => (route.holds[a].role === "FOOT") - (route.holds[b].role === "FOOT") || route.holds[a].v - route.holds[b].v);
    for (const index of order) {
      const h = route.holds[index], k = key(rid, index), tr = body.insertRow();
      tr.dataset.key = k;
      const problem = R.problems.get(k);
      const cells = [route.name, h.role[0] + h.role.slice(1).toLowerCase(), h.type[0].toUpperCase() + h.type.slice(1), h.difficulty.toFixed(1),
        h.role === "FOOT" ? "Foot" : STYLE_NAMES[h.style], `${h.spin >= 0 ? "+" : ""}${h.spin.toFixed(0)}°`,
        `${h.u.toFixed(0)}, ${h.v.toFixed(0)}${h.moved ? " ✎" : ""}${problem ? " ⚠" : ""}`, h.size.map((v) => v.toFixed(0)).join(" × ")];
      cells.forEach((text, c) => { const td = tr.insertCell(); td.textContent = text; });
      tr.cells[0].style.color = new THREE.Color(...route.color).getStyle();
      tr.cells[1].style.color = R.info.role_colors[h.role];
      tr.cells[3].style.color = difficultyColor(h.difficulty);
      if (problem) { tr.cells[6].classList.add("warn"); tr.cells[6].title = `This hold ${problem}.`; }
      else if (h.moved) tr.cells[6].title = "Moved or turned by hand";
      tr.onclick = () => select({ rid, index });
      tr.ondblclick = inspect;
    }
  }
  markSelected();
}
function markSelected() {
  const k = R.selected && key(R.selected.rid, R.selected.index);
  for (const tr of $("hold-table").tBodies[0].rows) {
    tr.classList.toggle("selected", tr.dataset.key === k);
    if (tr.dataset.key === k) tr.scrollIntoView({ block: "nearest" });
  }
  describeSelected();
  enableAdjusters();
}
function showSummary() {
  const route = R.routes.get($("route-pick").value);
  if (!route) { $("route-big").textContent = "–"; $("route-big").style.color = ""; $("route-grade").innerHTML = ""; $("route-summary").innerHTML = ""; return; }
  const s = route.summary;
  $("route-big").textContent = s.estimated_grade;
  $("route-big").style.color = new THREE.Color(...route.color).getStyle();
  const match = s.estimated_grade === s.requested_grade ? "on target" : `asked ${s.requested_grade}`;
  $("route-grade").innerHTML = `Estimated grade<br><span class="muted" style="font-size:12px;font-weight:400">${match} · score ${s.route_score} (target ${s.requested_score})</span>`;
  const notes = (s.notes || []).map((n) => `<br>⚠ ${n}`).join("");
  $("route-summary").innerHTML = `${s.hand_moves} hand moves, ${route.holds.filter((h) => h.role === "FOOT").length} foot holds, rated on a ${wallText(s.wall_angle_deg).toLowerCase()} wall.` +
    `<br>Move scores: ${s.move_costs.map((c) => c.toFixed(1)).join(" → ")}<br><span style="font-size:11px">Heuristic, not calibrated on climbers.</span>${notes}`;
}
function describeSelected() {
  if (!R.selected) { $("selected-line").textContent = "Select a hold to move or turn it."; return; }
  const h = findHold(R.selected.rid, R.selected.index), problem = R.problems.get(key(R.selected.rid, R.selected.index));
  const pull = h.role === "FOOT" ? "" : `, expected pull: ${pullText(h.pull_deg).toLowerCase()}`;
  $("selected-line").innerHTML = `<b>${h.type[0].toUpperCase() + h.type.slice(1)}</b> ${difficultyWord(h.difficulty).toLowerCase()} (${h.difficulty.toFixed(1)})${pull}, ` +
    `turned ${h.spin >= 0 ? "+" : ""}${h.spin.toFixed(0)}°, Ø${h.hole_mm.toFixed(1)} mm hole${h.moved ? " · moved by hand" : ""}.` + (problem ? ` <span class="warn">⚠ It ${problem}.</span>` : "");
  $("pos-u").value = h.u; $("pos-v").value = h.v;
  $("pos-u").step = $("pos-v").step = R.wall.bolt_spacing;
}
function enableAdjusters() {
  const on = !!R.selected && !R.locked;
  document.querySelectorAll(".adjust button, .adjust input").forEach((el) => el.disabled = !on);
  $("undo").disabled = !R.history.length || R.locked;
}
function select(sel) {
  R.selected = sel;
  if (sel && $("route-pick").value !== sel.rid) { $("route-pick").value = sel.rid; showSummary(); }
  markSelected(); recolor(); R.view.render();
}

// Move or turn one hold, re-rate its route in Python, update everything. One undo step per call.
async function adjust(sel, u, v, spin, face = false, record = true, moved = true) {
  const h = findHold(sel.rid, sel.index);
  u = u ?? h.u; v = v ?? h.v; spin = spin ?? h.spin;
  [u, v] = snap(u, v);
  if (!face && u === h.u && v === h.v && wrap(spin) === h.spin && moved === h.moved) { place(R.holdObjects.get(key(sel.rid, sel.index)).group, h.u, h.v, h.spin); describeSelected(); R.view.render(); return; }
  if (record) R.history.push({ ...sel, u: h.u, v: h.v, spin: h.spin, moved: h.moved });
  const before = R.routes.get(sel.rid).summary.estimated_grade;
  R.locked = true; enableAdjusters();
  try {
    const out = await busy("Re-rating the route…", () => rpc("adjust", [sel.rid, sel.index, u, v, spin, moved, face]));
    mergeRoute(out.route);
    setProblems(out.problems);
  } finally { R.locked = false; }
  const route = R.routes.get(sel.rid), held = route.holds[sel.index];
  place(R.holdObjects.get(key(sel.rid, sel.index)).group, held.u, held.v, held.spin);
  R.selected = sel;
  const after = route.summary.estimated_grade;
  $("route-message").textContent = `${route.name}: ${held.type} now difficulty ${held.difficulty.toFixed(1)}; route estimate ${after}` + (after !== before ? ` (was ${before}).` : ".");
  drawRouteLines(); recolor(); fillTable(); showSummary(); R.view.render();
}
function nudge(du, dv, turn) {
  if (!R.selected || R.locked) return;
  const h = findHold(R.selected.rid, R.selected.index), g = R.wall.bolt_spacing;
  adjust(R.selected, h.u + du * g, h.v + dv * g, wrap(h.spin + turn));
}
async function undo() {
  const step = R.history.pop();
  if (!step || R.locked) return;
  await adjust({ rid: step.rid, index: step.index }, step.u, step.v, step.spin, false, false, step.moved);
}
async function inspect() {
  if (!R.selected) return;
  const sel = R.selected, h = findHold(sel.rid, sel.index);
  const hold = await busy("Opening the hold…", () => rpc("inspect", [sel.rid, sel.index]));
  showView("hold");
  showHold(hold, [h.pull_deg, 0], false, `${h.role[0] + h.role.slice(1).toLowerCase()} hold from the Route view, as placed: drilled for its bolt, turned ${rotationText(h.spin)}.`);
}

// ---- dragging holds on the wall, as in the Blender add-on ------------------------------------------------
// Click a hold to select it; drag it to move it bolt to bolt; the wheel or Q / E turns it while dragging.
// Dragging empty wall still orbits the camera.
const canvas = R.view.renderer.domElement, ray = new THREE.Raycaster(), pointer = new THREE.Vector2();
function pointerRay(e) {
  const r = canvas.getBoundingClientRect();
  pointer.set((e.clientX - r.left) / r.width * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(pointer, R.view.camera);
  return [e.clientX - r.left, e.clientY - r.top];
}
function holdUnder(e) {
  const [px, py] = pointerRay(e);
  const meshes = [...R.holdObjects.values()].map((o) => o.mesh);
  const hit = ray.intersectObjects(meshes, false)[0];
  if (hit) return hit.object.userData;
  // Holds are a few pixels wide when the whole wall is in view: take the nearest one whose footprint contains the click.
  let best = null, nearest = Infinity;
  const r = canvas.getBoundingClientRect(), toScreen = (vec) => { vec.applyMatrix4(R.wallGroup.matrixWorld).project(R.view.camera); return [(vec.x + 1) / 2 * r.width, (1 - vec.y) / 2 * r.height]; };
  for (const { mesh } of R.holdObjects.values()) {
    const h = findHold(mesh.userData.rid, mesh.userData.index);
    const c = toScreen(new THREE.Vector3(h.u, h.v, h.size[2] / 2)), edge = toScreen(new THREE.Vector3(h.u + h.radius, h.v, h.size[2] / 2));
    const reach = Math.max(Math.hypot(edge[0] - c[0], edge[1] - c[1]), 8), gap = Math.hypot(px - c[0], py - c[1]);
    if (gap <= reach && gap < nearest) { best = mesh.userData; nearest = gap; }
  }
  return best;
}
function wallPoint(e) {
  pointerRay(e);
  R.wallGroup.updateMatrixWorld();
  const normal = new THREE.Vector3(0, 0, 1).applyQuaternion(R.wallGroup.quaternion);
  const plane = new THREE.Plane().setFromNormalAndCoplanarPoint(normal, R.wallGroup.position);
  const hit = ray.ray.intersectPlane(plane, new THREE.Vector3());
  return hit && R.wallGroup.worldToLocal(hit);
}
canvas.addEventListener("pointerdown", (e) => {
  if (e.button !== 0 || R.locked) return;
  const sel = holdUnder(e);
  if (!sel) return;                                   // orbit as usual
  e.stopImmediatePropagation();                       // keep OrbitControls from starting a rotation
  try { canvas.setPointerCapture(e.pointerId); } catch { /* not an active pointer (synthetic events) */ }
  const h = findHold(sel.rid, sel.index), grab = wallPoint(e);
  R.drag = { sel, offset: grab ? [h.u - grab.x, h.v - grab.y] : [0, 0], u: h.u, v: h.v, spin: h.spin, moved: false };
  R.view.controls.enabled = false;
  canvas.style.cursor = "grabbing";
  select(sel);
}, { capture: true });
canvas.addEventListener("pointermove", (e) => {
  if (!R.drag) {
    if (!R.locked && e.buttons === 0) canvas.style.cursor = holdUnder(e) ? "grab" : "";
    return;
  }
  const p = wallPoint(e);
  if (!p) return;
  const [u, v] = snap(p.x + R.drag.offset[0], p.y + R.drag.offset[1]);
  if (u !== R.drag.u || v !== R.drag.v) { Object.assign(R.drag, { u, v, moved: true }); previewDrag(); }
});
canvas.addEventListener("wheel", (e) => {
  if (!R.drag) return;                                // zoom as usual
  e.preventDefault(); e.stopImmediatePropagation();
  turnDrag(e.deltaY < 0 ? 15 : -15);
}, { capture: true, passive: false });
function turnKey(step) {
  if (R.drag) turnDrag(step);
  else if (R.selected) nudge(0, 0, step);
}
function turnDrag(step) { R.drag.spin = wrap(R.drag.spin + step); R.drag.moved = true; previewDrag(); }
function previewDrag() {
  const d = R.drag, h = findHold(d.sel.rid, d.sel.index);
  place(R.holdObjects.get(key(d.sel.rid, d.sel.index)).group, d.u, d.v, d.spin);
  // The drop target is checked against every other hold, as after any move.
  const touches = [...R.routes].some(([rid, route]) => route.holds.some((o, i) => !(rid === d.sel.rid && i === d.sel.index) && Math.hypot(o.u - d.u, o.v - d.v) < o.radius + h.radius));
  const off = Math.abs(d.u) + h.radius > R.wall.width / 2 || d.v - h.radius < 0 || d.v + h.radius > R.wall.height;
  const warn = off ? "goes past the edge of the wall" : touches ? "may touch another hold" : "";
  $("selected-line").innerHTML = `Moving <b>${h.type}</b> to ${d.u.toFixed(0)}, ${d.v.toFixed(0)}, turned ${d.spin >= 0 ? "+" : ""}${d.spin.toFixed(0)}°` +
    (warn ? ` <span class="warn">⚠ it ${warn}</span>` : "") + " · release to place, wheel or Q / E to turn";
  R.view.render();
}
function endDrag(e) {
  const d = R.drag;
  if (!d) return;
  R.drag = null;
  R.view.controls.enabled = true;
  canvas.style.cursor = "grab";
  try { if (e && canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId); } catch { /* ignore */ }
  if (d.moved) adjust(d.sel, d.u, d.v, d.spin);       // one undo step; re-rates and redraws
}
canvas.addEventListener("pointerup", endDrag);
canvas.addEventListener("pointercancel", endDrag);

// =========================================================================================================
function showView(name) {
  for (const v of ["hold", "route"]) {
    $(`view-${v}`).hidden = v !== name;
    $(`tab-${v}`).setAttribute("aria-selected", String(v === name));
  }
  (name === "hold" ? H : R).view.resize();
  if (name === "hold" && H.hold) { drawPullChart(); drawRangeBar(+$("big").textContent, H.hold.pull.min, H.hold.pull.max); }
}
document.querySelectorAll("nav button").forEach((b) => b.onclick = () => showView(b.dataset.view));

window.rockform = { H, R };          // for debugging from the console

async function started() {
  const info = await rpc("info");
  info.icons = {};
  for (const type of info.types) info.icons[type] = await rpc("hold_icon", [type]);
  setupHoldControls(info);
  setupRouteControls(info);
  $("loading").hidden = true;
  $("status").textContent = "Runs in your browser · nothing is uploaded";
  showView("hold");
  generateHold();
}
function failedToStart(message) {
  $("loading-text").textContent = `Could not start: ${message}. Check your internet connection (the first visit downloads Python and libraries) and reload.`;
  document.querySelector("#loading .busy").hidden = true;
}
