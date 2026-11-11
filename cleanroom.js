/* cleanroom.js — the JS twin of triage.py.
   Same rules, severities, weights, ordering and output shape. parity-check.mjs
   proves the two engines produce identical findings on the fixture repos; the
   browser demo (index.html + app.js) runs THIS file so the page agrees with
   the CLI. Pure module: no DOM, runs in Node and the browser. */

export const WEIGHTS = { critical: 30, high: 12, medium: 5, info: 0 };
export const VERDICT_STEPS = [[0, "clean"], [10, "caution"], [25, "high-risk"], [60, "dangerous"]];
export const MAX_HITS_PER_RULE_PER_FILE = 25;
export const MAX_FINDINGS = 400;

export const EXT_TEXT = new Set([
  ".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".json",
  ".sh", ".bash", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env",
  ".txt", ".md", ".rst", ".rb", ".go", ".rs", ".php", ".pl",
  ".ps1", ".bat", ".html", ".xml", ".csv", ".css",
]);

const PY_EXT = new Set([".py"]);
const JS_EXT = new Set([".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"]);
const SH_EXT = new Set([".sh", ".bash"]);

// MUST stay identical to triage.POPULAR (parity-check asserts equality).
export const POPULAR = [
  "requests", "numpy", "pandas", "flask", "django", "fastapi", "scikit-learn",
  "express", "react", "vue", "angular", "lodash", "axios", "moment", "jest",
  "webpack", "babel", "eslint", "typescript", "pillow", "sqlalchemy", "celery",
  "pytest", "boto3", "httpx", "aiohttp", "beautifulsoup4", "openai",
  "anthropic", "transformers", "torch", "tensorflow", "keras", "matplotlib",
  "seaborn", "click", "rich", "tqdm", "pydantic", "uvicorn", "gunicorn",
  "redis", "psycopg2", "pymongo", "mongoose", "chalk", "commander", "inquirer",
  "yargs", "zod", "tailwind", "bootstrap", "jquery", "d3", "three", "next",
  "nuxt", "svelte", "vite", "rollup", "esbuild", "prettier", "mocha", "chai",
  "sinon", "supertest", "body-parser", "cors", "dotenv", "graphql", "prisma",
  "socket.io", "ws",
];

// (rule_id, severity, category, pattern, flags, allowed_extensions|null)
// Patterns are valid in BOTH Python re and JS RegExp (no lookbehind, no
// inline flags). Must match triage.TEXT_RULES exactly.
export const TEXT_RULES = [
  ["PRIVATE_KEY", "critical", "secrets", "-----BEGIN [A-Z ]*PRIVATE KEY-----", "", null],
  ["AWS_KEY", "critical", "secrets", "AKIA[0-9A-Z]{16}", "", null],
  ["GH_TOKEN", "critical", "secrets", "gh[pousr]_[A-Za-z0-9]{36}", "", null],
  ["GOOGLE_KEY", "critical", "secrets", "AIza[0-9A-Za-z_\\-]{35}", "", null],
  ["SLACK_TOKEN", "critical", "secrets", "xox[baprs]-[0-9A-Za-z\\-]{10,}", "", null],
  ["STRIPE_KEY", "critical", "secrets", "sk_live_[0-9A-Za-z]{16,}", "", null],
  ["HARDCODED_SECRET", "medium", "secrets",
   "(password|passwd|secret|api_?key|token)\\s*[=:]\\s*[\\\"'][^\\\"']{12,}[\\\"']",
   "i", new Set([".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".sh", ".bash",
                 ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env", ".rb", ".go",
                 ".rs", ".php", ".pl", ".ps1", ".bat", ".html"])],
  ["DYNAMIC_EXEC_PY", "high", "exec", "\\b(eval|exec|compile)\\s*\\(", "", PY_EXT],
  ["DYNAMIC_EXEC_JS", "high", "exec", "new\\s+Function\\s*\\(|\\beval\\s*\\(", "", JS_EXT],
  ["PICKLE_LOAD", "high", "exec", "\\b(pickle|marshal)\\.(loads?|load)\\s*\\(", "", PY_EXT],
  ["IMPORT_EXEC", "medium", "exec", "__import__\\s*\\(", "", PY_EXT],
  ["SHELL_EXEC_PY", "high", "shell", "\\b(os\\.system|os\\.popen)\\b|subprocess\\.(run|call|check_output|check_call|Popen)\\b", "", PY_EXT],
  ["SHELL_EXEC_JS", "high", "shell", "child_process|execSync|spawnSync", "", JS_EXT],
  ["PIPED_INSTALL", "high", "install", "curl[^|\\n]{0,80}\\|\\s*(sudo\\s+)?(ba)?sh\\b", "", SH_EXT],
  ["DESTRUCTIVE_FS", "medium", "filesystem", "shutil\\.rmtree|os\\.remove|fs\\.rmSync|rm\\s+-rf\\b", "", new Set([...PY_EXT, ...JS_EXT, ...SH_EXT])],
  ["HOME_WRITE", "high", "filesystem", "expanduser|Path\\.home\\(\\)|os\\.homedir|/etc/(passwd|shadow)|AppData", "", new Set([...PY_EXT, ...JS_EXT])],
  ["PERSISTENCE", "critical", "persistence", "\\.bashrc|\\.zshrc|\\.profile\\b|crontab|schtasks|/etc/cron|CurrentVersion\\\\Run", "", new Set([...PY_EXT, ...JS_EXT, ...SH_EXT])],
  ["ENV_ACCESS", "info", "environment", "os\\.environ|os\\.getenv|process\\.env\\b", "", new Set([...PY_EXT, ...JS_EXT])],
  ["NET_PY", "info", "network", "socket\\.socket|urlopen|requests\\.(get|post|put|delete|request|Session)|urllib\\.request|httpx\\.|aiohttp", "", PY_EXT],
  ["NET_JS", "info", "network", "\\bfetch\\(|XMLHttpRequest|axios|https?\\.request|net\\.connect", "", JS_EXT],
  ["LONG_B64", "medium", "obfuscation", "[\\\"'][A-Za-z0-9+/=]{200,}[\\\"']", "", new Set([...PY_EXT, ...JS_EXT, ...SH_EXT])],
  ["HEX_BLOB", "medium", "obfuscation", "[\\\"'][0-9a-fA-F]{300,}[\\\"']", "", new Set([...PY_EXT, ...JS_EXT])],
];

