"""Tests for the tkinter coverage audit (tools/tk_audit.py): the scanner must
resolve every import style real repos use, grade honestly, and — the
known-truth check — predict "no gaps" for the demo fixture that is verified
running in-tab. Offline: the network parts are never touched here.
"""

import os
import sys

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
sys.path.insert(0, TOOLS)

import tk_audit  # noqa: E402  (imported from tools/ via the path insert above)

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")


def uses_of(code):
    return dict(tk_audit.scan_source("x.py", code)["uses"])


# -- import styles the corpus actually uses ----------------------------------

def test_import_as_tk_resolves_widgets_and_options():
    uses = uses_of(
        "import tkinter as tk\n"
        "root = tk.Tk()\n"
        "tk.Button(root, text='go', command=go)\n"
        "tk.Label(root, text='hi')\n"
        "img = tk.PhotoImage(file='x.png')\n")
    assert uses[("tk", "Button")] == 1
    assert uses[("tk", "Label")] == 1
    assert uses[("name", "PhotoImage")] == 1


def test_from_ttk_import_resolves_to_the_blocking_weight():
    uses = uses_of(
        "from tkinter.ttk import Treeview, Notebook\n"
        "t = Treeview(root)\n")
    assert uses[("ttk", "Treeview")] == 1
    assert tk_audit.WEIGHTS[("ttk", "Treeview")] == "blocking"


def test_import_ttk_without_alias_binds_the_root_not_a_bare_ttk():
    # `import tkinter.ttk` binds `tkinter`; bare `ttk.Treeview` would NameError
    # at runtime, so the scanner must not count it (no guessing).
    uses = uses_of(
        "import tkinter.ttk\n"
        "t = tkinter.ttk.Treeview(root)\n")
    assert uses[("ttk", "Treeview")] == 1


def test_messagebox_both_import_styles():
    a = uses_of("import tkinter as tk\n"
                "tk.messagebox.askyesno('t', 'm')\n")
    b = uses_of("from tkinter import messagebox\n"
                "messagebox.askyesno('t', 'm')\n")
    c = uses_of("from tkinter.messagebox import askyesno\n"
                "askyesno('t', 'm')\n")
    for uses in (a, b, c):
        assert uses[("mb", "askyesno")] == 1
        assert tk_audit.WEIGHTS[("mb", "askyesno")] == "behavior"


def test_showinfo_is_not_a_gap_signal():
    # showinfo WORKS in the shim — it must not appear in any weight bucket
    uses = uses_of("from tkinter import messagebox\n"
                   "messagebox.showinfo('t', 'm')\n")
    assert ("mb", "showinfo") not in tk_audit.WEIGHTS


def test_filedialog_and_simpledialog_are_behavior_weight():
    uses = uses_of(
        "from tkinter import filedialog, simpledialog\n"
        "p = filedialog.askopenfilename()\n"
        "s = simpledialog.askstring('t', 'p')\n")
    assert uses[("fd", "askopenfilename")] == 1
    assert uses[("sd", "askstring")] == 1


def test_bind_is_a_behavior_signal_but_non_tkinter_files_are_ignored():
    uses = uses_of("import tkinter as tk\n"
                   "b = tk.Button(root)\n"
                   "b.bind('<Button-1>', fn)\n")
    assert uses[("method", "bind")] == 1
    plain = tk_audit.scan_source("x.py", "import socket\ns.bind(('h', 80))\n")
    assert not plain["has_tkinter"] and not plain["uses"]


def test_menu_and_optionmenu_are_behavior():
    uses = uses_of("import tkinter as tk\n"
                   "m = tk.Menu(root)\n"
                   "m.add_command(label='x')\n")
    assert uses[("tk", "Menu")] == 1


def test_star_import_resolves_known_names_with_caveat():
    res = tk_audit.scan_source("x.py",
                               "from tkinter import *\n"
                               "Button(root)\n"
                               "Treeview(root)\n")
    assert res["star_import"]
    assert res["uses"][("tk", "Button")] == 1
    assert res["uses"][("ttk", "Treeview")] == 1


def test_parse_error_is_counted_not_guessed():
    res = tk_audit.scan_source("x.py", "def broken(:\n")
    assert res["parse_error"] and not res["uses"]


def test_unresolvable_calls_are_not_counted_as_features():
    # a plain function call in a tkinter file is nobody's feature
    uses = uses_of("import tkinter as tk\n"
                   "helper(1, 2)\n")
    assert not uses


# -- grading + verdicts -------------------------------------------------------

def test_grade_splits_the_three_weights_and_an_other_bucket():
    g = tk_audit.grade({
        ("ttk", "Treeview"): 3, ("mb", "askyesno"): 2, ("tk", "Scrollbar"): 1,
        ("other", "ttk.Mystery"): 4,
    })
    assert g["blocking"][("ttk", "Treeview")] == 3
    assert g["behavior"][("mb", "askyesno")] == 2
    assert g["cosmetic"][("tk", "Scrollbar")] == 1
    assert g["other"][("other", "ttk.Mystery")] == 4


