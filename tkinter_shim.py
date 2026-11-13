"""cleanroom's virtual tkinter — a SUBSET of the tkinter API rendered as real
HTML widgets in the cleanroom app screen (a browser tab).

This module is executed inside the in-tab Python VM (Pyodide) and registered
as `tkinter` before repo code runs. A real tkinter needs Tcl/Tk and a real
display; this shim instead talks to a small JS renderer (window.__cleanroomGui)
that builds actual HTML widgets and forwards clicks/keeps values, so GUI apps
can be *used* in the page — not just run headless.

Honest by design:
- anything outside the subset is counted and reported, never silently faked;
- mainloop() is non-blocking: the window opens and the terminal stays live;
- callbacks may be plain functions or coroutines (async ones are drained
  through the VM's event loop by the JS side).
"""

import asyncio as _asyncio
import inspect as _inspect
import json as _json
import re as _re
import traceback as _tb

import js as _js

gui = _js.__cleanroomGui  # installed by runner.js before any repo code runs

# -- state ------------------------------------------------------------------

_registry = {}   # widget id -> widget object
_timers = {}     # after() counter -> callable
_pending = []    # awaitables returned by callbacks, drained by the JS side
_skipped = {}    # unsupported option/feature -> call count
_noted = set()
_root = None

CONST_NAMES = [
    "END", "TRUE", "FALSE", "NONE", "TOP", "BOTTOM", "LEFT", "RIGHT",
    "BOTH", "X", "Y", "N", "S", "E", "W", "NW", "NE", "SW", "SE",
    "NS", "EW", "NSEW", "CENTER", "HORIZONTAL", "VERTICAL",
    "FLAT", "GROOVE", "RIDGE", "RAISED", "SUNKEN", "SOLID",
    "NORMAL", "DISABLED", "ACTIVE", "READONLY",
    "SINGLE", "BROWSE", "MULTIPLE", "EXTENDED", "WORD", "CHAR", "ALL",
    "YES", "NO",
]


def _unsupported(name):
    _skipped[name] = _skipped.get(name, 0) + 1
    if name not in _noted:
        _noted.add(name)
        print("  virtual screen: '" + name + "' is outside the tkinter subset — ignored")


def _call(fn, *args):
    """Run a callback; coroutine returns are parked for the JS event loop."""
    if fn is None:
        return
    try:
        res = fn(*args)
        if _inspect.isawaitable(res):
            _pending.append(res)
    except Exception:
        _tb.print_exc()


async def _drain():
    while _pending:
        batch = list(_pending)
        _pending.clear()
        await _asyncio.gather(*batch, return_exceptions=True)


def _dispatch(widget_id, name, payload):
    """The single entry point the JS renderer calls back into."""
    try:
        data = _json.loads(payload) if payload else {}
    except ValueError:
        data = {}
    if name == "after":
        fn = _timers.pop(int(data.get("cb", 0)), None)
        _call(fn)
        return str(len(_pending))
    w = _registry.get(int(widget_id))
    if w is not None:
        try:
            w._event(name, data)
        except Exception:
            _tb.print_exc()
    return str(len(_pending))


def _num(v):
    try:
        if isinstance(v, bool):
            return int(v)
        return int(v) if float(v) == int(float(v)) else float(v)
    except (TypeError, ValueError):
        return 0


def _tcl_list(s):
    """Simple Tcl list split: whitespace-separated, braces group one element —
    what `values="a b {c d}"` means in real Tk."""
    out, cur, depth = [], "", 0
    for ch in str(s):
        if ch == "{":
            depth += 1
            if depth == 1:
                continue
        elif ch == "}":
            depth -= 1
            if depth == 0:
                out.append(cur)
                cur = ""
                continue
        if ch == " " and depth == 0:
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def _font_css(value):
    """tk font spec (tuple or string) -> a CSS style dict for the renderer."""
    css, tokens = {}, []
    if isinstance(value, (list, tuple)):
        for part in value:
            tokens.extend(str(part).split())
    else:
        tokens = str(value).split()
    fam = None
    for tok in tokens:
        if _re.fullmatch(r"-?\d+", tok):
            css["fontSize"] = str(abs(int(tok))) + "px"
        elif tok == "bold":
            css["fontWeight"] = "600"
        elif tok in ("italic", "oblique"):
            css["fontStyle"] = "italic"
        elif tok == "underline":
            css["textDecoration"] = "underline"
        elif tok == "overstrike":
            css["textDecoration"] = "line-through"
        elif tok in ("normal",):
            pass
        elif fam is None and tok not in (
                "TkDefaultFont", "TkTextFont", "TkFixedFont",
                "TkHeadingFont", "TkMenuFont", "TkCaptionFont"):
            fam = tok
    if fam:
        css["fontFamily"] = "'" + fam + "', system-ui, sans-serif"
    return css


def _margins(px, py):
    x = px[0] if isinstance(px, (list, tuple)) else px
    y = py[0] if isinstance(py, (list, tuple)) else py
    return str(_num(y or 0)) + "px " + str(_num(x or 0)) + "px"


class TclError(Exception):
    pass


class _Event:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# -- variables ----------------------------------------------------------------

class _Variable:
    def __init__(self, master=None, value=None):
        self._v = value
        self._widgets = []
        self._traces = []
        self._name = "PY_VAR%d" % (id(self) % 100000)

    def get(self):
        return self._v

    def set(self, value):
        self._v = value
        for w in list(self._widgets):
            w._var_updated(value)
        for cb in list(self._traces):
            _call(cb, self._name, "", "write")

    def trace_add(self, mode, callback):
        self._traces.append(callback)
        return ("trace", len(self._traces))

    def trace_variable(self, mode, callback):
        return self.trace_add(mode, callback)

    def trace(self, mode="w", callback=None):
        if callback:
            self._traces.append(callback)
        return self._name

    def trace_vdelete(self, mode, cb):
        if cb in self._traces:
            self._traces.remove(cb)


