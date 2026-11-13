"""Unit tests for the virtual tkinter's ttk.Progressbar — the second audit
sprint (2026-11-14, 6 of 20 top-starred repos used it and it raised TclError).

The JS bridge (window.__cleanroomGui) is stubbed with a recorder, so the
widget's Python-side state machine runs for real under CPython. The wrap rule
these tests pin was measured against real Tk 9.0 on this machine (see the
shim's docstring): determinate mode WRAPS at maximum (value % maximum) and
keeps cycling until stop(); indeterminate mode's stored value climbs without
bound while the renderer draws the bounce. The ttk.* namespace is not
installed here (that happens inside the Pyodide runner), so the tests bind
the classes directly — the same objects the namespace binds.
"""

import json
import os
import sys
import types

import pytest


class _FakeGui:
    """Records every renderer op, answers like the JS bridge would."""

    def __init__(self):
        self.next_id = 1
        self.ops = []

    def create(self, parent, kind, props):
        self.ops.append(("create", parent, kind, json.loads(props)))
        self.next_id += 1
        return self.next_id - 1

    def config(self, wid, props):
        self.ops.append(("config", wid, json.loads(props)))

    def show(self, *a):
        self.ops.append(("show", a))

    def hide(self, *a):
        self.ops.append(("hide", a))

    def remove(self, wid):
        self.ops.append(("remove", wid))

    def layout(self, wid, geo):
        self.ops.append(("layout", wid, json.loads(geo)))

    def after(self, ms, cb):
        self.ops.append(("after", ms, cb))

    def dialog(self, *a):
        self.ops.append(("dialog", a))

    def notebook(self, *a):
        self.ops.append(("notebook", a))

    def tree(self, *a):
        self.ops.append(("tree",) + tuple(a))

    def value(self, wid):
        return 0


@pytest.fixture()
def shim(monkeypatch):
    gui = _FakeGui()
    stub = types.ModuleType("js")
    stub.__cleanroomGui = gui
    monkeypatch.setitem(sys.modules, "js", stub)
    monkeypatch.syspath_prepend(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    import tkinter_shim

    # module state persists across tests — start each one clean
    tkinter_shim._registry.clear()
    tkinter_shim._timers.clear()
    tkinter_shim._skipped.clear()
    tkinter_shim._root = None
    tkinter_shim.gui = gui
    return tkinter_shim


def fire_timer(shim, token):
    """One after() interval fires — the same callback path the JS side uses."""
    fn = shim._timers.pop(int(str(token).split("#")[-1]))
    fn()


def configs_for(gui, wid):
    return [op[2] for op in gui.ops if op[0] == "config" and op[1] == wid]


# -- construction -------------------------------------------------------------

def test_defaults_match_real_tk(shim):
    root = shim.Tk()
    p = shim.Progressbar(root)
    assert float(p["value"]) == 0.0
    assert float(p["maximum"]) == 100      # Tk's documented default
    assert str(p["mode"]) == "determinate"


def test_constructor_options_reach_the_renderer(shim):
    root = shim.Tk()
    shim.Progressbar(root, maximum=200, value=50, length=300)
    creates = [op for op in shim.gui.ops if op[0] == "create" and op[2] == "progress"]
    assert len(creates) == 1
    props = creates[0][3]
    assert props["maximum"] == 200 and props["value"] == 50
    assert props["length"] == 300 and props["mode"] == "determinate"


# -- step(): the measured wrap rule ------------------------------------------

def test_step_defaults_to_one_unit(shim):
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=10)
    p.step()
    assert float(p["value"]) == 1.0


def test_step_past_maximum_wraps_by_modulo_measured_rule(shim):
    # real Tk 9.0, measured: step(12) on maximum=5 lands on 2 (value % maximum)
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=5)
    p.step(12)
    assert float(p["value"]) == 2.0


def test_renderer_sees_every_step(shim):
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=5)
    p.step()
    p.step()
    assert {"value": 1.0} in configs_for(shim.gui, p._id)
    assert {"value": 2.0} in configs_for(shim.gui, p._id)


# -- start()/stop(): a real repeating after() timer ---------------------------

def test_start_steps_via_a_repeating_after_timer(shim):
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=100)
    p.start(60)                            # start() returns None, like real Tk
    assert str(p._running).startswith("after#")
    fire_timer(shim, p._running)          # the interval fires: one step
    assert float(p["value"]) == 1.0
    assert p._running is not None         # and it re-armed itself
    p.stop()


def test_start_twice_does_not_double_schedule(shim):
    root = shim.Tk()
    p = shim.Progressbar(root)
    p.start(50)
    afters = len([op for op in shim.gui.ops if op[0] == "after"])
    p.start(50)                           # real Tk: already started, no second timer
    assert len([op for op in shim.gui.ops if op[0] == "after"]) == afters
    p.stop()


