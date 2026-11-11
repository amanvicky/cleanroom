"""Ephemeral sandbox sessions: clone into a temp dir, run commands with a
scrubbed environment, optionally block outbound network for Python children,
then wipe everything and verify the wipe.

Design notes (all honest, documented in the README limitations table):
- The temp dir is a tempfile.mkdtemp() under the OS temp root, removed with
  shutil.rmtree(ignore_errors=False) after children are killed.
- Children receive a WHITELIST environment (about 15 safe variables). Every
  other variable — including every secret-shaped one — never reaches them.
- The network guard is a generated sitecustomize.py on the child's PYTHONPATH:
  it patches socket so non-local connects raise. It is enforced for Python
  children only; other runtimes get triage reporting instead (stated, not hidden).
- Teardown kills the child tree (taskkill /T /F on Windows, killpg on posix),
  deletes the temp dir, then re-checks the temp root for cleanroom-* leftovers.
"""

import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

import triage

# Variables a child may see. Everything else is withheld.
KEEP_ENV = {
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
    "HOME", "USERPROFILE", "LANG", "LC_ALL", "TZ", "PYTHONIOENCODING",
    "PYTHONUTF8", "NO_COLOR", "TERM", "PROGRAMFILES", "PROCESSOR_ARCHITECTURE",
}

SECRET_SHAPE = re.compile(
    r"(?i)(token|secret|password|passwd|credential|private|api_?key|auth)"
    r"|^(AWS|GITHUB|GIT_|AZURE|GOOGLE|OPENAI|ANTHROPIC|SLACK|NPM|PYPI|HF_|STRIPE|SSH|DOCKER|JWT)")

MAX_SCAN_FILES = 2000
MAX_SCAN_BYTES = 512 * 1024

NETWORK_GUARD = '''# cleanroom network guard — generated inside the sandbox, lives only there.
import socket as _s

class _Blocked(Exception):
    pass

def _is_local(host):
    try:
        if host is None:
            return False
        h = str(host)
        return h in ("localhost", "127.0.0.1", "::1") or h.startswith("127.")
    except Exception:
        return False

def _deny(*a, **k):
    raise _Blocked(
        "cleanroom: outbound network is disabled in this sandbox (run with --net to allow)")

class _GuardedSocket(_s.socket):
    def __init__(self, *a, **k):
        fam = a[0] if a else k.get("family", _s.AF_INET)
        if fam not in (_s.AF_INET, _s.AF_INET6):
            raise _Blocked("cleanroom: sockets are disabled in this sandbox")
        super().__init__(*a, **k)

    def connect(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else None
        if not _is_local(host):
            raise _Blocked(
                "cleanroom: outbound network is disabled in this sandbox (run with --net to allow)")
        return super().connect(address, *a, **k)

    def connect_ex(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) and len(address) > 0 else None
        if not _is_local(host):
            raise _Blocked(
                "cleanroom: outbound network is disabled in this sandbox (run with --net to allow)")
        return super().connect_ex(address, *a, **k)

_s.socket = _GuardedSocket

def _guarded_create_connection(address, *a, **k):
    host = address[0] if address else None
    if not _is_local(host):
        raise _Blocked(
            "cleanroom: outbound network is disabled in this sandbox (run with --net to allow)")
    return _orig_create_connection(address, *a, **k)

_orig_create_connection = _s.create_connection
_s.create_connection = _guarded_create_connection
'''


def scrub_env():
    """Return (child_env, blocked_names). Whitelist-only: the child sees KEEP_ENV
    (when present) and nothing else."""
    child = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    blocked = [k for k in os.environ if k not in KEEP_ENV and SECRET_SHAPE.search(k)]
    return child, sorted(blocked)


def _kill_tree(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _rmtree_robust(path, attempts=6):
    """Delete a tree even when Windows still holds file handles a moment
    after a child was killed. Returns True when the tree is really gone."""
    for i in range(attempts):
        try:
            shutil.rmtree(path)
            return True
        except (PermissionError, OSError):
            time.sleep(0.25 * (i + 1))
            try:
                for root, dirs, files in os.walk(path):
                    for name in dirs + files:
                        try:
                            os.chmod(os.path.join(root, name), 0o700)
                        except OSError:
                            pass
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)
    return not os.path.exists(path)


class SandboxError(RuntimeError):
    pass