class StringVar(_Variable):
    def __init__(self, master=None, value=None):
        super().__init__(master, "" if value is None else str(value))


class IntVar(_Variable):
    def __init__(self, master=None, value=None):
        super().__init__(master, 0 if value is None else int(value))


class DoubleVar(_Variable):
    def __init__(self, master=None, value=None):
        super().__init__(master, 0.0 if value is None else float(value))


class BooleanVar(_Variable):
    def __init__(self, master=None, value=None):
        super().__init__(master, False if value is None else bool(value))


Variable = _Variable


# -- widgets ------------------------------------------------------------------

class _Widget:
    _kind = "frame"

    def __init__(self, master=None, cnf=None, **kw):
        cnf = dict(cnf or {})
        cnf.update(kw)
        self._master = master
        self._cnf = {}
        self._geo = None
        self._var = None
        props = {}
        for k, v in list(cnf.items()):
            self._set(k, v, props)
        parent_id = master._id if master is not None else 0
        self._id = int(gui.create(parent_id, self._kind, _json.dumps(props)))
        _registry[self._id] = self
        self._cnf["id"] = self._id

    def _set(self, key, value, props):
        """Translate one tkinter option into a renderer prop (or honest skip)."""
        if key == "command":
            self._cnf["command"] = value
            props["hasCommand"] = True
        elif key in ("textvariable", "variable"):
            self._bind_var(value, props)
        elif key in ("text", "label"):
            self._cnf["text"] = str(value)
            props["text"] = str(value)
        elif key == "bg":
            props["background"] = str(value)
        elif key == "fg":
            props["color"] = str(value)
        elif key in ("background", "foreground"):
            props["background" if key[0] == "b" else "color"] = str(value)
        elif key == "font":
            props["font"] = _font_css(value)
        elif key in ("width", "height", "borderwidth", "wraplength", "bd"):
            props[{"bd": "borderwidth"}.get(key, key)] = _num(value)
        elif key in ("state", "relief", "anchor", "justify", "show", "orient",
                     "wrap", "selectmode"):
            props[key] = str(value)
        elif key in ("from_", "to", "onvalue", "offvalue", "value",
                     "resolution", "tickinterval"):
            props[{"from_": "from"}.get(key, key)] = _num(value)
        elif key == "values":
            props["values"] = [str(x) for x in value]
        elif key in ("padx", "pady", "takefocus", "cursor", "underline",
                     "activebackground", "activeforeground", "disabledforeground",
                     "selectbackground", "selectforeground", "insertwidth",
                     "highlightthickness", "highlightbackground", "highlightcolor",
                     "cursorarrow", "relief3d", "repeatdelay", "repeatinterval",
                     "buttoncursor", "readonlybackground"):
            if key in ("repeatdelay", "repeatinterval"):
                _unsupported("option '" + key + "'")
        elif key in ("image", "bitmap", "compound"):
            _unsupported("images (" + key + ")")
        else:
            _unsupported("option '" + key + "'")
        self._cnf[key] = value

    def _bind_var(self, var, props):
        self._var = var
        var._widgets.append(self)
        if isinstance(props, dict):
            props.setdefault("radioGroup", "v" + str(id(var)).replace("-", "n"))

    def _var_updated(self, value):
        pass

    def _event(self, name, data):
        if name == "command":
            _call(self._cnf.get("command"))

    def config(self, cnf=None, **kw):
        cnf = dict(cnf or {})
        cnf.update(kw)
        props = {}
        for k, v in list(cnf.items()):
            self._set(k, v, props)
        if props:
            gui.config(self._id, _json.dumps(props))

    configure = config

    def cget(self, key):
        return self._cnf.get(key)

    __getitem__ = lambda self, key: self._cnf.get(key)

    def destroy(self):
        _registry.pop(self._id, None)
        gui.remove(self._id)

    def winfo_width(self):
        return _num(self._cnf.get("width", 0))

    def winfo_reqwidth(self):
        return self.winfo_width()

    def winfo_height(self):
        return _num(self._cnf.get("height", 0))

    def winfo_reqheight(self):
        return self.winfo_height()

    def winfo_toplevel(self):
        w = self
        while getattr(w, "_master", None) is not None:
            w = w._master
        return w

    def update(self):
        pass

    def update_idletasks(self):
        pass

    def bind(self, seq, fn=None, add=None):
        _unsupported("bind " + str(seq))

    def unbind(self, seq, funcid=None):
        pass

    def focus_set(self):
        pass

    focus_force = focus_set

    def pack(self, **kw):
        self._apply_geo("pack", kw)

    def grid(self, **kw):
        self._apply_geo("grid", kw)

    def place(self, **kw):
        self._apply_geo("place", kw)

    def _apply_geo(self, mode, kw):
        geo = {"mode": mode}
        for k, v in kw.items():
            if k in ("side", "fill", "anchor", "sticky"):
                geo[k] = str(v)
            elif k in ("expand", "rowspan", "columnspan"):
                geo[k] = _num(v)
            elif k in ("padx", "pady", "row", "column", "x", "y", "relx", "rely",
                       "width", "height"):
                geo[k] = _num(v) if not isinstance(v, (list, tuple)) else [_num(x) for x in v]
            elif k == "in_":
                pass
            else:
                _unsupported("geometry option '" + k + "'")
        self._geo = geo
        gui.layout(self._id, _json.dumps(geo))

    def pack_forget(self):
        self._geo = {"mode": "pack", "forget": 1}
        gui.layout(self._id, _json.dumps(self._geo))

    grid_forget = pack_forget
    place_forget = pack_forget

    def pack_info(self):
        return dict(self._geo)

    grid_info = pack_info
    place_info = pack_info


