"""cleanroom's tkinter coverage audit — the evidence for the virtual tkinter's
sprint backlog.

Method (stated, not implied): pick the top N most-starred GitHub repos matching
`language:python tkinter` (forks excluded), download each repo's tarball, and
statically walk every Python file's AST for tkinter/ttk API usage. Every usage
is graded by what the virtual tkinter does with it today:

  blocking  — the shim raises TclError; the repo will not run in-tab
  behavior  — the repo runs but the feature silently degrades (dialogs answer
              canned "no", bound events never fire, menubar never renders)
  cosmetic  — visible reduction only (self-scrolling, plain-entry Spinbox)

The weight column, not intuition, ranks the shim sprint. Output is
docs/tkinter-coverage.md: methodology, the table, honest gaps.

Honest limits, stated up front:
- static scanning cannot tie a `.bind()` call to its object — a socket.bind in
  a tkinter repo counts as a signal, not proof (repo counts are the primary
  metric, call sites secondary);
- files that fail to parse are counted, never guessed;
- runtime cross-check lives in the doc, applied only where the repo actually
  ran in-tab (see the gaps section).

Pure functions are unit-tested (tests/test_tk_audit.py); the network parts are
not. stdlib only.
"""

import argparse
import ast
import json
import os
import sys
import tarfile
import tempfile
import urllib.request
import warnings
from collections import Counter

# -- the audited API surface (mirrors tkinter_shim.py's support table) -------

TK_WIDGETS = {
    "Tk", "Toplevel", "Frame", "LabelFrame", "Label", "Message", "Button",
    "Entry", "Text", "Listbox", "Canvas", "Checkbutton", "Radiobutton",
    "Scale", "Scrollbar", "Menu", "Spinbox", "OptionMenu",
}
TTK_NAMES = {
    "Treeview", "Notebook", "Progressbar", "Panedwindow", "PanedWindow",
    "Combobox", "Separator", "Sizegrip", "Style", "Labelframe", "Scale",
    "Entry", "Button", "Label", "Frame", "Checkbutton", "Radiobutton",
    "Scrollbar", "Spinbox",
}
MB_FNS = {"showinfo", "showwarning", "showerror",
          "askyesno", "askokcancel", "askretrycancel", "askquestion"}
FD_FNS = {"askopenfilename", "askopenfilenames", "asksaveasfilename", "askdirectory"}
SD_FNS = {"askstring", "askinteger", "askfloat"}
SUBMODULES = {"ttk", "messagebox", "filedialog", "simpledialog", "constants"}
SIGNAL_METHODS = {"bind", "tag_bind", "tag_config", "tag_configure"}
IMAGE_OPTIONS = {"image", "bitmap", "compound"}
ROOT_NAMES = {"tkinter", "tk"}  # bare aliases that mean the tkinter root

# what the virtual tkinter does with each of these today (tkinter_shim.py).
# ttk.Notebook left this table after the first audit sprint (2026-11-14),
# ttk.Progressbar after the second, and ttk.Treeview after the third (also
# 2026-11-14): all render for real now, so they grade "supported" automatically.
WEIGHTS = {
    ("ttk", "Panedwindow"): "blocking", ("ttk", "PanedWindow"): "blocking",
    ("mb", "askyesno"): "behavior", ("mb", "askokcancel"): "behavior",
    ("mb", "askretrycancel"): "behavior", ("mb", "askquestion"): "behavior",
    ("sd", "askstring"): "behavior", ("sd", "askinteger"): "behavior",
    ("sd", "askfloat"): "behavior",
    ("fd", "askopenfilename"): "behavior", ("fd", "askopenfilenames"): "behavior",
    ("fd", "asksaveasfilename"): "behavior", ("fd", "askdirectory"): "behavior",
    ("method", "bind"): "behavior", ("method", "tag_bind"): "behavior",
    ("tk", "Menu"): "behavior", ("tk", "OptionMenu"): "behavior",
    ("name", "PhotoImage"): "behavior", ("option", "image"): "behavior",
    ("option", "bitmap"): "behavior", ("option", "compound"): "behavior",
    ("tk", "Scrollbar"): "cosmetic", ("tk", "Spinbox"): "cosmetic",
    ("ttk", "Style"): "cosmetic", ("method", "tag_config"): "cosmetic",
    ("method", "tag_configure"): "cosmetic",
}
WEIGHT_ORDER = ["blocking", "behavior", "cosmetic"]
WEIGHT_LABEL = {
    "blocking": "blocks the run (TclError)",
    "behavior": "runs, but the feature silently degrades",
    "cosmetic": "visible reduction only",
}

