/* Node gate for the runner's pure helpers (runner_core.js): GUI/server
   detection and port extraction. These are convenience guidance, not the
   security engine — the security engine has its own parity gate. */

import { detectGuiSignals, extractPorts } from "./runner_core.js";

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

console.log(failed ? "runner-check: FAILED" : "runner-check: all good");
process.exitCode = failed ? 1 : 0;