class Tk(_Widget):
    _kind = "root"

    def __init__(self, cnf=None, **kw):
        global _root
        super().__init__(None, cnf, **kw)
        _root = self
        self._wm_delete = None
        gui.show(str(self._cnf.get("title", "")) or "app")

    def title(self, s):
        self._cnf["title"] = str(s)
        gui.config(self._id, _json.dumps({"title": str(s)}))

    def geometry(self, spec):
        m = _re.match(r"(\d+)x(\d+)", str(spec))
        if m:
            gui.config(self._id, _json.dumps({"winWidth": int(m.group(1)),
                                              "winHeight": int(m.group(2))}))

    def mainloop(self, *a, **k):
        gui.show(str(self._cnf.get("title", "")) or "app")
        print("── app screen ── the window is open in the panel above; click it any time")
        print("   (virtual tkinter: a subset rendered as real HTML widgets — the terminal stays live)")

    def quit(self):
        gui.hide()

    def destroy(self):
        _registry.pop(self._id, None)
        gui.hide()

    def protocol(self, what, fn=None):
        if what == "WM_DELETE_WINDOW":
            self._wm_delete = fn
        else:
            _unsupported("protocol " + str(what))

    def _event(self, name, data):
        if name == "wm_delete":
            fn = self._wm_delete
            self._wm_delete = None
            _call(fn)
            if fn is None:
                gui.hide()
        else:
            super()._event(name, data)

    def resizable(self, *a, **k):
        pass

    def minsize(self, *a, **k):
        pass

    maxsize = minsize

    def attributes(self, *a, **k):
        _unsupported("window attributes")

    def overrideredirect(self, *a, **k):
        pass

    def after(self, ms, fn=None):
        n = len(_timers) + 1
        _timers[n] = fn
        gui.after(int(ms), n)
        return "after#" + str(n)

    def after_cancel(self, token):
        try:
            _timers.pop(int(str(token).split("#")[-1]), None)
        except (ValueError, IndexError):
            pass

    def winfo_children(self):
        return []

    def bell(self):
        pass


class Toplevel(_Widget):
    _kind = "toplevel"

    def __init__(self, master=None, cnf=None, **kw):
        super().__init__(None, cnf, **kw)  # a Toplevel mounts on the screen itself
        gui.show(str(self._cnf.get("title", "")) or "app")

    def title(self, s):
        gui.config(self._id, _json.dumps({"title": str(s)}))

    def geometry(self, spec):
        m = _re.match(r"(\d+)x(\d+)", str(spec))
        if m:
            gui.config(self._id, _json.dumps({"winWidth": int(m.group(1)),
                                              "winHeight": int(m.group(2))}))

    def transient(self, *a, **k):
        pass

    def grab_set(self, *a, **k):
        pass

    def lift(self, *a, **k):
        pass

    def protocol(self, what, fn=None):
        if what != "WM_DELETE_WINDOW":
            _unsupported("protocol " + str(what))


class Frame(_Widget):
    _kind = "frame"


class LabelFrame(Frame):
    _kind = "labelframe"


class Label(_Widget):
    _kind = "label"

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        props["text"] = str(var.get())

    def _var_updated(self, value):
        gui.config(self._id, _json.dumps({"text": str(value)}))


class Message(Label):
    pass


class Button(_Widget):
    _kind = "button"

    def invoke(self):
        _call(self._cnf.get("command"))


class Entry(_Widget):
    _kind = "entry"

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        props["value"] = str(var.get())

    def _var_updated(self, value):
        gui.config(self._id, _json.dumps({"value": str(value)}))

    def _event(self, name, data):
        if name == "entry":
            v = str(data.get("value", ""))
            if self._var is not None and str(self._var.get()) != v:
                self._var.set(v)
        else:
            super()._event(name, data)

    def get(self):
        return str(gui.value(self._id))

    def insert(self, index, s):
        gui.entryInsert(self._id, str(index), str(s))

    def delete(self, first="0", last=None):
        gui.entryDelete(self._id, str(first), None if last is None else str(last))

    def icursor(self, index):
        pass

    def selection_range(self, first, last):
        pass

    def xview(self, *a):
        pass


class Text(_Widget):
    _kind = "text"

    def get(self, first="1.0", last=None):
        return str(gui.value(self._id))

    def insert(self, index, s, *tags):
        if str(index) == "end" or str(index).endswith("end"):
            gui.textAppend(self._id, str(s))
        else:
            _unsupported("Text.insert at an index other than end (appended instead)")
            gui.textAppend(self._id, str(s))

    def delete(self, first="1.0", last=None):
        if last is None or str(last) == "end" or str(last).endswith("end"):
            gui.textSet(self._id, "")
        else:
            _unsupported("Text.delete over a range (cleared instead)")
            gui.textSet(self._id, "")

    def see(self, *a):
        pass

    def mark_set(self, *a, **k):
        pass

    def yview(self, *a):
        pass

    def tag_config(self, *a, **k):
        _unsupported("Text tag_config (colored ranges)")

    tag_configure = tag_config


class Listbox(_Widget):
    _kind = "listbox"

    def insert(self, index, *items):
        gui.list(self._id, "insert",
                 _json.dumps({"index": str(index), "items": [str(x) for x in items]}))

    def delete(self, first, last=None):
        gui.list(self._id, "delete",
                 _json.dumps({"first": str(first),
                              "last": None if last is None else str(last)}))

    def get(self, first=0, last=None):
        vals = [str(x) for x in gui.list(self._id, "get", "{}")]
        if last is None:
            try:
                return vals[int(first)]
            except (IndexError, ValueError):
                return ""
        return tuple(vals[int(first):int(last) + 1])

    def curselection(self):
        return tuple(int(x) for x in gui.list(self._id, "cur", "{}"))

    def size(self):
        return int(gui.list(self._id, "size", "{}"))

    def selection_set(self, first, to=None):
        gui.list(self._id, "select", _json.dumps({"first": int(first),
                                                  "to": None if to is None else int(to)}))

    def selection_clear(self, first, to=None):
        gui.list(self._id, "clearsel", "{}")

    def see(self, index):
        gui.list(self._id, "see", _json.dumps({"index": int(index)}))

    def _event(self, name, data):
        if name == "select":
            _call(self._cnf.get("command"))
        else:
            super()._event(name, data)