# -- import resolution -------------------------------------------------------
# Every style the corpus actually uses: import tkinter as tk; from tkinter
# import ttk; from tkinter.ttk import Treeview; from tkinter.messagebox import
# askyesno; import tkinter.ttk as ttk. Unresolvable calls are not counted as
# known features — never guessed.


def build_alias_map(tree):
    """Map local names to what they mean, from the file's tkinter imports."""
    amap = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "tkinter":
                    amap[a.asname or "tkinter"] = "root"
                elif a.name.startswith("tkinter."):
                    sub = a.name.split(".", 1)[1]
                    if a.asname:
                        amap[a.asname] = "sub:" + sub
                    else:
                        # `import tkinter.ttk` binds the root name `tkinter`,
                        # with ttk reachable as an attribute — not a bare `ttk`
                        amap["tkinter"] = "root"
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            mod = node.module
            if mod == "tkinter":
                for a in node.names:
                    if a.name == "*":
                        amap["*"] = "star"
                    elif a.name in SUBMODULES:
                        amap[a.asname or a.name] = "sub:" + a.name
                    elif a.name == "PhotoImage":
                        amap[a.asname or a.name] = "name:PhotoImage"
                    elif a.name in TK_WIDGETS:
                        amap[a.asname or a.name] = "tkwidget:" + a.name
            elif mod.startswith("tkinter."):
                sub = mod.split(".", 1)[1]
                for a in node.names:
                    if a.name == "*":
                        amap["*"] = "star"
                    elif sub == "ttk" and a.name in TTK_NAMES:
                        amap[a.asname or a.name] = "ttk:" + a.name
                    elif sub == "messagebox" and a.name in MB_FNS:
                        amap[a.asname or a.name] = "mb:" + a.name
                    elif sub == "filedialog" and a.name in FD_FNS:
                        amap[a.asname or a.name] = "fd:" + a.name
                    elif sub == "simpledialog" and a.name in SD_FNS:
                        amap[a.asname or a.name] = "sd:" + a.name
                    elif sub == "constants":
                        pass
                    elif a.name == "PhotoImage":
                        amap[a.asname or a.name] = "name:PhotoImage"
                    elif a.name in TK_WIDGETS:
                        amap[a.asname or a.name] = "tkwidget:" + a.name
    return amap


def _resolve_attribute_call(func, amap):
    """Resolve tk.Button(...) / tkinter.ttk.Treeview(...) / ttk.Style(...) —
    an Attribute-qualified call — to a tag tuple, or None."""
    parts = []
    node = func
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    parts.reverse()  # ["tk", "messagebox", "showinfo"] etc.

    head = amap.get(parts[0], parts[0] if parts[0] in ROOT_NAMES else None)
    if head == "root":
        if len(parts) >= 3 and parts[1] in SUBMODULES and parts[1] != "constants":
            return ("sub", parts[1], parts[2])
        if len(parts) == 2:
            if parts[1] in TK_WIDGETS:
                return ("tk", parts[1])
            if parts[1] == "PhotoImage":
                return ("name", "PhotoImage")
        return None
    if head and head.startswith("sub:") and len(parts) == 2:
        return ("sub", head[4:], parts[1])
    return None


