/* Triage tab + tab wiring. The live runner lives in runner.js.
   Both tabs share repo fetching via repo_fetch.js. */

import { scanTree } from "./cleanroom.js";
import { fetchRepoFiles } from "./repo_fetch.js";
import "./runner.js";

function status(msg, isError) {
  const el = document.getElementById("status");
  el.textContent = msg;
  el.className = "status" + (isError ? " error" : "");
}

async function scanRepoUrl(raw) {
  const { files, label } = await fetchRepoFiles(raw);
  return { report: scanTree(files), label, files: files.length };
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
    render(await fn());
    status("done — report below. Nothing was uploaded anywhere; it all ran in this tab.");
  } catch (e) {
    status(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

document.getElementById("scanBtn").addEventListener("click", () => {
  runScan(() => scanRepoUrl(document.getElementById("repoUrl").value));
});
document.getElementById("repoUrl").addEventListener("keydown", e => {
  if (e.key === "Enter") document.getElementById("scanBtn").click();
});
document.querySelectorAll("button[data-preset]").forEach(btn => {
  btn.addEventListener("click", () => runScan(() => scanPreset(btn.dataset.preset)));
});

async function scanPreset(name) {
  const file = name === "sneaky" ? "sneaky-installer" : "benign-notes-app";
  const res = await fetch("./fixtures/" + file + ".json");
  if (!res.ok) throw new Error("preset not found");
  const files = await res.json();
  return { report: scanTree(files.map(f => [f.path, f.text])), label: file + " (synthetic fixture)", files: files.length };
}

/* tab wiring */
document.querySelectorAll(".tab").forEach(tab => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t === tab));
    for (const section of ["tab-run", "tab-check"]) {
      document.getElementById(section).classList.toggle("hidden", section !== tab.dataset.tab);
    }
  });
});