class Canvas(_Widget):
    _kind = "canvas"

    def _c(self, op, coords, kw):
        payload = {"coords": [_num(x) for x in coords], "opts": _canvas_opts(kw)}
        try:
            return int(gui.canvas(self._id, "create_" + op, _json.dumps(payload)))
        except Exception:
            return 0

    def create_line(self, *coords, **kw):
        return self._c("line", coords, kw)

    def create_rectangle(self, *coords, **kw):
        return self._c("rectangle", coords, kw)

    def create_oval(self, *coords, **kw):
        return self._c("oval", coords, kw)

    def create_polygon(self, *coords, **kw):
        return self._c("polygon", coords, kw)

    def create_text(self, *coords, **kw):
        return self._c("text", coords, kw)

    def create_arc(self, *coords, **kw):
        return self._c("arc", coords, kw)

    def create_window(self, *a, **k):
        _unsupported("Canvas.create_window (embedding widgets in a canvas)")
        return 0

    def create_image(self, *a, **k):
        _unsupported("Canvas.create_image")
        return 0

    def itemconfig(self, item, **kw):
        gui.canvas(self._id, "itemconfig",
                   _json.dumps({"item": int(item), "opts": _canvas_opts(kw)}))

    itemconfigure = itemconfig

    def delete(self, *items):
        gui.canvas(self._id, "delete",
                   _json.dumps({"items": [str(x) for x in items] or ["all"]}))

    def move(self, item, dx, dy):
        gui.canvas(self._id, "move", _json.dumps({"item": int(item),
                                                  "dx": _num(dx), "dy": _num(dy)}))

    def coords(self, item, *new):
        if new:
            gui.canvas(self._id, "coords",
                       _json.dumps({"item": int(item), "coords": [_num(x) for x in new]}))

    def bind(self, seq, fn=None, add=None):
        if seq == "<Button-1>":
            self._cnf["canvas_click"] = fn
            gui.canvas(self._id, "bind_click", "{}")
        else:
            _unsupported("Canvas.bind " + str(seq))

    def tag_bind(self, *a, **k):
        _unsupported("Canvas.tag_bind")

    def _event(self, name, data):
        if name == "click":
            fn = self._cnf.get("canvas_click")
            if fn is not None:
                _call(fn, _Event(x=data.get("x", 0), y=data.get("y", 0)))
        else:
            super()._event(name, data)


def _canvas_opts(kw):
    opts = {}
    for k, v in kw.items():
        if k in ("fill", "outline", "width", "text", "start", "extent", "style", "state"):
            opts[k] = str(v) if k in ("fill", "outline", "text", "style", "state") else _num(v)
        elif k == "font":
            opts["fontSize"] = _num(_font_css(v).get("fontSize", "12px").replace("px", ""))
        elif k == "tags" or k == "tag":
            opts["tags"] = [str(x) for x in (v if isinstance(v, (list, tuple)) else [v])]
        elif k in ("dash", "arrow", "stipple", "activewidth", "arrowshape",
                   "capstyle", "joinstyle", "smooth", "splinesteps", "offset"):
            _unsupported("canvas option '" + k + "'")
        else:
            _unsupported("canvas option '" + k + "'")
    return opts


class Checkbutton(_Widget):
    _kind = "check"

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        props["checked"] = bool(var.get() == self._cnf.get("onvalue", 1) or var.get() is True)

    def _var_updated(self, value):
        checked = bool(value == self._cnf.get("onvalue", 1) or value is True)
        gui.config(self._id, _json.dumps({"checked": checked}))

    def _event(self, name, data):
        if name == "command":
            checked = bool(data.get("checked"))
            if self._var is not None:
                self._var.set(self._cnf.get("onvalue", 1) if checked
                              else self._cnf.get("offvalue", 0))
            _call(self._cnf.get("command"))
        else:
            super()._event(name, data)


class Radiobutton(_Widget):
    _kind = "radio"

    def __init__(self, *a, **kw):
        self._radio_group = None
        super().__init__(*a, **kw)

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        self._radio_group = props.get("radioGroup")
        props["radioGroup"] = self._radio_group
        props["value"] = str(self._cnf.get("value", ""))
        props["checked"] = bool(var.get() == self._cnf.get("value"))

    def _set(self, key, value, props):
        if key == "value" and isinstance(self, Radiobutton):
            self._cnf["value"] = value
            props["value"] = str(value)
        else:
            super()._set(key, value, props)

    def _var_updated(self, value):
        checked = bool(value == self._cnf.get("value"))
        gui.config(self._id, _json.dumps({"checked": checked}))

    def _event(self, name, data):
        if name == "command" and data.get("checked"):
            if self._var is not None:
                self._var.set(self._cnf.get("value"))
            _call(self._cnf.get("command"))
        else:
            super()._event(name, data)


class Scale(_Widget):
    _kind = "scale"

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        props["value"] = _num(var.get())

    def _var_updated(self, value):
        gui.config(self._id, _json.dumps({"value": _num(value)}))

    def _event(self, name, data):
        if name == "command":
            v = _num(data.get("value", 0))
            if self._var is not None:
                self._var.set(v)
            _call(self._cnf.get("command"), v)
        else:
            super()._event(name, data)

    def get(self):
        try:
            return float(gui.value(self._id))
        except (TypeError, ValueError):
            return 0.0

    def set(self, v):
        gui.config(self._id, _json.dumps({"value": _num(v)}))


class Scrollbar(_Widget):
    _kind = "scrollbar"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _unsupported("Scrollbar (content scrolls by itself instead)")

    def set(self, *a, **k):
        pass