def _resolve_bare_call(name, amap, star):
    """Resolve Button(...) / Treeview(...) / askyesno(...) — a bare-name call."""
    bare = amap.get(name)
    if bare and bare.startswith("tkwidget:"):
        return ("tk", bare.split(":", 1)[1])
    if bare and bare.startswith("ttk:"):
        return ("ttk", bare.split(":", 1)[1])
    if bare and bare.startswith("name:"):
        return ("name", bare.split(":", 1)[1])
    if bare and bare.startswith(("mb:", "fd:", "sd:")):
        kind, fn = bare.split(":", 1)
        real = {"mb": "messagebox", "fd": "filedialog", "sd": "simpledialog"}[kind]
        return ("sub", real, fn)
    if bare is None and star:
        if name in TK_WIDGETS:
            return ("tk", name)
        if name in TTK_NAMES:
            return ("ttk", name)
        if name == "PhotoImage":
            return ("name", "PhotoImage")
    return None


# -- the scanner -------------------------------------------------------------


def scan_source(rel_path, text):
    """One file's tkinter usage: {"uses": Counter, "parse_error": bool, ...}."""
    out = {"uses": Counter(), "parse_error": False, "star_import": False,
           "has_tkinter": False}
    try:
        with warnings.catch_warnings():
            # scanned repos often carry invalid escape sequences; their
            # SyntaxWarnings are their business, not audit output
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(text)
    except SyntaxError:
        out["parse_error"] = True
        return out

    amap = build_alias_map(tree)
    star = out["star_import"] = "*" in amap
    out["has_tkinter"] = any(
        v == "root" or v == "star" or v.startswith(
            ("sub:", "tkwidget:", "ttk:", "mb:", "fd:", "sd:", "name:"))
        for v in amap.values()) or star
    if not out["has_tkinter"]:
        return out

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in SIGNAL_METHODS and node.args:
                tag = ("method", node.func.attr)
            else:
                tag = _resolve_attribute_call(node.func, amap)
        elif isinstance(node.func, ast.Name):
            tag = _resolve_bare_call(node.func.id, amap, star)
        else:
            tag = None
        if tag is not None:
            if tag[0] == "sub":
                # normalize submodule calls into canonical feature tags
                sub, fn = tag[1], tag[2]
                if sub == "messagebox" and fn in MB_FNS:
                    out["uses"][("mb", fn)] += 1
                elif sub == "filedialog" and fn in FD_FNS:
                    out["uses"][("fd", fn)] += 1
                elif sub == "simpledialog" and fn in SD_FNS:
                    out["uses"][("sd", fn)] += 1
                elif sub == "ttk" and fn in TTK_NAMES:
                    out["uses"][("ttk", fn)] += 1
                else:
                    out["uses"][("other", sub + "." + fn)] += 1
            else:
                out["uses"][tag] += 1
        # option kwargs on widget constructions: the image family
        widget_tag = None
        if isinstance(node.func, ast.Attribute):
            widget_tag = _resolve_attribute_call(node.func, amap)
        elif isinstance(node.func, ast.Name):
            widget_tag = _resolve_bare_call(node.func.id, amap, star)
        if widget_tag is not None and widget_tag[0] in ("tk", "ttk"):
            for kw in node.keywords:
                if kw.arg in IMAGE_OPTIONS:
                    out["uses"][("option", kw.arg)] += 1
    return out


def grade(uses):
    """Split a usage counter into the three weights + an honest other-bucket."""
    g = {w: Counter() for w in WEIGHT_ORDER}
    other = Counter()
    for feat, n in uses.items():
        w = WEIGHTS.get(feat)
        if w:
            g[w][feat] += n
        else:
            other[feat] += n
    g["other"] = other
    return g


def verdict(g):
    """One honest line: what happens if this repo is pasted into the tab today."""
    if g["blocking"]:
        names = sorted({f[1] for f in g["blocking"]})
        return "will not run in-tab — ttk." + ", ttk.".join(names) + " raises TclError"
    if g["behavior"]:
        return "opens, but " + str(len(g["behavior"])) + " feature(s) silently degrade"
    if g["cosmetic"]:
        return "usable; cosmetic skips only"
    if g["other"]:
        return "no known-gap signals statically; " + str(len(g["other"])) + " unclassified call(s)"
    return "no tkinter-subset signals found statically"