class Session:
    """One disposable cleanroom. open() → report() → execute()* → close()."""

    def __init__(self, url=None, local=None, net="blocked", timeout=120, runner=None,
                 use_venv=True):
        self.url = url
        self.local = os.path.abspath(local) if local else None
        self.net = net          # "blocked" | "allowed"
        self.timeout = timeout  # seconds per command
        self.runner = runner or sys.executable
        self.use_venv = use_venv
        self._venv_ready = False
        self.venv_note = None   # set if the venv could not be created
        self.dir = None
        self.child = None
        self._timer = None
        self.blocked_env = []
        self.commands = 0
        self.started_at = None
        self.closed = False
        self.close_reason = None
        self.kill_reason = None
        self.findings_count = None

    # -- lifecycle ----------------------------------------------------------

    def open(self):
        if not self.url and not self.local:
            raise SandboxError("Session needs a url or a local directory")
        self.dir = tempfile.mkdtemp(prefix="cleanroom-")
        self.started_at = time.time()
        target = os.path.join(self.dir, "repo")
        try:
            if self.local:
                shutil.copytree(
                    self.local, target,
                    ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__",
                                                  ".venv", "venv", "__pypackages__"))
                self.commit = "local"
            else:
                p = subprocess.run(
                    ["git", "clone", "--depth", "1", "--single-branch", "--quiet",
                     self.url, target],
                    capture_output=True, text=True)
                if p.returncode != 0:
                    err = (p.stderr or "").strip().splitlines()
                    raise SandboxError("clone failed: " + (err[-1] if err else "unknown error"))
                self.commit = subprocess.run(
                    ["git", "-C", target, "rev-parse", "--short", "HEAD"],
                    capture_output=True, text=True).stdout.strip()
        except BaseException:
            shutil.rmtree(self.dir, ignore_errors=True)
            self.dir = None
            raise
        self.report = triage.scan_tree(self._tree())
        self.findings_count = len(self.report["findings"])
        self.blocked_env = scrub_env()[1]
        return self

    def _tree(self):
        root = os.path.join(self.dir, "repo")
        files = []
        n = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    if os.path.getsize(full) > MAX_SCAN_BYTES:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        text = fh.read()
                except OSError:
                    continue
                files.append((rel, text))
                n += 1
                if n >= MAX_SCAN_FILES:
                    return files
        return files

    # -- execution ----------------------------------------------------------

    def _ensure_venv(self):
        """Create a throwaway venv INSIDE the sandbox. pip installs — even with
        --net — land here and die with the wipe; the real Python is untouchable."""
        if self._venv_ready or not self.use_venv or not self.dir:
            return
        venv_dir = os.path.join(self.dir, "venv")
        p = subprocess.run([sys.executable, "-m", "venv", venv_dir],
                           capture_output=True, text=True)
        if p.returncode == 0 and os.path.isdir(venv_dir):
            self._venv_ready = True
        else:
            self.use_venv = False
            self.venv_note = "venv creation failed — children use the host interpreter"

    def venv_bin_dir(self):
        if not self._venv_ready:
            return None
        return os.path.join(self.dir, "venv",
                            "Scripts" if sys.platform == "win32" else "bin")

    def _child_env(self):
        self._ensure_venv()
        env, _ = scrub_env()
        real_py = os.path.dirname(sys.executable)
        if self._venv_ready:
            # venv first: `python` and `pip` inside the sandbox are the
            # disposable ones; the host interpreter stays as a fallback
            vbin = self.venv_bin_dir()
            env["PATH"] = vbin + os.pathsep + real_py + os.pathsep + env.get("PATH", "")
            env["VIRTUAL_ENV"] = os.path.dirname(vbin)
        else:
            # real interpreter first so `python` is the CLI's binary, not a
            # Store/installer stub that would download a runtime
            env["PATH"] = real_py + os.pathsep + env.get("PATH", "")
        if self.net == "blocked":
            guard_dir = os.path.join(self.dir, "guard")
            os.makedirs(guard_dir, exist_ok=True)
            with open(os.path.join(guard_dir, "sitecustomize.py"), "w",
                      encoding="utf-8") as fh:
                fh.write(NETWORK_GUARD)
            env["PYTHONPATH"] = guard_dir
        return env

    def execute(self, cmd, capture=False):
        """Run one shell command inside the sandbox. Returns
        (returncode, stdout, stderr, seconds); streams instead when capture=False."""
        if self.closed:
            raise SandboxError("session is closed")
        env = self._child_env()
        t0 = time.time()
        self.commands += 1
        proc = subprocess.Popen(cmd, cwd=os.path.join(self.dir, "repo"),
                                env=env, shell=True,
                                start_new_session=(sys.platform != "win32"),
                                stdout=(subprocess.PIPE if capture else None),
                                stderr=(subprocess.PIPE if capture else None),
                                text=(capture or None),
                                errors="replace" if capture else None)
        self.child = proc

        def _on_timeout():
            self.kill_reason = "timeout (%ds)" % self.timeout
            _kill_tree(proc.pid)

        timer = threading.Timer(self.timeout, _on_timeout)
        self._timer = timer
        timer.start()
        try:
            out, err = proc.communicate() if capture else (None, None)
        except KeyboardInterrupt:
            _kill_tree(proc.pid)
            out, err = proc.communicate() if capture else (None, None)
            self.kill_reason = "interrupt"
        finally:
            timer.cancel()
        seconds = time.time() - t0
        return proc.returncode, out or "", err or "", seconds

    # -- teardown -----------------------------------------------------------

    def close(self, reason="exit"):
        """Kill children, delete the temp dir, verify the wipe. Idempotent."""
        audit = {
            "url": self.url or self.local,
            "commit": getattr(self, "commit", None),
            "commands": self.commands,
            "blocked_env": self.blocked_env,
            "kill_reason": self.kill_reason,
            "duration_s": round(time.time() - self.started_at, 1) if self.started_at else 0.0,
        }
        if self.closed:
            audit["already_closed"] = True
            return audit
        if self.child and self.child.poll() is None:
            _kill_tree(self.child.pid)
            try:
                self.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.child.kill()
        stream = getattr(self, "active_stream", None)
        if stream:
            stream._done.wait(5)
        if self._timer:
            self._timer.cancel()
        deleted = False
        if self.dir and os.path.isdir(self.dir):
            deleted = _rmtree_robust(self.dir)
        residue = sorted(n for n in os.listdir(tempfile.gettempdir())
                         if n.startswith("cleanroom-"))
        audit.update({
            "reason": reason,
            "temp_dir": self.dir,
            "deleted": deleted,
            "residue": residue,
        })
        self.closed = True
        self.close_reason = reason
        self.dir = None
        return audit