class Notebook(_Widget):
    """ttk.Notebook — the audit's #1 blocker (10 of 20 top-starred tkinter
    repos). Rendered as a real tab bar; tabs switch visually, which is what
    real tkinter does too (no callback fires on switch)."""

    _kind = "notebook"

    def __init__(self, master=None, cnf=None, **kw):
        self._tabs = []      # {"widget": child, "text": str}
        self._current = -1
        super().__init__(master, cnf, **kw)

    def _resolve(self, tab):
        if isinstance(tab, int):
            return tab
        for i, t in enumerate(self._tabs):
            if t["widget"] is tab:
                return i
        try:
            return int(str(tab))
        except (TypeError, ValueError):
            return None

    def add(self, child, **kw):
        text = str(kw.get("text", ""))
        self._tabs.append({"widget": child, "text": text})
        gui.notebook(self._id, "add",
                     _json.dumps({"widget": getattr(child, "_id", 0), "text": text}))
        if self._current < 0:
            self._current = 0

    def select(self, tab=None):
        if tab is None:
            return self._tabs[self._current]["widget"] if 0 <= self._current < len(self._tabs) else None
        idx = self._resolve(tab)
        if idx is None or not self._tabs:
            return None
        idx = max(0, min(idx, len(self._tabs) - 1))
        self._current = idx
        gui.notebook(self._id, "select", _json.dumps({"index": idx}))
        return self._tabs[idx]["widget"]

    def index(self, child):
        idx = self._resolve(child)
        if idx is None or not (0 <= idx < len(self._tabs)):
            raise TclError("not a tab of this notebook")
        return idx

    def tab(self, tab_id, option=None, **kw):
        idx = self._resolve(tab_id)
        if idx is None or not (0 <= idx < len(self._tabs)):
            raise TclError("no such tab")
        if option is not None:
            kw.setdefault(option, None)
        if "text" in kw and kw["text"] is not None:
            self._tabs[idx]["text"] = str(kw["text"])
            gui.notebook(self._id, "settext",
                         _json.dumps({"index": idx, "text": str(kw["text"])}))
            return None if option is None else str(kw["text"])
        if option is not None and option != "text":
            _unsupported("Notebook.tab option '" + str(option) + "'")
            return None
        return {"text": self._tabs[idx]["text"]}

    def forget(self, child):
        idx = self._resolve(child)
        if idx is not None and 0 <= idx < len(self._tabs):
            self._tabs.pop(idx)
            if self._current >= len(self._tabs):
                self._current = len(self._tabs) - 1
            gui.notebook(self._id, "forget", _json.dumps({"index": idx}))


class Progressbar(_Widget):
    """ttk.Progressbar — the audit's top blocker (6 of 20 top-starred tkinter
    repos). start()/step()/stop() work the way the real widget does: start()
    schedules a repeating after() timer that calls step() every interval.
    The wrap rule is measured against real Tk 9.0, not guessed: determinate
    mode WRAPS at maximum and keeps cycling forever (0..max, 0..max, … until
    stop(); a step past the maximum lands on value % maximum), indeterminate
    mode's stored value climbs without bound while the renderer draws the
    bounce."""

    _kind = "progress"

    def __init__(self, master=None, cnf=None, **kw):
        self._running = None    # the pending after() token while started
        self._interval = 50     # Tk's documented default
        cnf = dict(cnf or {})
        for _d, _v in (("maximum", 100), ("mode", "determinate"), ("value", 0)):
            if _d not in cnf and _d not in kw:
                cnf[_d] = _v
        super().__init__(master, cnf, **kw)

    def _set(self, key, value, props):
        if key == "maximum":
            self._cnf[key] = _num(value)
            props["maximum"] = self._cnf[key]
        elif key == "mode":
            self._cnf[key] = str(value)
            props["mode"] = self._cnf[key]
        elif key == "length":
            self._cnf[key] = _num(value)
            props["length"] = self._cnf[key]
        elif key == "value":
            if self._var is not None:
                self._var.set(_num(value))
            self._cnf[key] = _num(value)
            props["value"] = self._cnf[key]
        else:
            return super()._set(key, value, props)

    def __setitem__(self, key, value):
        # the idiomatic real-tkinter update: pbar["value"] += 10
        self.config(**{key: value})

    def _bind_var(self, var, props):
        super()._bind_var(var, props)
        self._cnf["value"] = _num(var.get())
        props["value"] = self._cnf["value"]

    def _var_updated(self, value):
        self._cnf["value"] = _num(value)
        gui.config(self._id, _json.dumps({"value": _num(value)}))

    def _master_root(self):
        top = self.winfo_toplevel()
        return top if hasattr(top, "after") else _root

    def start(self, interval=None):
        if interval is not None:
            self._interval = int(interval)
        if self._running is not None:
            return                      # real Tk does not double-schedule
        root = self._master_root()
        if root is None or not hasattr(root, "after"):
            _unsupported("Progressbar.start() (no Tk window to schedule on)")
            return
        if str(self._cnf.get("mode")) == "indeterminate":
            gui.config(self._id, _json.dumps({"running": 1}))
        self._running = root.after(self._interval, self._auto)

    def _auto(self):
        self._running = None
        self.step()
        root = self._master_root()
        if root is not None and hasattr(root, "after"):
            self._running = root.after(self._interval, self._auto)

    def step(self, amount=None):
        amount = 1.0 if amount is None else _num(amount)
        maximum = _num(self._cnf.get("maximum", 100)) or 1.0
        value = _num(self._cnf.get("value", 0)) + amount
        if str(self._cnf.get("mode")) == "determinate" and value > maximum:
            value = value % maximum     # measured on Tk 9.0: cycle, never stop
        self._cnf["value"] = value
        if self._var is not None:
            self._var.set(value)
        gui.config(self._id, _json.dumps({"value": value}))

    def stop(self):
        if self._running is not None:
            root = self._master_root()
            if root is not None and hasattr(root, "after_cancel"):
                root.after_cancel(self._running)
            self._running = None
        if str(self._cnf.get("mode")) == "indeterminate":
            gui.config(self._id, _json.dumps({"running": 0}))


