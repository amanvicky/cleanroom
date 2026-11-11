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
  } catch (e) {
    setLiveStatus(e.message, true);
    line("failed: " + e.message, "t-err");
  } finally {
    loadBtn.disabled = false;
  }
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
    if (label === "<repl>") {
      // compile mode "single" = real REPL semantics: runs statements AND
      // echoes the value of every expression statement, like python's shell
      const lit = JSON.stringify(source);
      await pyodide.runPythonAsync(
        "exec(compile(" + lit + ', "<repl>", "single"))');
    } else {
      await pyodide.runPythonAsync(source);
    }
  } catch (e) {
    const msg = (e.message || String(e)).trim();
    line(msg, "t-err");
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
  await execLine(text);
});
document.querySelectorAll("button[data-live-preset]").forEach(btn => {
  btn.addEventListener("click", () => {
    liveUrl.value = btn.dataset.livePreset;
    loadRepo(btn.dataset.livePreset);
  });
});
