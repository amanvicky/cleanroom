#!/usr/bin/env python3
"""cleanroom — run a GitHub repo in a disposable sandbox and leave zero trace.

    python cleanroom.py scan  <url-or-local-path>   # clone → triage → wipe
    python cleanroom.py run   <url> [--cmd "..."]   # triage → sandbox → wipe
    python cleanroom.py scan  <url> --json          # machine-readable (for agents)

The sandbox: a temp-dir clone, a whitelist environment (children never see your
secrets), an optional outbound-network guard for Python children, a per-command
timeout, and a teardown that deletes everything and verifies the deletion.

This is a best-effort containment tool, not a VM. Read the threat-model section
in the README before trusting it with genuinely hostile code. Use it on your
own responsibility.
"""

import argparse
import json
import os
import sys

import sandbox
import triage

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SEV_TAG = {
    "critical": "CRITICAL", "high": "HIGH    ", "medium": "MED     ", "info": "info    ",
}
SEV_COLOR_ON = True


def _c(s, code):
    return "\x1b[%sm%s\x1b[0m" % (code, s) if SEV_COLOR_ON else s


def render_report(rep, title):
    lines = []
    lines.append("TRIAGE REPORT — %s" % title)
    lines.append("scanned %d files  ·  %d findings" % (rep["files_scanned"], len(rep["findings"])))
    if rep["by_category"]:
        lines.append("categories: " + "  ".join(
            "%s=%d" % (k, v) for k, v in rep["by_category"].items()))
    lines.append("-" * 72)

    scored = [f for f in rep["findings"] if f["severity"] != "info"]
    infos = [f for f in rep["findings"] if f["severity"] == "info"]
    for f in scored:
        tag = SEV_TAG[f["severity"]]
        color = {"critical": "31;1", "high": "33;1", "medium": "35"}.get(f["severity"], "0")
        lines.append("  %s  %-12s %-16s %s:%d" % (
            _c(tag, color), f["category"], f["rule"], f["file"], f["line"]))
        lines.append("      └ %s" % f["snippet"][:90])
    if infos:
        by_rule = {}
        for f in infos:
            by_rule.setdefault((f["rule"], f["category"]), []).append(f)
        for (rule, cat), fs in sorted(by_rule.items()):
            where = ", ".join(sorted({f["file"] for f in fs})[:5])
            extra = ""
            if rule == "ENV_READS":
                extra = " → " + ", ".join(rep["touched_env"][:12])
            lines.append("  %-8s  %-12s %-16s %d hit(s) in %s%s" % (
                _c("info", "36"), cat, rule, len(fs), where, extra))
    lines.append("-" * 72)
    lines.append("RISK SCORE %d/100 — %s" % (rep["score"], rep["verdict"].upper()))
    if rep["touched_env"]:
        lines.append("env vars it reads: " + ", ".join(rep["touched_env"][:15]))
    return "\n".join(lines)


def before_you_run(rep):
    """Deterministic plain-language checklist generated from findings."""
    rules = {f["rule"] for f in rep["findings"]}
    cats = set(rep["by_category"])
    steps = []
    if "INSTALL_HOOK" in rules:
        steps.append("this repo runs code at INSTALL time (npm pre/postinstall) — "
                     "that code executes before anyone reviews it")
    if "SETUP_HOOK" in rules:
        steps.append("setup.py overrides build/install steps — read them first")
    if cats & {"secrets"}:
        steps.append("credential-shaped strings are present — assume any key you "
                     "can see, attackers can see too")
    if "TYPOSQUAT_HINT" in rules:
        steps.append("a dependency name closely mimics a popular package — verify "
                     "the spelling on the registry page before installing")
    if "PERSISTENCE" in rules:
        steps.append("code touches shell profiles / schedulers — that survives "
                     "your session even if the sandbox is wiped")
    if cats & {"exec"}:
        steps.append("dynamic execution (eval/exec/pickle) — obfuscated payloads "
                     "hide behind these calls; read the surrounding lines")
    if cats & {"network"}:
        steps.append("it phones home — run with the network guard and check WHERE "
                     "it connects if the behavior matters to you")
    if not steps:
        steps.append("no red flags in static triage — static analysis can't see "
                     "everything, so treat 'clean' as 'nothing obvious', not 'safe'")
    return steps


def _is_url(target):
    return "://" in target or target.startswith("git@")


def cmd_scan(args):
    local = not _is_url(args.target)
    if local:
        if not os.path.isdir(args.target):
            print("not a directory: %s" % args.target)
            return 2
        files = []
        root = os.path.abspath(args.target)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules", "__pycache__")]
            for name in sorted(filenames):
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    if os.path.getsize(full) > sandbox.MAX_SCAN_BYTES:
                        continue
                    with open(full, "r", encoding="utf-8", errors="replace") as fh:
                        files.append((rel, fh.read()))
                except OSError:
                    continue
        rep = triage.scan_tree(files)
        title = os.path.basename(root) + " (local)"
        if args.json:
            rep = dict(rep)
            rep["target"] = title
            print(json.dumps(rep, indent=2, sort_keys=True))
            return 0
        print(render_report(rep, title))
        for s in before_you_run(rep):
            print("  · " + s)
        return 0

    sess = sandbox.Session(url=args.target)
    print("== cleanroom ==")
    print("[1/2] clone    %s" % args.target)
    try:
        sess.open()
    except sandbox.SandboxError as e:
        print("      " + str(e))
        return 2
    print("      ok — commit %s" % sess.commit)
    rep = sess.report
    if args.json:
        rep = dict(rep)
        rep["target"] = args.target
        rep["commit"] = sess.commit
        print(json.dumps(rep, indent=2, sort_keys=True))
        audit = sess.close("scan-done")
        print("[2/2] wipe     temp dir removed [%s]  residue: %d" % (
            "ok" if audit["deleted"] else "FAILED", len(audit["residue"])))
        return 0
    print("[2/2] triage")
    print()
    print(render_report(rep, args.target))
    print()
    for s in before_you_run(rep):
        print("  · " + s)
    print()
    audit = sess.close("scan-done")
    print("wipe      temp dir removed [%s]  residue scan: %d entries" % (
        "ok" if audit["deleted"] else "FAILED", len(audit["residue"])))
    return 0


