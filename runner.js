/* The live runner: paste a GitHub repo → its Python files are loaded into an
   in-browser Python VM (Pyodide, WebAssembly) and you use the repo right here,
   interactively, in real time.

   The VM lives in this tab's memory only: no server, no install, nothing on
   disk. Closing the tab wipes it. The VM has no sockets, so repo code cannot
   make real network calls — that's the sandbox, for free.

   The app screen (a second panel above the terminal) renders GUI apps:
     - Python/tkinter repos → the virtual tkinter shim (real HTML widgets)
     - static web apps (index.html) → a sandboxed, origin-less iframe
     - full-stack servers → "this PC" mode runs the real process via the
       local bridge and the app's own UI opens at 127.0.0.1:<port> in-frame */

import { fetchRepoFiles } from "./repo_fetch.js";
import { detectGuiSignals, extractPorts, RULE_WHAT, SHELF, beforeRunSteps, parseSetup,
         fullMachineReasons } from "./runner_core.js";
import { scanTree } from "./cleanroom.js";

const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
// keep in step with bridge.py VERSION — printed on VM boot so a stale cache
// is always visible in the terminal
const RUNNER_VERSION = "0.1.7";

let pyodide = null;
let vmLoading = null;
let busy = false;
let vmFiles = [];          // [[path, text]] of the last VM repo load

const term = document.getElementById("term");
const termInput = document.getElementById("termInput");
const liveStatus = document.getElementById("liveStatus");
const liveCard = document.getElementById("liveCard");
const loadBtn = document.getElementById("loadBtn");
const liveUrl = document.getElementById("liveUrl");
const pcRow = document.getElementById("pcRow");
const pcToken = document.getElementById("pcToken");
const pcConnect = document.getElementById("pcConnect");
const pcSpecs = document.getElementById("pcSpecs");
const appCard = document.getElementById("appCard");
const appTitle = document.getElementById("appTitle");
const appCloseBtn = document.getElementById("appCloseBtn");
const appContent = document.getElementById("appContent");
const webWrap = document.getElementById("webWrap");
const webFrame = document.getElementById("webFrame");
const webUrl = document.getElementById("webUrl");
const pcWebRow = document.getElementById("pcWebRow");
const pcWebCmd = document.getElementById("pcWebCmd");
const pcWebNet = document.getElementById("pcWebNet");
const pcWebStart = document.getElementById("pcWebStart");
const pcWebStop = document.getElementById("pcWebStop");
const pcWebChips = document.getElementById("pcWebChips");
const triCard = document.getElementById("triCard");
const triScore = document.getElementById("triScore");
const triVerdict = document.getElementById("triVerdict");
const triMeta = document.getElementById("triMeta");
const triToggle = document.getElementById("triToggle");
const triBody = document.getElementById("triBody");
const triCats = document.getElementById("triCats");
const triFindings = document.getElementById("triFindings");
const triEnv = document.getElementById("triEnv");
const triCheck = document.getElementById("triCheck");
const runRow = document.getElementById("runRow");
const filesCard = document.getElementById("filesCard");
const filesMeta = document.getElementById("filesMeta");
const filePills = document.getElementById("filePills");
const fileView = document.getElementById("fileView");
const fileViewPath = document.getElementById("fileViewPath");
const fileViewPre = document.getElementById("fileViewPre");
const fileViewClose = document.getElementById("fileViewClose");
const shelfRow = document.getElementById("shelfRow");

/* run mode: "vm" (WebAssembly in this tab) or "pc" (real processes via the
   localhost bridge started by `python cleanroom.py serve`) */
let mode = "vm";
const bridge = { base: "http://127.0.0.1:8641", token: "", specs: null };

document.querySelectorAll('input[name="runmode"]').forEach(radio => {
  radio.addEventListener("change", () => {
    mode = document.querySelector('input[name="runmode"]:checked').value;
    pcRow.classList.toggle("hidden", mode !== "pc");
    const vmSpecsRow = document.getElementById("vmSpecsRow");
    if (vmSpecsRow) {
      vmSpecsRow.classList.toggle("hidden", mode !== "vm");
      if (mode === "vm") renderVmSpecs();
    }
    if (pcWebRow) pcWebRow.classList.toggle("hidden", mode !== "pc" || !pcWebRow.dataset.shown);
    if (mode === "vm") {
      setLiveStatus(pyodide ? "in-tab VM — close the tab and nothing remains"
                            : "in-tab VM mode — the repo runs inside this browser tab");
    } else {
      setLiveStatus("this-PC mode — enter the token printed by `python cleanroom.py serve`");
    }
  });
});

async function bridgeCall(path, body) {
  const res = await fetch(bridge.base + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Cleanroom-Token": bridge.token },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  return { status: res.status, data };
}

async function connectBridge() {
  bridge.token = pcToken.value.trim();
  if (!bridge.token) {
    setLiveStatus("enter the token printed by `python cleanroom.py serve`", true);
    return;
  }
  setLiveStatus("looking for the bridge on 127.0.0.1:8641…");
  try {
    const { status, data } = await bridgeCall("/api/status");
    if (status === 200 && data.ok) {
      bridge.specs = data.device;
      const d = data.device;
      pcSpecs.innerHTML = "";
      [d.platform, d.cpu_cores + " cores", d.memory_gb ? d.memory_gb + " GB RAM" : null,
       "python " + d.python, d.hostname].filter(Boolean).forEach(txt => {
        const chip = document.createElement("span");
        chip.className = "chip env";
        chip.textContent = txt;
        pcSpecs.appendChild(chip);
      });
      setLiveStatus("bridge connected — repos will run on the COMPLETE PC (real processes), sandboxed and wiped after");
      liveCard.classList.remove("hidden");
      line("── connected to this PC's sandbox ──", "t-meta");
      line([d.platform, d.cpu_cores + " cores", d.memory_gb ? d.memory_gb + " GB RAM" : "",
            "python " + d.python].filter(Boolean).join(" · "), "t-dim");
      line("paste a repo link above and press Run it", "t-dim");
      renderStageChips();
    } else if (data.needs_token) {
      setLiveStatus("bridge found, but that token didn't match — check the cleanroom serve window", true);
      liveCard.classList.remove("hidden");
      line("bridge found, but that token didn't match — check the cleanroom serve window", "t-err");
    } else {
      setLiveStatus("bridge said: " + (data.error || "unknown error"), true);
    }
  } catch (e) {
    setLiveStatus("no bridge on 127.0.0.1:8641 — run `python cleanroom.py serve` on this PC first", true);
    liveCard.classList.remove("hidden");
    line("no bridge on 127.0.0.1:8641 — run `python cleanroom.py serve` on this PC first", "t-err");
  }
}
pcConnect?.addEventListener("click", connectBridge);
pcToken?.addEventListener("keydown", e => { if (e.key === "Enter") connectBridge(); });

function deviceInfo() {
  const parts = [];
  try { if (navigator.hardwareConcurrency) parts.push(navigator.hardwareConcurrency + " logical cores"); } catch (e) { /* none */ }
  try { if (navigator.deviceMemory) parts.push(navigator.deviceMemory + " GB RAM"); } catch (e) { /* none */ }
  try {
    const cv = document.createElement("canvas");
    const gl = cv.getContext("webgl");
    const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    if (ext) parts.push(String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)).slice(0, 44));
  } catch (e) { /* none */ }
  return parts.join(" · ") || "your device's CPU";
}

function multiCoreOn() {
  return window.crossOriginIsolated === true && typeof SharedArrayBuffer !== "undefined";
}

/* the in-tab VM's honest device panel: what this tab can actually see and
   use. Rendered at startup and refreshed when the mode changes. */
function renderVmSpecs() {
  const row = document.getElementById("vmSpecs");
  if (!row) return;
  row.replaceChildren();
  const chips = [];
  try { chips.push(navigator.platform || "unknown platform"); } catch (e) { /* none */ }
  try { if (navigator.hardwareConcurrency) chips.push(navigator.hardwareConcurrency + " logical cores"); } catch (e) { /* none */ }
  try { if (navigator.deviceMemory) chips.push("≥" + navigator.deviceMemory + " GB RAM (browser-reported)"); } catch (e) { /* none */ }
  try {
    const gl = document.createElement("canvas").getContext("webgl");
    const ext = gl && gl.getExtension("WEBGL_debug_renderer_info");
    if (ext) chips.push("GPU: " + String(gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)).slice(0, 40));
  } catch (e) { /* none */ }
  chips.push("WebGPU: " + ("gpu" in navigator ? "available" : "no"));
  chips.push(multiCoreOn()
    ? "multi-core: enabled (Web Workers + SharedArrayBuffer)"
    : "multi-core: single-thread (the browser did not isolate this tab)");
  for (const txt of chips) {
    const chip = document.createElement("span");
    chip.className = "chip " + (txt.startsWith("multi-core: enabled") ? "info" : "env");
    chip.textContent = txt;
    row.appendChild(chip);
  }
}
renderVmSpecs();

function line(text, cls) {
  const el = document.createElement("div");
  el.className = "tline " + (cls || "t-out");
  el.textContent = text === "" ? " " : text;
  term.appendChild(el);
  term.scrollTop = term.scrollHeight;
}

function hintChip(text) {
  const el = document.createElement("div");
  el.className = "tline t-hint";
  el.textContent = "  " + text;
  el.addEventListener("click", () => { termInput.value = text; termInput.focus(); });
  term.appendChild(el);
  term.scrollTop = term.scrollHeight;
}

function setLiveStatus(msg, isError) {
  liveStatus.textContent = msg;
  liveStatus.className = "status" + (isError ? " error" : "");
}

/* ---------- see it before you run it: report card, run cards, file browser ---------- */

function runCommand(cmd) {
  termInput.value = "";
  line("repo> " + cmd, "t-echo");
  if (mode === "pc") pcExec(cmd); else execLine(cmd);
}

function renderRunCards(items) {
  if (!runRow) return;
  runRow.replaceChildren();
  if (!items.length) { runRow.classList.add("hidden"); return; }
  for (const it of items) {
    if (it.kind === "link") {
      const a = document.createElement("a");
      a.className = "runcard full";
      a.href = it.href;
      a.target = "_blank";
      a.rel = "noopener";
      a.innerHTML = "▶ <b></b>";
      a.querySelector("b").textContent = it.label;
      a.title = it.title || it.href;
      runRow.appendChild(a);
      continue;
    }
    const b = document.createElement("button");
    b.className = "runcard" + (it.kind === "approve" ? " danger" : "")
                + (it.kind === "autorun" ? " autorun" : "");
    b.innerHTML = "▶ <b></b>";
    b.querySelector("b").textContent = it.label;
    b.title = it.title || it.cmd;
    b.addEventListener("click", () => {
      if (it.kind === "autorun" && mode === "pc") {
        // the bridge built this chain from the repo's own README/manifests —
        // fill the start row and go, with network on if installs need it
        pcWebCmd.value = it.cmd;
        pcWebNet.checked = !!it.net;
        showPcWebRow();
        pcWebStartFn();
        return;
      }
      runCommand(it.cmd);
    });
    runRow.appendChild(b);
  }
  runRow.classList.remove("hidden");
}

const VERDICT_CLS = { clean: "v-clean", caution: "v-caution",
                      "high-risk": "v-high-risk", dangerous: "v-dangerous" };

