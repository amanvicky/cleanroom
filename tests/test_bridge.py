"""Bridge tests: the localhost server that lets the web page drive this PC's
sandbox. Real HTTP, real child processes, real temp dirs — no mocks."""

import json
import os
import threading
import time
import urllib.error
import urllib.request

import pytest

import bridge as bm

def _fixtures():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "fixtures")


def _fixture(name):
    return os.path.join(_fixtures(), name)


@pytest.fixture()
def server():
    b = bm.Bridge()
    srv = bm.serve(b, port=0)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield b, "http://127.0.0.1:%d" % port
    srv.shutdown()


def _get(base, path, token):
    req = urllib.request.Request(base + path,
                                 headers={"X-Cleanroom-Token": token,
                                          "User-Agent": "test"})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read().decode())


def _post(base, path, body, token):
    req = urllib.request.Request(base + path, method="POST",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "X-Cleanroom-Token": token,
                                          "User-Agent": "test"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _drain(base, token, cursor=0, wait_s=10):
    lines = []
    for _ in range(int(wait_s / 0.2)):
        _, out = _get(base, "/api/output?since=%d" % cursor, token)
        lines.extend(out["lines"])
        cursor = out["cursor"]
        if out["done"]:
            return lines, out
        time.sleep(0.2)
    return lines, {"done": False}



def test_bridge_requires_a_token(server):
    _, base = server
    with pytest.raises(urllib.error.HTTPError) as err:
        _get(base, "/api/status?token=WRONG", "WRONG")
    assert err.value.code == 401


def test_status_reports_real_device_specs(server):
    b, base = server
    _, st = _get(base, "/api/status?token=" + b.token, b.token)
    d = st["device"]
    assert isinstance(d["cpu_cores"], int) and d["cpu_cores"] >= 1
    assert d["platform"]
    assert d["python"]
    assert st["session"] is None


def test_full_flow_open_run_output_wipe(server):
    import sandbox
    b, base = server
    before = sandbox.residue_count()
    s, res = _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    assert s == 200 and res["ok"]
    assert res["report"]["verdict"] == "clean"
    s, res = _post(base, "/api/run",
                   {"cmd": 'python -c "print(\'bridge-marker-ok\')"'}, b.token)
    assert s == 200 and res["ok"]
    lines, out = _drain(base, b.token)
    assert any("bridge-marker-ok" in ln for ln in lines)
    assert out["exit_code"] == 0
    s, res = _post(base, "/api/wipe", {}, b.token)
    assert res["ok"] and res["deleted"] is True
    assert sandbox.residue_count() == before


def test_dangerous_repo_needs_explicit_approval(server):
    b, base = server
    s, res = _post(base, "/api/open", {"url": _fixture("sneaky-installer")}, b.token)
    assert res["report"]["verdict"] == "dangerous"
    assert res["needs_approval"] is True
    s, res = _post(base, "/api/run", {"cmd": "echo should-not-run"}, b.token)
    assert s == 409 and res["needs_approval"] is True
    s, res = _post(base, "/api/approve", {}, b.token)
    assert res["ok"]
    s, res = _post(base, "/api/run", {"cmd": "echo approved-run"}, b.token)
    assert s == 200 and res["ok"]
    lines, out = _drain(base, b.token)
    assert any("approved-run" in ln for ln in lines)
    _post(base, "/api/wipe", {}, b.token)


def test_py_mode_uses_repl_echo_semantics(server):
    b, base = server
    _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    s, res = _post(base, "/api/run", {"py": "40 + 2"}, b.token)
    assert s == 200 and res["ok"]
    lines, out = _drain(base, b.token)
    assert any(ln.strip() == "42" for ln in lines)
    _post(base, "/api/wipe", {}, b.token)


def test_file_viewer_reads_guards_and_reports_binary(server):
    b, base = server
    _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    # a real text file comes back readable
    s, res = _post(base, "/api/file", {"path": "gui_notes.py"}, b.token)
    assert s == 200 and res["ok"] and res["binary"] is False
    assert "mainloop" in res["text"]
    # nested paths work
    s, res = _post(base, "/api/file", {"path": "webapp/index.html"}, b.token)
    assert s == 200 and res["ok"] and "replaceChildren" in res["text"]
    # traversal and absolute shapes are refused outright
    for bad in ("../outside.txt", "a/../../outside.txt", "/abs.txt", "..\\win.txt", "dir/"):
        s, res = _post(base, "/api/file", {"path": bad}, b.token)
        assert res["ok"] is False and res["error"] == "bad path", bad
    # a missing file is an honest refusal, not a guess
    s, res = _post(base, "/api/file", {"path": "nope.txt"}, b.token)
    assert res["ok"] is False and "no such file" in res["error"]
    # binary content is detected as binary, never decoded into garbage
    sess = b.session
    with open(os.path.join(sess.dir, "repo", "blob.bin"), "wb") as fh:
        fh.write(b"\x00\x89PNG\r\n\x1a\n" + bytes(range(256)))
    s, res = _post(base, "/api/file", {"path": "blob.bin"}, b.token)
    assert res["ok"] and res["binary"] is True and res["text"] is None
    # after the wipe there is nothing to read from
    _post(base, "/api/wipe", {}, b.token)
    s, res = _post(base, "/api/file", {"path": "app.py"}, b.token)
    assert res["ok"] is False and "no open session" in res["error"]


WEB_PORT = 8747


def _free_port():
    """A port that is actually free RIGHT NOW. Fixed test ports stack orphan
    listeners on Windows (SO_REUSEADDR lets servers double-bind silently)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_web_app_serves_stops_and_wipes(server):
    """The app-screen promise for this-PC mode, end to end: start a real
    server in the sandbox, prove it serves a real port (the browser iframe
    will load exactly this), stop it via the process tree, wipe clean."""
    import sandbox
    b, base = server
    port = _free_port()
    before = sandbox.residue_count()
    s, res = _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    assert s == 200 and res["ok"]
    assert res["net"] == "blocked"
    assert res["tkinter_entry"] == "gui_notes.py"
    assert "webapp/index.html" in res["paths"]

    s, res = _post(base, "/api/web",
                   {"cmd": "python -m http.server %d --bind 127.0.0.1" % port}, b.token)
    assert s == 200 and res["ok"] and res["net"] == "blocked"

    lines = []
    deadline = time.time() + 10
    while time.time() < deadline:
        _, out = _get(base, "/api/output?since=0", b.token)
        lines = out["lines"]
        if any(str(port) in ln for ln in lines):
            break
        time.sleep(0.2)
    assert any(str(port) in ln for ln in lines), lines

    # the app really serves — the browser iframe will load exactly this.
    # bypass any system proxy: 127.0.0.1 must never leave the machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:%d/" % port, timeout=5) as r:
        assert r.status == 200

    s, res = _post(base, "/api/web_stop", {}, b.token)
    assert res["ok"] and res["stopped"] is True
    time.sleep(0.5)

    # after a stop, an ordinary command still works in the same sandbox
    s, res = _post(base, "/api/run", {"cmd": "echo after-stop"}, b.token)
    assert s == 200 and res["ok"]
    lines, out = _drain(base, b.token)
    assert any("after-stop" in ln for ln in lines)

    s, res = _post(base, "/api/wipe", {}, b.token)
    assert res["ok"] and res["deleted"] is True
    assert sandbox.residue_count() == before


def test_web_start_reopens_sandbox_with_network_allowed(server):
    b, base = server
    port = _free_port()
    _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    s, res = _post(base, "/api/web",
                   {"cmd": "python -m http.server %d --bind 127.0.0.1" % port,
                    "net": "allowed"}, b.token)
    assert s == 200 and res["ok"] and res["net"] == "allowed"
    _, st = _get(base, "/api/status?token=" + b.token, b.token)
    assert st["session"]["net"] == "allowed"
    time.sleep(0.4)
    _post(base, "/api/web_stop", {}, b.token)
    _post(base, "/api/wipe", {}, b.token)


def test_app_proxy_streams_the_running_app_with_embed_headers(server):
    """An isolated page may only embed responses carrying CORP/COEP headers,
    which the app's own server doesn't send — the bridge streams them and
    adds the headers itself, and routes the app's own absolute-path
    subrequests to the remembered port."""
    import sandbox
    b, base = server
    port = _free_port()
    _post(base, "/api/open", {"url": _fixture("benign-notes-app")}, b.token)
    _post(base, "/api/web",
          {"cmd": "python -m http.server %d --bind 127.0.0.1" % port}, b.token)
    deadline = time.time() + 10
    while time.time() < deadline:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})) \
                .open("http://127.0.0.1:%d/" % port, timeout=2) as r:
            if r.status == 200:
                break
        time.sleep(0.2)

    # the token-gated proxy entry streams the document with embed headers
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(base + "/api/app/%d/?token=%s" % (port, b.token), timeout=10) as r:
        assert r.status == 200
        assert r.headers.get("Cross-Origin-Resource-Policy") == "cross-origin"
        assert r.headers.get("Cross-Origin-Embedder-Policy") == "credentialless"
        assert b"gui_notes.py" in r.read()  # the fixture's root is a listing
    # a real document through the same proxy path
    with opener.open(base + "/api/app/%d/webapp/index.html?token=%s" % (port, b.token),
                     timeout=10) as r:
        assert r.status == 200 and b"Notes" in r.read()
    # no token, no proxy tunnel to an arbitrary local port
    try:
        opener.open(base + "/api/app/%d/" % port, timeout=10)
        raise AssertionError("proxy without token must be refused")
    except urllib.error.HTTPError as e:
        assert e.code == 401
    # the app's own absolute-path subrequest lands at the bridge root and is
    # routed to the remembered port
    with opener.open(base + "/webapp/index.html", timeout=10) as r:
        assert r.status == 200 and b"replaceChildren" in r.read()
    _post(base, "/api/web_stop", {}, b.token)
    _post(base, "/api/wipe", {}, b.token)


def test_web_start_needs_approval_for_dangerous(server):
    b, base = server
    _post(base, "/api/open", {"url": _fixture("sneaky-installer")}, b.token)
    s, res = _post(base, "/api/web", {"cmd": "echo nope"}, b.token)
    assert s == 409 and res["needs_approval"] is True
    _post(base, "/api/wipe", {}, b.token)