def test_verdict_lines_are_honest_about_what_happens():
    blocking = tk_audit.verdict(tk_audit.grade({("ttk", "Treeview"): 1}))
    assert "will not run in-tab" in blocking and "ttk.Treeview" in blocking
    behavior = tk_audit.verdict(tk_audit.grade({("mb", "askyesno"): 1}))
    assert behavior.startswith("opens, but")
    cosmetic = tk_audit.verdict(tk_audit.grade({("tk", "Scrollbar"): 1}))
    assert "cosmetic" in cosmetic
    clean = tk_audit.verdict(tk_audit.grade({}))
    assert "no tkinter-subset signals found statically" in clean


def test_audit_repo_skips_non_tkinter_files_and_counts_parse_failures():
    prof = tk_audit.audit_repo({
        "gui.py": "import tkinter as tk\nw = tk.Button(root)\n",
        "logic.py": "def add(a, b):\n    return a + b\n",
        "broken.py": "def (:\n",
    })
    assert prof["scanned"] == 1
    assert prof["parse_errors"] == 1
    assert prof["uses"][("tk", "Button")] == 1
    assert "failed to parse" in prof["verdict"]


# -- the known-truth cross-check ---------------------------------------------

def test_demo_fixture_predicts_no_gap_signals():
    # gui_notes.py is verified running in-tab on the app screen; the scanner
    # must agree: no blocking, no behavior features.
    with open(os.path.join(FIXTURES, "benign-notes-app", "gui_notes.py"),
              encoding="utf-8") as fh:
        prof = tk_audit.audit_repo({"gui_notes.py": fh.read()})
    assert not prof["graded"]["blocking"]
    assert not prof["graded"]["behavior"]


def test_doc_renders_method_table_and_gaps():
    profiles = [
        {"repo": "a/one", "stars": 10, "uses": {("ttk", "Treeview"): 2},
         "verdict": "will not run in-tab — ttk.Treeview raises TclError"},
        {"repo": "b/two", "stars": 5, "error": "HTTPError: 404"},
    ]
    doc = tk_audit.render_doc(profiles, "2026-11-14", ["gap one"], 20)
    assert "## Method" in doc and "forks excluded" in doc
    assert "| `ttk/Treeview` | blocking | 1 | 2 |" in doc
    assert "| a/one | 10 | will not run in-tab" in doc
    assert "| b/two | 5 | fetch failed (HTTPError: 404) |" in doc
    assert "- gap one" in doc


def test_doc_names_the_sprint_item_from_the_blocking_rows():
    profiles = [
        {"repo": "a/one", "stars": 10, "uses": {("ttk", "Treeview"): 3, ("tk", "Label"): 5},
         "verdict": "will not run in-tab"},
        {"repo": "b/two", "stars": 5, "uses": {("ttk", "Treeview"): 1, ("tk", "Tk"): 1},
         "verdict": "will not run in-tab"},
    ]
    doc = tk_audit.render_doc(profiles, "2026-11-14", [], 20)
    assert "`ttk/Treeview`, used by 2 of 2 repos — that is the sprint item" in doc


def test_supported_widgets_render_as_supported_not_other():
    # tk/Label works in the shim — the table must not imply it is a gap
    profiles = [{"repo": "a/one", "stars": 1, "uses": {("tk", "Label"): 5},
                 "verdict": "no tkinter-subset signals found statically"}]
    doc = tk_audit.render_doc(profiles, "2026-11-14", [], 20)
    assert "| `tk/Label` | supported | 1 | 5 |" in doc


def test_notebook_graded_supported_after_the_first_sprint():
    # the 2026-11-14 audit ranked ttk.Notebook the #1 blocker (10 of 20 repos);
    # the sprint shipped it as a real tab bar, so it must grade supported now
    assert ("ttk", "Notebook") not in tk_audit.WEIGHTS
    assert tk_audit._weight_of(("ttk", "Notebook")) == "supported"
    assert tk_audit._weight_of(("ttk", "Treeview")) == "blocking"  # still a wall


def test_progressbar_graded_supported_after_the_second_sprint():
    # the 2026-11-14 re-run ranked ttk.Progressbar the top blocker (6 of 20
    # repos); the sprint shipped it (start/step/stop with the wrap rule
    # measured against real Tk 9.0), so it must grade supported now
    assert ("ttk", "Progressbar") not in tk_audit.WEIGHTS
    assert tk_audit._weight_of(("ttk", "Progressbar")) == "supported"
    assert tk_audit._weight_of(("ttk", "Treeview")) == "blocking"  # still a wall