class Treeview(_Widget):
    """ttk.Treeview — the audit's top blocker (5 of 20 top-starred tkinter
    repos), the third widget sprint. The surface here is the one the corpus
    actually calls (dumped call-site by call-site before building):
    insert/heading/column/selection/item/delete/get_children in both shapes
    the repos use — `show="headings"` tables and default tree mode with the
    #0 outline column, parent= hierarchies and open=. Clicking a row updates
    the widget's selection state (so selection()/item() answer real data);
    <<TreeviewSelect>> callbacks stay outside the subset — bind() is counted,
    as the audit's own table says."""

    _kind = "tree"

    def __init__(self, master=None, cnf=None, **kw):
        self._items = {}          # iid -> {"text", "values", "tags", "open"}
        self._kids = {"": []}     # parent iid -> ordered child iids ("" = top level)
        self._up = {}             # iid -> parent iid
        self._selection = []
        self._focus = ""
        self._headings = {}
        self._colconf = {}
        self._seq = 0
        cnf = dict(cnf or {})
        cnf.setdefault("columns", "")
        cnf.setdefault("show", "tree headings")
        super().__init__(master, cnf, **kw)

    # -- options -------------------------------------------------------------

    def _set(self, key, value, props):
        if key == "columns":
            cols = value.split() if isinstance(value, str) else [str(c) for c in value]
            self._cnf[key] = cols
            props["columns"] = cols
        elif key == "show":
            self._cnf[key] = str(value)
            props["show"] = self._cnf[key]
        elif key == "height":
            self._cnf[key] = _num(value)
            props["height"] = self._cnf[key]
        elif key == "selectmode":
            self._cnf[key] = str(value)
            props["selectmode"] = self._cnf[key]
        else:
            return super()._set(key, value, props)

    # -- the model -> renderer sync ------------------------------------------

    def _rows(self):
        """Visible rows in display order: depth-first from the top level,
        children of a closed item are hidden (what real Tk draws)."""
        out = []

        def walk(pid, depth):
            for iid in self._kids.get(pid, []):
                it = self._items[iid]
                out.append({
                    "iid": iid, "depth": depth, "text": it["text"],
                    "values": it["values"], "open": 1 if it["open"] else 0,
                    "kids": 1 if self._kids.get(iid) else 0,
                    "sel": 1 if iid in self._selection else 0,
                })
                if it["open"]:
                    walk(iid, depth + 1)

        walk("", 0)
        return out

    def _sync(self):
        cols = self._cnf.get("columns") or []
        show = str(self._cnf.get("show", "tree headings"))
        payload = {
            "columns": cols,
            "showTree": "tree" in show,
            "headings": self._headings,
            "cols": {c: self._colconf.get(c, {}) for c in cols},
            "rows": self._rows(),
        }
        gui.tree(self._id, "rows", _json.dumps(payload))

    def _one(self, iid):
        """Dvlv-style calls pass the whole selection() tuple to item()/see();
        real tkinter stringifies a 1-tuple to its single element."""
        if isinstance(iid, (list, tuple)):
            if len(iid) != 1:
                raise TclError("expected a single item id, got %d" % len(iid))
            iid = iid[0]
        return iid

    # -- items ---------------------------------------------------------------

    def insert(self, parent="", index="end", iid=None, **kw):
        parent = self._one(parent)
        if parent != "" and parent not in self._items:
            raise TclError("item %r does not exist" % (parent,))
        if iid is None:
            self._seq += 1
            iid = "I%03d" % self._seq
        else:
            iid = self._one(iid)
            if iid in self._items:
                raise TclError("item %r already exists" % (iid,))
        values = kw.get("values")
        if values is None:
            values = []
        elif isinstance(values, str):
            values = _tcl_list(values)
        else:
            values = [str(v) for v in values]
        self._items[iid] = {
            "text": str(kw.get("text", "")),
            "values": values,
            "tags": [str(t) for t in (kw.get("tags") or ())],
            "open": bool(kw.get("open", False)),
            "image": str(kw.get("image", "")),
        }
        if kw.get("image"):
            _unsupported("Treeview images")
        self._up[iid] = parent
        siblings = self._kids.setdefault(parent, [])
        if index in ("end", END) or index is None:
            try:
                at = max(0, min(int(index), len(siblings)))
            except (TypeError, ValueError):
                at = len(siblings)
            siblings.insert(at, iid)
        self._sync()
        return iid

    def delete(self, *iids):
        def drop(iid):
            for child in self._kids.pop(iid, []):
                drop(child)
            parent = self._up.pop(iid, "")
            siblings = self._kids.get(parent, [])
            if iid in siblings:
                siblings.remove(iid)
            self._items.pop(iid, None)
            if iid in self._selection:
                self._selection.remove(iid)
            if self._focus == iid:
                self._focus = ""

        for iid in iids:
            iid = self._one(iid)
            if iid in self._items:
                drop(iid)
        self._sync()

    def detach(self, *iids):
        for iid in iids:
            iid = self._one(iid)
            if iid in self._items:
                parent = self._up.get(iid, "")
                siblings = self._kids.get(parent, [])
                if iid in siblings:
                    siblings.remove(iid)
                if iid in self._selection:
                    self._selection.remove(iid)
        self._sync()

    def move(self, iid, parent, index):
        iid = self._one(iid)
        if iid not in self._items:
            raise TclError("item %r does not exist" % (iid,))
        parent = self._one(parent)
        if parent != "" and parent not in self._items:
            raise TclError("item %r does not exist" % (parent,))
        old = self._kids.get(self._up.get(iid, ""), [])
        if iid in old:
            old.remove(iid)
        self._up[iid] = parent
        siblings = self._kids.setdefault(parent, [])
        if index in ("end", END):
            siblings.append(iid)
        else:
            try:
                siblings.insert(max(0, min(int(index), len(siblings))), iid)
            except (TypeError, ValueError):
                siblings.append(iid)
        self._sync()

    def get_children(self, item=""):
        return tuple(self._kids.get(self._one(item), []))

    def parent(self, iid):
        return self._up.get(self._one(iid), "")

    def exists(self, iid):
        try:
            return self._one(iid) in self._items
        except TclError:
            return False

    def index(self, iid):
        iid = self._one(iid)
        siblings = self._kids.get(self._up.get(iid, ""), [])
        if iid not in siblings:
            raise TclError("item %r is not in the tree" % (iid,))
        return siblings.index(iid)

    # -- per-item data -------------------------------------------------------

    def item(self, iid, option=None, **kw):
        iid = self._one(iid)
        if iid not in self._items:
            raise TclError("item %r does not exist" % (iid,))
        it = self._items[iid]
        if kw:
            if "text" in kw:
                it["text"] = str(kw["text"])
            if "values" in kw:
                vals = kw["values"]
                it["values"] = [vals] if isinstance(vals, str) else [str(v) for v in vals]
            if "open" in kw:
                it["open"] = bool(kw["open"])
            if "tags" in kw:
                it["tags"] = [str(t) for t in (kw["tags"] or ())]
            if set(kw) - {"text", "values", "open", "tags"}:
                _unsupported("Treeview.item option(s) " +
                             ", ".join(sorted(set(kw) - {"text", "values", "open", "tags"})))
            self._sync()
            return None
        if option is not None:
            if option == "open":
                return 1 if it["open"] else 0
            return list(it["values"]) if option == "values" else it.get(option)
        return {"text": it["text"], "values": list(it["values"]),
                "open": 1 if it["open"] else 0, "tags": list(it["tags"]),
                "image": it["image"]}

    # -- columns -------------------------------------------------------------

    def heading(self, col, option=None, **kw):
        col = str(col)
        h = self._headings.setdefault(col, {"text": "", "anchor": ""})
        if kw:
            if "text" in kw:
                h["text"] = str(kw["text"])
            if "anchor" in kw:
                h["anchor"] = str(kw["anchor"])
            if set(kw) - {"text", "anchor", "image", "command"}:
                _unsupported("Treeview.heading option(s) " +
                             ", ".join(sorted(set(kw) - {"text", "anchor", "image", "command"})))
            self._sync()
            return None
        if option is not None:
            return h.get(str(option))
        return dict(h)

    def column(self, col, option=None, **kw):
        col = str(col)
        c = self._colconf.setdefault(col, {})
        if kw:
            for k in ("width", "minwidth"):
                if k in kw:
                    c[k] = _num(kw[k])
            for k in ("anchor", "stretch", "stretchcol"):
                if k in kw:
                    c[k] = str(kw[k])
            if set(kw) - {"width", "minwidth", "anchor", "stretch"}:
                _unsupported("Treeview.column option(s) " +
                             ", ".join(sorted(set(kw) - {"width", "minwidth", "anchor", "stretch"})))
            self._sync()
            return None
        if option is not None:
            if str(option) == "width":
                return _num(c.get("width", 200))
            if str(option) == "minwidth":
                return _num(c.get("minwidth", 20))
            if str(option) == "stretch":
                return c.get("stretch", True)
            return c.get(str(option))
        merged = {"width": 200, "minwidth": 20, "stretch": True}
        merged.update(c)
        return merged

    # -- selection / focus ---------------------------------------------------

    def selection(self):
        return tuple(self._selection)

    def selection_set(self, *iids):
        chosen = []
        for a in iids:
            if isinstance(a, (list, tuple)):
                chosen.extend(a)
            elif a != "" and a is not None:
                chosen.append(a)
        self._selection = [self._one(i) for i in chosen]
        self._sync()

    def selection_add(self, *iids):
        self._addsel(iids, add=True)

    def selection_remove(self, *iids):
        self._addsel(iids, add=False)

    def selection_toggle(self, *iids):
        for a in iids:
            for i in (a if isinstance(a, (list, tuple)) else [a]):
                if i in self._selection:
                    self._selection.remove(i)
                else:
                    self._selection.append(i)
        self._sync()

    def _addsel(self, iids, add):
        for a in iids:
            for i in (a if isinstance(a, (list, tuple)) else [a]):
                i = self._one(i)
                if add and i not in self._selection:
                    self._selection.append(i)
                elif not add and i in self._selection:
                    self._selection.remove(i)
        self._sync()

    def focus(self, iid=None):
        # real Tk: "A focused item is NOT the same as a selected item" —
        # focus() records where the cursor sits and touches no selection
        if iid is None:
            return self._focus
        self._focus = self._one(iid)
        return self._focus

    def see(self, iid):
        iid = self._one(iid)
        if iid not in self._items:
            return
        # measured on real Tk 9.0: see() opens closed ancestors on the way up
        p = self._up.get(iid, "")
        while p:
            if not self._items[p]["open"]:
                self._items[p]["open"] = True
            p = self._up.get(p, "")
        self._sync()
        gui.tree(self._id, "see", _json.dumps({"iid": iid}))

    # -- outside the subset, counted not faked -------------------------------

    def tag_configure(self, tag, **kw):
        _unsupported("Treeview.tag_configure colors/fonts")

    tag_config = tag_configure

    def tag_bind(self, *a, **k):
        _unsupported("Treeview.tag_bind")

    def bbox(self, *a, **k):
        _unsupported("Treeview.bbox (cell geometry)")

    def identify(self, *a, **k):
        _unsupported("Treeview.identify (cell hit-testing)")

    # -- renderer events -----------------------------------------------------

    def _event(self, name, data):
        if name == "treeselect":
            iid = data.get("iid")
            if iid not in self._items or str(self._cnf.get("selectmode")) == "none":
                return
            if data.get("toggle"):
                if iid in self._selection:
                    self._selection.remove(iid)
                else:
                    self._selection.append(iid)
            elif self._selection != [iid]:
                self._selection = [iid]
            self._focus = iid
            self._sync()
        elif name == "treetoggle":
            iid = data.get("iid")
            if iid in self._items:
                self._items[iid]["open"] = not self._items[iid]["open"]
                self._sync()
        else:
            super()._event(name, data)


