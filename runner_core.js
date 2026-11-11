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
                     cmd: "python -m http.server 8000 --bind 127.0.0.1", net: false });
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