def test_stop_cancels_the_pending_timer(shim):
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=100)
    p.start(50)
    token = p._running
    p.stop()
    assert int(str(token).split("#")[-1]) not in shim._timers
    assert p._running is None


def test_determinate_mode_cycles_forever(shim):
    # measured on real Tk 9.0: the value wraps at maximum and keeps going
    # until stop() — it never parks at maximum (contrary to popular folklore)
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=5)
    p.start(50)
    for _ in range(12):
        fire_timer(shim, p._running)
    assert float(p["value"]) == 2.0       # 12 % 5
    assert p._running is not None         # still cycling
    p.stop()


def test_indeterminate_value_climbs_and_the_bar_bounces(shim):
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=10, mode="indeterminate")
    p.start(50)
    for _ in range(12):
        fire_timer(shim, p._running)
    assert float(p["value"]) == 12.0      # climbs past maximum — measured, no wrap
    p.stop()
    runs = [op[2] for op in shim.gui.ops
            if op[0] == "config" and "running" in op[2]]
    assert {"running": 1} in runs         # the renderer was told to bounce
    assert {"running": 0} in runs         # and to stop


# -- variable binding + item assignment --------------------------------------

def test_variable_binding_stays_in_sync(shim):
    root = shim.Tk()
    v = shim.IntVar(value=30)
    p = shim.Progressbar(root, variable=v)
    assert float(p["value"]) == 30.0
    p.step()
    assert int(v.get()) == 31


def test_item_assignment_updates_the_bar(shim):
    # the idiomatic real-tkinter update: pbar["value"] += 10
    root = shim.Tk()
    p = shim.Progressbar(root, maximum=100)
    p["value"] = 40
    assert float(p["value"]) == 40.0
    assert {"value": 40} in configs_for(shim.gui, p._id)


# -- ttk.Treeview: the third sprint -------------------------------------------
# The surface below is the one the five blocking repos actually call (dumped
# call-site by call-site before building). The measured rules these tests pin:
# see() opens closed ancestors (real Tk 9.0), values= strings split like Tcl
# lists, focus() is not selection().

def tree(shim, **kw):
    root = shim.Tk()
    return shim.Treeview(root, **kw), root


def rows_payload(gui, wid):
    ops = [op for op in gui.ops if op[0] == "tree" and op[1] == wid and op[2] == "rows"]
    assert ops, "no rows payload reached the renderer"
    import json as _j
    return _j.loads(ops[-1][3])


def test_insert_returns_the_iid_and_generates_one_when_absent(shim):
    t, _ = tree(shim)
    iid = t.insert("", "end", values=("a", "b"))
    assert iid == "I001"                       # the auto id, like real Tk
    assert t.insert("", "end") == "I002"
    explicit = t.insert("", "end", iid="custom", text="x")
    assert explicit == "custom"


def test_duplicate_iid_and_missing_parent_raise_like_real_tk(shim):
    t, _ = tree(shim)
    t.insert("", "end", iid="a")
    with pytest.raises(shim.TclError):
        t.insert("", "end", iid="a")
    with pytest.raises(shim.TclError):
        t.insert("no-such-parent", "end")


def test_values_string_splits_like_a_tcl_list(shim):
    t, _ = tree(shim)
    t.insert("", "end", values="one two {three four}")
    assert t.item("I001", "values") == ["one", "two", "three four"]


def test_hierarchies_parents_and_index(shim):
    t, _ = tree(shim)
    p = t.insert("", "end", text="parent", open=True)
    k1 = t.insert(p, "end", text="kid1")
    k2 = t.insert(p, "end", text="kid2")
    assert t.get_children() == (p,)
    assert t.get_children(p) == (k1, k2)
    assert t.parent(k2) == p and t.parent(p) == ""
    assert t.index(k2) == 1
    assert t.exists(p) and not t.exists("ghost")


def test_delete_takes_varargs_and_kills_descendants(shim):
    t, _ = tree(shim)
    p = t.insert("", "end", text="p")
    kid = t.insert(p, "end", text="kid")
    grand = t.insert(kid, "end", text="grand")
    t.insert("", "end", text="survivor")
    t.selection_set(p)
    t.delete(p, "ghost-not-there")            # pygubu deletes *selection()
    assert not t.exists(p) and not t.exists(kid) and not t.exists(grand)
    assert t.get_children() == ("I004",)      # the survivor
    assert t.selection() == ()                # selection died with the row