function renderTriCard(report, label, filesScanned) {
  if (!triCard) return;
  triScore.textContent = report.score;
  triScore.style.color = report.score >= 25 ? "var(--red)"
                        : report.score >= 10 ? "var(--amber)" : "var(--accent)";
  triVerdict.textContent = report.verdict.toUpperCase();
  triVerdict.className = "verdict-word " + (VERDICT_CLS[report.verdict] || "");
  triMeta.replaceChildren();
  const l = document.createElement("b");
  l.textContent = label;
  triMeta.appendChild(l);
  triMeta.append(document.createElement("br"),
    filesScanned + " files scanned · " + report.findings.length + " findings (" +
    report.findings.filter(f => f.severity !== "info").length + " scored)");
  triCats.replaceChildren();
  for (const [cat, n] of Object.entries(report.by_category || {})) {
    const chip = document.createElement("span");
    chip.className = "chip info";
    chip.textContent = cat + " × " + n;
    triCats.appendChild(chip);
  }
  triFindings.replaceChildren();
  const scored = report.findings.filter(f => f.severity !== "info");
  const infos = report.findings.filter(f => f.severity === "info");
  const rows = scored.concat(infos.slice(0, 40));
  const cls = { critical: "crit", high: "high", medium: "med", info: "info" };
  if (!rows.length) {
    const row = div("frow");
    row.innerHTML = '<span class="rule">—</span><span class="where">—</span><span class="snip">nothing found</span>';
    triFindings.appendChild(row);
  }
  for (const f of rows) {
    const row = div("frow");
    row.style.gridTemplateColumns = "86px 150px 1fr 220px";
    const what = RULE_WHAT[f.rule];
    row.innerHTML =
      '<span class="chip ' + (cls[f.severity] || "info") + '"></span>' +
      '<span class="rule"></span>' +
      '<span class="snip"></span>' +
      '<span class="where"></span>' +
      (what ? '<span class="fwhat"></span>' : "");
    const s = row.children;
    s[0].textContent = f.severity;
    s[1].textContent = f.rule;
    s[2].textContent = f.snippet;
    s[2].title = f.snippet;
    s[3].textContent = f.file + ":" + f.line;
    if (what) { s[4].textContent = "· " + what; s[4].style.gridColumn = "1 / -1"; }
    triFindings.appendChild(row);
  }
  triEnv.replaceChildren();
  if (!(report.touched_env || []).length) {
    const chip = document.createElement("span");
    chip.className = "chip env";
    chip.textContent = "reads no environment variables";
    triEnv.appendChild(chip);
  } else {
    for (const name of report.touched_env) {
      const chip = document.createElement("span");
      chip.className = "chip env";
      chip.textContent = name;
      triEnv.appendChild(chip);
    }
  }
  triCheck.replaceChildren();
  for (const step of beforeRunSteps(report)) {
    const li = document.createElement("li");
    li.textContent = step;
    triCheck.appendChild(li);
  }
  triBody.classList.add("hidden");
  triToggle.textContent = "show findings ▾";
  triCard.classList.remove("hidden");
}

triToggle?.addEventListener("click", () => {
  if (!triBody) return;
  const open = !triBody.classList.contains("hidden");
  triBody.classList.toggle("hidden", open);
  triToggle.textContent = open ? "show findings ▾" : "hide findings ▴";
});

/* the file browser: every file the repo has, clickable, strictly read-only */
const FILE_VIEW_MAX = 256 * 1024;

function renderFilePills(paths) {
  if (!filesCard || !filePills) return;
  filePills.replaceChildren();
  if (fileView) fileView.classList.add("hidden");
  if (!paths.length) { filesCard.classList.add("hidden"); return; }
  for (const p of paths.slice(0, 200)) {
    const b = document.createElement("button");
    b.className = "filepill";
    b.textContent = p;
    b.title = "view (read-only): " + p;
    b.addEventListener("click", () => viewRepoFile(p));
    filePills.appendChild(b);
  }
  if (paths.length > 200) {
    const more = div("mode-hint");
    more.style.flexBasis = "100%";
    more.textContent = "… " + (paths.length - 200) + " more files — type 'ls' to list them";
    filePills.appendChild(more);
  }
  if (filesMeta) {
    filesMeta.textContent = paths.length + " files · click one to read it (read-only — nothing executes)";
  }
  filesCard.classList.remove("hidden");
}

function showFileView(path, text, note) {
  if (!fileView || !fileViewPath || !fileViewPre) return;
  fileViewPath.textContent = path;
  fileViewPre.textContent = note || text;
  fileView.classList.remove("hidden");
  fileView.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

async function viewRepoFile(path) {
  if (mode === "pc") {
    const { data } = await bridgeCall("/api/file", { path });
    if (!data.ok) { line("file: " + (data.error || "unreadable"), "t-err"); return; }
    if (data.too_big) {
      showFileView(path, "", "(file is " + Math.round(data.size / 1024) + " KB — over the 256 KB view cap)");
      return;
    }
    if (data.binary) { showFileView(path, "", "(binary or non-UTF-8 file — not shown)"); return; }
    showFileView(path, data.text);
    return;
  }
  const entry = vmFiles.find(f => f[0] === path);
  if (!entry) { line("no such file: " + path, "t-err"); return; }
  if (entry[1].length > FILE_VIEW_MAX) {
    showFileView(path, "", "(file is over 256 KB — not shown here)");
    return;
  }
  showFileView(path, entry[1]);
}

fileViewClose?.addEventListener("click", () => fileView?.classList.add("hidden"));

/* wipe/reset clears every panel this section owns */
function resetRunUi() {
  triCard?.classList.add("hidden");
  if (runRow) { runRow.replaceChildren(); runRow.classList.add("hidden"); }
  if (filePills) filePills.replaceChildren();
  filesCard?.classList.add("hidden");
  if (fileView) fileView.classList.add("hidden");
}

/* the shelf: curated entries that run for real, each saying what you'll see */
function renderShelf() {
  if (!shelfRow) return;
  shelfRow.replaceChildren();
  for (const item of SHELF) {
    const b = document.createElement("button");
    b.className = "shelfbtn";
    const name = document.createElement("b");
    name.textContent = item.repo.replace("https://github.com/amanvicky/", "");
    const what = document.createElement("small");
    what.textContent = item.what;
    b.appendChild(name);
    b.appendChild(what);
    b.title = item.repo + " — verified at commit " + item.at;
    b.addEventListener("click", () => {
      liveUrl.value = item.repo;
      loadRepo(item.repo);
    });
    shelfRow.appendChild(b);
  }
  const you = document.createElement("button");
  you.className = "shelfbtn yours";
  const yn = document.createElement("b");
  yn.textContent = "your repo";
  const yw = document.createElement("small");
  yw.textContent = "paste any GitHub link above — nothing installs";
  you.appendChild(yn);
  you.appendChild(yw);
  you.addEventListener("click", () => liveUrl.focus());
  shelfRow.appendChild(you);
}

const HELP = [
  "commands:",
  "  run <file.py>    execute a file from the repo (its output prints here)",
  "  ls               list the files loaded into the virtual machine",
  "  pip <package>    install a package into the VM (numpy, pandas, micropip wheels)",
  "  setup            install the repo's own requirements (from its manifests + README), honestly per package",
  "  serve            mount the repo's Flask/Django app in the VM and serve it through this page's virtual network",
  "  preview          open a static web app (index.html) sandboxed in the app screen",
  "  clear            clear this terminal",
  "  wipe             erase everything: files, variables, modules — residue: 0",
  "  anything else    runs as Python in the repo's directory",
  "notes:",
  "  the VM has no network sockets — code cannot phone home; server apps run behind the page's virtual network",
  "  tkinter apps render on the app screen (virtual tkinter: real HTML widgets, a subset)",
  "  packages with compiled binaries can't load in a tab — the catalog and pure-Python wheels can",
  "  input() is not wired up — assign values directly in code",
  "  an infinite loop freezes the tab — refresh to recover, nothing was saved anyway",
  "  closing this tab wipes the VM. nothing was ever written to your disk.",
];

const VM_SETUP = `
import os, sys
os.makedirs("/repo", exist_ok=True)
if "/repo" not in sys.path:
    sys.path.insert(0, "/repo")
try:
    os.chdir("/repo")
except Exception:
    pass
`;

async function ensureVM(onStatus) {
  if (pyodide) return pyodide;
  if (!vmLoading) {
    vmLoading = (async () => {
      if (!window.loadPyodide) {
        onStatus("fetching the in-browser Python VM (one-time, ~10 MB, cached by your browser)…");
        await new Promise((resolve, reject) => {
        const s = document.createElement("script");
        s.src = PYODIDE_BASE + "pyodide.js";
        s.crossOrigin = "anonymous";  // COEP require-corp: CDN scripts need CORS mode
          s.onload = resolve;
          s.onerror = () => reject(new Error("could not fetch the Python VM from the CDN"));
          document.head.appendChild(s);
        });
      }
      onStatus("starting the VM…");
      const py = await window.loadPyodide({ indexURL: PYODIDE_BASE });
      py.setStdout({ batched: s => line(s, "t-out") });
      py.setStderr({ batched: s => line(s, "t-err") });
      await py.runPythonAsync(VM_SETUP);
      await installShim(py);
      pyodide = py;
      return py;
    })();
  }
  return vmLoading;
}

/* ---------- the app screen: virtual tkinter + sandboxed web preview ---------- */

let guiEv = null;          // PyProxy of builtins.__cr_gui_event (set by the shim)
let shimSrc = null;
let nextId = 1;
const W = new Map();       // widget id → record
let renderQueued = false;

async function installShim(py) {
  if (shimSrc === null) {
    try {
      shimSrc = await (await fetch(new URL("tkinter_shim.py", import.meta.url))).text();
    } catch (e) {
      shimSrc = "";
      line("note: the virtual tkinter shim couldn't load (" + e.message + ") — GUI apps won't render", "t-dim");
    }
  }
  if (!shimSrc) return;
  py.FS.writeFile("/_cleanroom_tkinter_shim.py", shimSrc, { encoding: "utf8" });
  await py.runPythonAsync(`
import sys, types as _t
_m = _t.ModuleType("tkinter"); _m.__path__ = []
exec(compile(open("/_cleanroom_tkinter_shim.py", encoding="utf-8").read(),
             "tkinter_shim.py", "exec"), _m.__dict__)
_m._install_submodules(_m)
sys.modules["tkinter"] = _m
sys.modules["tkinter.constants"] = _m.constants
sys.modules["tkinter.ttk"] = _m.ttk
sys.modules["tkinter.messagebox"] = _m.messagebox
sys.modules["tkinter.filedialog"] = _m.filedialog
sys.modules["tkinter.simpledialog"] = _m.simpledialog
del _m, _t
`);
  guiEv = py.runPython("__import__('builtins').__cr_gui_event");
}

function fire(id, name, data) {
  if (!guiEv) return 0;
  let pending = 0;
  try {
    pending = Number(guiEv(id, name, JSON.stringify(data || {}))) || 0;
  } catch (e) {
    line("gui callback error: " + e.message, "t-err");
    return 0;
  }
  if (pending > 0) {
    pyodide.runPythonAsync("import builtins as _b; await _b.__cr_gui_drain()")
      .catch(e => line("gui async error: " + e.message, "t-err"));
  }
  return pending;
}

function div(cls) {
  const el = document.createElement("div");
  el.className = cls;
  return el;
}

function reliefCss(relief) {
  return { flat: "solid", groove: "groove", ridge: "ridge",
           raised: "outset", sunken: "inset", solid: "solid" }[String(relief)] || "solid";
}

function margins(px, py) {
  const x = Array.isArray(px) ? (px[0] || 0) : (px || 0);
  const y = Array.isArray(py) ? (py[0] || 0) : (py || 0);
  return y + "px " + x + "px";
}

function buildEl(rec) {
  const p = rec.props;
  let el;
  if (rec.kind === "root" || rec.kind === "toplevel") {
    el = div("tkwindow");
    const bar = div("tkbar");
    const title = document.createElement("span");
    title.className = "tkbar-title";
    title.textContent = p.title || "";
    const close = document.createElement("button");
    close.className = "tkclose";
    close.textContent = "✕";
    close.title = "close the window";
    close.addEventListener("click", () => {
      fire(rec.id, "wm_delete", {});
      appCard.classList.add("hidden");
    });
    bar.appendChild(title);
    bar.appendChild(close);
    const body = div("tkbody");
    el.appendChild(bar);
    el.appendChild(body);
    rec.body = body;
    rec.titleEl = title;
    return el;
  }
  switch (rec.kind) {
    case "frame": el = div("tkframe"); break;
    case "labelframe": {
      el = document.createElement("fieldset");
      el.className = "tkframe tkfieldset";
      const legend = document.createElement("legend");
      legend.textContent = p.text || "";
      el.appendChild(legend);
      break;
    }
    case "label": el = div("tklabel"); break;
    case "button": {
      el = document.createElement("button");
      el.className = "tkbutton";
      el.addEventListener("click", () => fire(rec.id, "command", {}));
      break;
    }
    case "entry": {
      el = document.createElement("input");
      el.className = "tkentry";
      el.type = p.show === "*" ? "password" : "text";
      el.spellcheck = false;
      el.addEventListener("input", () => fire(rec.id, "entry", { value: el.value }));
      break;
    }
    case "text": {
      el = document.createElement("textarea");
      el.className = "tktext";
      el.spellcheck = false;
      break;
    }
    case "listbox": {
      el = document.createElement("select");
      el.className = "tklistbox";
      el.size = Math.max(2, p.height || 10);
      const mode = String(p.selectmode || "single");
      if (mode === "multiple" || mode === "extended") el.multiple = true;
      el.addEventListener("change", () => {
        const indices = Array.from(el.options)
          .map((o, i) => o.selected ? i : -1).filter(i => i >= 0);
        fire(rec.id, "select", { indices });
      });
      break;
    }
    case "check": {
      el = document.createElement("label");
      el.className = "tkcheck";
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.addEventListener("change", () => fire(rec.id, "command", { checked: cb.checked }));
      const span = document.createElement("span");
      span.textContent = p.text || "";
      el.appendChild(cb);
      el.appendChild(span);
      rec.input = cb;
      rec.span = span;
      break;
    }
    case "radio": {
      el = document.createElement("label");
      el.className = "tkcheck";
      const rb = document.createElement("input");
      rb.type = "radio";
      if (p.radioGroup) rb.name = p.radioGroup;
      rb.addEventListener("change", () => fire(rec.id, "command", { checked: rb.checked, value: p.value }));
      const span = document.createElement("span");
      span.textContent = p.text || "";
      el.appendChild(rb);
      el.appendChild(span);
      rec.input = rb;
      rec.span = span;
      break;
    }
    case "scale": {
      el = document.createElement("input");
      el.className = "tkscale";
      el.type = "range";
      el.min = p.from !== undefined ? p.from : 0;
      el.max = p.to !== undefined ? p.to : 100;
      el.step = p.resolution || 1;
      if (p.value !== undefined) el.value = p.value;
      if (p.orient === "vertical") el.style.writingMode = "vertical-lr";
      el.addEventListener("input", () => fire(rec.id, "command", { value: Number(el.value) }));
      break;
    }
    case "canvas": {
      el = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      el.setAttribute("width", p.width || 300);
      el.setAttribute("height", p.height || 200);
      el.style.background = p.background || "#ffffff";
      el.addEventListener("mousedown", ev => {
        const r = el.getBoundingClientRect();
        const sx = (Number(el.getAttribute("width")) || 300) / r.width;
        const sy = (Number(el.getAttribute("height")) || 200) / r.height;
        fire(rec.id, "click", { x: Math.round((ev.clientX - r.left) * sx),
                                y: Math.round((ev.clientY - r.top) * sy) });
      });
      break;
    }
    case "scrollbar": el = div("tk-noscroll"); break;
    case "menu": el = document.createComment("menu"); break;
    default: el = div("tkframe");
  }
  return el;
}

function applyProps(rec) {
  const el = rec.el, p = rec.props;
  if (!el || el.nodeType !== 1) return;
  if (rec.kind === "root" || rec.kind === "toplevel") {
    if (p.title !== undefined && rec.titleEl) rec.titleEl.textContent = p.title;
    if (p.winWidth) el.style.width = p.winWidth + "px";
    if (p.winHeight) el.style.minHeight = p.winHeight + "px";
    return;
  }
  if (p.background !== undefined && rec.kind !== "canvas") el.style.background = p.background;
  if (p.color !== undefined) el.style.color = p.color;
  if (p.font !== undefined) Object.assign(el.style, p.font);
  if (p.relief !== undefined) {
    el.style.borderStyle = reliefCss(p.relief);
    el.style.borderWidth = (p.borderwidth !== undefined ? p.borderwidth : 1) + "px";
    el.style.borderColor = "#8a97a8";
  } else if (p.borderwidth !== undefined) {
    el.style.borderWidth = p.borderwidth + "px";
  }
  if (p.justify !== undefined) el.style.textAlign = p.justify;
  if (p.state === "disabled" && "disabled" in (rec.input || el)) (rec.input || el).disabled = true;
  if (p.state && p.state !== "disabled" && rec.input) rec.input.disabled = false;
  if (p.text !== undefined) {
    if (rec.kind === "label") el.textContent = p.text;
    else if (rec.kind === "button") el.textContent = p.text;
    else if (rec.span) rec.span.textContent = p.text;
  }
  if (p.value !== undefined) {
    if (rec.kind === "entry" && document.activeElement !== el) el.value = p.value;
    if (rec.kind === "scale") el.value = p.value;
    if (rec.kind === "radio" && rec.input) rec.input.checked = !!p.checked;
  }
  if (p.checked !== undefined && rec.input) rec.input.checked = !!p.checked;
  if (p.show !== undefined && rec.kind === "entry") el.type = p.show === "*" ? "password" : "text";
  if (p.wraplength !== undefined) el.style.maxWidth = p.wraplength + "px";
  if (p.width !== undefined) {
    if (rec.kind === "entry") el.size = p.width;
    else if (rec.kind === "text") el.cols = p.width;
    else el.style.width = p.width + "px";
  }
  if (p.height !== undefined) {
    if (rec.kind === "text") el.rows = p.height;
    else if (rec.kind === "listbox") el.size = Math.max(2, p.height);
    else el.style.height = p.height + "px";
  }
  if (p.wrap !== undefined && rec.kind === "text") el.wrap = p.wrap === "none" ? "off" : "soft";
}

function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  queueMicrotask(renderAll);
}

