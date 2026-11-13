/* Pure, dependency-free helpers for the live runner. No DOM, no pyodide —
   this file must stay importable from node (runner-check.mjs) and the
   browser (runner.js) alike.

   Detection here is convenience guidance only. It is NOT part of the
   security engine — the triage engine lives in cleanroom.js / triage.py
   and has its own parity gate. */

/* What kind of app does this repo look like?
     tkinter — a Python GUI app (some file imports tkinter) → app screen
     webapp  — a static web app (root index.html)          → sandboxed preview
     rails   — Ruby on Rails (Gemfile + config.ru/rails)   → this-PC mode only
     hints   — start-command suggestions for running the repo as a real
               server on this PC ("Start app")
   files: [[path, text], ...]; text may be "" when unknown. */
export function detectGuiSignals(files) {
  const paths = files.map(f => f[0]);
  const sig = { tkinter: false, entry: null, webapp: false, rails: false, hints: [] };

  const tkFiles = [];
  for (const [p, t] of files) {
    if (/\.py$/i.test(p) && /(^|\n)\s*(?:import\s+tkinter\b|from\s+tkinter\b)/.test(t || "")) {
      tkFiles.push(p);
    }
    if (p === "index.html") sig.webapp = true;
  }
  if (tkFiles.length) {
    sig.tkinter = true;
    const prefer = ["main.py", "app.py", "gui.py", "demo.py", "run.py", "__main__.py"];
    sig.entry = prefer.find(e => tkFiles.includes(e)) ||
      tkFiles.find(p => /mainloop\s*\(/.test(textOf(files, p))) || tkFiles[0];
  }

  // full-stack server hints (this-PC mode): best-guess start commands
  const has = p => paths.includes(p);
  if (has("manage.py")) {
    sig.hints.push({ label: "Django dev server", cmd: "python manage.py runserver 127.0.0.1:8000", net: false });
  }
  if (has("Gemfile") && (has("config.ru") || /rails/.test(textOf(files, "Gemfile")))) {
    sig.rails = true;
    sig.hints.push({ label: "Rails server", cmd: "bin/rails server -b 127.0.0.1 -p 3000", net: true });
  }
  const flaskFile = paths.find(p => /\.py$/i.test(p) && /Flask\s*\(/.test(textOf(files, p)));
  if (flaskFile) {
    sig.hints.push({ label: "Flask app (" + flaskFile + ")", cmd: "python " + flaskFile, net: true });
  }
  const fastFile = paths.find(p => /\.py$/i.test(p) && /FastAPI\s*\(/.test(textOf(files, p)));
  if (fastFile) {
    const mod = fastFile.replace(/\.py$/i, "").replace(/\//g, ".");
    sig.hints.push({ label: "FastAPI/uvicorn (" + fastFile + ")",
                     cmd: "python -m uvicorn " + mod + ":app --host 127.0.0.1 --port 8000", net: true });
  }
  const pkg = safeJson(textOf(files, "package.json"));
  if (pkg && pkg.scripts) {
    if (pkg.scripts.dev) sig.hints.push({ label: "npm run dev", cmd: "npm run dev", net: true });
    if (pkg.scripts.start) sig.hints.push({ label: "npm start", cmd: "npm start", net: true });
  }
  if (sig.webapp && !sig.hints.length) {
    sig.hints.push({ label: "serve the static files",
                     cmd: "python -m http.server 0 --bind 127.0.0.1", net: false });
  }
  return sig;
}

function textOf(files, path) {
  const f = files.find(x => x[0] === path);
  return f ? (f[1] || "") : "";
}

function safeJson(s) {
  if (!s) return null;
  try { return JSON.parse(s.replace(/^\uFEFF/, "")); } catch (e) { return null; }
}

/* One-line explanation for every rule the triage engine can emit, shown in
   the run-tab report card. Presentation only — not part of the security
   engine. runner-check.mjs asserts this map covers exactly the rule ids the
   engine produces (TEXT_RULES + ENV_READS + the three package-level rules). */
export const RULE_WHAT = {
  PRIVATE_KEY: "an embedded private key — anyone holding this file owns whatever it opens",
  AWS_KEY: "an AWS access key id — pairs with a secret key; treat as leaked",
  GH_TOKEN: "a GitHub token — can read or write repos under its scopes",
  GOOGLE_KEY: "a Google API key — usable against the matching cloud project",
  SLACK_TOKEN: "a Slack token — can read or post in its workspace scopes",
  STRIPE_KEY: "a live Stripe secret key — can move money",
  HARDCODED_SECRET: "a credential-shaped assignment (password/secret/key/token = \"…\")",
  DYNAMIC_EXEC_PY: "eval/exec/compile — strings become code; payloads hide behind them",
  DYNAMIC_EXEC_JS: "eval or new Function — strings become code; payloads hide behind them",
  PICKLE_LOAD: "pickle/marshal load — deserializing runs embedded code",
  IMPORT_EXEC: "__import__ — imports computed at runtime, invisible to a static read",
  SHELL_EXEC_PY: "spawns shell processes — arbitrary commands at its fingertips",
  SHELL_EXEC_JS: "child_process — arbitrary commands at its fingertips",
  PIPED_INSTALL: "curl … | sh — downloads and executes code straight from the network",
  DESTRUCTIVE_FS: "deletes files or directories — check what it points at",
  HOME_WRITE: "touches your home directory or /etc — outside any project directory",
  PERSISTENCE: "shell profiles or schedulers — survives even a verified wipe",
  ENV_ACCESS: "reads environment variables — where your secrets live",
  ENV_READS: "reads this named environment variable",
  NET_PY: "makes network calls from Python",
  NET_JS: "makes network calls from JavaScript",
  LONG_B64: "a long base64 blob — classic obfuscation",
  HEX_BLOB: "a long hex blob — classic obfuscation",
  TYPOSQUAT_HINT: "a dependency name closely mimics a popular package — check the spelling on the registry",
  INSTALL_HOOK: "npm pre/postinstall — code runs at install time, before anyone reviews it",
  SETUP_HOOK: "setup.py overrides build/install steps — read them first",
};

/* Per-rule and per-category advice shown under a report ("before you run
   this"). Moved here from app.js so both tabs and runner-check share it. */
const RULE_ADVICE = [
  ["INSTALL_HOOK", "this repo runs code at INSTALL time (npm pre/postinstall) — that executes before anyone reviews it"],
  ["SETUP_HOOK", "setup.py overrides build/install steps — read them first"],
];
const CAT_ADVICE = [
  ["secrets", "credential-shaped strings are present — assume any key you can see, attackers can see too"],
  ["typosquat", "a dependency name closely mimics a popular package — verify the spelling on the registry page"],
  ["persistence", "code touches shell profiles / schedulers — that survives your session even if the sandbox is wiped"],
  ["exec", "dynamic execution (eval/exec/pickle) — obfuscated payloads hide behind these calls"],
  ["network", "it phones home — if you run it, run it with outbound network blocked"],
];

/* The "before you run this" steps for a scanTree report. */
export function beforeRunSteps(rep) {
  const rules = new Set(rep.findings.map(f => f.rule));
  const cats = new Set(Object.keys(rep.by_category || {}));
  const steps = [];
  for (const [rule, text] of RULE_ADVICE) if (rules.has(rule)) steps.push(text);
  for (const [cat, text] of CAT_ADVICE) if (cats.has(cat)) steps.push(text);
  if (!steps.length) steps.push("no red flags in static triage — treat \u201cclean\u201d as \u201cnothing obvious\u201d, not \u201csafe\u201d");
  return steps;
}

/* The curated shelf on the run tab: repos verified to run in the in-tab VM,
   each saying what you will see. `at` is the commit each entry was last
   verified against (spot-checked again whenever the shelf changes).
   runner-check.mjs validates the shape. */
export const SHELF = [
  { repo: "https://github.com/amanvicky/spamcheck",
    what: "train the naive-Bayes classifier, then classify a message live", at: "8f3cc12" },
  { repo: "https://github.com/amanvicky/attention-from-scratch",
    what: "self-attention in ~90 lines of NumPy — print the attention weights", at: "9ce42fb" },
  { repo: "https://github.com/amanvicky/tokenizer-playground",
    what: "the byte-BPE trainer's Python side — tokens, merges, encode/decode", at: "af34d94" },
  { repo: "https://github.com/amanvicky/tk-gui-demo",
    what: "a tkinter notes app opens as a real window on the app screen", at: "82841f5" },
  { repo: "https://github.com/amanvicky/csv-clinic",
    what: "diagnose a messy CSV from examples/ — types, nulls, duplicates", at: "0f62878" },
  { repo: "https://github.com/amanvicky/fine-tune-prep",
    what: "inspect and validate a chat JSONL dataset before fine-tuning", at: "7dc4090" },
  { repo: "https://github.com/amanvicky/flask-notes-demo",
    what: "a real Flask app served through the page's virtual network — add and delete notes", at: "61ec690" },
  { repo: "https://github.com/amanvicky/fastapi-notes-demo",
    what: "a real FastAPI app driven by the VM's own event loop — no uvicorn, no socket", at: "21da05a" },
  { repo: "https://github.com/amanvicky/notebook-demo",
    what: "three tabs, real switching — the audit's #1 blocker now renders", at: "f40eaf2" },
  { repo: "https://github.com/amanvicky/asking-cli",
    what: "an input() quiz — the terminal box queues answers for the VM's stdin", at: "75c5601" },
  { repo: "https://github.com/amanvicky/progressbar-demo",
    what: "three progress bars — step() wrap, start()/stop() timers, indeterminate bounce", at: "0565af3" },
  { repo: "https://github.com/amanvicky/treeview-demo",
    what: "a task table with real selection state, and a tree outline with expand toggles", at: "6d5393f" },
];

/* README-and-manifest understanding for Python repos: what to install and
   what to run. Convenience guidance only — the security engine lives in
   cleanroom.js / triage.py. Node-testable (runner-check.mjs). */
export function parseSetup(files) {
  const byPath = new Map(files);
  const textOf = p => byPath.get(p) || "";
  const paths = [...byPath.keys()];
  const out = { installs: [], runCommands: [], notes: [], appKind: "cli" };
  // does any Python file call input()? The VM has no keyboard of its own —
  // the stdin lane queues terminal-box lines for those calls.
  out.usesStdin = paths.some(p => /\.py$/i.test(p) && /\binput\s*\(/.test(textOf(p)));

  // requirements files: one requirement per line, comments and -r/-- flags out
  for (const p of paths.filter(p => /requirements[^/]*\.txt$/i.test(p))) {
    for (const raw of textOf(p).split("\n")) {
      const s = raw.trim();
      if (!s || s.startsWith("#") || s.startsWith("-")) continue;
      const name = s.split(/[;=\[><~ ]/)[0].trim().toLowerCase();
      if (name && !out.installs.includes(name)) out.installs.push(name);
    }
  }
  // pyproject [project] dependencies + setup.py install_requires
  const pyproject = textOf("pyproject.toml");
  const depBlock = /dependencies\s*=\s*\[([^\]]*)\]/s.exec(pyproject);
  if (depBlock) {
    for (const m of depBlock[1].matchAll(/["']([^"']+)["']/g)) {
      const name = m[1].split(/[;=\[><~ ]/)[0].trim().toLowerCase();
      if (name && /^[a-z0-9._-]+$/.test(name) && !out.installs.includes(name)) {
        out.installs.push(name);
      }
    }
  }
  const reqRe = /install_requires\s*=\s*\[([^\]]*)\]/s.exec(textOf("setup.py"));
  if (reqRe) {
    for (const m of reqRe[1].matchAll(/["']([^"']+)["']/g)) {
      const name = m[1].split(/[;=\[><~ ]/)[0].trim().toLowerCase();
      if (name && /^[a-z0-9._-]+$/.test(name) && !out.installs.includes(name)) {
        out.installs.push(name);
      }
    }
  }
  if (out.installs.length) {
    out.notes.push("found " + out.installs.length + " requirement(s) in the repo's manifests");
  }

  // README: pip install / python <file> commands a human would copy
  const readmePath = paths.find(p => /^readme(\.md|\.rst|\.txt)?$/i.test(p));
  if (readmePath) {
    const md = textOf(readmePath);
    for (const m of md.matchAll(/(?:^|[{`\n ])pip install ([^\n`"]{2,80})/gi)) {
      const args = m[1].trim().replace(/`.*$/, "").trim();
      if (args && !out.runCommands.some(c => c.cmd === args)) {
        out.runCommands.push({ kind: "install", cmd: args });
      }
    }
    for (const m of md.matchAll(/(?:^|[{`\n ])python ([A-Za-z0-9_./-]+\.py)\b/g)) {
      const f = m[1].replace(/^\.\//, "");
      if (paths.includes(f) && !out.runCommands.some(c => c.cmd === "run " + f)) {
        out.runCommands.push({ kind: "run", cmd: "run " + f });
      }
    }
    if (/flask/i.test(md) && out.appKind === "cli") out.appKind = "wsgi";
    if (/django/i.test(md) && out.appKind === "cli") out.appKind = "wsgi";
  }

  // manifest-and-code reality beats README wording
  if (out.installs.includes("flask") || paths.some(p => /(^|\/)app\.py$/i.test(p) && /Flask\s*\(/.test(textOf(p)))) {
    out.appKind = "wsgi";
  } else if (out.installs.includes("django") || paths.includes("manage.py")) {
    out.appKind = "wsgi";
  } else if (out.installs.includes("fastapi") || paths.some(p => /\.py$/i.test(p) && /FastAPI\s*\(/.test(textOf(p)))) {
    out.appKind = "asgi";
  }
  // tkinter detection lives in detectGuiSignals; the runner combines both.
  return out;
}

/* When the in-tab VM cannot carry a repo, name the reasons — the honest
   hand-off is the operator's own PC (this-PC mode, free). Node-testable. */
const NATIVE_ONLY = new Set([
  "torch", "tensorflow", "onnxruntime", "ctranslate2", "faster-whisper",
  "av", "sounddevice", "soundfile", "pyaudio", "opencv-python", "cv2",
  "pyannote", "pyannote.audio", "transformers", "accelerate", "bitsandbytes",
  "playwright", "weasyprint", "cairosvg", "mysqlclient", "psycopg2",
  "psycopg2-binary", "pymupdf", "fitz", "ddddocr", "paddleocr", "paddlepaddle",
]);

export function fullMachineReasons(setup, sig, hasPythonRunnable) {
  const reasons = [];
  const native = setup.installs.filter(n => NATIVE_ONLY.has(n));
  if (native.length) {
    reasons.push("native-binary dependencies that cannot exist in WebAssembly: " + native.join(", "));
  }
  if (!hasPythonRunnable) {
    reasons.push("no runnable Python — the in-tab VM is CPython only");
  }
  return reasons;
}

/* Pull localhost ports a server prints while starting up, in output order.
   Matches the shapes real servers actually print:
     "Serving HTTP on 127.0.0.1 port 8000 (http://127.0.0.1:8000/)"
     "Uvicorn running on http://127.0.0.1:8000"
     "Starting development server at http://127.0.0.1:8000/"
     "Local: http://localhost:5173/"                                     */
export function extractPorts(lines) {
  const text = lines.join("\n");
  const out = [];
  const patterns = [
    /https?:\/\/(?:127\.0\.0\.1|localhost|\[::1\]):(\d{2,5})/gi,
    /\b(?:127\.0\.0\.1|localhost|0\.0\.0\.0)\D{0,24}?port\s+(\d{2,5})/gi,
    /\bport\s+(\d{2,5})\b/gi,
  ];
  for (const re of patterns) {
    let m;
    while ((m = re.exec(text)) !== null) {
      const p = parseInt(m[1], 10);
      if (p >= 1024 && p <= 65535 && !out.includes(p)) out.push(p);
      if (out.length >= 8) return out;
    }
  }
  return out;
}
