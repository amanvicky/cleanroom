/* Parity gate: the browser engine (cleanroom.js) must be the SAME engine as
   the CLI engine (triage.py). Compares, on both fixture repos:
     - findings, score, verdict, touched_env (exact)
     - rule tables, weights, verdict steps, POPULAR list (exact)
   CI fails on any drift. */

import { readFileSync, writeFileSync, readdirSync, statSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { join, relative, sep } from "node:path";
import { scanTree, POPULAR, TEXT_RULES, WEIGHTS, VERDICT_STEPS } from "./cleanroom.js";

function walk(root) {
  const files = [];
  for (const name of readdirSync(root).sort()) {
    if (name === ".git" || name === "node_modules" || name === "__pycache__") continue;
    const full = join(root, name);
    if (statSync(full).isDirectory()) {
      for (const f of walk(full)) files.push(f);
    } else {
      files.push([relative(root, full).split(sep).join("/"), readFileSync(full, "utf-8")]);
    }
  }
  return files;
}

writeFileSync("parity-tree.json", JSON.stringify(
  walk("fixtures/benign-notes-app").map(([path, text]) => ({ path, text }))), "utf-8");

execFileSync("python", ["-X", "utf8", "parity_py.py", "parity-tree.json", "parity-py.json"],
             { stdio: "inherit" });

const py = JSON.parse(readFileSync("parity-py.json", "utf-8"));

let fails = 0;
function check(name, a, b) {
  const ok = JSON.stringify(a) === JSON.stringify(b);
  console.log(`${ok ? "ok  " : "DRIFT"} ${name}`);
  if (!ok) {
    fails += 1;
    if (String(JSON.stringify(a)).length < 400 && String(JSON.stringify(b)).length < 400) {
      console.log("   py:", JSON.stringify(a));
      console.log("   js:", JSON.stringify(b));
    }
  }
  return ok;
}

const ruleRows = TEXT_RULES.map(([rid, sev, cat, pat, flags, exts]) => ({
  rule: rid, severity: sev, category: cat, pattern: pat, flags,
  exts: exts ? [...exts].sort() : null,
}));

check("POPULAR package list", py.popular, POPULAR);
check(`rule table (${TEXT_RULES.length} rules)`, py.rules, ruleRows);
check("severity weights", py.weights, WEIGHTS);
check("verdict steps", py.verdict_steps, VERDICT_STEPS);

// per-fixture reports
const repos = ["sneaky-installer", "benign-notes-app"];
for (const repo of repos) {
  const files = walk(join("fixtures", repo));
  const js = scanTree(files);
  writeFileSync("parity-tree.json", JSON.stringify(
    files.map(([path, text]) => ({ path, text }))), "utf-8");
  execFileSync("python", ["-X", "utf8", "parity_py.py", "parity-tree.json", "parity-py.json"],
               { stdio: "pipe" });
  const pyr = JSON.parse(readFileSync("parity-py.json", "utf-8")).report;
  console.log(`--- ${repo}: py score ${pyr.score}/100 ${pyr.verdict} · js score ${js.score}/100 ${js.verdict}`);
  check(`${repo} score`, pyr.score, js.score);
  check(`${repo} verdict`, pyr.verdict, js.verdict);
  check(`${repo} touched_env`, pyr.touched_env, js.touched_env);
  check(`${repo} by_category`, pyr.by_category, js.by_category);
  check(`${repo} findings (${pyr.findings.length})`, pyr.findings, js.findings);
}

console.log(fails ? `FAIL: ${fails} drift(s)` : "parity: cleanroom.js === triage.py on all fixtures");
process.exit(fails ? 1 : 0);