const PKG_HOOKS = ["preinstall", "postinstall", "prepare", "prepack"];

const ENV_NAME_PY = "os\\.environ\\[.([A-Za-z0-9_]+)\\.]|os\\.environ\\.get\\(.([A-Za-z0-9_]+).|os\\.getenv\\(.([A-Za-z0-9_]+).";
const ENV_NAME_JS = "process\\.env\\.([A-Za-z0-9_]+)|process\\.env\\[.([A-Za-z0-9_]+).]";

function ext(path) {
  const i = path.lastIndexOf(".");
  return i === -1 ? "" : path.slice(i).toLowerCase();
}

function lineOf(text, idx) {
  return text.slice(0, idx).split("\n").length;
}

export function editDistance(a, b, cap = 3) {
  const la = a.length, lb = b.length;
  if (Math.abs(la - lb) > cap) return cap + 1;
  if (la === 0) return lb;
  if (lb === 0) return la;
  let prev2 = null;
  let prev = Array.from({ length: lb + 1 }, (_, j) => j);
  for (let i = 1; i <= la; i++) {
    const cur = [i].concat(new Array(lb).fill(0));
    for (let j = 1; j <= lb; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) {
        cur[j] = Math.min(cur[j], prev2[j - 2] + 1);
      }
    }
    if (Math.min(...cur) > cap) return cap + 1;
    prev2 = prev; prev = cur;
  }
  return prev[lb];
}

function cleanDepName(raw) {
  let name = raw.split("[")[0];
  for (const ch of "=<>~! ;,") name = name.split(ch)[0];
  return name.trim().toLowerCase();
}

function depLinesFromRequirements(text) {
  const out = [];
  const lines = text.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].trim();
    if (!s || s.startsWith("#") || s.startsWith("-")) continue;
    const name = cleanDepName(s);
    if (name && !name.includes(".") && !name.includes("/")) out.push([name, i + 1]);
  }
  return out;
}

function depsFromPyproject(text) {
  const m = text.match(/dependencies\s*=\s*\[(.*?)\]/s);
  if (!m) return [];
  const contentStart = m.index + m[0].indexOf(m[1]);
  const out = [];
  const items = [...m[1].matchAll(/["']([^"']+)["']/g)];
  for (let i = 0; i < items.length; i++) {
    const name = cleanDepName(items[i][1]);
    if (name && !name.includes(".") && !name.includes("/")) {
      out.push([name, lineOf(text, contentStart) + i]);
    }
  }
  return out;
}

function depsFromPackageJson(text, parsed) {
  const out = [];
  for (const section of ["dependencies", "devDependencies"]) {
    const obj = parsed[section] || {};
    for (const name of Object.keys(obj).sort()) {
      const idx = text.indexOf('"' + name + '"');
      out.push([name.toLowerCase(), idx === -1 ? 1 : lineOf(text, idx)]);
    }
  }
  return out;
}

