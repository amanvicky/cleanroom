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
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import sandbox
import stages as stages_mod
import triage

VERSION = "0.1.5"

# the read-only file viewer's per-file cap (bytes); bigger files report
# their size instead of their contents
FILE_VIEW_MAX_BYTES = 256 * 1024

# the app-proxy: when the page is cross-origin isolated (multi-core VM), a
# cross-origin iframe may only embed responses that carry CORP/COEP headers
# — and a real server you serve can't be asked to send them. The bridge
# streams the app's responses and adds them itself. Only loopback ports,
# one server at a time.
_APP_PROXY_MAX_BYTES = 32 * 1024 * 1024

# a file that imports tkinter means the repo has a Python GUI — on a real PC
# it opens a real window on the operator's desktop
_TK_IMPORT = re.compile(r"(?m)^\s*(?:import\s+tkinter\b|from\s+tkinter\b)")
_TK_ENTRY_PREFER = ["main.py", "app.py", "gui.py", "demo.py", "run.py", "__main__.py"]


def _tkinter_entry(tree):
    """Best entry file to `run` for a tkinter GUI app, or None."""
    candidates = [(rel, "mainloop" in text) for rel, text in tree
                  if rel.endswith(".py") and _TK_IMPORT.search(text)]
    if not candidates:
        return None
    names = dict(candidates)
    for p in _TK_ENTRY_PREFER:
        if p in names:
            return p
    for rel, has_mainloop in candidates:
        if has_mainloop:
            return rel
    return candidates[0][0]


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
        self.active_web_port = None

    def app_proxy_fetch(self, port, rest, method, body, ctype, query):
        """Stream one response from the running app at 127.0.0.1:<port>, with
        the embed headers an isolated page requires. Never executes anything;
        only ever talks to loopback."""
        if not (1024 <= port <= 65535):
            return 400, "text/plain", b"bad port"
        url = "http://127.0.0.1:%d/%s%s" % (port, rest,
                                            ("?" + query) if query else "")
        req = urllib.request.Request(url, data=body if method in ("POST", "PUT") else None,
                                     method=method)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(req, timeout=30) as r:
                data = r.read(_APP_PROXY_MAX_BYTES)
                out_ct = r.headers.get("Content-Type", "application/octet-stream")
                return r.status, out_ct, data
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.headers.get("Content-Type", "text/plain"), e.read(_APP_PROXY_MAX_BYTES)
            except Exception:
                return 502, "text/plain", ("app proxy error: %s" % e).encode()
        except Exception as e:
            return 502, "text/plain", ("app proxy error: %s" % e).encode()

    def state(self):
        s = self.session
        return {
            "ok": True,
            "version": VERSION,
            "device": device_specs(),
            "sandbox_defaults": {"net": "blocked", "timeout_s": 120},
            "stages": stages_mod.list_stages(),
            "session": None if not s else {
                "url": s.url or s.local,
                "commit": getattr(s, "commit", None),
                "verdict": s.report["verdict"],
                "score": s.report["score"],
                "commands": s.commands,
                "open": not s.closed,
                "net": s.net,
                "resumed_from": getattr(s, "resumed_from", None),
            },
        }

    def api_open(self, url, approve=False, net="blocked"):
        with self.lock:
            if self.session and not self.session.closed:
                return {"ok": False, "error": "a session is already open — wipe it first"}
            if net not in ("blocked", "allowed"):
                net = "blocked"
            if os.path.isdir(url):
                sess = sandbox.Session(local=url, net=net, timeout=120)
            else:
                sess = sandbox.Session(url=url, net=net, timeout=120)
            try:
                sess.open()
            except sandbox.SandboxError as e:
                return {"ok": False, "error": str(e)}
            self.session = sess
            rep = sess.report
            tree = sess._tree()
            dangerous = rep["verdict"] in ("high-risk", "dangerous")
            return {
                "ok": True,
                "report": rep,
                "needs_approval": dangerous and not approve,
                "commit": sess.commit,
                "net": sess.net,
                "paths": [rel for rel, _ in tree],
                "tkinter_entry": _tkinter_entry(tree),
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

    def api_file(self, path):
        """Read one file from the sandbox clone for the page's read-only
        viewer: strictly relative, traversal-guarded, size-capped, and it
        never executes anything."""
        s = self.session
        if not s or s.closed:
            return {"ok": False, "error": "no open session"}
        rel = (path or "").strip().replace("\\", "/")
        if not rel or rel.startswith("/") or any(seg in ("", ".", "..") for seg in rel.split("/")):
            return {"ok": False, "error": "bad path"}
        root = os.path.realpath(os.path.join(s.dir, "repo"))
        full = os.path.realpath(os.path.join(root, rel))
        if full != root and not full.startswith(root + os.sep):
            return {"ok": False, "error": "bad path"}
        if os.path.isdir(full):
            return {"ok": False, "error": "not a file: " + rel}
        if not os.path.isfile(full):
            return {"ok": False, "error": "no such file: " + rel}
        size = os.path.getsize(full)
        if size > FILE_VIEW_MAX_BYTES:
            return {"ok": True, "path": rel, "too_big": True, "size": size}
        with open(full, "rb") as fh:
            data = fh.read()
        try:
            text, binary = data.decode("utf-8"), False
        except UnicodeDecodeError:
            text, binary = None, True
        return {"ok": True, "path": rel, "text": text, "binary": binary, "size": size}

    def api_web(self, cmd, net=None):
        """Run the repo as a real web app: a long-lived process with NO command
        timeout (it must keep serving until the operator stops it). If the
        requested network policy differs from the session's, the sandbox is
        re-opened with it — logged honestly, because a re-open is a fresh clone."""
        with self.lock:
            s = self.session
            if not s or s.closed:
                return {"ok": False, "error": "no open session"}
            if net in ("blocked", "allowed") and net != s.net:
                url = s.url or s.local
                was_approved = getattr(s, "approved", False)
                s.close("re-open with net=" + net)
                self.session = None
                s2 = (sandbox.Session(local=url, net=net, timeout=120)
                      if os.path.isdir(url) else sandbox.Session(url=url, net=net, timeout=120))
                try:
                    s2.open()
                except sandbox.SandboxError as e:
                    return {"ok": False, "error": str(e)}
                s2.approved = was_approved
                self.session = s2
                s = s2
                self._log_ext("re-open %s with net=%s (fresh clone — web installs need it)"
                              % (url, net))
            if s.report["verdict"] in ("high-risk", "dangerous") and not getattr(s, "approved", False):
                return {"ok": False, "needs_approval": True,
                        "error": "triage says %s (%d/100) — approve first"
                                 % (s.report["verdict"], s.report["score"])}
            if getattr(s, "active_stream", None) and not s.active_stream.poll()[0]:
                return {"ok": False, "error": "a command is already running — stop it first"}
            try:
                s.execute_stream(cmd, timeout=0)  # 0 = no timeout: servers serve until stopped
            except sandbox.SandboxError as e:
                return {"ok": False, "error": str(e)}
            s.approved = True  # an explicit start by the operator is the approval
            return {"ok": True, "net": s.net}

    def api_web_stop(self):
        s = self.session
        if not s or s.closed:
            return {"ok": True, "stopped": False}
        stream = getattr(s, "active_stream", None)
        if not stream or stream.poll()[0]:
            return {"ok": True, "stopped": False}
        stream.kill()
        return {"ok": True, "stopped": True}

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

    def api_save(self, name):
        with self.lock:
            s = self.session
            if not s or s.closed:
                return {"ok": False, "error": "no open session to save"}
            try:
                res = stages_mod.save(s, name)
            except (stages_mod.StageError, ValueError) as e:
                return {"ok": False, "error": str(e)}
            self._log_ext("save   %s — %d files, %d packages" % (name, res["files"], res["packages"]))
            return res

    def api_resume(self, name):
        with self.lock:
            if self.session and not self.session.closed:
                return {"ok": False, "error": "a session is already open — wipe it first"}
            try:
                sess = stages_mod.resume(name, net="blocked", timeout=120)
            except (stages_mod.StageError, ValueError, sandbox.SandboxError) as e:
                return {"ok": False, "error": str(e)}
            self.session = sess
            self._log_ext("resume %s — fresh sandbox pre-loaded from the saved stage" % name)
            return {"ok": True, "report": sess.report,
                    "resumed_from": sess.resumed_from,
                    "packages": sess.stage_meta.get("packages", 0),
                    "needs_approval": sess.report["verdict"] in ("high-risk", "dangerous")
                                      and not getattr(sess, "approved", False)}

    def api_forget(self, name):
        try:
            res = stages_mod.forget(name)
        except (stages_mod.StageError, ValueError) as e:
            return {"ok": False, "error": str(e)}
        self._log_ext("forget  %s" % name)
        return res

    def _log_ext(self, msg):
        try:
            print("  [%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
        except OSError:
            pass


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

    def _serve_app_proxy(self, port, rest, method, query, body, ctype):
        status, out_ct, data = self.bridge.app_proxy_fetch(
            port, rest, method, body, ctype, query)
        self._raw(status, out_ct, data, {
            "Cross-Origin-Resource-Policy": "cross-origin",
            "Cross-Origin-Embedder-Policy": "credentialless",
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store",
        })

    def _raw(self, status, ctype, data, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        body = {}
        m = re.match(r"^/api/app/(\d+)(/.*)?$", u.path)
        if m:
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            self.bridge.active_web_port = int(m.group(1))
            return self._serve_app_proxy(int(m.group(1)), m.group(2) or "/",
                                         "GET", u.query, None, None)
        # the app's own absolute-path subrequests land at the bridge root;
        # only one app server runs at a time, so route them to it
        if self.bridge.active_web_port and not u.path.startswith("/api/"):
            return self._serve_app_proxy(self.bridge.active_web_port, u.path,
                                         "GET", u.query, None, None)
        if u.path == "/api/status":
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            return self._reply(self.bridge.state())
        if u.path == "/api/output":
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            since = int(qs.get("since", ["0"])[0])
            return self._reply(self.bridge.api_output(since))
        if u.path == "/api/saves":
            if not self._token_ok(qs, body):
                return self._reply({"ok": False, "needs_token": True}, 401)
            return self._reply({"ok": True, "stages": stages_mod.list_stages()})
        return self._reply({"ok": False, "error": "unknown endpoint"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        m = re.match(r"^/api/app/(\d+)(/.*)?$", u.path)
        if m:
            if not self._token_ok(parse_qs(u.query), {}):
                return self._reply({"ok": False, "needs_token": True}, 401)
            self.bridge.active_web_port = int(m.group(1))
            return self._serve_app_proxy(int(m.group(1)), m.group(2) or "/",
                                         "POST", u.query, raw,
                                         self.headers.get("Content-Type"))
        if self.bridge.active_web_port and not u.path.startswith("/api/"):
            return self._serve_app_proxy(self.bridge.active_web_port, u.path,
                                         "POST", u.query, raw,
                                         self.headers.get("Content-Type"))
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return self._reply({"ok": False, "error": "bad json"}, 400)
        if not self._token_ok({}, body):
            return self._reply({"ok": False, "needs_token": True}, 401)

        if u.path == "/api/open":
            url = (body.get("url") or "").strip()
            if not url:
                return self._reply({"ok": False, "error": "url required"}, 400)
            res = self.bridge.api_open(url, approve=bool(body.get("approve")),
                                       net=body.get("net") or "blocked")
            if res.get("ok"):
                rep = res["report"]
                self._log("open   %s — %d/100 %s (net=%s)"
                          % (url, rep["score"], rep["verdict"], res["net"]))
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

        if u.path == "/api/file":
            res = self.bridge.api_file(body.get("path") or "")
            if res.get("ok"):
                self._log("file   %s" % res.get("path", "")[:80])
            return self._reply(res, 200 if res.get("ok") else 409)

        if u.path == "/api/web":
            cmd = (body.get("cmd") or "").strip()
            if not cmd:
                return self._reply({"ok": False, "error": "cmd required"}, 400)
            res = self.bridge.api_web(cmd, net=body.get("net"))
            if res.get("ok"):
                self._log("web    %s (no timeout — stop it from the page)" % cmd[:80])
            return self._reply(res, 200 if res.get("ok") else 409)

        if u.path == "/api/web_stop":
            res = self.bridge.api_web_stop()
            if res.get("stopped"):
                self._log("web    stopped — process tree killed")
            return self._reply(res)

        if u.path == "/api/wipe":
            res = self.bridge.api_wipe()
            self._log("wipe   residue %d" % res.get("residue", -1))
            return self._reply(res)

        if u.path == "/api/save":
            name = (body.get("name") or "").strip()
            if not name:
                return self._reply({"ok": False, "error": "name required"}, 400)
            return self._reply(self.bridge.api_save(name))

        if u.path == "/api/resume":
            name = (body.get("name") or "").strip()
            if not name:
                return self._reply({"ok": False, "error": "name required"}, 400)
            res = self.bridge.api_resume(name)
            return self._reply(res, 200 if res.get("ok") else 409)

        if u.path == "/api/forget":
            name = (body.get("name") or "").strip()
            if not name:
                return self._reply({"ok": False, "error": "name required"}, 400)
            return self._reply(self.bridge.api_forget(name))

        return self._reply({"ok": False, "error": "unknown endpoint"}, 404)

    def _log(self, msg):
        try:
            print("  [%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
        except OSError:
            pass  # a broken console pipe must never break an API response


class _Server(ThreadingHTTPServer):
    # refuse to double-bind: on Windows SO_REUSEADDR lets a second server
    # bind the same port silently and the two then race for connections
    allow_reuse_address = False


def serve(bridge, host="127.0.0.1", port=8641):
    _Handler.bridge = bridge
    server = _Server((host, port), _Handler)
    server.daemon_threads = True
    return server
