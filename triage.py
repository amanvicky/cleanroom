"""Static triage engine: scan a file tree for risk signals BEFORE you run it.

Pure stdlib. The same rules, severities, weights and ordering are implemented in
cleanroom.js — parity-check.mjs proves both engines produce identical findings,
scores and verdicts on the fixture repos in CI.

Input is always a list of (path, text) pairs, so the engine never touches the
disk itself: the CLI walks a clone, the browser fetches a repo over the GitHub
API, and both feed the same scan.
"""

import json
import re

WEIGHTS = {"critical": 30, "high": 12, "medium": 5, "info": 0}
VERDICT_STEPS = [(0, "clean"), (10, "caution"), (25, "high-risk"), (60, "dangerous")]

MAX_HITS_PER_RULE_PER_FILE = 25
MAX_FINDINGS = 400

# Extensions that are scanned as text. Everything else is skipped.
EXT_TEXT = {
    ".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".json",
    ".sh", ".bash", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env",
    ".txt", ".md", ".rst", ".rb", ".go", ".rs", ".php", ".pl",
    ".ps1", ".bat", ".html", ".xml", ".csv", ".css",
}

PY_EXT = {".py"}
JS_EXT = {".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx"}
SH_EXT = {".sh", ".bash"}

# Top PyPI / npm package names used for typosquat hints. MUST stay identical
# to the list in cleanroom.js (parity-check asserts equality).
POPULAR = [
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
]

# (rule_id, severity, category, regex_pattern, flags, allowed_extensions)
# flags: "" or "i". Patterns are written to be valid in BOTH Python re and JS
# RegExp (no lookbehind, no inline flags, no proprietary classes).
TEXT_RULES = [
    # --- secrets: scan every text file ---
    ("PRIVATE_KEY", "critical", "secrets", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "", None),
    ("AWS_KEY", "critical", "secrets", r"AKIA[0-9A-Z]{16}", "", None),
    ("GH_TOKEN", "critical", "secrets", r"gh[pousr]_[A-Za-z0-9]{36}", "", None),
    ("GOOGLE_KEY", "critical", "secrets", r"AIza[0-9A-Za-z_\-]{35}", "", None),
    ("SLACK_TOKEN", "critical", "secrets", r"xox[baprs]-[0-9A-Za-z\-]{10,}", "", None),
    ("STRIPE_KEY", "critical", "secrets", r"sk_live_[0-9A-Za-z]{16,}", "", None),
    ("HARDCODED_SECRET", "medium", "secrets",
     r"(password|passwd|secret|api_?key|token)\s*[=:]\s*[\"'][^\"']{12,}[\"']",
     "i", {".py", ".js", ".mjs", ".cjs", ".ts", ".jsx", ".tsx", ".sh", ".bash",
           ".yml", ".yaml", ".toml", ".cfg", ".ini", ".env", ".rb", ".go",
           ".rs", ".php", ".pl", ".ps1", ".bat", ".html"}),
    # --- dynamic execution ---
    ("DYNAMIC_EXEC_PY", "high", "exec", r"\b(eval|exec|compile)\s*\(", "", PY_EXT),
    ("DYNAMIC_EXEC_JS", "high", "exec", r"new\s+Function\s*\(|\beval\s*\(", "", JS_EXT),
    ("PICKLE_LOAD", "high", "exec", r"\b(pickle|marshal)\.(loads?|load)\s*\(", "", PY_EXT),
    ("IMPORT_EXEC", "medium", "exec", r"__import__\s*\(", "", PY_EXT),
    # --- shells & processes ---
    ("SHELL_EXEC_PY", "high", "shell", r"\b(os\.system|os\.popen)\b|subprocess\.(run|call|check_output|check_call|Popen)\b", "", PY_EXT),
    ("SHELL_EXEC_JS", "high", "shell", r"child_process|execSync|spawnSync", "", JS_EXT),
    ("PIPED_INSTALL", "high", "install", r"curl[^|\n]{0,80}\|\s*(sudo\s+)?(ba)?sh\b", "", SH_EXT),
    # --- filesystem / persistence ---
    ("DESTRUCTIVE_FS", "medium", "filesystem", r"shutil\.rmtree|os\.remove|fs\.rmSync|rm\s+-rf\b", "", PY_EXT | JS_EXT | SH_EXT),
    ("HOME_WRITE", "high", "filesystem", r"expanduser|Path\.home\(\)|os\.homedir|/etc/(passwd|shadow)|AppData", "", PY_EXT | JS_EXT),
    ("PERSISTENCE", "critical", "persistence", r"\.bashrc|\.zshrc|\.profile\b|crontab|schtasks|/etc/cron|CurrentVersion\\Run", "", PY_EXT | JS_EXT | SH_EXT),
    # --- environment & network (signals, not score) ---
    ("ENV_ACCESS", "info", "environment", r"os\.environ|os\.getenv|process\.env\b", "", PY_EXT | JS_EXT),
    ("NET_PY", "info", "network", r"socket\.socket|urlopen|requests\.(get|post|put|delete|request|Session)|urllib\.request|httpx\.|aiohttp", "", PY_EXT),
    ("NET_JS", "info", "network", r"\bfetch\(|XMLHttpRequest|axios|https?\.request|net\.connect", "", JS_EXT),
    # --- obfuscation ---
    ("LONG_B64", "medium", "obfuscation", r"[\"'][A-Za-z0-9+/=]{200,}[\"']", "", PY_EXT | JS_EXT | SH_EXT),
    ("HEX_BLOB", "medium", "obfuscation", r"[\"'][0-9a-fA-F]{300,}[\"']", "", PY_EXT | JS_EXT),
]
TEXT_RULES = [(rid, sev, cat, pat, fl, (None if exts is None else set(exts)))
              for rid, sev, cat, pat, fl, exts in TEXT_RULES]

