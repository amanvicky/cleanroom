/* Node gate for the runner's pure helpers (runner_core.js): GUI/server
   detection, port extraction, the run-tab report card's rule explanations,
   and the curated shelf. These are convenience guidance, not the security
   engine — the security engine has its own parity gate. */

import { detectGuiSignals, extractPorts, RULE_WHAT, SHELF, beforeRunSteps, parseSetup,
         fullMachineReasons } from "./runner_core.js";
import { TEXT_RULES } from "./cleanroom.js";

let failed = 0;
function ok(cond, msg) {
  if (cond) console.log("ok   " + msg);
  else { console.error("FAIL " + msg); failed++; }
}

/* tkinter detection + entry picking */
const gui = detectGuiSignals([
  ["README.md", "# demo"],
  ["gui_notes.py", "import tkinter as tk\nroot = tk.Tk()\nroot.mainloop()"],
  ["util.py", "def helper():\n    return 1"],
]);
ok(gui.tkinter === true, "tkinter app detected");
ok(gui.entry === "gui_notes.py", "entry picked from the tkinter file");
ok(gui.webapp === false && gui.rails === false, "no false webapp/rails positives");

const prefer = detectGuiSignals([
  ["main.py", "from tkinter import *"],
  ["gui.py", "import tkinter"],
]);
ok(prefer.entry === "main.py", "entry prefers the conventional file name");

const fromTk = detectGuiSignals([["my_app.py", "import tkinter\nroot.mainloop()"]]);
ok(fromTk.entry === "my_app.py", "entry falls back to the file with mainloop()");

/* webapp detection */
const web = detectGuiSignals([["index.html", "<html></html>"], ["app.js", "x"]]);
ok(web.webapp === true, "static web app detected (index.html)");
ok(web.hints.some(h => h.cmd.includes("http.server")), "static repo suggests a static server");

/* server stack hints */
const django = detectGuiSignals([["manage.py", ""], ["app", ""]]);
ok(django.hints[0].cmd === "python manage.py runserver 127.0.0.1:8000", "django start hint");

const rails = detectGuiSignals([
  ["Gemfile", 'gem "rails"'],
  ["config.ru", "run Rails.application"],
]);
ok(rails.rails === true, "rails detected");
ok(rails.hints.some(h => h.cmd.includes("bin/rails server")), "rails start hint");

const flask = detectGuiSignals([
  ["app.py", "from flask import Flask\napp = Flask(__name__)"],
  ["requirements.txt", "flask==3.0.0"],
]);
ok(flask.hints.some(h => h.cmd === "python app.py"), "flask start hint (binds to the app file)");

const fast = detectGuiSignals([
  ["main.py", "app = FastAPI()"],
]);
ok(fast.hints.some(h => h.cmd.includes("uvicorn main:app")), "fastapi start hint");

const node = detectGuiSignals([
  ["package.json", '{"name":"x","scripts":{"dev":"vite"}}'],
]);
ok(node.hints.some(h => h.cmd === "npm run dev"), "npm start hint from package.json scripts");

const none = detectGuiSignals([["README.md", "# plain"]]);
ok(!none.tkinter && !none.webapp && !none.rails && !none.hints.length, "plain repo → no signals");

/* port extraction: the shapes real servers print */
ok(JSON.stringify(extractPorts([
  " * Serving Flask app 'app'",
  " * Running on http://127.0.0.1:5000",
])) === "[5000]", "flask port line");

ok(JSON.stringify(extractPorts([
  "Serving HTTP on 127.0.0.1 port 8000 (http://127.0.0.1:8000/) ...",
])) === "[8000]", "http.server port line (both shapes, deduped)");

ok(JSON.stringify(extractPorts([
  "INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)",
  "Starting development server at http://127.0.0.1:8000/",
])) === "[8000]", "uvicorn/django port lines");

ok(JSON.stringify(extractPorts(["Local: http://localhost:5173/"])) === "[5173]", "vite port line");
ok(extractPorts(["random log line 2024", "version 3.12.7"]).length === 0, "no ports in ordinary logs");
ok(extractPorts(["listening on port 80"]).length === 0, "privileged ports are not suggested");

/* the run-tab report card: every rule the engine can emit has an explanation,
   and the map carries nothing the engine can't emit (exact set equality) */
const engineRules = new Set([
  ...TEXT_RULES.map(r => r[0]),
  "ENV_READS", "TYPOSQUAT_HINT", "INSTALL_HOOK", "SETUP_HOOK",
]);
const explained = new Set(Object.keys(RULE_WHAT));
let ruleGaps = [...engineRules].filter(r => !explained.has(r));
ok(ruleGaps.length === 0, "every engine rule has a one-line explanation" +
  (ruleGaps.length ? " (missing: " + ruleGaps.join(", ") + ")" : ""));
let orphans = [...explained].filter(r => !engineRules.has(r));
ok(orphans.length === 0, "no explanations for rules the engine cannot emit" +
  (orphans.length ? " (orphans: " + orphans.join(", ") + ")" : ""));

