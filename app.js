/* Browser demo: paste a repo URL, fetch its text files over the GitHub API
   (CORS-friendly), and run the SAME triage engine the CLI uses (cleanroom.js,
   parity-checked against triage.py in CI). Everything stays in memory. */

import { scanTree } from "./cleanroom.js";

const MAX_FILES = 250;
const MAX_FILE_BYTES = 200 * 1024;
const SKIP_PARTS = ["node_modules/", "dist/", "vendor/", ".min.js", ".lock", "package-lock.json"];
const FETCH_POOL = 8;

const TEXT_EXT = new Set([
  "py", "js", "mjs", "cjs", "ts", "jsx", "tsx", "json", "sh", "bash",
  "yml", "yaml", "toml", "cfg", "ini", "env", "txt", "md", "rst",
  "rb", "go", "rs", "php", "pl", "ps1", "bat", "html", "xml", "csv", "css",
]);

function parseRepo(raw) {
  let s = raw.trim().replace(/\.git$/, "").replace(/\/$/, "");
  s = s.replace(/^https?:\/\/(www\.)?github\.com\//i, "").replace(/^github\.com\//i, "");
  const m = s.match(/^([A-Za-z0-9_.-]+)\/([A-Za-z0-9_.-]+)$/);
  if (!m) return null;
  return { owner: m[1], repo: m[2] };
}

function status(msg, isError) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status" + (isError ? " error" : "");
}

async function gh(path) {
  const res = await fetch("https://api.github.com" + path);
  if (res.status === 403 && res.headers.get("X-RateLimit-Remaining") === "0") {
    throw new Error("GitHub API rate limit reached (60/hour anonymous). The fixture presets below still work.");
  }
  if (!res.ok) throw new Error("GitHub API said " + res.status + " for " + path);
  return res.json();
}

async function pool(items, worker) {
  const out = new Array(items.length);
  let next = 0;
  const runners = Array.from({ length: Math.min(FETCH_POOL, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      out[i] = await worker(items[i], i);
    }
  });
  await Promise.all(runners);
  return out;
}

async function scanRepoUrl(raw) {
  const parsed = parseRepo(raw);
  if (!parsed) throw new Error("That doesn't look like a GitHub repo URL.");
  const { owner, repo } = parsed;

  const meta = await gh("/repos/" + owner + "/" + repo);
  const branch = meta.default_branch || "main";
  const tree = await gh("/repos/" + owner + "/" + repo + "/git/trees/" + branch + "?recursive=1");
  if (!tree.tree) throw new Error("Could not read the file tree (is the repo empty?)");

  const candidates = tree.tree.filter(t => {
    if (t.type !== "blob") return false;
    if (t.size > MAX_FILE_BYTES) return false;
    const p = t.path;
    if (SKIP_PARTS.some(part => p.includes(part))) return false;
    const dot = p.lastIndexOf(".");
    if (dot === -1) return false;
    return TEXT_EXT.has(p.slice(dot + 1).toLowerCase());
  }).slice(0, MAX_FILES);

  const files = (await pool(candidates, async t => {
    const res = await fetch("https://raw.githubusercontent.com/" + owner + "/" + repo +
                            "/" + branch + "/" + t.path.split("/").map(encodeURIComponent).join("/"));
    const text = res.ok ? await res.text() : "";
    return [t.path, text];
  })).filter(f => f[1]);

  return { report: scanTree(files), label: owner + "/" + repo + " @ " + branch, files: files.length };
}

async function scanPreset(name) {
  const file = name === "sneaky" ? "sneaky-installer" : "benign-notes-app";
  const res = await fetch("./fixtures/" + file + ".json");
  if (!res.ok) throw new Error("preset not found");
  const files = await res.json();
  const label = file + " (synthetic fixture)";
  return { report: scanTree(files.map(f => [f.path, f.text])), label, files: files.length };
}

const CHECKS = [
  ["INSTALL_HOOK", "this repo runs code at INSTALL time (npm pre/postinstall) — that executes before anyone reviews it"],
  ["SETUP_HOOK", "setup.py overrides build/install steps — read them first"],
];
const CAT_CHECKS = [
  ["secrets", "credential-shaped strings are present — assume any key you can see, attackers can see too"],
  ["typosquat", "a dependency name closely mimics a popular package — verify the spelling on the registry page"],
  ["persistence", "code touches shell profiles / schedulers — that survives your session even if the sandbox is wiped"],
  ["exec", "dynamic execution (eval/exec/pickle) — obfuscated payloads hide behind these calls"],
  ["network", "it phones home — if you run it, run it with outbound network blocked"],
];

