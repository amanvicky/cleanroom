"""Tests for cleanroom: triage rules, sandbox guarantees, teardown honesty.

The sandbox tests run REAL child processes on REAL temp dirs — no mocks —
because the whole point is what actually happens on your machine.
"""

import json
import os
import sys
import time

import pytest

import sandbox
import triage
from cleanroom import render_report, before_you_run

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


def fixture_tree(name):
    root = os.path.join(FIXTURES, name)
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                files.append((rel, fh.read()))
    return files


# --- triage -----------------------------------------------------------------

def test_benign_fixture_is_clean():
    rep = triage.scan_tree(fixture_tree("benign-notes-app"))
    assert rep["score"] == 0
    assert rep["verdict"] == "clean"
    assert not [f for f in rep["findings"] if f["severity"] != "info"]


def test_sneaky_fixture_is_dangerous():
    rep = triage.scan_tree(fixture_tree("sneaky-installer"))
    assert rep["verdict"] == "dangerous"
    rules = {f["rule"] for f in rep["findings"]}
    assert {"AWS_KEY", "INSTALL_HOOK", "PERSISTENCE", "DYNAMIC_EXEC_PY",
            "TYPOSQUAT_HINT", "LONG_B64", "PIPED_INSTALL"} <= rules
    assert "AWS_SECRET_ACCESS_KEY" in rep["touched_env"]


def test_pinned_expectations_match():
    """The rule engine must not drift: findings are pinned to expected.json."""
    with open(os.path.join(FIXTURES, "expected.json"), encoding="utf-8") as fh:
        expected = json.load(fh)
    for name, exp in expected.items():
        rep = triage.scan_tree(fixture_tree(name))
        assert rep["score"] == exp["score"], name
        assert rep["verdict"] == exp["verdict"], name
        got = [[f["rule"], f["file"], f["line"]] for f in rep["findings"]]
        assert got == exp["findings"], name


@pytest.mark.parametrize("pattern,label", [
    ("key = \"AKIAIOSFODNN7EXAMPLE\"", "AWS_KEY"),
    ("-----BEGIN RSA PRIVATE KEY-----", "PRIVATE_KEY"),
    ("t = \"ghp_" + "A" * 36 + "\"", "GH_TOKEN"),
    ("c = \"AIza" + "A" * 35 + "\"", "GOOGLE_KEY"),
    ("s = \"sk_live_" + "A" * 16 + "\"", "STRIPE_KEY"),
])
def test_secret_patterns(pattern, label):
    rep = triage.scan_tree([("app.py", pattern)])
    assert label in {f["rule"] for f in rep["findings"]}


def test_css_custom_property_is_not_a_secret():
    # the real-world miss behind the rule: chart style tokens in a Rails
    # component (`token: "--color-success"`) are color names, not credentials
    chart = ('LOAN_SERIES_STYLES = { "actual" => { token: "--color-success", '
             'dashed: false } }\nPALETTE = { saved: { token: "--color-warning" } }\n')
    rep = triage.scan_tree([("app/components/UI/account/chart.rb", chart)])
    assert not any(f["rule"] == "HARDCODED_SECRET" for f in rep["findings"])
    # a genuinely secret-shaped value on the same keyword still fires
    rep2 = triage.scan_tree([("settings.rb", 'auth_token = "s3cr3t-value-123456"')])
    assert any(f["rule"] == "HARDCODED_SECRET" for f in rep2["findings"])


def test_typosquat_flags_lookalikes_not_the_real_package():
    rep = triage.scan_tree([("requirements.txt", "reqeusts==2.31.0\nrequests==2.31.0\n")])
    flagged = [f["snippet"] for f in rep["findings"] if f["rule"] == "TYPOSQUAT_HINT"]
    assert any(s.startswith("reqeusts") for s in flagged)
    assert not any(s.startswith("requests ≈") for s in flagged)


def test_install_hooks_detected():
    pkg = json.dumps({"scripts": {"postinstall": "node hack.js"}, "name": "x"})
    rep = triage.scan_tree([("package.json", pkg), ("setup.py", "setup(cmdclass={'x': X})")])
    rules = {f["rule"] for f in rep["findings"]}
    assert "INSTALL_HOOK" in rules and "SETUP_HOOK" in rules