function renderAll() {
  renderQueued = false;
  for (const rec of W.values()) {
    if ((rec.kind === "root" || rec.kind === "toplevel") && rec.body) {
      renderParent(rec.body, rec.kids);
    } else if (rec.kind === "frame" || rec.kind === "labelframe") {
      renderParent(rec.el, rec.kids);
    }
  }
}

function renderParent(container, kids) {
  const packed = kids.filter(k => k.el && k.el.nodeType === 1 && (k.geo || null));
  if (!packed.length) return;
  const mode = packed[0].geo.mode;
  if (mode === "pack") renderPack(container, kids);
  else if (mode === "grid") renderGrid(container, kids);
  else renderPlace(container, kids);
}

function renderPack(container, kids) {
  const lanes = [];
  const plain = [];
  for (const rec of kids) {
    if (!rec.el || rec.el.nodeType !== 1) continue;
    if (!rec.geo) { plain.push(rec); continue; }
    if (rec.geo.mode !== "pack") continue;
    const side = rec.geo.side || "top";
    const dir = (side === "left" || side === "right") ? "row" : "col";
    const last = lanes[lanes.length - 1];
    if (dir === "row" && last && last.dir === "row") last.items.push(rec);
    else lanes.push({ dir, items: [rec] });
  }
  if (plain.length) lanes.push({ dir: "col", items: plain });
  const frag = document.createDocumentFragment();
  for (const lane of lanes) {
    const laneEl = div("tklane " + (lane.dir === "row" ? "tkrow" : "tkcol"));
    for (const rec of lane.items) {
      laneEl.appendChild(rec.el);
      stylePack(rec, lane.dir);
    }
    frag.appendChild(laneEl);
  }
  container.replaceChildren(frag);
}

function stylePack(rec, laneDir) {
  const g = rec.geo || {};
  const el = rec.el;
  const expand = g.expand && g.expand !== "0";
  el.style.flexGrow = expand ? "1" : "0";
  el.style.margin = margins(g.padx, g.pady);
  if (g.fill === "x" && laneDir === "col") el.style.width = "100%";
  if (g.fill === "y" && laneDir === "row") el.style.height = "100%";
  if (g.fill === "both") { el.style.width = "100%"; }
  const anchor = String(g.anchor || "");
  if (anchor.includes("n") && !anchor.includes("s")) el.style.alignSelf = "flex-start";
  else if (anchor.includes("s") && !anchor.includes("n")) el.style.alignSelf = "flex-end";
  else if (anchor === "center") el.style.alignSelf = "center";
  if (expand && laneDir === "row") el.style.alignSelf = "stretch";
}

function renderGrid(container, kids) {
  let cols = 1;
  for (const rec of kids) {
    if (!rec.el || rec.el.nodeType !== 1 || !rec.geo) continue;
    const c = (rec.geo.column || 0) + (rec.geo.columnspan || 1);
    if (c > cols) cols = c;
  }
  container.style.display = "grid";
  container.style.gridTemplateColumns = "repeat(" + cols + ", minmax(0, auto))";
  container.style.gap = "4px";
  const frag = document.createDocumentFragment();
  for (const rec of kids) {
    if (!rec.el || rec.el.nodeType !== 1) continue;
    const g = rec.geo;
    if (g.mode !== "grid") { frag.appendChild(rec.el); continue; }
    rec.el.style.gridColumn = ((g.column || 0) + 1) + " / span " + (g.columnspan || 1);
    rec.el.style.gridRow = ((g.row || 0) + 1) + " / span " + (g.rowspan || 1);
    const sticky = String(g.sticky || "").toLowerCase();
    if (sticky.includes("e") && sticky.includes("w")) rec.el.style.justifySelf = "stretch";
    else if (sticky.includes("w")) rec.el.style.justifySelf = "start";
    else if (sticky.includes("e")) rec.el.style.justifySelf = "end";
    if (sticky.includes("n") && sticky.includes("s")) rec.el.style.alignSelf = "stretch";
    else if (sticky.includes("n")) rec.el.style.alignSelf = "start";
    else if (sticky.includes("s")) rec.el.style.alignSelf = "end";
    rec.el.style.margin = margins(g.padx, g.pady);
    frag.appendChild(rec.el);
  }
  container.replaceChildren(frag);
}

function renderPlace(container, kids) {
  container.style.position = "relative";
  const frag = document.createDocumentFragment();
  for (const rec of kids) {
    if (!rec.el || rec.el.nodeType !== 1) continue;
    const g = rec.geo;
    if (g.mode !== "place") { frag.appendChild(rec.el); continue; }
    rec.el.style.position = "absolute";
    rec.el.style.left = g.rely !== undefined && g.relx !== undefined
      ? "calc(" + g.relx * 100 + "%)" : (g.x !== undefined ? g.x + "px" : "0px");
    rec.el.style.top = g.rely !== undefined
      ? "calc(" + g.rely * 100 + "%)" : (g.y !== undefined ? g.y + "px" : "0px");
    if (g.width) rec.el.style.width = g.width + "px";
    if (g.height) rec.el.style.height = g.height + "px";
    frag.appendChild(rec.el);
  }
  container.replaceChildren(frag);
}

/* canvas → SVG */
const SVG_NS = "http://www.w3.org/2000/svg";