function beforeYouRun(rep) {
  const rules = new Set(rep.findings.map(f => f.rule));
  const cats = new Set(Object.keys(rep.by_category));
  const steps = [];
  for (const [rule, text] of CHECKS) if (rules.has(rule)) steps.push(text);
  for (const [cat, text] of CAT_CHECKS) if (cats.has(cat)) steps.push(text);
  if (!steps.length) steps.push("no red flags in static triage — treat “clean” as “nothing obvious”, not “safe”");
  return steps;
}

function render({ report, label, files }) {
  const colors = { clean: "v-clean", caution: "v-caution", "high-risk": "v-high-risk", dangerous: "v-dangerous" };
  const word = document.getElementById("verdictWord");
  word.textContent = report.verdict.toUpperCase();
  word.className = "verdict-word " + colors[report.verdict];
  document.getElementById("scoreNum").textContent = report.score;
  document.getElementById("scoreNum").style.color =
    report.score >= 25 ? "var(--red)" : report.score >= 10 ? "var(--amber)" : "var(--accent)";
  document.getElementById("scanMeta").innerHTML =
    "<b>" + label + "</b><br>" + files + " files scanned · " +
    report.findings.length + " findings (" +
    report.findings.filter(f => f.severity !== "info").length + " scored)";

  const cats = document.getElementById("catRow");
  cats.innerHTML = "";
  for (const [cat, n] of Object.entries(report.by_category)) {
    const chip = document.createElement("span");
    chip.className = "chip info";
    chip.textContent = cat + " × " + n;
    cats.appendChild(chip);
  }

  const box = document.getElementById("findings");
  box.innerHTML = "";
  const scored = report.findings.filter(f => f.severity !== "info");
  const infos = report.findings.filter(f => f.severity === "info");
  const rows = scored.concat(infos.slice(0, 40));
  const cls = { critical: "crit", high: "high", medium: "med", info: "info" };
  if (!rows.length) {
    box.innerHTML = '<div class="frow"><span class="rule">—</span><span class="where">—</span><span class="snip">nothing found</span></div>';
  }
  for (const f of rows) {
    const row = document.createElement("div");
    row.className = "frow";
    row.innerHTML =
      '<span class="chip ' + cls[f.severity] + '">' + f.severity + "</span>" +
      '<span class="rule">' + f.rule + "</span>" +
      '<span class="snip" title="' + f.snippet.replace(/"/g, "&quot;") + '">' + f.snippet + "</span>" +
      '<span class="where">' + f.file + ":" + f.line + "</span>";
    row.style.gridTemplateColumns = "86px 150px 1fr 220px";
    box.appendChild(row);
  }

  const envRow = document.getElementById("envRow");
  envRow.innerHTML = "";
  if (!report.touched_env.length) {
    envRow.innerHTML = '<span class="chip env">reads no environment variables</span>';
  } else {
    for (const name of report.touched_env) {
      const chip = document.createElement("span");
      chip.className = "chip env";
      chip.textContent = name;
      envRow.appendChild(chip);
    }
  }

  const list = document.getElementById("checklist");
  list.innerHTML = "";
  for (const step of beforeYouRun(report)) {
    const li = document.createElement("li");
    li.textContent = step;
    list.appendChild(li);
  }

  for (const id of ["verdictCard", "findingsCard", "envCard", "checkCard"]) {
    document.getElementById(id).classList.remove("hidden");
  }
}

async function runScan(fn) {
  const btn = document.getElementById("scanBtn");
  btn.disabled = true;
  for (const id of ["verdictCard", "findingsCard", "envCard", "checkCard"]) {
    document.getElementById(id).classList.add("hidden");
  }
  try {
    status("scanning…");
    const result = await fn();
    status("done — report below. Nothing was uploaded anywhere; it all ran in this tab.");
    render(result);
  } catch (e) {
    status(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("scanBtn").addEventListener("click", () => {
  const v = document.getElementById("repoUrl").value;
  runScan(() => scanRepoUrl(v));
});
document.getElementById("repoUrl").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("scanBtn").click();
});
document.querySelectorAll("button[data-preset]").forEach(btn => {
  btn.addEventListener("click", () => {
    if (btn.dataset.preset === "live") {
      document.getElementById("repoUrl").value = "https://github.com/amanvicky/edgar-rev";
      runScan(() => scanRepoUrl("https://github.com/amanvicky/edgar-rev"));
    } else {
      runScan(() => scanPreset(btn.dataset.preset));
    }
  });
});