def test_verdict_steps_are_monotonic():
    labels = [triage.verdict_for(s) for s in (0, 9, 10, 24, 25, 59, 60, 100)]
    assert labels == ["clean", "clean", "caution", "caution",
                      "high-risk", "high-risk", "dangerous", "dangerous"]


def test_report_renders_and_json_serializes():
    rep = triage.scan_tree(fixture_tree("sneaky-installer"))
    text = render_report(rep, "unit-test")
    assert "RISK SCORE" in text and "DANGEROUS" in text
    for step in before_you_run(rep):
        assert isinstance(step, str)
    json.dumps(rep)  # must be agent-consumable


# --- sandbox (real processes, real temp dirs) -------------------------------

@pytest.fixture()
def local_fixture():
    return os.path.join(FIXTURES, "benign-notes-app")


def test_env_scrub_hides_secret_shaped_vars(local_fixture, monkeypatch):
    monkeypatch.setenv("CLEANROOM_TEST_TOKEN", "must-never-leak")
    monkeypatch.setenv("MY_APP_PASSWORD", "also-secret-shaped")
    s = sandbox.Session(local=local_fixture)
    s.open()
    assert "CLEANROOM_TEST_TOKEN" in s.blocked_env
    assert "MY_APP_PASSWORD" in s.blocked_env
    code, out, _, _ = s.execute(
        'python -c "import os; print(os.environ.get(\'CLEANROOM_TEST_TOKEN\', \'ABSENT\'))"',
        capture=True)
    s.close("test")
    assert code == 0
    assert "ABSENT" in out and "must-never-leak" not in out


def test_child_env_is_a_whitelist(local_fixture, monkeypatch):
    monkeypatch.setenv("CLEANROOM_TEST_TOKEN", "x")
    s = sandbox.Session(local=local_fixture)
    s.open()
    code, out, _, _ = s.execute(
        'python -c "import os, json; print(json.dumps(sorted(os.environ)))"',
        capture=True)
    s.close("test")
    assert code == 0
    keys = json.loads(out.strip().splitlines()[-1])
    assert not any(sandbox.SECRET_SHAPE.search(k) for k in keys)
    assert "CLEANROOM_TEST_TOKEN" not in keys


def test_temp_dir_wiped_and_residue_verified(local_fixture):
    before = sandbox.residue_count()
    s = sandbox.Session(local=local_fixture)
    s.open()
    target = s.dir
    assert os.path.isdir(target)
    audit = s.close("test")
    assert audit["deleted"] is True
    assert not os.path.exists(target)
    assert sandbox.residue_count() == before


def test_failed_open_does_not_leak_a_temp_dir():
    s = sandbox.Session(url="https://github.com/amanvicky/no-such-repo-cleanroom-selftest-404")
    with pytest.raises(sandbox.SandboxError):
        s.open()
    assert s.dir is None


def test_timeout_kills_a_runaway_child(local_fixture):
    s = sandbox.Session(local=local_fixture, timeout=2)
    s.open()
    t0 = time.time()
    code, _, _, secs = s.execute('python -c "import time; time.sleep(30)"', capture=True)
    wall = time.time() - t0
    s.close("test")
    assert code != 0
    assert "timeout" in (s.kill_reason or "")
    assert wall < 20


def test_network_guard_blocks_outbound(local_fixture):
    s = sandbox.Session(local=local_fixture, net="blocked")
    s.open()
    code, out, err, _ = s.execute(
        'python -c "import socket; socket.create_connection((\'example.com\', 80), 3)"',
        capture=True)
    s.close("test")
    assert code != 0
    assert "cleanroom" in err


def test_network_guard_allows_localhost(local_fixture):
    s = sandbox.Session(local=local_fixture, net="blocked")
    s.open()
    code, out, err, _ = s.execute(
        'python -c "import socket; a=socket.socket(); a.bind((\'127.0.0.1\',0)); '
        'a.listen(1); p=socket.socket(); p.connect(a.getsockname()); print(\'LOCAL-OK\')"',
        capture=True)
    s.close("test")
    assert code == 0
    assert "LOCAL-OK" in out


def test_cli_scan_json(local_fixture):
    import subprocess
    p = subprocess.run([sys.executable, "-X", "utf8", "cleanroom.py", "scan",
                        os.path.join(FIXTURES, "sneaky-installer"), "--json"],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=os.path.dirname(FIXTURES))
    assert p.returncode == 0
    rep = json.loads(p.stdout)
    assert rep["verdict"] == "dangerous"
    assert rep["score"] == 100