function svgItem(kind, coords, opts) {
  const n = v => Number(v) || 0;
  let el = null;
  if (kind === "line") {
    el = document.createElementNS(SVG_NS, "line");
    el.setAttribute("x1", n(coords[0])); el.setAttribute("y1", n(coords[1]));
    el.setAttribute("x2", n(coords[2])); el.setAttribute("y2", n(coords[3]));
    el.setAttribute("stroke", opts.outline || opts.fill || "#111");
    el.setAttribute("stroke-width", n(opts.width) || 1);
  } else if (kind === "rectangle") {
    el = document.createElementNS(SVG_NS, "rect");
    el.setAttribute("x", Math.min(n(coords[0]), n(coords[2])));
    el.setAttribute("y", Math.min(n(coords[1]), n(coords[3])));
    el.setAttribute("width", Math.abs(n(coords[2]) - n(coords[0])));
    el.setAttribute("height", Math.abs(n(coords[3]) - n(coords[1])));
    el.setAttribute("fill", opts.fill || "none");
    el.setAttribute("stroke", opts.outline || "#111");
    el.setAttribute("stroke-width", n(opts.width) || 1);
  } else if (kind === "oval") {
    el = document.createElementNS(SVG_NS, "ellipse");
    el.setAttribute("cx", (n(coords[0]) + n(coords[2])) / 2);
    el.setAttribute("cy", (n(coords[1]) + n(coords[3])) / 2);
    el.setAttribute("rx", Math.abs(n(coords[2]) - n(coords[0])) / 2);
    el.setAttribute("ry", Math.abs(n(coords[3]) - n(coords[1])) / 2);
    el.setAttribute("fill", opts.fill || "none");
    el.setAttribute("stroke", opts.outline || "#111");
    el.setAttribute("stroke-width", n(opts.width) || 1);
  } else if (kind === "polygon") {
    el = document.createElementNS(SVG_NS, "polygon");
    el.setAttribute("points", coords.map(n).join(","));
    el.setAttribute("fill", opts.fill || "none");
    el.setAttribute("stroke", opts.outline || "#111");
  } else if (kind === "text") {
    el = document.createElementNS(SVG_NS, "text");
    el.setAttribute("x", n(coords[0]));
    el.setAttribute("y", n(coords[1]));
    el.setAttribute("fill", opts.fill || "#111");
    el.setAttribute("font-size", n(opts.fontSize) || 12);
    el.textContent = opts.text || "";
  } else if (kind === "arc") {
    const cx = (n(coords[0]) + n(coords[2])) / 2, cy = (n(coords[1]) + n(coords[3])) / 2;
    const rx = Math.abs(n(coords[2]) - n(coords[0])) / 2, ry = Math.abs(n(coords[3]) - n(coords[1])) / 2;
    const a0 = (n(opts.start) || 0) * Math.PI / 180;
    const a1 = a0 + (n(opts.extent) || 90) * Math.PI / 180;
    const x0 = cx + rx * Math.cos(a0), y0 = cy + ry * Math.sin(a0);
    const x1 = cx + rx * Math.cos(a1), y1 = cy + ry * Math.sin(a1);
    el = document.createElementNS(SVG_NS, "path");
    const large = Math.abs(n(opts.extent) || 90) > 180 ? 1 : 0;
    const sweep = (n(opts.extent) || 90) >= 0 ? 1 : 0;
    let d = "M " + x0 + " " + y0 + " A " + rx + " " + ry + " 0 " + large + " " + sweep + " " + x1 + " " + y1;
    if (opts.style === "pieslice") d += " L " + cx + " " + cy + " Z";
    el.setAttribute("d", d);
    el.setAttribute("fill", opts.style === "pieslice" ? (opts.fill || "none") : "none");
    el.setAttribute("stroke", opts.outline || "#111");
    el.setAttribute("stroke-width", n(opts.width) || 1);
  }
  return el;
}

function patchSvg(el, opts) {
  if (!el) return;
  if (opts.fill !== undefined) el.setAttribute("fill", opts.fill);
  if (opts.outline !== undefined) el.setAttribute("stroke", opts.outline);
  if (opts.width !== undefined) el.setAttribute("stroke-width", Number(opts.width) || 1);
  if (opts.text !== undefined) el.textContent = opts.text;
  if (opts.fontSize !== undefined) el.setAttribute("font-size", Number(opts.fontSize) || 12);
}

function guiApiImpl() {
  return {
    show(title) {
      appCard.classList.remove("hidden");
      if (title) appTitle.textContent = title + " — app screen (virtual tkinter)";
    },
    hide() { appCard.classList.add("hidden"); },
    reset() {
      W.clear();
      appContent.replaceChildren();
      nextId = 1;
      appCard.classList.add("hidden");
      webWrap.classList.add("hidden");
      webFrame.removeAttribute("srcdoc");
      webFrame.src = "about:blank";
      guiEv = null;
    },
    create(parentId, kind, propsJson) {
      const id = nextId++;
      let props = {};
      try { props = JSON.parse(propsJson || "{}"); } catch (e) { /* keep {} */ }
      const rec = { id, kind, props, parent: parentId, el: null, geo: null, kids: [], items: new Map(), nextItem: 1 };
      rec.el = buildEl(rec);
      W.set(id, rec);
      const p = W.get(parentId);
      if (p && p.body) p.body.appendChild(rec.el);
      else if (p && p.el && p.el.nodeType === 1) p.el.appendChild(rec.el);
      else if (rec.kind === "root" || rec.kind === "toplevel") appContent.appendChild(rec.el);
      applyProps(rec);
      scheduleRender();
      return id;
    },
    config(id, propsJson) {
      const rec = W.get(id);
      if (!rec) return;
      try { Object.assign(rec.props, JSON.parse(propsJson || "{}")); } catch (e) { /* keep */ }
      applyProps(rec);
      scheduleRender();
    },
    layout(id, geoJson) {
      const rec = W.get(id);
      if (!rec) return;
      try { rec.geo = JSON.parse(geoJson || "{}"); } catch (e) { rec.geo = null; }
      scheduleRender();
    },
    remove(id) {
      const rec = W.get(id);
      if (!rec) return;
      rec.el && rec.el.remove && rec.el.remove();
      W.delete(id);
      scheduleRender();
    },
    after(ms, cb) { setTimeout(() => fire(0, "after", { cb }), Math.max(0, ms)); },
    value(id) {
      const rec = W.get(id);
      if (!rec || !rec.el) return "";
      return String(rec.el.value ?? "");
    },
    entryInsert(id, index, text) {
      const rec = W.get(id);
      if (!rec) return;
      const v = rec.el.value || "";
      if (String(index) === "end") rec.el.value = v + text;
      else {
        const at = Math.min(Math.max(parseInt(index, 10) || 0, 0), v.length);
        rec.el.value = v.slice(0, at) + text + v.slice(at);
      }
    },
    entryDelete(id, first, last) {
      const rec = W.get(id);
      if (!rec) return;
      const v = rec.el.value || "";
      const a = Math.min(Math.max(parseInt(first, 10) || 0, 0), v.length);
      const b = (last === null || last === undefined || String(last) === "end")
        ? v.length : Math.min(Math.max(parseInt(last, 10) || 0, 0), v.length);
      rec.el.value = v.slice(0, Math.min(a, b)) + v.slice(Math.max(a, b));
    },
    textSet(id, t) {
      const rec = W.get(id);
      if (rec) rec.el.value = t;
    },
    textAppend(id, t) {
      const rec = W.get(id);
      if (!rec) return;
      rec.el.value += t;
      rec.el.scrollTop = rec.el.scrollHeight;
    },
    list(id, op, payloadJson) {
      const rec = W.get(id);
      if (!rec || !rec.el) return op === "get" ? [] : (op === "cur" ? [] : (op === "size" ? 0 : 0));
      const el = rec.el;
      let p = {};
      try { p = JSON.parse(payloadJson || "{}"); } catch (e) { /* keep {} */ }
      if (op === "insert") {
        let at = el.options.length;
        const idx = String(p.index ?? "end");
        if (/^\d+$/.test(idx)) at = Math.min(parseInt(idx, 10), el.options.length);
        for (const t of (p.items || [])) {
          const o = document.createElement("option");
          o.value = t;
          o.textContent = t;
          el.insertBefore(o, el.options[at] || null);
          at++;
        }
        return 0;
      }
      if (op === "delete") {
        const a = String(p.first);
        if (/^end/.test(String(p.last ?? "end")) || p.last === null) {
          const from = /^\d+$/.test(a) ? parseInt(a, 10) : 0;
          Array.from(el.options).slice(from).forEach(o => o.remove());
        } else {
          const from = /^\d+$/.test(a) ? parseInt(a, 10) : 0;
          const to = parseInt(String(p.last), 10);
          Array.from(el.options).slice(from, to + 1).forEach(o => o.remove());
        }
        return 0;
      }
      if (op === "get") return Array.from(el.options).map(o => o.textContent);
      if (op === "cur") {
        return Array.from(el.options).map((o, i) => o.selected ? i : -1).filter(i => i >= 0);
      }
      if (op === "size") return el.options.length;
      if (op === "select") {
        Array.from(el.options).forEach((o, i) => {
          o.selected = i >= p.first && (p.to === null || p.to === undefined || i <= p.to);
        });
        return 0;
      }
      if (op === "clearsel") {
        Array.from(el.options).forEach(o => { o.selected = false; });
        return 0;
      }
      if (op === "see") {
        if (el.options[p.index]) el.selectedIndex = p.index;
        return 0;
      }
      return 0;
    },
    canvas(id, op, payloadJson) {
      const rec = W.get(id);
      if (!rec || !rec.el) return 0;
      const svg = rec.el;
      let p = {};
      try { p = JSON.parse(payloadJson || "{}"); } catch (e) { /* keep {} */ }
      if (op.startsWith("create_")) {
        const el = svgItem(op.slice(7), p.coords || [], p.opts || {});
        if (!el) return 0;
        const itemId = rec.nextItem++;
        el.dataset.item = String(itemId);
        const tags = (p.opts && p.opts.tags) || [];
        if (tags.length) el.dataset.tags = tags.join(" ");
        svg.appendChild(el);
        rec.items.set(itemId, el);
        return itemId;
      }
      if (op === "itemconfig") { patchSvg(rec.items.get(p.item), p.opts || {}); return 0; }
      if (op === "delete") {
        for (const it of (p.items && p.items.length ? p.items : ["all"])) {
          if (it === "all") { svg.replaceChildren(); rec.items.clear(); }
          else if (/^\d+$/.test(String(it))) {
            const el = rec.items.get(Number(it));
            if (el) { el.remove(); rec.items.delete(Number(it)); }
          } else {
            for (const [k, el] of Array.from(rec.items)) {
              if ((el.dataset.tags || "").split(" ").includes(String(it))) {
                el.remove(); rec.items.delete(k);
              }
            }
          }
        }
        return 0;
      }
      if (op === "move") {
        const el = rec.items.get(p.item);
        if (el) {
          const dx = (+el.dataset.dx || 0) + Number(p.dx || 0);
          const dy = (+el.dataset.dy || 0) + Number(p.dy || 0);
          el.dataset.dx = String(dx); el.dataset.dy = String(dy);
          el.setAttribute("transform", "translate(" + dx + " " + dy + ")");
        }
        return 0;
      }
      if (op === "coords") {
        const el = rec.items.get(p.item);
        if (el) {
          const c = (p.coords || []).map(Number);
          if (el.tagName === "line") {
            el.setAttribute("x1", c[0]); el.setAttribute("y1", c[1]);
            el.setAttribute("x2", c[2]); el.setAttribute("y2", c[3]);
          } else if (el.tagName === "polygon") {
            el.setAttribute("points", c.join(","));
          } else {
            el.setAttribute("x", c[0]); el.setAttribute("y", c[1]);
          }
        }
        return 0;
      }
      if (op === "bind_click") { svg.style.cursor = "pointer"; return 0; }
      return 0;
    },
    dialog(kind, title, msg) {
      appCard.classList.remove("hidden");
      const ov = div("tkdialog");
      const box = div("tkdialog-box");
      const icon = div("tkdialog-icon " + kind);
      icon.textContent = kind === "error" ? "✖" : kind === "warning" ? "⚠" : "ℹ";
      const text = div("tkdialog-text");
      text.textContent = (title ? title + "\n" : "") + msg;
      const btn = document.createElement("button");
      btn.textContent = "OK";
      btn.addEventListener("click", () => ov.remove());
      box.appendChild(icon);
      box.appendChild(text);
      box.appendChild(btn);
      ov.appendChild(box);
      appCard.appendChild(ov);
    },
  };
}
window.__cleanroomGui = guiApiImpl();
// small debug hook for verification scripts: call the mounted WSGI app directly
window.__crLoadPkg = (n) => pyodide.loadPackage(n);
window.__crAsgiTest = async (payload) => {
  if (!pyodide || appTransport !== "asgi") throw new Error("no ASGI app mounted");
  return JSON.parse(await pyodide.runPythonAsync(
    "__cr_asgi_entry(" + JSON.stringify(JSON.stringify(payload)) + ")"));
};
window.__crWsgiTest = async (payload) => {
  if (!pyodide) throw new Error("no VM");
  return JSON.parse(await pyodide.runPythonAsync(
    "__cr_wsgi_entry(" + JSON.stringify(JSON.stringify(payload)) + ")"));
};