# -- network parts (not unit-tested; failures are counted, not swallowed) ----


def pick_corpus(token, count=20):
    """Top-N most-starred GitHub repos matching language:python tkinter."""
    url = ("https://api.github.com/search/repositories?q=language%3Apython+tkinter"
           "+fork%3Afalse&sort=stars&order=desc&per_page=" + str(count))
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [{"repo": r["full_name"], "stars": r["stargazers_count"],
             "branch": r["default_branch"]} for r in data.get("items", [])]


def fetch_repo_py_files(full_name, branch, token):
    """Download the default-branch tarball, return {rel_path: text} for .py."""
    url = "https://codeload.github.com/%s/tar.gz/refs/heads/%s" % (full_name, branch)
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    files = {}
    with tempfile.TemporaryDirectory() as tmp:
        tgz = os.path.join(tmp, "repo.tgz")
        with urllib.request.urlopen(req, timeout=120) as resp, open(tgz, "wb") as fh:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                fh.write(chunk)
        with tarfile.open(tgz, "r:gz") as tf:
            for member in tf.getmembers():
                if member.isfile() and member.name.endswith(".py"):
                    stripped = member.name.split("/", 1)[1] if "/" in member.name else member.name
                    data = tf.extractfile(member).read()
                    files[stripped] = data.decode("utf-8", errors="replace")
    return files


# -- the doc -----------------------------------------------------------------


def _weight_of(feat):
    """blocking/behavior/cosmetic for gaps; known-working calls are
    'supported' (shown last, they are not gaps); anything unknown is 'other'."""
    if feat in WEIGHTS:
        return WEIGHTS[feat]
    if feat[0] in ("tk", "ttk", "mb", "fd", "sd"):
        return "supported"
    return "other"


def render_doc(profiles, when, gaps, corpus_count, crosscheck=None):
    lines = []
    lines.append("# Virtual tkinter coverage: what real repos actually use")
    lines.append("")
    lines.append("*Snapshot %s — generated by `tools/tk_audit.py` (static AST scan).*" % when)
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("Corpus: the top %d most-starred GitHub repos matching "
                 "`language:python tkinter`, forks excluded, selected by star rank"
                 % corpus_count + " — the criterion was chosen before the scan, so the"
                 " selection is not hand-picked. Each repo's default-branch tarball is"
                 " scanned with Python's `ast`; every tkinter/ttk call is graded by what"
                 " the virtual tkinter does with it today:")
    lines.append("")
    for w in WEIGHT_ORDER:
        lines.append("- **%s** — %s" % (w, WEIGHT_LABEL[w]))
    lines.append("- rows marked **supported** use APIs the virtual tkinter already carries; "
                 "**other** is the unclassified remainder — both are shown for context, "
                 "neither is a gap")
    lines.append("")
    lines.append("The sprint takes the highest-weight fixable item — the column decides, "
                 "not intuition.")
    lines.append("")
    lines.append("## The table")
    lines.append("")
    totals = Counter()
    repos_by_feat = Counter()
    for p in profiles:
        if "error" in p:
            continue
        for feat, n in p["uses"].items():
            totals[feat] += n
            repos_by_feat[feat] += 1
    order = WEIGHT_ORDER + ["other", "supported"]
    rows = sorted(totals.items(),
                  key=lambda kv: (order.index(_weight_of(kv[0])),
                                  -repos_by_feat[kv[0]], -kv[1]))
    blockers = [f for f in rows if _weight_of(f[0]) == "blocking"]
    if blockers:
        top = blockers[0]
        nxt = ", ".join("`%s` (%d repos)" % ("/".join(f), repos_by_feat[f])
                        for f, _ in blockers[1:3])
        lines.append("**What the table says:** the highest-weight blocker is `%s`, used by"
                     % ("/".join(top[0])) + " %d of %d repos — that is the sprint item."
                     % (repos_by_feat[top[0]], len(profiles))
                     + (" Next by weight: %s." % nxt if nxt else ""))
        lines.append("")
    lines.append("Features sorted by weight, then by how many repos use them.")
    lines.append("")
    lines.append("| feature | weight | repos (of %d) | call sites |" % len(profiles))
    lines.append("|---|---|---|---|")
    for feat, n in rows:
        lines.append("| `%s` | %s | %d | %d |" % ("/".join(feat), _weight_of(feat),
                                                  repos_by_feat[feat], n))
    lines.append("")
    lines.append("## Per-repo verdicts")
    lines.append("")
    lines.append("| repo | stars | verdict |")
    lines.append("|---|---|---|")
    for p in profiles:
        if "error" in p:
            lines.append("| %s | %d | fetch failed (%s) |" % (p["repo"], p["stars"], p["error"]))
        else:
            lines.append("| %s | %d | %s |" % (p["repo"], p["stars"], p["verdict"]))
    lines.append("")
    lines.append("## Honest gaps")
    lines.append("")
    for gap in gaps:
        lines.append("- " + gap)
    lines.append("")
    if crosscheck:
        lines.append("## Cross-check")
        lines.append("")
        for line in crosscheck:
            lines.append("- " + line)
        lines.append("")
    return "\n".join(lines)


