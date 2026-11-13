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
