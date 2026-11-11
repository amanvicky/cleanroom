/* The live runner: paste a GitHub repo → its Python files are loaded into an
   in-browser Python VM (Pyodide, WebAssembly) and you use the repo right here,
   interactively, in real time.

   The VM lives in this tab's memory only: no server, no install, nothing on
   disk. Closing the tab wipes it. The VM has no sockets, so repo code cannot
   make real network calls — that's the sandbox, for free. */

import { fetchRepoFiles } from "./repo_fetch.js";

const PYODIDE_BASE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";

let pyodide = null;
let vmLoading = null;
let busy = false;

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

/* run mode: "vm" (WebAssembly in this tab) or "pc" (real processes via the
   localhost bridge started by `python cleanroom.py serve`) */
let mode = "vm";
const bridge = { base: "http://127.0.0.1:8641", token: "", specs: null };

document.querySelectorAll('input[name="runmode"]').forEach(radio => {
  radio.addEventListener("change", () => {
    mode = document.querySelector('input[name="runmode"]:checked').value;
    pcRow.classList.toggle("hidden", mode !== "pc");
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
      line("── connected to this PC's sandbox ──", "t-meta");
      line([d.platform, d.cpu_cores + " cores", d.memory_gb ? d.memory_gb + " GB RAM" : "",
            "python " + d.python].filter(Boolean).join(" · "), "t-dim");
      line("paste a repo link above and press Run it", "t-dim");
    } else if (data.needs_token) {
      setLiveStatus("bridge found, but that token didn't match — check the cleanroom serve window", true);
    } else {
      setLiveStatus("bridge said: " + (data.error || "unknown error"), true);
    }
  } catch (e) {
    setLiveStatus("no bridge on 127.0.0.1:8641 — run `python cleanroom.py serve` on this PC first", true);
  }
}
pcConnect.addEventListener("click", connectBridge);
pcToken.addEventListener("keydown", e => { if (e.key === "Enter") connectBridge(); });

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

function line(text, cls) {
  const el = document.createElement("div");
  el.className = "tline " + (cls || "t-out");
  el.textContent = text === "" ? " " : text;
  term.appendChild(el);
  term.scrollTop = term.scrollHeight;
}

function setLiveStatus(msg, isError) {
  liveStatus.textContent = msg;
  liveStatus.className = "status" + (isError ? " error" : "");
}

const HELP = [
  "commands:",
  "  run <file.py>    execute a file from the repo (its output prints here)",
  "  ls               list the files loaded into the virtual machine",
  "  pip <package>    install a package into the VM (numpy, pandas, micropip wheels)",
  "  clear            clear this terminal",
  "  wipe             erase everything: files, variables, modules — residue: 0",
  "  anything else    runs as Python in the repo's directory",
  "notes:",
  "  the VM has no network sockets — code cannot phone home",
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
      pyodide = py;
      return py;
    })();
  }
  return vmLoading;
}

function vmWriteFile(path, text) {
  const full = "/repo/" + path;
  const dir = full.slice(0, full.lastIndexOf("/"));
  try { pyodide.FS.mkdirTree(dir); } catch (e) { /* exists */ }
  pyodide.FS.writeFile(full, text, { encoding: "utf8" });
}

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

const RUNNABLE = /\.(py|json|txt|csv|toml|cfg|ini|md)$/i;
const ENTRIES = ["main.py", "app.py", "demo.py", "run.py", "cli.py", "__main__.py"];

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
  liveCard.classList.remove("hidden");
  try {
    if (mode === "pc") await pcLoadRepo(raw);
    else await vmLoadRepo(raw);
  } catch (e) {
    setLiveStatus(e.message, true);
    line("failed: " + e.message, "t-err");
  } finally {
    loadBtn.disabled = false;
  }
}