def cmd_run(args):
    is_local = os.path.isdir(args.target)
    sess = sandbox.Session(url=None if is_local else args.target,
                           local=args.target if is_local else None,
                           net=("allowed" if args.net else "blocked"),
                           timeout=args.timeout)
    print("== cleanroom ==")
    print("[1/4] %s    %s" % ("copy    " if is_local else "clone", args.target))
    try:
        sess.open()
    except sandbox.SandboxError as e:
        print("      " + str(e))
        return 2
    print("      ok — commit %s, sandbox is a temp dir that will be deleted on exit" % sess.commit)
    print("[2/4] triage")
    rep = sess.report
    print(render_report(rep, args.target))
    if rep["verdict"] in ("high-risk", "dangerous") and not args.yes:
        try:
            ans = input("run anyway inside the sandbox? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            audit = sess.close("declined")
            print("declined — sandbox wiped [%s], residue: %d" % (
                "ok" if audit["deleted"] else "FAILED", len(audit["residue"])))
            return 1
    env, blocked = sandbox.scrub_env()
    print("[3/4] sandbox env: whitelist of %d vars; %d secret-shaped var(s) hidden%s" % (
        len(env), len(blocked),
        (" (" + ", ".join(blocked[:6]) + ("…" if len(blocked) > 6 else "") + ")") if blocked else ""))
    print("      network: %s%s" % (
        "blocked" if sess.net == "blocked" else "ALLOWED by flag",
        " (python children via sitecustomize guard; other runtimes: reported-only)"
        if sess.net == "blocked" else ""))
    print("      per-command timeout: %ds" % sess.timeout)
    print("      commands run inside the sandbox. type 'exit' to close and wipe everything.")

    if args.cmd:
        code, out, err, secs = sess.execute(args.cmd, capture=True)
        if out:
            print(out, end="" if out.endswith("\n") else "\n")
        if err:
            print(err, end="" if err.endswith("\n") else "\n")
        print("      → exit code %s in %.1fs%s" % (
            code, secs, (" — " + sess.kill_reason) if sess.kill_reason else ""))
        sess.close("done")
        _print_audit(sess, args.target)
        return 0 if code == 0 else 1

    code = 0
    try:
        while True:
            try:
                line = input("cleanroom> ").strip()
            except EOFError:
                break
            if not line:
                continue
            if line in ("exit", "quit", "q"):
                break
            if line in ("help", "?"):
                print("  <command>   run it inside the sandbox (shell)")
                print("  report      show the triage report again")
                print("  exit        kill everything and wipe the sandbox")
                continue
            if line == "report":
                print(render_report(rep, args.target))
                continue
            try:
                code, _, _, secs = sess.execute(line)
            except sandbox.SandboxError as e:
                print("  " + str(e))
                continue
            print("  → exit code %s in %.1fs%s" % (
                code, secs, (" — " + sess.kill_reason) if sess.kill_reason else ""))
            sess.kill_reason = None
    except KeyboardInterrupt:
        print("\n(interrupted)")
    finally:
        audit = sess.close("exit")
        print("[4/4] wipe     temp dir removed [%s]  residue scan: %d entries" % (
            "ok" if audit["deleted"] else "FAILED", len(audit["residue"])))
        _print_audit(sess, args.target)
    return code


def _print_audit(sess, target):
    print("session summary")
    print("  repo        %s @ %s" % (target, getattr(sess, "commit", "?")))
    print("  commands    %d" % sess.commands)
    print("  env hidden  %s" % (", ".join(sess.blocked_env[:8]) or "none secret-shaped in your env"))
    print("  network     %s" % ("blocked (python children)" if sess.net == "blocked" else "allowed"))
    print("nothing was written outside the sandbox. you were never here.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="cleanroom",
                                 description="run a GitHub repo in a disposable sandbox, leave zero trace")
    sub = ap.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="clone → triage report → wipe (no execution)")
    scan.add_argument("target", help="git URL or local directory")
    scan.add_argument("--json", action="store_true", help="machine-readable output")
    scan.set_defaults(func=cmd_scan)

    run = sub.add_parser("run", help="triage → interactive sandbox → wipe")
    run.add_argument("target", help="git URL or local directory")
    run.add_argument("--cmd", default=None, help="non-interactive: run one command then wipe")
    run.add_argument("--net", action="store_true", help="ALLOW outbound network (default: blocked for python)")
    run.add_argument("--timeout", type=int, default=120, help="per-command timeout seconds")
    run.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    run.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