def audit_repo(files):
    """Scan one repo's .py files → a profile dict (pure; unit-tested)."""
    uses = Counter()
    parse_errors = 0
    scanned = 0
    for rel, text in files.items():
        res = scan_source(rel, text)
        if res["parse_error"]:
            parse_errors += 1
            continue
        if res["has_tkinter"]:
            scanned += 1
            uses.update(res["uses"])
    g = grade(uses)
    v = verdict(g)
    if parse_errors:
        v += " (%d file(s) failed to parse — counted, not guessed)" % parse_errors
    return {"scanned": scanned, "parse_errors": parse_errors, "uses": dict(uses),
            "graded": {w: dict(g[w]) for w in WEIGHT_ORDER}, "verdict": v}


def main(argv=None):
    ap = argparse.ArgumentParser(description="tkinter coverage audit")
    ap.add_argument("--count", type=int, default=20)
    ap.add_argument("--out", default="docs/tkinter-coverage.md")
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    ap.add_argument("--token-file", default="")
    args = ap.parse_args(argv)

    token = args.token
    if args.token_file and os.path.exists(args.token_file):
        with open(args.token_file) as fh:
            token = fh.read().strip()

    when = __import__("datetime").date.today().isoformat()
    print("picking corpus: top %d starred, language:python tkinter, no forks" % args.count)
    corpus = pick_corpus(token, args.count)
    profiles = []
    for c in corpus:
        print("  scanning %s (%d stars)" % (c["repo"], c["stars"]))
        try:
            files = fetch_repo_py_files(c["repo"], c["branch"], token)
            prof = audit_repo(files)
            prof.update(repo=c["repo"], stars=c["stars"])
        except Exception as e:
            prof = {"repo": c["repo"], "stars": c["stars"],
                    "error": type(e).__name__ + ": " + str(e)[:80]}
        profiles.append(prof)

    gaps = [
        "static scan only: a `.bind()` call cannot be tied to its object from an AST, so "
        "socket/event-library binds in tkinter repos count as signals, not proof; repo "
        "counts are the primary metric, call sites secondary",
        "star rank selects for popularity, not shim-relevant diversity; themes and wrapper "
        "libraries near the top of the list report their surface as-is",
        "runtime cross-check is applied only to repos that actually ran in-tab; where that "
        "was possible it is recorded below, otherwise the demo fixtures stand in as the "
        "known-truth check",
    ]
    crosscheck = [
        "known-truth check: `fixtures/benign-notes-app/gui_notes.py` — a demo verified "
        "running in-tab on the app screen; the scanner predicts no gap signals for it, "
        "matching runtime (pinned by tests/test_tk_audit.py)",
    ]
    doc = render_doc(profiles, when, gaps, len(corpus), crosscheck)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("wrote %s (%d repos scanned)" % (args.out, len(profiles)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