def residue_count():
    tmp = tempfile.gettempdir()
    return len([n for n in os.listdir(tmp) if n.startswith("cleanroom-")])


class StreamHandle:
    """A running sandbox command whose output can be polled as it streams."""

    def __init__(self, session, proc, buf, lock, done, result):
        self._session = session
        self._proc = proc
        self._buf = buf
        self._lock = lock
        self._done = done
        self._result = result

    def lines_since(self, n):
        with self._lock:
            return list(self._buf[n:]), len(self._buf)

    def poll(self):
        return self._done.is_set(), self._result.get("code")

    def wait(self, timeout=None):
        self._done.wait(timeout)
        return self.poll()

    def kill(self):
        _kill_tree(self._proc.pid)
        self._done.wait(5)


def execute_stream(self, cmd):
    """Start one command inside the sandbox and return a StreamHandle whose
    output arrives line by line. Only one streamed command at a time."""
    if self.closed:
        raise SandboxError("session is closed")
    if getattr(self, "active_stream", None) and not self.active_stream.poll()[0]:
        raise SandboxError("a command is already running")
    env = self._child_env()
    self.commands += 1
    self.kill_reason = None
    proc = subprocess.Popen(
        cmd, cwd=os.path.join(self.dir, "repo"), env=env, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
        start_new_session=(sys.platform != "win32"))
    buf, lock, done, result = [], threading.Lock(), threading.Event(), {"code": None}

    def _reader():
        try:
            for raw in proc.stdout:
                with lock:
                    buf.append(raw.rstrip("\r\n"))
        finally:
            result["code"] = proc.wait()
            done.set()

    threading.Thread(target=_reader, daemon=True).start()
    self.child = proc

    def _on_timeout():
        self.kill_reason = "timeout (%ds)" % self.timeout
        _kill_tree(proc.pid)

    timer = threading.Timer(self.timeout, _on_timeout)
    self._timer = timer
    timer.start()
    handle = StreamHandle(self, proc, buf, lock, done, result)
    self.active_stream = handle
    return handle


Session.execute_stream = execute_stream