def test_item_dict_get_and_set_the_dvlv_tuple_shape(shim):
    t, _ = tree(shim)
    iid = t.insert("", "end", text="row", values=("1", "2"))
    d = t.item(iid)
    assert d["text"] == "row" and d["values"] == ["1", "2"] and d["open"] == 0
    assert t.item(iid, "text") == "row"
    # Dvlv passes the whole selection() tuple straight into item()
    t.selection_set(iid)
    assert t.item(t.selection())["values"] == ["1", "2"]
    t.item(iid, values=("3", "4"))
    assert t.item(iid, "values") == ["3", "4"]
    assert rows_payload(shim.gui, t._id)["rows"][0]["values"] == ["3", "4"]


def test_selection_set_shapes_including_the_clear_call(shim):
    t, _ = tree(shim)
    a = t.insert("", "end")
    b = t.insert("", "end")
    t.selection_set(a)                        # single string
    assert t.selection() == (a,)
    t.selection_set([a, b])                   # PySimpleGUI passes a list of ids
    assert t.selection() == (a, b)
    t.selection_set("")                       # pygubu clears with selection_set("")
    assert t.selection() == ()
    t.selection_set(*[a, b])                  # varargs spread
    assert t.selection() == (a, b)


def test_headings_and_columns_with_the_width_getter(shim):
    t, root = tree(shim, columns=("name", "time"), show="headings")
    t.heading("name", text="task", anchor="center")
    t.column("name", width=240, minwidth=50, anchor="w")
    assert t.heading("name", "text") == "task"
    assert t.column("name", "width") == 240   # the pygubu single-arg getter
    assert t.column("never-set", "width") == 200  # Tk's default
    payload = rows_payload(shim.gui, t._id)
    assert payload["showTree"] is False and payload["columns"] == ["name", "time"]
    assert payload["headings"]["name"]["text"] == "task"
    assert payload["cols"]["name"]["width"] == 240


def test_rows_payload_hides_children_of_closed_items(shim):
    t, _ = tree(shim)
    p = t.insert("", "end", text="p", open=False)
    t.insert(p, "end", text="hidden")
    payload = rows_payload(shim.gui, t._id)
    assert [r["text"] for r in payload["rows"]] == ["p"]
    assert payload["rows"][0]["kids"] == 1 and payload["rows"][0]["open"] == 0
    t.item(p, open=True)
    payload = rows_payload(shim.gui, t._id)
    assert [r["text"] for r in payload["rows"]] == ["p", "hidden"]
    assert payload["rows"][1]["depth"] == 1


def test_see_opens_closed_ancestors_measured_rule(shim):
    t, _ = tree(shim)
    p = t.insert("", "end", text="p", open=False)
    kid = t.insert(p, "end", text="kid")
    t.see(kid)
    assert t.item(p, "open") == 1
    sees = [op for op in shim.gui.ops if op[0] == "tree" and op[2] == "see"]
    assert sees and sees[-1][1] == t._id


def test_a_row_click_updates_selection_state(shim):
    t, _ = tree(shim)
    a = t.insert("", "end")
    b = t.insert("", "end")
    t._event("treeselect", {"iid": a})
    assert t.selection() == (a,) and t.focus() == a
    t._event("treeselect", {"iid": b, "toggle": True})
    assert t.selection() == (a, b)            # extended-mode toggle
    t._event("treeselect", {"iid": a})
    assert t.selection() == (a,)              # plain click replaces


def test_selectmode_none_ignores_clicks(shim):
    t, _ = tree(shim, selectmode="none")
    iid = t.insert("", "end")
    t._event("treeselect", {"iid": iid})
    assert t.selection() == ()


def test_outside_the_subset_is_counted_not_faked(shim):
    t, _ = tree(shim)
    iid = t.insert("", "end", tags=("odd",))
    t.tag_configure("odd", background="#222")
    t.bbox(iid)
    t.identify("region", 5, 5)
    assert shim._skipped["Treeview.tag_configure colors/fonts"] == 1
    assert shim._skipped["Treeview.bbox (cell geometry)"] == 1
    assert shim._skipped["Treeview.identify (cell hit-testing)"] == 1


def test_yes_no_constants_exist_for_stretch_yes(shim):
    # YEDDA passes stretch=YES; the shim now carries the constant
    assert shim.YES == "yes" and shim.NO == "no"


def test_detach_moves_and_index_roundtrip(shim):
    t, _ = tree(shim)
    p = t.insert("", "end")
    kid = t.insert(p, "end")
    t.detach(kid)
    assert t.get_children(p) == ()            # off the tree, data kept
    assert t.exists(kid)
    t.move(kid, p, "end")
    assert t.get_children(p) == (kid,)
