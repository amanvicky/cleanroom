"""Local bridge: lets the cleanroom web page drive THIS machine's sandbox.

- binds 127.0.0.1 only — nothing is reachable from the network
- every request needs the one-time launch token `cleanroom serve` prints
- repos run with the complete PC: real interpreter, real processes — inside
  the same cleanroom sandbox (whitelist env, network guard, timeout, wipe
  with residue verification)
"""

import ctypes
import json
import os
import platform
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import sandbox
import triage

VERSION = "0.1.0"


def _total_memory_gb():
    try:
        if sys.platform == "win32":
            class _STATUS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = _STATUS()
            st.dwLength = ctypes.sizeof(_STATUS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return round(st.ullTotalPhys / 1024 ** 3, 1)
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 ** 2, 1)
    except Exception:
        return None


def device_specs():
    specs = {
        "hostname": platform.node() or "this-pc",
        "platform": platform.system() + " " + platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "cpu_cores": os.cpu_count(),
        "cpu": (platform.processor() or platform.machine())[:60],
    }
    mem = _total_memory_gb()
    if mem:
        specs["memory_gb"] = mem
    return specs


class Bridge:
    """One bridge = one token = up to one active session at a time."""

    def __init__(self):
        self.token = "-".join(uuid.uuid4().hex[:4] for _ in range(2)).upper()
        self.session = None
        self.lock = threading.Lock()

    def state(self):
        s = self.session
        return {
            "ok": True,
            "version": VERSION,
            "device": device_specs(),
            "sandbox_defaults": {"net": "blocked", "timeout_s": 120},
            "session": None if not s else {
                "url": s.url or s.local,
                "commit": getattr(s, "commit", None),
                "verdict": s.report["verdict"],
                "score": s.report["score"],
                "commands": s.commands,
                "open": not s.closed,
            },
        }

    def api_open(self, url, approve=False):
        with self.lock:
            if self.session and not self.session.closed:
                return {"ok": False, "error": "a session is already open — wipe it first"}
            if os.path.isdir(url):
                sess = sandbox.Session(local=url, net="blocked", timeout=120)
            else:
                sess = sandbox.Session(url=url, net="blocked", timeout=120)
            try:
                sess.open()
            except sandbox.SandboxError as e:
                return {"ok": False, "error": str(e)}
            self.session = sess
            rep = sess.report
            dangerous = rep["verdict"] in ("high-risk", "dangerous")
            return {
                "ok": True,
                "report": rep,
                "needs_approval": dangerous and not approve,
                "commit": sess.commit,
            }

    def api_run(self, cmd=None, py=None):
        with self.lock:
            s = self.session
            if not s or s.closed:
                return {"ok": False, "error": "no open session"}
            if s.report["verdict"] in ("high-risk", "dangerous") and not getattr(s, "approved", False):
                return {"ok": False, "needs_approval": True,
                        "error": "triage says %s (%d/100) — approve first"
                                 % (s.report["verdict"], s.report["score"])}
        if py is not None:
            # REPL semantics: compile mode "single" echoes expression values,
            # exactly like the in-tab VM
            n = getattr(self, "_repl_seq", 0) + 1
            self._repl_seq = n
            script = os.path.join(s.dir, "repo", "_repl_%d.py" % n)
            with open(script, "w", encoding="utf-8") as fh:
                fh.write("exec(compile(%s, \"<repl>\", \"single\"))\n"
                         % json.dumps(py))
            cmd = "python _repl_%d.py" % n
        try:
            handle = s.execute_stream(cmd)
        except sandbox.SandboxError as e:
            return {"ok": False, "error": str(e)}
        s.approved = True  # an explicit run by the operator is the approval
        return {"ok": True, "started": True}

    def api_output(self, since):
        s = self.session
        if not s:
            return {"ok": False, "error": "no session"}
        handle = getattr(s, "active_stream", None)
        if not handle:
            return {"ok": True, "lines": [], "cursor": since, "done": True, "exit_code": None}
        lines, cursor = handle.lines_since(since)
        done, code = handle.poll()
        return {"ok": True, "lines": lines, "cursor": cursor, "done": done,
                "exit_code": code, "kill_reason": s.kill_reason}

    def api_approve(self):
        s = self.session
        if not s:
            return {"ok": False, "error": "no session"}
        s.approved = True
        return {"ok": True, "approved": True}

    def api_wipe(self):
        with self.lock:
            s = self.session
            if not s:
                return {"ok": True, "was_open": False, "residue": sandbox.residue_count()}
            audit = s.close("wipe-from-web")
            self.session = None
            return {"ok": True, "was_open": True,
                    "deleted": audit["deleted"], "residue": len(audit["residue"]),
                    "commands": audit["commands"], "duration_s": audit["duration_s"]}


class _Handler(BaseHTTPRequestHandler):
    bridge = None  # set by serve()

    def log_message(self, fmt, *args):  # keep the console readable
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Cleanroom-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _token_ok(self, qs, body):
        given = (qs.get("token", [None])[0]
                 or self.headers.get("X-Cleanroom-Token")
                 or (body or {}).get("token"))
        return given == self.bridge.token

    def _reply(self, obj, code=200):
        payload = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self._cors()
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        body = {}
        if u.path == "/api/status":
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            return self._reply(self.bridge.state())
        if u.path == "/api/output":
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            since = int(qs.get("since", ["0"])[0])
            return self._reply(self.bridge.api_output(since))
        return self._reply({"ok": False, "error": "unknown endpoint"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except ValueError:
            return self._reply({"ok": False, "error": "bad json"}, 400)
        if not self._token_ok({}, body):
            return self._reply({"ok": False, "needs_token": True}, 401)

        if u.path == "/api/open":
            url = (body.get("url") or "").strip()
            if not url:
                return self._reply({"ok": False, "error": "url required"}, 400)
            res = self.bridge.api_open(url, approve=bool(body.get("approve")))
            if res.get("ok"):
                rep = res["report"]
                self._log("open   %s — %d/100 %s" % (url, rep["score"], rep["verdict"]))
            return self._reply(res, 200 if res.get("ok") else 409)

        if u.path == "/api/approve":
            res = self.bridge.api_approve()
            if res.get("ok"):
                self._log("approve — operator accepted the triage verdict")
            return self._reply(res)

        if u.path == "/api/run":
            cmd = (body.get("cmd") or "").strip() or None
            py = body.get("py")
            if not cmd and py is None:
                return self._reply({"ok": False, "error": "cmd or py required"}, 400)
            res = self.bridge.api_run(cmd=cmd, py=py)
            if res.get("ok"):
                self._log("run    %s" % (cmd or "(python) " + py)[:90])
            return self._reply(res, 200 if res.get("ok") else 409)

        if u.path == "/api/wipe":
            res = self.bridge.api_wipe()
            self._log("wipe   residue %d" % res.get("residue", -1))
            return self._reply(res)

        return self._reply({"ok": False, "error": "unknown endpoint"}, 404)

    def _log(self, msg):
        print("  [%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def serve(bridge, host="127.0.0.1", port=8641):
    _Handler.bridge = bridge
    server = ThreadingHTTPServer((host, port), _Handler)
    server.daemon_threads = True
    return server
