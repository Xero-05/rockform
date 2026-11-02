// Runs Rockform's hold generator in the browser: Pyodide (Python in WebAssembly) for the Python code, and
// manifold's WebAssembly build for the boolean cuts that drill bolt holes. Everything stays on this computer.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v0.28.3/full/pyodide.mjs";
import Manifold from "https://cdn.jsdelivr.net/npm/manifold-3d@3.2.1/manifold.js";

let api = null;

async function start() {
  const say = (text) => postMessage({ progress: text });
  say("Loading the geometry engine…");
  const wasm = await Manifold();
  wasm.setup();
  // Called from Python (drill.BOOLEAN): solid minus cutter, as flat vertex and triangle arrays.
  self.manifoldDifference = (va, fa, vb, fb) => {
    const make = (v, f) => {
      const mesh = new wasm.Mesh({ numProp: 3, vertProperties: v, triVerts: f });
      mesh.merge();
      return new wasm.Manifold(mesh);
    };
    const a = make(va, fa), b = make(vb, fb), cut = a.subtract(b);
    const out = cut.getMesh();
    const result = [out.vertProperties.slice(), out.triVerts.slice(), out.numProp];
    a.delete(); b.delete(); cut.delete();
    return result;
  };

  say("Loading Python…");
  const py = await loadPyodide();
  say("Loading NumPy and trimesh…");
  await py.loadPackage(["numpy", "micropip"]);
  await py.pyimport("micropip").install("trimesh==5.1.0");
  say("Loading the hold generator…");
  const archive = await (await fetch(new URL("rockform.zip", import.meta.url))).arrayBuffer();
  py.unpackArchive(archive, "zip", { extractDir: "/app" });
  // The hold library lives in /library, kept in this browser's IndexedDB between visits.
  py.FS.mkdirTree("/library");
  try {
    py.FS.mount(py.FS.filesystems.IDBFS, {}, "/library");
    await new Promise((resolve, reject) => py.FS.syncfs(true, (error) => error ? reject(error) : resolve()));
    self.librarySaved = true;
  } catch (error) {
    self.librarySaved = false;        // e.g. private browsing: the library lasts until the tab closes
  }
  py.runPython(`
import sys
sys.path.insert(0, "/app")
import numpy as np, trimesh, js
from pyodide.ffi import to_js
from rockform.holdgen import drill

def manifold_difference(solid, cutter):
    out = js.manifoldDifference(to_js(np.ascontiguousarray(solid.vertices, np.float32).ravel()),
                                to_js(np.ascontiguousarray(solid.faces, np.uint32).ravel()),
                                to_js(np.ascontiguousarray(cutter.vertices, np.float32).ravel()),
                                to_js(np.ascontiguousarray(cutter.faces, np.uint32).ravel()))
    vertices = np.asarray(out[0].to_py(), float).reshape(-1, int(out[2]))[:, :3]
    faces = np.asarray(out[1].to_py(), np.int64).reshape(-1, 3)
    # Keep manifold's topology: merging its coincident vertices would break the closed surface.
    return trimesh.Trimesh(vertices, faces, process=False)

drill.BOOLEAN = manifold_difference
`);
  api = py.pyimport("rockform.holdgen.webapi");
  self.py = py;
}

const ready = start().then(
  () => postMessage({ ready: true }),
  (error) => postMessage({ failed: String(error && error.message || error) }));

// Collect the typed arrays in a result so they move to the page instead of being copied.
function buffers(value, out = []) {
  if (ArrayBuffer.isView(value)) out.push(value.buffer);
  else if (Array.isArray(value)) value.forEach((v) => buffers(v, out));
  else if (value && typeof value === "object") Object.values(value).forEach((v) => buffers(v, out));
  return out;
}

onmessage = async ({ data: { id, fn, args = [] } }) => {
  await ready;
  try {
    const py = self.py;
    const pyArgs = args.map((a) => py.toPy(a));
    const progress = (text) => postMessage({ id, progress: String(text) });
    const call = fn === "plan" ? api[fn].callKwargs(...pyArgs, { progress }) : api[fn](...pyArgs);
    let result = call;
    if (result && typeof result.toJs === "function") {
      result = call.toJs({ dict_converter: Object.fromEntries });
      call.destroy();
    }
    pyArgs.forEach((a) => a && a.destroy && a.destroy());
    if (self.librarySaved && /^library_(import|save|rename|retype|remove|merge)$/.test(fn))
      await new Promise((resolve) => py.FS.syncfs(false, resolve));
    postMessage({ id, ok: true, result }, buffers(result));
  } catch (error) {
    // Python errors end with the exception line; show that, not the traceback.
    const text = String(error && error.message || error).trim().split("\n").pop();
    postMessage({ id, ok: false, error: text });
  }
};