# --- meta rules (not plain regex scans) -------------------------------------

PKG_HOOKS = ("preinstall", "postinstall", "prepare", "prepack")

# env var names a repo reads, so the report can show WHAT it touches
ENV_NAME_PY = (r"os\.environ\[.([A-Za-z0-9_]+).\]"
               r"|os\.environ\.get\(.([A-Za-z0-9_]+)."
               r"|os\.getenv\(.([A-Za-z0-9_]+).")
ENV_NAME_JS = r"process\.env\.([A-Za-z0-9_]+)|process\.env\[.([A-Za-z0-9_]+).\]"


def _ext(path):
    i = path.rfind(".")
    return path[i:].lower() if i != -1 else ""


def _line_of(text, idx):
    return text.count("\n", 0, idx) + 1


def edit_distance(a, b, cap=3):
    """Damerau-Levenshtein (unrestricted adjacent transpositions), with early
    exit. Must behave identically to the JS copy in cleanroom.js."""
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev2 = None
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > cap:
            return cap + 1
        prev2, prev = prev, cur
    return prev[lb]


def _dep_lines_from_requirements(text):
    out = []
    for ln, line in enumerate(text.split("\n"), start=1):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("-"):
            continue
        name = s.split("[")[0]
        for ch in "=<>~! ;,":
            name = name.split(ch)[0]
        name = name.strip().lower()
        if name and "." not in name and "/" not in name:
            out.append((name, ln))
    return out


def _deps_from_pyproject(text):
    m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.S)
    if not m:
        return []
    out = []
    for i, raw in enumerate(re.findall(r"[\"']([^\"']+)[\"']", m.group(1))):
        name = raw.split("[")[0]
        for ch in "=<>~! ;,":
            name = name.split(ch)[0]
        name = name.strip().lower()
        if name and "." not in name and "/" not in name:
            out.append((name, _line_of(text, m.start(1)) + i))
    return out


def _deps_from_package_json(text, parsed):
    out = []
    for section in ("dependencies", "devDependencies"):
        for name in sorted((parsed.get(section) or {}).keys()):
            idx = text.find('"' + name + '"')
            out.append((name.lower(), _line_of(text, idx) if idx != -1 else 1))
    return out


