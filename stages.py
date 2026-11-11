"""Stage freezes: save a sandbox's exact state, resume it later, or forget it.

Saving is the ONE thing in cleanroom that writes outside the sandbox — it is
opt-in (the user asks for it by name) and lands in ~/.cleanroom/saves/<name>/.
Everything else stays ephemeral; wiping still leaves zero residue.

A freeze preserves FILES (including everything the repo wrote while running)
and the exact PACKAGE LIST from the disposable venv. A live process's memory
cannot be frozen — resuming re-opens a fresh sandbox pre-loaded with the saved
tree, and the saved packages are placed inside as a requirements file so one
pip command (with network allowed) restores the environment.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

import sandbox

NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def saves_root():
    root = os.environ.get("CLEANROOM_SAVES_ROOT")
    if not root:
        root = os.path.join(os.path.expanduser("~"), ".cleanroom", "saves")
    return root


def _stage_dir(name):
    if not NAME_RE.match(name or ""):
        raise ValueError("stage name must be 1-64 chars: letters, digits, . _ -")
    return os.path.join(saves_root(), name)


def _pip_freeze(venv_bin):
    pip = os.path.join(venv_bin, "pip" + (".exe" if sys.platform == "win32" else ""))
    p = subprocess.run([pip, "freeze", "--disable-pip-version-check"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


class StageError(RuntimeError):
    pass


def save(session, name):
    """Freeze an OPEN sandbox under <name>. Returns a summary dict."""
    if session is None or session.closed:
        raise StageError("no open session to save")
    stage = _stage_dir(name)
    if os.path.exists(stage):
        raise StageError("stage '%s' already exists — pick another name or forget it first" % name)

    repo_src = os.path.join(session.dir, "repo")
    repo_dst = os.path.join(stage, "repo")
    started = time.time()
    os.makedirs(stage, exist_ok=True)
    shutil.copytree(
        repo_src, repo_dst,
        ignore=shutil.ignore_patterns(".git", "node_modules", "__pycache__",
                                      ".venv", "venv", "__pypackages__",
                                      "_repl_*", "_cleanroom_stage_packages.txt"))

    packages = []
    venv_bin = session.venv_bin_dir()
    if venv_bin:
        packages = _pip_freeze(venv_bin)
        with open(os.path.join(stage, "requirements-frozen.txt"), "w",
                  encoding="utf-8") as fh:
            fh.write("\n".join(packages) + ("\n" if packages else ""))

    meta = {
        "name": name,
        "source": session.url or session.local,
        "commit": getattr(session, "commit", None),
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "commands_run": session.commands,
        "open_verdict": session.report["verdict"],
        "open_score": session.report["score"],
        "packages": len(packages),
    }
    with open(os.path.join(stage, "stage.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    files = sum(len(f) for _, _, f in os.walk(repo_dst))
    return {
        "ok": True, "name": name, "path": stage,
        "files": files, "packages": len(packages),
        "seconds": round(time.time() - started, 1),
    }


def resume(name, net="blocked", timeout=120):
    """Open a NEW sandbox pre-loaded from a saved stage. Fresh venv; the saved
    package list ships inside the tree as _cleanroom_stage_packages.txt."""
    stage = _stage_dir(name)
    meta_path = os.path.join(stage, "stage.json")
    if not os.path.exists(meta_path):
        raise StageError("no saved stage named '%s'" % name)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)

    sess = sandbox.Session(local=os.path.join(stage, "repo"),
                           net=net, timeout=timeout)
    sess.open()
    sess.resumed_from = {"name": name, "saved_at": meta["saved_at"]}
    pkg_file = os.path.join(stage, "requirements-frozen.txt")
    if os.path.exists(pkg_file):
        shutil.copy(pkg_file, os.path.join(sess.dir, "repo",
                                           "_cleanroom_stage_packages.txt"))
    with open(meta_path, encoding="utf-8") as fh:
        sess.stage_meta = json.load(fh)
    return sess


def forget(name):
    stage = _stage_dir(name)
    if not os.path.exists(stage):
        raise StageError("no saved stage named '%s'" % name)
    shutil.rmtree(stage)
    return {"ok": True, "name": name}


def list_stages():
    root = saves_root()
    out = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        meta_path = os.path.join(root, name, "stage.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            out.append({"name": name, "source": meta.get("source"),
                        "saved_at": meta.get("saved_at"),
                        "packages": meta.get("packages", 0),
                        "commands_run": meta.get("commands_run", 0)})
        except (ValueError, OSError):
            continue
    return out