class Menu(_Widget):
    _kind = "menu"

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _unsupported("Menu (menubars don't render on the virtual screen)")

    def add_command(self, *a, **k):
        _unsupported("Menu entries")

    add_cascade = add_command
    add_separator = add_command
    add_checkbutton = add_command
    add_radiobutton = add_command


class Spinbox(Entry):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        _unsupported("Spinbox (rendered as a plain entry)")


class OptionMenu(Menu):
    pass


# -- messagebox / filedialog / simpledialog -----------------------------------

def _mb(kind):
    def show(title="cleanroom", message="", **kw):
        gui.dialog(kind, str(title), str(message))
        return "ok"
    return show


def _ask(default, note):
    def ask(title="cleanroom", message="", **kw):
        _unsupported(note)
        return default
    return ask


class _Namespace:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _install_submodules(mod):
    """Called by the runner right after the shim is exec'd: registers the
    tkinter subpackages and the JS-side event hooks."""
    import builtins as _b
    import sys as _s
    import types as _t

    constants = _t.SimpleNamespace(**{n: getattr(mod, n) for n in CONST_NAMES})
    constants.TclError = TclError

    ttk = _t.SimpleNamespace(
        Frame=Frame, Label=Label, Button=Button, Entry=Entry,
        Checkbutton=Checkbutton, Radiobutton=Radiobutton, Scale=Scale,
        Combobox=Entry, Separator=Frame, Sizegrip=Frame,
        Notebook=Notebook, Progressbar=Progressbar, Treeview=Treeview,
        Style=_t.SimpleNamespace,
        Labelframe=LabelFrame, LabelFrame=LabelFrame,
    )

    class _Style:
        def __init__(self, *a, **k):
            pass

        def configure(self, *a, **k):
            pass

        def map(self, *a, **k):
            return {}

        def lookup(self, *a, **k):
            return ""

    ttk.Style = _Style

    def _not_available(name):
        def gone(*a, **k):
            raise TclError(
                "the virtual tkinter subset has no " + name +
                " — run the app on a real PC instead: `python cleanroom.py run <repo>`")
        return gone

    for _name in ("Panedwindow", "PanedWindow"):
        setattr(ttk, _name, _not_available("ttk." + _name))
    ttk.Scrollbar = Scrollbar
    ttk.Spinbox = Spinbox

    messagebox = _t.SimpleNamespace(
        showinfo=_mb("info"), showwarning=_mb("warning"), showerror=_mb("error"),
        askyesno=_ask(False, "askyesno (a dialog that needs an answer — returned False)"),
        askokcancel=_ask(False, "askokcancel (returned False)"),
        askretrycancel=_ask(False, "askretrycancel (returned False)"),
        askquestion=_ask("no", "askquestion (returned 'no')"),
    )

    def _fd_stub(name):
        def stub(*a, **k):
            _unsupported(name)
            return ""
        return stub

    filedialog = _t.SimpleNamespace(
        askopenfilename=_fd_stub("filedialog.askopenfilename"),
        askopenfilenames=_fd_stub("filedialog.askopenfilenames"),
        asksaveasfilename=_fd_stub("filedialog.asksaveasfilename"),
        askdirectory=_fd_stub("filedialog.askdirectory"),
    )

    simpledialog = _t.SimpleNamespace(
        askstring=_fd_stub("simpledialog.askstring"),
        askinteger=_fd_stub("simpledialog.askinteger"),
        askfloat=_fd_stub("simpledialog.askfloat"),
    )

    mod.constants = constants
    mod.ttk = ttk
    mod.messagebox = messagebox
    mod.filedialog = filedialog
    mod.simpledialog = simpledialog

    _s.modules["tkinter.constants"] = constants
    _s.modules["tkinter.ttk"] = ttk
    _s.modules["tkinter.messagebox"] = messagebox
    _s.modules["tkinter.filedialog"] = filedialog
    _s.modules["tkinter.simpledialog"] = simpledialog

    _b.__cr_gui_event = _dispatch
    _b.__cr_gui_drain = _drain
    _b.__cr_gui_pending = lambda: len(_pending)