function typosquatFindings(deps, path) {
  const out = [];
  for (const [name, ln] of deps) {
    if (POPULAR.includes(name) || name.length < 4) continue;
    let best = null, bestD = 99;
    for (const pop of POPULAR) {
      const d = editDistance(name, pop, 2);
      if (d < bestD) { best = pop; bestD = d; }
    }
    if (bestD <= 2) {
      out.push({ rule: "TYPOSQUAT_HINT", severity: "medium", category: "typosquat",
                 file: path, line: ln, snippet: name + " ≈ " + best });
    }
  }
  return out;
}

function firstDefined(m) {
  for (let i = 1; i < m.length; i++) if (m[i] !== undefined) return m[i];
  return "";
}

export function scanTree(files) {
  const findings = [];

  for (const [path, text] of files) {
    const e = ext(path);
    if (!EXT_TEXT.has(e)) continue;

    for (const [rid, sev, cat, pat, flags, exts] of TEXT_RULES) {
      if (exts && !exts.has(e)) continue;
      const rx = new RegExp(pat, (flags || "") + "g");
      let hits = 0;
      for (const m of text.matchAll(rx)) {
        findings.push({ rule: rid, severity: sev, category: cat,
                        file: path, line: lineOf(text, m.index),
                        snippet: m[0].slice(0, 80) });
        hits += 1;
        if (hits >= MAX_HITS_PER_RULE_PER_FILE) break;
      }
    }

    if (PY_EXT.has(e)) {
      for (const m of text.matchAll(new RegExp(ENV_NAME_PY, "g"))) {
        findings.push({ rule: "ENV_READS", severity: "info", category: "environment",
                        file: path, line: lineOf(text, m.index), snippet: firstDefined(m) });
      }
    } else if (JS_EXT.has(e)) {
      for (const m of text.matchAll(new RegExp(ENV_NAME_JS, "g"))) {
        findings.push({ rule: "ENV_READS", severity: "info", category: "environment",
                        file: path, line: lineOf(text, m.index), snippet: firstDefined(m) });
      }
    }

    if (path === "package.json" || path.endsWith("/package.json")) {
      let parsed = null;
      try { parsed = JSON.parse(text); } catch { parsed = null; }
      if (parsed && typeof parsed === "object") {
        const scripts = parsed.scripts || {};
        for (const hook of PKG_HOOKS) {
          if (hook in scripts) {
            const idx = text.indexOf('"' + hook + '"');
            findings.push({ rule: "INSTALL_HOOK", severity: "high", category: "install",
                            file: path, line: idx === -1 ? 1 : lineOf(text, idx),
                            snippet: hook + ": " + String(scripts[hook]).slice(0, 60) });
          }
        }
        for (const d of depsFromPackageJson(text, parsed)) {
          for (const f of typosquatFindings([d], path)) findings.push(f);
        }
      }
    }

    if (path === "setup.py" || path.endsWith("/setup.py")) {
      const idx = text.indexOf("cmdclass");
      if (idx !== -1) {
        findings.push({ rule: "SETUP_HOOK", severity: "medium", category: "install",
                        file: path, line: lineOf(text, idx),
                        snippet: "cmdclass overrides install/build steps" });
      }
    }

    if (path === "requirements.txt" || path.endsWith("/requirements.txt")) {
      for (const f of typosquatFindings(depLinesFromRequirements(text), path)) findings.push(f);
    }
    if (path === "pyproject.toml" || path.endsWith("/pyproject.toml")) {
      for (const f of typosquatFindings(depsFromPyproject(text), path)) findings.push(f);
    }
  }

  findings.sort((a, b) =>
    a.rule < b.rule ? -1 : a.rule > b.rule ? 1 :
    a.file < b.file ? -1 : a.file > b.file ? 1 :
    a.line - b.line);
  const trimmed = findings.slice(0, MAX_FINDINGS);

  let score = 0;
  for (const f of trimmed) score += WEIGHTS[f.severity] || 0;
  score = Math.min(100, score);

  let verdict = VERDICT_STEPS[0][1];
  for (const [threshold, label] of VERDICT_STEPS) {
    if (score >= threshold) verdict = label;
  }

  const byCat = {};
  for (const f of trimmed) byCat[f.category] = (byCat[f.category] || 0) + 1;
  const by_category = {};
  for (const k of Object.keys(byCat).sort()) by_category[k] = byCat[k];

  const touchedEnv = [...new Set(trimmed.filter(f => f.rule === "ENV_READS").map(f => f.snippet))].sort();

  return {
    score,
    verdict,
    findings: trimmed,
    by_category,
    touched_env: touchedEnv,
    files_scanned: files.length,
  };
}

export function verdictFor(score) {
  let v = VERDICT_STEPS[0][1];
  for (const [threshold, label] of VERDICT_STEPS) {
    if (score >= threshold) v = label;
  }
  return v;
}