/* static web preview: inline every repo-local script/stylesheet into the
   entry HTML, then load it in an origin-less sandboxed iframe. No fetches
   happen inside the frame — it has no origin to fetch from. */
function vmPreview() {
  const html = vmFiles.find(f => f[0] === "index.html");
  if (!html) { line("no index.html at the repo root — nothing to preview", "t-dim"); return; }
  const byPath = new Map(vmFiles);
  const seen = new Set();
  const inlined = [];
  const local = path => {
    if (!path || /^(https?:)?\/\//i.test(path) || path.startsWith("data:")) return null;
    const key = path.replace(/^\.\//, "");
    if (!byPath.has(key) || seen.has(key)) return null;
    seen.add(key);
    inlined.push(key);
    return byPath.get(key);
  };
  let doc = html[1];
  doc = doc.replace(/<script\b[^>]*\bsrc=["']([^"']+)["'][^>]*>\s*<\/script>/gi, (m, src) => {
    const t = local(src);
    if (t === null) return m;
    return "<script>\n" + t.replace(/<\/script/gi, "<\\/script") + "\n</script>";
  });
  doc = doc.replace(/<link\b[^>]*rel=["']?stylesheet["']?[^>]*>/gi, m => {
    const href = /href=["']([^"']+)["']/i.exec(m);
    if (!href) return m;
    const t = local(href[1]);
    if (t === null) return m;
    return "<style>\n" + t + "\n</style>";
  });
  if (doc.length > 4 * 1024 * 1024) {
    line("preview: the entry page and its assets are over 4 MB — too big to inline here", "t-err");
    return;
  }
  webFrame.removeAttribute("src");
  webFrame.srcdoc = doc;
  webWrap.classList.remove("hidden");
  appCard.classList.remove("hidden");
  appTitle.textContent = "app screen — preview (sandboxed frame, no origin, no storage)";
  if (inlined.length) line("preview: inlined " + inlined.join(", "), "t-dim");
  line("the preview frame runs scripts but has no origin, no storage and no network — nothing escapes it", "t-dim");
}

/* ---------- this-PC mode: run the repo as a real web app ---------- */

let webPolling = false;

function showPcWebRow() {
  pcWebRow.dataset.shown = "1";
  pcWebRow.classList.remove("hidden");
}

function renderPcWebChips(hints) {
  pcWebChips.innerHTML = "";
  for (const h of hints) {
    const b = document.createElement("button");
    b.className = "chip env";
    b.textContent = h.label;
    b.title = h.cmd;
    b.addEventListener("click", () => {
      pcWebCmd.value = h.cmd;
      pcWebNet.checked = !!h.net;
    });
    pcWebChips.appendChild(b);
  }
}

function addPortChip(port) {
  if (pcWebChips.querySelector('[data-port="' + port + '"]')) return;
  const b = document.createElement("button");
  b.className = "chip env";
  b.dataset.port = String(port);
  b.textContent = "open 127.0.0.1:" + port;
  b.addEventListener("click", () => {
    webFrame.removeAttribute("srcdoc");
    if (multiCoreOn()) {
      // an isolated tab may only embed responses that carry CORP/COEP
      // headers — the bridge streams the app and adds them
      webFrame.src = bridge.base + "/api/app/" + port + "/?token=" +
                     encodeURIComponent(bridge.token);
      webUrl.textContent = "the app at http://127.0.0.1:" + port +
                           ", streamed through the bridge proxy (isolated tab)";
    } else {
      webFrame.src = "http://127.0.0.1:" + port;
      webUrl.textContent = "http://127.0.0.1:" + port + " — the app's own UI, served by your PC";
    }
  });
  pcWebChips.appendChild(b);
}

async function pcWebStartFn() {
  if (mode !== "pc") return;
  const cmd = pcWebCmd.value.trim();
  if (!cmd) { line("enter a start command first (a suggestion above fills it in)", "t-dim"); return; }
  const net = pcWebNet.checked ? "allowed" : "blocked";
  line("── starting the app on this PC (inside the sandbox): " + cmd, "t-meta");
  const { data } = await bridgeCall("/api/web", { cmd, net });
  if (!data.ok) {
    line("bridge: " + data.error + (data.needs_approval ? " — type 'approve' first" : ""), "t-err");
    return;
  }
  if (data.reopened) line("sandbox re-opened with network " + net + " — installs and downloads need it", "t-dim");
  line("the app runs as a real process in the sandbox; its output streams below.", "t-dim");
  line("when it prints a port, click the chip — the app's own UI opens in the app screen.", "t-dim");
  line("honest note: cleanroom's network guard covers Python children only, and a server must hold its port open to serve you — that part is the app doing its job. Check that your firewall blocks inbound connections.", "t-dim");
  webWrap.classList.remove("hidden");
  appCard.classList.remove("hidden");
  appTitle.textContent = "app screen — this PC app";
  webUrl.textContent = "waiting for the server to print a port…";
  webFrame.removeAttribute("srcdoc");
  webFrame.src = "about:blank";
  pcWebStart.classList.add("hidden");
  pcWebStop.classList.remove("hidden");
  webPolling = true;
  pollWebOutput(0);
}

async function pollWebOutput(cursor) {
  while (webPolling) {
    let out;
    try { out = await bridgeCall("/api/output?since=" + cursor); }
    catch (e) { line("bridge error: " + e.message, "t-err"); break; }
    const data = out.data;
    if (!data.ok) { line("bridge: " + (data.error || "lost session"), "t-err"); break; }
    for (const ln of data.lines) {
      line(ln, "t-out");
      for (const p of extractPorts([ln])) addPortChip(p);
    }
    cursor = data.cursor;
    if (data.done) {
      webPolling = false;
      line("app process exited (code " + data.exit_code + ")" +
           (data.kill_reason ? " — " + data.kill_reason : ""), "t-dim");
      pcWebStop.classList.add("hidden");
      pcWebStart.classList.remove("hidden");
      break;
    }
    await new Promise(r => setTimeout(r, 300));
  }
}

async function pcWebStopFn() {
  webPolling = false;
  const { data } = await bridgeCall("/api/web_stop", {});
  line(data.stopped
    ? "stopped — the process tree was killed. the sandbox still exists; run 'wipe' to erase everything."
    : "nothing is running", "t-meta");
  pcWebStop.classList.add("hidden");
  pcWebStart.classList.remove("hidden");
}

/* ---------- repo loading ---------- */

function listRepoFiles() {
  const out = [];
  (function walk(path) {
    for (const name of pyodide.FS.readdir(path)) {
      if (name === "." || name === "..") continue;
      const full = path + "/" + name;
      if (pyodide.FS.isDir(full)) walk(full);
      else out.push(full);
    }
  })("/repo");
  return out.sort();
}

// written into the VM's filesystem — includes web assets so WSGI apps can
// serve their own templates and static folders through the virtual network
const RUNNABLE = /\.(py|json|jsonl|txt|csv|toml|cfg|ini|md|html|css|js|mjs|svg|yml|yaml|xml)$/i;
const ENTRIES = ["main.py", "app.py", "demo.py", "run.py", "cli.py", "__main__.py"];

/* ---------- setup & run: the README/manifest understanding ---------- */

const PIP_RE = /^(flask|django|fastapi|jinja2|werkzeug|click|itsdangerous|markupsafe|rich|pydantic|httpx?|pillow|numpy|pandas|matplotlib|requests|beautifulsoup4|lxml|pyyaml|python-dotenv|attrs|certifi|chardet|charset-normalizer|idna|packaging|six|typing-extensions|sniffio|anyio|starlette|uvicorn|multidict|yarl|aiosignal|frozenlist|uvloop|watchfiles|websockets|python-multipart|pyjwt|cryptography|sqlparse|django-debug-toolbar)$/i;

function micropipProxy() { return pyodide.pyimport("micropip"); }

// micropip refuses requirements whose wheel is compiled (pydantic-core) —
// the Pyodide catalog carries compiled builds of these under another name
const CATALOG_SUBSTITUTES = {
  "pydantic-core": "pydantic",
  "pydantic-core-binary": "pydantic",
};

async function runSetup(setup) {
  line("── setting up (" + setup.installs.length + " requirement" +
       (setup.installs.length === 1 ? "" : "s") + ") — installing into the VM ──", "t-meta");
  let okCount = 0, failCount = 0;
  const failed = [];
  for (const name of setup.installs) {
    let how = null;
    try {
      await pyodide.loadPackage(name);
      how = "the VM's catalog";
    } catch (e1) { /* not in the catalog — try PyPI wheels */ }
    if (!how && name.toLowerCase() === "fastapi") {
      // fastapi needs pydantic, whose core is compiled — but the catalog
      // carries a WASM build of pydantic 2.7.0, and fastapi 0.111.0 is the
      // release that pairs with it. the pure deps install from PyPI, the
      // pinned fastapi skips its own resolution (it would demand a newer
      // pydantic whose core cannot exist here), and the catalog build of
      // pydantic lands last.
      try {
        await pyodide.loadPackage("micropip");
        await pyodide.runPythonAsync(
          "import micropip; await micropip.install("
          + "['starlette==0.41.3','anyio','typing-extensions==4.16.0','sniffio','idna','annotated-types'])");
        await pyodide.runPythonAsync(
          "import micropip; await micropip.install('fastapi==0.111.0', deps=False)");
        await pyodide.loadPackage("pydantic");
        await pyodide.runPythonAsync("import fastapi");
        how = "PyPI wheels + the VM's catalog build of pydantic";
      } catch (eF) {
        const parts = String(eF.message || eF).split("\n").filter(Boolean);
        line("      (fastapi: " + parts.slice(-3).join(" | ") + ")", "t-dim");
      }
    }
    if (!how) {
      try {
        await pyodide.loadPackage("micropip");
        await pyodide.runPythonAsync(
          "import micropip; await micropip.install(" + JSON.stringify(name) + ")");
        how = "pure-Python wheels from PyPI (via your browser's own fetch)";
      } catch (e2) {
        const msg = String(e2.message || e2);
        // micropip refuses when a requirement has no pure wheel but the
        // Pyodide catalog carries a compiled build of it (pydantic-core →
        // pydantic): load the catalog build, then retry once
        const blocked = /Can't find a pure Python 3 wheel for '([A-Za-z0-9_.-]+?)==/.exec(msg);
        const alt = blocked && CATALOG_SUBSTITUTES[blocked[1].toLowerCase()];
        if (alt) {
          try {
            await pyodide.loadPackage(alt);
            await pyodide.runPythonAsync(
              "import micropip; await micropip.install(" + JSON.stringify(name) + ")");
            how = "PyPI wheels + the VM's catalog build of " + alt;
          } catch (e3) { /* fall through to the honest refusal */ }
        }
        if (!how) {
          const parts = msg.split("\n").filter(Boolean);
          line("      (" + name + ": " + parts.slice(-3).join(" | ") + ")", "t-dim");
        }
      }
    }
    if (how) {
      line("  ✓ " + name + " — installed from " + how, "t-out");
      okCount++;
    } else {
      line("  ✗ " + name + " — unavailable: it needs compiled binaries, and a "
           + "browser tab has no compiler. The VM can only load catalog packages "
           + "and pure-Python wheels. This one can't run in-tab (this-PC mode can).", "t-err");
      failed.push(name); failCount++;
    }
  }
  line("setup: " + okCount + " installed" + (failCount ? " · " + failCount +
       " unavailable" + (failCount ? " — " + failed.join(", ") : "") : ""), "t-meta");
  return failCount === 0;
}

/* ---------- WSGI-in-tab: a server app whose only network is this page ------
   The VM has no sockets — by design. So the WSGI app never listens: the app
   screen's iframe asks, the VM answers. Requests travel browser → postMessage
   → the WSGI callable → browser. Nothing listens on the machine, nothing
   escapes the tab. Wipe ends it. */

const WSGI_PY_BOOT = (mod) => `
import importlib as _imp
_m = _imp.import_module(${JSON.stringify(mod)})
_a = getattr(_m, "app", None) or getattr(_m, "application", None)
if _a is None or not hasattr(_a, "wsgi_app"):
    raise RuntimeError("no WSGI app object (Flask/Django 'app') found in module " + ${JSON.stringify(mod)})
__cr_wsgi_app = _a.wsgi_app

def __cr_wsgi_entry(payload):
    import io, json as _json, base64 as _b64, sys as _sys
    d = _json.loads(payload)
    body = _b64.b64decode(d.get("bodyB64", ""))
    environ = {
        "REQUEST_METHOD": d["method"], "PATH_INFO": d["path"],
        "QUERY_STRING": d.get("query", ""), "SERVER_NAME": "cleanroom.local",
        "SERVER_PORT": "80", "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(body), "wsgi.errors": _sys.stderr,
        "wsgi.multithread": False, "wsgi.multiprocess": False,
        "wsgi.run_once": False, "CONTENT_LENGTH": str(len(body)),
    }
    for k, v in (d.get("headers") or {}).items():
        ku = k.upper().replace("-", "_")
        # WSGI passes Content-Type/Content-Length unprefixed, everything else
        # as HTTP_*
        if ku in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            environ[ku] = str(v)
        else:
            environ["HTTP_" + ku] = str(v)
    state = {}
    def _sr(status, hdrs, exc_info=None):
        state["status"] = status
        state["headers"] = [[str(a), str(b)] for a, b in hdrs]
    chunks = __cr_wsgi_app(environ, _sr)
    data = b"".join(bytes(c) for c in chunks)
    if hasattr(chunks, "close"):
        chunks.close()
    code = int(state["status"].split(" ", 1)[0])
    return _json.dumps({"code": code, "headers": state["headers"],
                        "bodyB64": _b64.b64encode(data).decode()})
`;

/* ASGI needs no server either: the app is an async callable and this tab's
   VM already runs an event loop. Same virtual network as the WSGI path. */
const ASGI_PY_BOOT = (mod) => `
import anyio.to_thread, anyio.from_thread
import importlib as _imp
_m = _imp.import_module(${JSON.stringify(mod)})
_a = getattr(_m, "app", None) or getattr(_m, "application", None)
if _a is None or hasattr(_a, "wsgi_app") and not hasattr(_a, "client"):
    raise RuntimeError("no ASGI app object found in module " + ${JSON.stringify(mod)})
__cr_asgi_app = _a

async def __cr_asgi_entry(payload):
    import json as _json, base64 as _b64
    d = _json.loads(payload)
    body = _b64.b64decode(d.get("bodyB64", ""))
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": d["method"], "scheme": "http",
        "path": d["path"], "raw_path": d["path"].encode(),
        "query_string": d.get("query", "").encode(), "root_path": "",
        "headers": [[k.lower().encode(), str(v).encode()]
                    for k, v in (d.get("headers") or {}).items()],
        "client": ("127.0.0.1", 0), "server": ("cleanroom.local", 80),
    }
    state = {"body": b"", "status": 500, "headers": []}
    async def _receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def _send(msg):
        if msg["type"] == "http.response.start":
            state["status"] = msg["status"]
            # ASGI headers are byte pairs — decode them, str() would embed b''
            state["headers"] = [[a.decode() if isinstance(a, bytes) else str(a),
                                 b.decode() if isinstance(b, bytes) else str(b)]
                                for a, b in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            state["body"] += msg.get("body", b"")
    await __cr_asgi_app(scope, _receive, _send)
    return _json.dumps({"code": state["status"], "headers": state["headers"],
                        "bodyB64": _b64.b64encode(state["body"]).decode()})
`;

let appTransport = null;   // "wsgi" | "asgi" — which virtual network is mounted

const WSGI_SHIM_BODY = `
var __cr = (window.__cr = window.__cr || { pending: {} });
function __crCall(payload) {
  return new Promise(function (res) {
    var id = "r" + Math.random().toString(36).slice(2);
    __cr.pending[id] = res;
    parent.postMessage({ crWsgi: "request", id: id, payload: payload }, "*");
  });
}
window.addEventListener("message", function (ev) {
  var d = ev.data || {};
  if (d.crWsgi === "response" && __cr.pending[d.id]) {
    var f = __cr.pending[d.id];
    delete __cr.pending[d.id];
    f(d.resp);
  }
});
function __crBody(r) {
  return Uint8Array.from(atob(r.bodyB64), function (c) { return c.charCodeAt(0); });
}
function __crLoc(r) {
  if (r.code >= 300 && r.code < 400 && r.headers) {
    for (var i = 0; i < r.headers.length; i++) {
      if (String(r.headers[i][0]).toLowerCase() === "location") return r.headers[i][1];
    }
  }
  return null;
}
function __crWrite(r) {
  var html = new TextDecoder().decode(__crBody(r));
  var shim = "<scr" + "ipt>var __crSrc=" + JSON.stringify(__crSrc) + ";" + __crSrc + ";</scr" + "ipt>";
  document.open();
  if (/<head[^>]*>/i.test(html)) document.write(html.replace(/<head[^>]*>/i, "<head>" + shim));
  else document.write(shim + html);
  document.close();
}
function __crNav(path) {
  return __crCall({ method: "GET", path: path, query: "", body: "", headers: {} }).then(function (r) {
    var loc = __crLoc(r);
    if (loc) return __crNav(loc);
    __crWrite(r);
  });
}
function __crFollow(r) {
  var loc = __crLoc(r);
  if (loc) return __crNav(loc);
  __crWrite(r);
}
var __of = window.fetch;
window.fetch = function (input, init) {
  var url = typeof input === "string" ? input : (input && input.url) || "";
  if (/^(https?:)?\\/\\//i.test(url) || String(url).indexOf("data:") === 0) {
    return __of ? __of.apply(window, arguments) : Promise.reject(new Error("no network"));
  }
  init = init || {};
  var headers = {};
  if (init.headers && init.headers.forEach) init.headers.forEach(function (v, k) { headers[k] = v; });
  else if (init.headers) for (var k in init.headers) headers[k] = init.headers[k];
  return __crCall({ method: (init.method || "GET").toUpperCase(), path: url, query: "",
                    body: init.body == null ? "" : String(init.body), headers: headers })
    .then(function (r) { return new Response(__crBody(r), { status: r.code, headers: r.headers || [] }); });
};
document.addEventListener("submit", function (ev) {
  var f = ev.target;
  var action = f.getAttribute("action") || "/";
  var m = (f.method || "get").toLowerCase();
  ev.preventDefault();
  if (m === "post") {
    var body = new URLSearchParams(new FormData(f)).toString();
    __crCall({ method: "POST", path: action, query: "", body: body,
               headers: { "Content-Type": "application/x-www-form-urlencoded" } })
      .then(__crFollow);
  } else {
    __crNav(action + "?" + new URLSearchParams(new FormData(f)).toString());
  }
}, true);
document.addEventListener("click", function (ev) {
  var a = ev.target.closest && ev.target.closest("a[href]");
  var href = a && a.getAttribute("href");
  if (a && href && !/^(https?:|data:|#|mailto:)/i.test(href)) {
    ev.preventDefault();
    __crNav(href);
  }
}, true);
function crBoot(startPath) {
  __crNav(startPath || "/");
}
`;

function wsgiShellHtml() {
  return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>app — virtual network</title>'
    + '<style>body{font:14px system-ui,sans-serif;background:#fff;margin:0;padding:20px;color:#333}'
    + '.hint{color:#999;font-size:12px;margin:0 0 10px}</style></head><body>'
    + '<p class="hint">this app runs in the VM of this tab; this page is its only network — nothing listens on any machine</p>'
    + '<div id="app">loading the app from the VM…</div>'
    + '<script>var __crSrc=' + JSON.stringify(WSGI_SHIM_BODY) + ';'
    + WSGI_SHIM_BODY + ';crBoot("/");<\/script></body></html>';
}

window.addEventListener("message", async ev => {
  const d = ev.data || {};
  if (d.crWsgi !== "request") return;
  if (!pyodide || mode !== "vm" || !appTransport) return;
  try {
    const payload = {
      method: d.payload.method,
      path: d.payload.path,
      query: d.payload.query || "",
      bodyB64: btoa(unescape(encodeURIComponent(d.payload.body || ""))),
      headers: d.payload.headers || {},
    };
    const fn = appTransport === "asgi" ? "__cr_asgi_entry" : "__cr_wsgi_entry";
    const resp = await pyodide.runPythonAsync(
      fn + "(" + JSON.stringify(JSON.stringify(payload)) + ")");
    ev.source.postMessage({ crWsgi: "response", id: d.id, resp: JSON.parse(resp) }, "*");
  } catch (e) {
    line("virtual network error: " + (e.message || e), "t-err");
    try {
      ev.source.postMessage({ crWsgi: "response", id: d.id,
        resp: { code: 500, headers: [["Content-Type", "text/plain"]],
                bodyB64: btoa("the VM returned an error: " + (e.message || e)) } }, "*");
    } catch (e2) { /* frame gone */ }
  }
});

async function vmServeWSGI() {
  const mod = ["app", "main", "wsgi", "server", "run"].find(
    m => vmFiles.some(f => f[0] === m + ".py"));
  if (!mod) {
    line("no obvious WSGI module (app.py / main.py / wsgi.py) to mount", "t-err");
    return;
  }
  line("── mounting the WSGI app inside the VM — its only network is this page ──", "t-meta");
  await pyodide.loadPackage("ssl");
  await pyodide.loadPackage("anyio");
  try {
    await pyodide.runPythonAsync(WSGI_PY_BOOT(mod));
  } catch (e) {
    line("could not mount the WSGI app: " + (e.message || e).split("\n")[0], "t-err");
    return;
  }
  appTransport = "wsgi";
  webFrame.removeAttribute("src");
  webFrame.srcdoc = wsgiShellHtml();
  webWrap.classList.remove("hidden");
  appCard.classList.remove("hidden");
  appTitle.textContent = "app screen — " + mod + " (WSGI in the VM · virtual network)";
  line("the app is served by the page: browser → VM → browser. no sockets, "
       + "nothing listens on your machine, nothing escapes the tab. wipe ends it.", "t-dim");
}

async function vmServeASGI() {
  const mod = ["app", "main", "main_asgi", "server", "run"].find(
    m => vmFiles.some(f => f[0] === m + ".py"));
  if (!mod) {
    line("no obvious ASGI module (app.py / main.py) to mount", "t-err");
    return;
  }
  line("── mounting the ASGI app inside the VM — its only network is this page ──", "t-meta");
  await pyodide.loadPackage("ssl");
  try {
    await pyodide.runPythonAsync(ASGI_PY_BOOT(mod));
  } catch (e) {
    const parts = String(e.message || e).split("\n").filter(Boolean);
    line("could not mount the ASGI app: " + parts.slice(-3).join(" | "), "t-err");
    return;
  }
  appTransport = "asgi";
  webFrame.removeAttribute("src");
  webFrame.srcdoc = wsgiShellHtml();
  webWrap.classList.remove("hidden");
  appCard.classList.remove("hidden");
  appTitle.textContent = "app screen — " + mod + " (ASGI in the VM · virtual network)";
  line("the FastAPI app is served by the page: browser → VM → browser. no uvicorn, "
       + "no sockets, nothing listens on your machine, nothing escapes the tab.", "t-dim");
}

function suggestCommands(files) {
  const paths = files.map(f => f[0]);
  const hints = [];
  const entry = ENTRIES.find(e => paths.includes(e));
  if (entry) hints.push("run " + entry);
  if (paths.includes("spamcheck.py") && paths.includes("model.json")) {
    hints.push('import spamcheck as s; m = s.load_model(); s.classify(m, "URGENT! You have won a FREE prize, claim now!")');
    hints.push('import spamcheck as s; m = s.load_model(); s.classify(m, "hey are we still meeting at the library at 5?")');
  }
  if (paths.includes("attention.py")) {
    hints.push("import attention; attention.demo()");
  }
  if (paths.includes("train_bpe.py") && paths.includes("bpe.py")) {
    hints.push("import bpe; print(bpe.demo())");
  }
  if (paths.some(p => p.endsWith(".py")) && !hints.length) {
    hints.push("import " + paths.find(p => p.endsWith(".py")).replace(/\.py$/, "").replace("/", "."));
  }
  return hints;
}

async function loadRepo(raw) {
  loadBtn.disabled = true;
  termInput.disabled = true;
  liveCard.classList.remove("hidden");
  try {
    if (mode === "pc") await pcLoadRepo(raw);
    else await vmLoadRepo(raw);
  } catch (e) {
    setLiveStatus(e.message, true);
    line("failed: " + e.message, "t-err");
  } finally {
    loadBtn.disabled = false;
    termInput.disabled = false;
  }
}

async function vmLoadRepo(raw) {
  const started = performance.now();
  line("cleanroom live runner v" + RUNNER_VERSION + " — a disposable Python VM inside this tab", "t-meta");
  line("nothing to install · nothing to pay · nothing left behind", "t-dim");
  const { files, label } = await fetchRepoFiles(raw,
    (d, n) => setLiveStatus("fetching repo files " + d + "/" + n + "…"),
    { maxFileBytes: 2 * 1024 * 1024 });
  const vm = await ensureVM(setLiveStatus);
  const runnable = files.filter(([p]) => RUNNABLE.test(p) && !p.includes("/"));
  const runnableDeep = files.filter(([p]) => RUNNABLE.test(p) && p.includes("/"));
  for (const f of runnable.concat(runnableDeep)) vmWriteFile(f[0], f[1]);
  await vm.runPythonAsync("import sys; sys.modules.pop('repo', None)");

  vmFiles = files;
  line("── loaded " + label + " ──", "t-meta");
  line(runnable.length + runnableDeep.length + " files → /repo (virtual, in-memory only)", "t-dim");
  line("on your device: " + deviceInfo() +
       (multiCoreOn() ? " · multi-core enabled (numpy's threads run on your cores)"
                      : " · single-thread (the browser did not isolate this tab)"), "t-dim");
  const sig = detectGuiSignals(files);
  const rep = scanTree(files);
  line("triage: " + rep.score + "/100 " + rep.verdict.toUpperCase() +
       " · " + rep.findings.length + " findings — the report card below has the details",
       rep.score >= 25 ? "t-err" : "t-dim");
  renderTriCard(rep, label, files.length);
  const cards = [];
  if (sig.tkinter) {
    line("GUI app detected (tkinter) — the virtual screen is ready", "t-meta");
    cards.push({ label: "run " + sig.entry, cmd: "run " + sig.entry, kind: "run" });
  }
  if (sig.webapp) {
    line("static web app detected — it opens sandboxed in the app screen", "t-meta");
    cards.push({ label: "preview the web app", cmd: "preview", kind: "run" });
  }
  if (sig.rails) {
    line("Ruby on Rails app — the in-tab VM is Python-only. In 'this PC' mode (with Ruby installed), 'Start app' runs it on your machine and its real UI opens in the app screen.", "t-dim");
  }
  renderRunCards(cards);
  renderFilePills(files.map(f => f[0]));
  const setup = parseSetup(new Map(files));
  if (setup.installs.length) {
    line("setup understood from the manifests: pip " + setup.installs.join(", "), "t-dim");
    cards.push({ label: "set up & run — install " + setup.installs.length +
                       " package" + (setup.installs.length === 1 ? "" : "s") + " in the VM",
                 cmd: "setup", kind: "run" });
  }
  if (setup.appKind === "wsgi") {
    cards.push({ label: "serve the web app in-tab (virtual network)", cmd: "serve", kind: "run" });
    line("server app detected (Flask/Django) — in the tab it runs behind a virtual "
         + "network: the page asks, the VM answers, nothing listens anywhere.", "t-dim");
  } else if (setup.appKind === "asgi") {
    cards.push({ label: "serve the FastAPI app in-tab (virtual network)", cmd: "serve", kind: "run" });
    line("FastAPI app detected — ASGI needs no server: the VM's own event loop drives "
         + "the app, and this page is its network. no uvicorn, no sockets.", "t-dim");
  }
  // beyond the tab: name the reasons — the free hand-off is the operator's PC
  const hasRunnablePy = files.some(([p]) => /\.py$/i.test(p));
  const fm = fullMachineReasons(setup, sig, hasRunnablePy);
  if (fm.length) {
    for (const r of fm) line("beyond the tab: " + r, "t-dim");
    line("the free way to run THIS repo: switch to 'this PC' mode (python cleanroom.py serve) — "
         + "cleanroom understands the repo there, installs its requirements in the sandbox and "
         + "serves the app. your hardware, your bandwidth, nothing to pay.", "t-dim");
  }
  renderRunCards(cards);
  if (!files.some(([p]) => /\.py$/i.test(p))) {
    line("this repo has no Python files — the in-tab VM runs Python only, so there is nothing to execute here. the triage report above still applies.", "t-dim");
  }
  line("the VM is yours. type a command, or 'help'.", "t-dim");
  const hints = suggestCommands(files);
  if (hints.length && !sig.tkinter) {
    line("try: (click to fill)", "t-hint");
    hints.slice(0, 3).forEach(h => hintChip(h));
  }
  setLiveStatus("running — " + label + " is live in your browser (" +
    Math.round(performance.now() - started) / 1000 + "s to boot, VM cached for next time)");
  termInput.focus();
}

const PC_HELP = [
  "commands on this PC (real processes):",
  "  run <file.py>    execute a file from the repo on this machine",
  "  ls               list the sandbox clone",
  "  pip <package>    installs go into the sandbox's disposable venv — PyPI downloads need network, which is blocked by design",
  "  serve <cmd>      run the repo as a real app (server) — its UI opens in the app screen; 'stop' ends it",
  "  save <name>      FREEZE this stage: files + package list → ~/.cleanroom/saves/<name>",
  "  stages           list saved stage freezes",
  "  resume <name>    wipe this sandbox, re-open a saved stage (files restored)",
  "  forget <name>    delete a saved stage",
  "  approve          accept a DANGEROUS triage verdict (your responsibility)",
  "  wipe             delete the sandbox and verify zero residue",
  "  anything else    runs as Python in the repo directory (expressions are echoed)",
  "the sandbox: temp-dir clone + throwaway venv · whitelist environment (your secrets never reach it)",
  "             · no outbound network for python · 120s timeout · verified wipe",
  "             · freeze saves files+packages; a live process's memory can't be frozen",
];

async function pcLoadRepo(raw) {
  line("this-PC mode — asking the local bridge to fetch and triage " + raw, "t-meta");
  setLiveStatus("cloning and triaging on your PC…");
  const { status, data } = await bridgeCall("/api/open", { url: raw });
  if (!data.ok) {
    line("bridge said: " + (data.error || "refused"), "t-err");
    setLiveStatus(data.error || "bridge refused", true);
    return;
  }
  const rep = data.report;
  line("── triaged on your PC: " + raw + " @ " + (data.commit || "?") + " ──", "t-meta");
  line("score " + rep.score + "/100 " + rep.verdict.toUpperCase() +
       " · " + rep.findings.length + " findings · " + rep.files_scanned + " files scanned",
       rep.score >= 25 ? "t-err" : "t-dim");
  const scored = rep.findings.filter(f => f.severity !== "info");
  scored.slice(0, 8).forEach(f =>
    line("  " + f.severity.toUpperCase().padEnd(8) + " " + f.rule.padEnd(16) + f.file + ":" + f.line, "t-dim"));
  if (scored.length > 8) line("  … " + (scored.length - 8) + " more scored findings", "t-dim");
  if (data.needs_approval) {
    line("this triaged DANGEROUS — type 'approve' if you still want to run it (at your own responsibility)", "t-err");
  } else {
    line("sandbox ready on your PC: scrubbed secrets, disposable venv (pip never touches your real Python), no outbound network (python), wiped on exit", "t-dim");
  }
  const hints = [];
  const sig = detectGuiSignals((data.paths || []).map(p => [p, ""]));
  // npm hints need file contents — the bridge computes them (paths alone can't)
  const hintsAll = (data.hints && data.hints.length) ? data.hints : sig.hints;
  renderTriCard(rep, raw + " @ " + (data.commit || "?"), (data.paths || []).length);
  const cards = [];
  if (data.tkinter_entry) {
    line("GUI app detected (tkinter) — on this PC it opens a real window on your desktop:", "t-meta");
    cards.push({ label: "run " + data.tkinter_entry, cmd: "run " + data.tkinter_entry, kind: "run" });
  }
  if (sig.rails) {
    line("Ruby on Rails app — 'Start app' below runs it here if Ruby is installed on this machine.", "t-dim");
  }
  for (const h of hintsAll) {
    cards.push({ label: "serve: " + h.label, cmd: "serve " + h.cmd, kind: "serve" });
  }
  if (data.needs_approval) {
    cards.push({ label: "approve — run it anyway (my responsibility)", cmd: "approve", kind: "approve" });
  }
  renderRunCards(cards);
  renderFilePills(data.paths || []);
  // the free, automatic path: the bridge understood the repo — one card runs it
  const su = data.setup;
  if (su && su.chain) {
    line("understood from the repo's own files: " +
         (su.installs.length ? su.installs.length + " requirement(s) · " : "") +
         "run: " + su.run_cmd, "t-dim");
    cards.push({
      label: "understand, install & run on this PC — free",
      cmd: su.chain,
      kind: "autorun",
      net: !!su.needs_net,
      title: su.chain,
    });
    renderRunCards(cards);
  }
  if (hintsAll.length) {
    line("run it as a real app (its UI opens in the app screen):", "t-meta");
    renderPcWebChips(hintsAll);
    hints.push(...hintsAll);
  }
  if (data.tkinter_entry || hintsAll.length) showPcWebRow();
  if (!data.needs_approval) line("type python, 'run <file>', 'serve <cmd>', or 'help'", "t-dim");
  setLiveStatus("this-PC session live — real processes on your machine, wiped clean on exit");
  termInput.focus();
}

async function pcStream(payload) {
  busy = true;
  try {
    const { status, data } = await bridgeCall("/api/run", payload);
    if (!data.ok) {
      line(data.error + (data.needs_approval ? " — type 'approve' to accept the triage" : ""), "t-err");
      return;
    }
    let cursor = 0;
    for (;;) {
      const { data: out } = await bridgeCall("/api/output?since=" + cursor);
      if (!out.ok) { line("bridge: " + (out.error || "lost session"), "t-err"); return; }
      for (const ln of out.lines) line(ln, "t-out");
      cursor = out.cursor;
      if (out.done) {
        if (out.exit_code !== null && out.exit_code !== 0) {
          line("→ exit code " + out.exit_code + (out.kill_reason ? " — " + out.kill_reason : ""), "t-err");
        } else if (out.exit_code === 0) {
          line("→ exit 0", "t-dim");
        }
        break;
      }
      await new Promise(r => setTimeout(r, 250));
    }
  } catch (e) {
    line("bridge error: " + e.message, "t-err");
  } finally {
    busy = false;
  }
}

async function pcExec(text) {
  const c = text.trim();
  if (!c) return;
  if (c === "clear") { term.innerHTML = ""; return; }
  if (c === "help" || c === "?") { PC_HELP.forEach(l => line(l, "t-dim")); return; }
  if (c === "approve") {
    const { data } = await bridgeCall("/api/approve", {});
    line(data.ok ? "approved — the sandbox will run it. your responsibility." : "nothing to approve",
         data.ok ? "t-meta" : "t-dim");
    return;
  }
  if (c === "stop") { await pcWebStopFn(); return; }
  if (c.startsWith("serve ")) {
    pcWebCmd.value = c.slice(6).trim();
    await pcWebStartFn();
    return;
  }
  if (c === "wipe") {
    webPolling = false;
    const { data } = await bridgeCall("/api/wipe", {});
    line("wiped on your PC — deleted: " + data.deleted + " · residue: " + data.residue +
         " · commands run: " + (data.commands || 0) + " · nothing was written outside the sandbox", "t-meta");
    appCard.classList.add("hidden");
    webWrap.classList.add("hidden");
    webFrame.removeAttribute("srcdoc");
    webFrame.src = "about:blank";
    pcWebChips.innerHTML = "";
    pcWebStop.classList.add("hidden");
    pcWebStart.classList.remove("hidden");
    resetRunUi();
    renderStageChips();
    return;
  }
  if (c.startsWith("save ") || c === "save") {
    const name = c.slice(5).trim();
    const { data } = await bridgeCall("/api/save", { name });
    if (data.ok) {
      line("stage '" + name + "' frozen → " + data.path +
           "  (" + data.files + " files, " + data.packages + " packages, " + data.seconds + "s)", "t-meta");
      line("resume it any time with 'resume " + name + "' — or start fresh with the same repo", "t-dim");
      renderStageChips();
    } else {
      line("save failed: " + data.error, "t-err");
    }
    return;
  }
  if (c === "stages") {
    const { data } = await bridgeCall("/api/saves");
    if (!data.stages.length) { line("no saved stages", "t-dim"); return; }
    data.stages.forEach(s => line("  " + s.name.padEnd(20) + " saved " + s.saved_at +
                                  "  pkgs=" + s.packages + "  " + String(s.source).slice(0, 50), "t-dim"));
    return;
  }
  if (c.startsWith("resume ") || c === "resume") {
    const name = c.slice(7).trim();
    const { status, data } = await bridgeCall("/api/resume", { name });
    if (!data.ok) { line("resume failed: " + data.error, "t-err"); return; }
    const rep = data.report;
    line("── stage '" + name + "' re-opened in a fresh sandbox (saved " + data.resumed_from.saved_at + ") ──", "t-meta");
    line("score " + rep.score + "/100 " + rep.verdict.toUpperCase() +
         " · " + rep.findings.length + " findings · " + rep.files_scanned + " files scanned", "t-dim");
    line("files restored · " + data.packages + " package(s) listed in _cleanroom_stage_packages.txt" +
         " · run 'pip install -r _cleanroom_stage_packages.txt' with network allowed to rebuild the venv", "t-dim");
    if (data.needs_approval) line("this triaged DANGEROUS — type 'approve' before running", "t-err");
    else line("continue where it left off — type python, or 'run <file>'", "t-dim");
    return;
  }
  if (c.startsWith("forget ") || c === "forget") {
    const name = c.slice(7).trim();
    const { data } = await bridgeCall("/api/forget", { name });
    line(data.ok ? "stage '" + name + "' deleted" : "forget failed: " + data.error,
         data.ok ? "t-meta" : "t-err");
    renderStageChips();
    return;
  }
  if (c === "ls") {
    line("(this-PC mode — the clone lives in the sandbox; the repo-files panel lists every file, or use 'run <file>')", "t-dim");
    return;
  }
  const runMatch = c.match(/^run\s+(\S+)$/);
  if (runMatch) { await pcStream({ cmd: "python " + runMatch[1] }); return; }
  const pipMatch = c.match(/^pip\s+([A-Za-z0-9_.\-]+)$/);
  if (pipMatch) { await pcStream({ cmd: "python -m pip install " + pipMatch[1] }); return; }
  await pcStream({ py: c });
}

async function renderStageChips() {
  try {
    const { data } = await bridgeCall("/api/saves");
    const row = document.getElementById("pcStages");
    if (!row) return;
    row.innerHTML = "";
    if (!data.stages || !data.stages.length) {
      document.getElementById("pcStagesRow").classList.add("hidden");
      return;
    }
    document.getElementById("pcStagesRow").classList.remove("hidden");
    data.stages.forEach(s => {
      const chip = document.createElement("button");
      chip.className = "chip env";
      chip.textContent = s.name + " (" + s.saved_at.slice(11, 16) + ")";
      chip.title = "resume this stage — " + String(s.source).slice(0, 60);
      chip.addEventListener("click", () => pcExec("resume " + s.name));
      row.appendChild(chip);
    });
  } catch (e) { /* bridge gone — ignore */ }
}

async function execLine(raw) {
  const cmd = raw.trim();
  if (!cmd) return;
  if (cmd === "clear") { term.innerHTML = ""; return; }
  if (cmd === "help" || cmd === "?") { HELP.forEach(l => line(l, "t-dim")); return; }
  if (cmd === "wipe") { await wipeVM(); return; }
  if (cmd === "preview") { vmPreview(); return; }
  if (cmd === "setup") {
    const okAll = await runSetup(parseSetup(new Map(vmFiles)));
    const st = parseSetup(new Map(vmFiles));
    if (okAll && st.appKind === "wsgi") await vmServeWSGI();
    if (okAll && st.appKind === "asgi") await vmServeASGI();
    return;
  }
  if (cmd === "serve") {
    const st = parseSetup(new Map(vmFiles));
    if (st.appKind === "asgi") await vmServeASGI();
    else await vmServeWSGI();
    return;
  }
  if (cmd === "screen") {
    if (W.size) { appCard.classList.remove("hidden"); }
    else line("no window yet — run the repo's GUI entry (e.g. 'run gui_notes.py')", "t-dim");
    return;
  }
  if (cmd === "stages" || cmd.startsWith("save ") || cmd.startsWith("resume ") || cmd.startsWith("forget ")) {
    line("freeze/resume is a this-PC feature — the in-tab VM dies with this tab, so there is nothing to save here", "t-dim");
    return;
  }
  if (cmd === "ls") {
    listRepoFiles().forEach(f => line(f, "t-dim"));
    return;
  }
  const runMatch = cmd.match(/^run\s+(\S+)$/);
  if (runMatch) {
    const path = "/repo/" + runMatch[1].replace(/^\//, "");
    let source;
    try {
      source = pyodide.FS.readFile(path, { encoding: "utf8" });
    } catch (e) {
      line("no such file: " + runMatch[1], "t-err");
      return;
    }
    await runPython(source, runMatch[1]);
    return;
  }
  const pipMatch = cmd.match(/^pip\s+([A-Za-z0-9_.\-]+)$/);
  if (pipMatch) {
    const name = pipMatch[1];
    line("installing " + name + " into the VM…", "t-dim");
    try {
      await pyodide.loadPackage(name);
      line("installed " + name, "t-meta");
    } catch (e) {
      try {
        const micropip = pyodide.pyimport("micropip");
        await micropip.install(name);
        line("installed " + name + " (from PyPI, in-memory)", "t-meta");
      } catch (e2) {
        line("could not install " + name + ": " + (e2.message || e.message).split("\n")[0], "t-err");
      }
    }
    return;
  }
  await runPython(cmd, "<repl>");
}

async function runPython(source, label) {
  busy = true;
  try {
    try { await pyodide.loadPackagesFromImports(source); } catch (e) { /* optional */ }
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        if (label === "<repl>") {
          if (/\bawait\b/.test(source)) {
            // top-level await needs runPythonAsync's compiler (single-mode
            // compile() would reject it); echo semantics don't apply here
            await pyodide.runPythonAsync(source);
            return;
          }
          // compile mode "single" = real REPL semantics: runs statements AND
          // echoes the value of every expression statement, like python's shell
          const lit = JSON.stringify(source);
          await pyodide.runPythonAsync(
            "exec(compile(" + lit + ', "<repl>", "single"))');
        } else {
          await pyodide.runPythonAsync(source);
        }
        return;
      } catch (e) {
        const msg = (e.message || String(e)).trim();
        const missing = /No module named '([A-Za-z0-9_.]+)'/.exec(msg);
        if (attempt === 0 && missing && /included in the Pyodide distribution/.test(msg)) {
          line("this repo needs '" + missing[1] + "' — loading it into the VM…", "t-dim");
          await pyodide.loadPackage(missing[1]);
          line(missing[1] + " ready, running again…", "t-dim");
          continue;
        }
        line(msg, "t-err");
        return;
      }
    }
  } finally {
    busy = false;
  }
}

async function wipeVM() {
  if (!pyodide) { line("nothing to wipe — no VM started", "t-dim"); return; }
  await pyodide.runPythonAsync(`
import sys, os, shutil
_keep = {"sys", "builtins", "_frozen_importlib", "_frozen_importlib_external", "_imp", "marshal", "posix"}
_spares = {"sys", "os", "shutil", "_keep", "_m", "_k", "_spares"}
for _m in list(sys.modules):
    if _m not in _keep:
        sys.modules.pop(_m, None)
for _k in list(globals()):
    if not _k.startswith("__") and _k not in _spares:
        del globals()[_k]
if os.path.isdir("/repo"):
    shutil.rmtree("/repo", ignore_errors=True)
os.makedirs("/repo", exist_ok=True)
del _keep, _m, _k, _spares
`);
  await pyodide.runPythonAsync(VM_SETUP);
  await installShim(pyodide);
  window.__cleanroomGui.reset();
  vmFiles = [];
  resetRunUi();
  line("wiped — files, variables and modules erased. residue: 0. nothing was ever on your disk.", "t-meta");
  line("load another repo with the box above, or keep going with plain python.", "t-dim");
}

function vmWriteFile(path, text) {
  const full = "/repo/" + path;
  const dir = full.slice(0, full.lastIndexOf("/"));
  try { pyodide.FS.mkdirTree(dir); } catch (e) { /* exists */ }
  pyodide.FS.writeFile(full, text, { encoding: "utf8" });
}

loadBtn.addEventListener("click", () => loadRepo(liveUrl.value));
liveUrl.addEventListener("keydown", e => {
  if (e.key === "Enter") loadRepo(liveUrl.value);
});
termInput.addEventListener("keydown", async e => {
  if (e.key !== "Enter" || busy) return;
  const text = termInput.value;
  termInput.value = "";
  line("repo> " + text, "t-echo");
  if (mode === "pc") await pcExec(text);
  else await execLine(text);
});
document.querySelectorAll("button[data-live-preset]").forEach(btn => {
  btn.addEventListener("click", () => {
    liveUrl.value = btn.dataset.livePreset;
    loadRepo(btn.dataset.livePreset);
  });
});
renderShelf();
appCloseBtn?.addEventListener("click", () => appCard.classList.add("hidden"));
pcWebStart?.addEventListener("click", pcWebStartFn);
pcWebStop?.addEventListener("click", pcWebStopFn);
document.getElementById("wipeBtn")?.addEventListener("click", () => {
  if (mode === "pc") pcExec("wipe");
  else wipeVM();
});