# -- module-level API ---------------------------------------------------------

def mainloop(*a, **k):
    if _root is not None:
        _root.mainloop(*a, **k)
    else:
        _unsupported("mainloop() without a Tk() window")


def _module_noop(*a, **k):
    return None


Tcl = _module_noop
getint = int
getdouble = float

TkVersion = 8.6
TclVersion = 8.6

END = "end"
TRUE = 1
FALSE = 0
NONE = "none"
TOP = "top"
BOTTOM = "bottom"
LEFT = "left"
RIGHT = "right"
BOTH = "both"
X = "x"
Y = "y"
N = "n"
S = "s"
E = "e"
W = "w"
NW = "nw"
NE = "ne"
SW = "sw"
SE = "se"
NS = "ns"
EW = "ew"
NSEW = "nsew"
CENTER = "center"
HORIZONTAL = "horizontal"
VERTICAL = "vertical"
FLAT = "flat"
GROOVE = "groove"
RIDGE = "ridge"
RAISED = "raised"
SUNKEN = "sunken"
SOLID = "solid"
NORMAL = "normal"
DISABLED = "disabled"
ACTIVE = "active"
READONLY = "readonly"
SINGLE = "single"
BROWSE = "browse"
MULTIPLE = "multiple"
EXTENDED = "extended"
WORD = "word"
CHAR = "char"
ALL = "all"
YES = "yes"
NO = "no"