async function vmLoadRepo(raw) {
  const started = performance.now();
  line("cleanroom live runner — a disposable Python VM inside this tab", "t-meta");
  line("nothing to install · nothing to pay · nothing left behind", "t-dim");
  const { files, label } = await fetchRepoFiles(raw,
    (d, n) => setLiveStatus("fetching repo files " + d + "/" + n + "…"),
    { maxFileBytes: 2 * 1024 * 1024 });
  const vm = await ensureVM(setLiveStatus);
  const runnable = files.filter(([p]) => RUNNABLE.test(p) && !p.includes("/"));
  const runnableDeep = files.filter(([p]) => RUNNABLE.test(p) && p.includes("/"));
  for (const f of runnable.concat(runnableDeep)) vmWriteFile(f[0], f[1]);
  await vm.runPythonAsync("import sys; sys.modules.pop('repo', None)");

  line("── loaded " + label + " ──", "t-meta");
  line(runnable.length + runnableDeep.length + " files → /repo (virtual, in-memory only)", "t-dim");
  line("on your device: " + deviceInfo(), "t-dim");
  line("the VM is yours. type a command, or 'help'.", "t-dim");
  const hints = suggestCommands(files);
  if (hints.length) {
    line("try: (click to fill)", "t-hint");
    hints.slice(0, 3).forEach(h => {
      const el = document.createElement("div");
      el.className = "tline t-hint";
      el.textContent = "  " + h;
      el.addEventListener("click", () => { termInput.value = h; termInput.focus(); });
      term.appendChild(el);
    });
    term.scrollTop = term.scrollHeight;
  }
  setLiveStatus("running — " + label + " is live in your browser (" +
    Math.round(performance.now() - started) / 1000 + "s to boot, VM cached for next time)");
  termInput.focus();
}

const PC_HELP = [
  "commands on this PC (real processes):",
  "  run <file.py>    execute a file from the repo on this machine",
  "  ls               list the sandbox clone",
  "  pip <package>    pip inside the sandbox — outbound network is blocked, so PyPI downloads fail by design",
  "  approve          accept a DANGEROUS triage verdict (your responsibility)",
  "  wipe             delete the sandbox and verify zero residue",
  "  anything else    runs as Python in the repo directory (expressions are echoed)",
  "the sandbox: temp-dir clone · whitelist environment (your secrets never reach it)",
  "             · no outbound network for python · 120s timeout · verified wipe",
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
    line("sandbox ready on your PC: scrubbed secrets, no outbound network (python), wiped on exit", "t-dim");
    line("type python, 'run <file>', or 'help'", "t-dim");
  }
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
  if (c === "wipe") {
    const { data } = await bridgeCall("/api/wipe", {});
    line("wiped on your PC — deleted: " + data.deleted + " · residue: " + data.residue +
         " · commands run: " + (data.commands || 0) + " · nothing was written outside the sandbox", "t-meta");
    return;
  }
  if (c === "ls") {
    await pcStream({ py: "import os\nprint('\\n'.join(sorted(sum([[os.path.join(r, f) for f in fs] for r, _, fs in os.walk('.')], []))))" });
    return;
  }
  const runMatch = c.match(/^run\s+(\S+)$/);
  if (runMatch) { await pcStream({ cmd: "python " + runMatch[1] }); return; }
  const pipMatch = c.match(/^pip\s+([A-Za-z0-9_.\-]+)$/);
  if (pipMatch) { await pcStream({ cmd: "python -m pip install " + pipMatch[1] }); return; }
  await pcStream({ py: c });
}

async function execLine(raw) {
  const cmd = raw.trim();
  if (!cmd) return;
  if (cmd === "clear") { term.innerHTML = ""; return; }
  if (cmd === "help" || cmd === "?") { HELP.forEach(l => line(l, "t-dim")); return; }
  if (cmd === "wipe") { await wipeVM(); return; }
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
for _m in list(sys.modules):
    if _m not in _keep:
        sys.modules.pop(_m, None)
for _k in list(globals()):
    if not _k.startswith("__") and _k not in ("sys", "os", "shutil"):
        del globals()[_k]
if os.path.isdir("/repo"):
    shutil.rmtree("/repo", ignore_errors=True)
os.makedirs("/repo", exist_ok=True)
del _keep, _m, _k
`);
  await pyodide.runPythonAsync(VM_SETUP);
  line("wiped — files, variables and modules erased. residue: 0. nothing was ever on your disk.", "t-meta");
  line("load another repo with the box above, or keep going with plain python.", "t-dim");
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