def _typosquat_findings(deps, path):
    out = []
    for name, ln in deps:
        if name in POPULAR or len(name) < 4:
            continue
        best, best_d = None, 99
        for pop in POPULAR:
            d = edit_distance(name, pop, 2)
            if d < best_d:
                best, best_d = pop, d
        if best_d <= 2:
            out.append({
                "rule": "TYPOSQUAT_HINT", "severity": "medium", "category": "typosquat",
                "file": path, "line": ln, "snippet": "%s ≈ %s" % (name, best),
            })
    return out


def scan_tree(files):
    """files: list of (path, text). Returns the report dict. Deterministic."""
    findings = []

    for path, text in files:
        ext = _ext(path)
        if ext not in EXT_TEXT:
            continue

        for rid, sev, cat, pat, flags, exts in TEXT_RULES:
            if exts is not None and ext not in exts:
                continue
            rx = re.compile(pat, re.I if flags else 0)
            hits = 0
            for m in rx.finditer(text):
                findings.append({
                    "rule": rid, "severity": sev, "category": cat,
                    "file": path, "line": _line_of(text, m.start()),
                    "snippet": m.group(0)[:80],
                })
                hits += 1
                if hits >= MAX_HITS_PER_RULE_PER_FILE:
                    break

        # env var names the repo reads (report-only signal)
        if ext in PY_EXT:
            for m in re.finditer(ENV_NAME_PY, text):
                name = next(g for g in m.groups() if g is not None)
                findings.append({"rule": "ENV_READS", "severity": "info",
                                 "category": "environment", "file": path,
                                 "line": _line_of(text, m.start()),
                                 "snippet": name})
        elif ext in JS_EXT:
            for m in re.finditer(ENV_NAME_JS, text):
                name = next(g for g in m.groups() if g is not None)
                findings.append({"rule": "ENV_READS", "severity": "info",
                                 "category": "environment", "file": path,
                                 "line": _line_of(text, m.start()),
                                 "snippet": name})

        # package.json install hooks
        if path == "package.json" or path.endswith("/package.json"):
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                scripts = parsed.get("scripts") or {}
                for hook in PKG_HOOKS:
                    if hook in scripts:
                        idx = text.find('"%s"' % hook)
                        findings.append({
                            "rule": "INSTALL_HOOK", "severity": "high",
                            "category": "install", "file": path,
                            "line": _line_of(text, idx) if idx != -1 else 1,
                            "snippet": "%s: %s" % (hook, str(scripts[hook])[:60]),
                        })
                for d in _deps_from_package_json(text, parsed):
                    findings.extend(_typosquat_findings([d], path))

        # setup.py build hooks
        if path == "setup.py" or path.endswith("/setup.py"):
            idx = text.find("cmdclass")
            if idx != -1:
                findings.append({
                    "rule": "SETUP_HOOK", "severity": "medium", "category": "install",
                    "file": path, "line": _line_of(text, idx),
                    "snippet": "cmdclass overrides install/build steps",
                })

        # typosquat on requirements.txt / pyproject.toml
        if path == "requirements.txt" or path.endswith("/requirements.txt"):
            findings.extend(_typosquat_findings(_dep_lines_from_requirements(text), path))
        if path == "pyproject.toml" or path.endswith("/pyproject.toml"):
            findings.extend(_typosquat_findings(_deps_from_pyproject(text), path))

    findings.sort(key=lambda f: (f["rule"], f["file"], f["line"]))
    findings = findings[:MAX_FINDINGS]

    score = min(100, sum(WEIGHTS[f["severity"]] for f in findings))
    verdict = VERDICT_STEPS[0][1]
    for threshold, label in VERDICT_STEPS:
        if score >= threshold:
            verdict = label

    by_cat = {}
    for f in findings:
        by_cat[f["category"]] = by_cat.get(f["category"], 0) + 1

    touched_env = sorted({f["snippet"] for f in findings
                          if f["rule"] == "ENV_READS"})

    return {
        "score": score,
        "verdict": verdict,
        "findings": findings,
        "by_category": dict(sorted(by_cat.items())),
        "touched_env": touched_env,
        "files_scanned": len(files),
    }


def verdict_for(score):
    v = VERDICT_STEPS[0][1]
    for threshold, label in VERDICT_STEPS:
        if score >= threshold:
            v = label
    return v