/* beforeRunSteps: the shared "before you run this" advice */
const steps1 = beforeRunSteps({ findings: [{ rule: "INSTALL_HOOK" }], by_category: { secrets: 2 } });
ok(steps1.length === 2 && steps1[0].includes("INSTALL time") && steps1[1].includes("credential-shaped"),
   "beforeRunSteps fires on rules then categories");
const steps2 = beforeRunSteps({ findings: [], by_category: {} });
ok(steps2.length === 1 && steps2[0].includes("nothing obvious"), "beforeRunSteps falls back to the honest default");

/* the shelf: every entry names a real repo shape, says what you'll see, and
   carries the commit it was verified against */
ok(SHELF.length >= 4 && SHELF.length <= 14, "shelf size is curated (" + SHELF.length + " entries)");
const seen = new Set();
let shelfOk = true;
for (const e of SHELF) {
  if (!/^https:\/\/github\.com\/[A-Za-z0-9_.\-]+\/[A-Za-z0-9_.\-]+$/.test(e.repo)) shelfOk = false;
  if (seen.has(e.repo)) shelfOk = false;
  seen.add(e.repo);
  if (!e.what || e.what.length > 90) shelfOk = false;
  if (!/^[0-9a-f]{7,40}$/.test(e.at || "")) shelfOk = false;
}
ok(shelfOk, "shelf entries: unique repos, a 'what you'll see' line, a verified commit");

/* parseSetup: manifests + README → install list, run commands, app kind */
const setup1 = parseSetup(new Map([
  ["requirements.txt", "# web app\nflask==3.0.0\n-r other.txt\n\nrequests>=2.0 ; python_version>'3'\n"],
  ["README.md", "Install:\n```\npip install flask\n```\nRun:\n```\npython app.py\n```\nA flask application."],
  ["app.py", "from flask import Flask\napp = Flask(__name__)"],
]));
ok(setup1.installs.join(",") === "flask,requests",
   "parseSetup requirements: deduped, flags/comments/markers stripped (got: " + setup1.installs.join(",") + ")");
ok(setup1.runCommands.some(c => c.kind === "install" && c.cmd === "flask"), "parseSetup reads pip install from the README");
ok(setup1.runCommands.some(c => c.kind === "run" && c.cmd === "run app.py"), "parseSetup reads the run file from the README (only real files)");
ok(setup1.appKind === "wsgi", "parseSetup: flask repo detected as wsgi");
const setup2 = parseSetup(new Map([
  ["pyproject.toml", '[project]\ndependencies = ["rich>=13", "click"]\n'],
  ["setup.py", "setup(\n    install_requires=[\"numpy\"],\n)"],
  ["main.py", "print(1)"],
]));
ok(setup2.installs.join(",") === "rich,click,numpy",
   "parseSetup: pyproject + setup.py requirements merged (got: " + setup2.installs.join(",") + ")");
ok(setup2.appKind === "cli", "parseSetup: plain repo stays cli");
const setup3 = parseSetup(new Map([["manage.py", ""], ["README.md", "a django blog"]]));
ok(setup3.appKind === "wsgi" && !setup3.installs.length, "parseSetup: django via manage.py, no installs");

/* the stdin lane: input()-using repos get the terminal box as their stdin */
const setup4 = parseSetup(new Map([
  ["quiz.py", "name = input('name? ')\nprint('hi', name)\n"],
  ["util.py", "value = my_input(5)\n"],
]));
ok(setup4.usesStdin === true,
   "parseSetup flags input()-using repos for the stdin lane (and not my_input)");
const setup5 = parseSetup(new Map([
  ["app.py", "from flask import Flask\napp = Flask(__name__)"],
]));
ok(setup5.usesStdin === false, "parseSetup: no input() call — stdin lane stays off");

/* fullMachineReasons: names exactly why the tab can't carry a repo, plus the
   Codespaces hand-off URL — the honest tier for repos beyond the tab */
const fmSetup = parseSetup(new Map([
  ["requirements.txt", "fastapi\nuvicorn\nfaster-whisper\nav\nsounddevice\nnumpy\n"],
]));
const fmReasons = fullMachineReasons(fmSetup, null, true);
ok(fmReasons.length === 1 && fmReasons[0].startsWith("native-binary dependencies"),
   "fullMachineReasons: native deps named, ASGI no longer a reason (got: " + fmReasons.join(" / ") + ")");
ok(fmReasons[0].includes("faster-whisper") && fmReasons[0].includes("sounddevice"),
   "fullMachineReasons lists the native packages by name");
const fmNone = fullMachineReasons(parseSetup(new Map([["requirements.txt", "flask\n"]])), null, true);
ok(fmNone.length === 0, "fullMachineReasons: a WSGI repo with pure deps stays in-tab");
const fmNoPy = fullMachineReasons(parseSetup(new Map([["package.json", "{}"]])), null, false);
ok(fmNoPy.some(r => r.startsWith("no runnable Python")), "fullMachineReasons: non-Python repo named");

console.log(failed ? "runner-check: FAILED" : "runner-check: all good");
process.exitCode = failed ? 1 : 0;
