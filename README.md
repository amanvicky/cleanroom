# cleanroom

[![CI](https://github.com/amanvicky/cleanroom/actions/workflows/ci.yml/badge.svg)](https://github.com/amanvicky/cleanroom/actions/workflows/ci.yml)

**Paste a GitHub repo link. Run it right there. Use it live. Close it — and it's like you were never there.**

**Run repos live, right now: [amanvicky.github.io/cleanroom](https://amanvicky.github.io/cleanroom/)** — free, no install, no account, no server.

![Live runner: a real attention demo running in the browser VM](docs/cleanroom-demo.png)

## The live runner (what most people want)

The page boots a real CPython (WebAssembly, via Pyodide) **inside your browser tab**, fetches the
repo's files over the CORS-friendly GitHub API into an **in-memory virtual filesystem**, and gives
you a REPL at the repo's root. You use the repo in real time:

```
cleanroom live runner — a disposable Python VM inside this tab
── loaded amanvicky/spamcheck @ main ──
6 files → /repo (virtual, in-memory only)
try: (click to fill)

repo> import spamcheck as s; m = s.load_model(); s.classify(m, "URGENT! You have won a FREE prize, claim now!")
('spam', 0.9999999968719924, [...], {...})

repo> import attention; attention.demo()      # needs numpy → auto-loaded into the VM
head 0 attention weights (rows = attending position, cols = attended):
  pos 2:  0.29  0.36  0.36  0.00
```

- **`run main.py`** · **`ls`** · **`pip numpy`** · **`wipe`** · or just type Python
- packages the repo imports are **loaded on demand** (numpy, pandas, micropip wheels)
- the VM has **no network sockets** — the code you run cannot phone home. That's the sandbox,
  and it's free.
- **`wipe`** erases the files, variables and modules. **Closing the tab** erases everything else.
  Nothing was ever written to your disk — there is no disk access to begin with.

Because the VM is a browser tab, "runs virtually without disturbing anything" is structural, not
promised: the repo never sees your files, your environment variables, or your network.

## The CLI sandbox (stronger isolation, real processes)

For full control — including a **static triage report** before you run anything — the CLI runs the
same idea on your machine with real process isolation:

```bash
pip install cleanroom-runner          # or: git clone + pip install -e .

python cleanroom.py scan https://github.com/owner/repo     # triage report → wipe
python cleanroom.py run  https://github.com/owner/repo     # triage → sandbox REPL → wipe
python cleanroom.py scan https://github.com/owner/repo --json   # for agents
```

`run` clones into a temp dir that will be deleted, prints the triage report, then gives you a
shell inside the sandbox:

- **whitelist environment** — children see ~15 safe variables; every secret-shaped variable in
  your real environment (`AWS_*`, `*_TOKEN`, `*KEY*`, …) never reaches them
- **outbound network blocked** for Python children via a generated `sitecustomize` guard
  (`--net` to allow)
- **per-command timeout**, process-tree kill, and a teardown that **re-scans the temp root to
  prove the wipe**:

```
[4/4] wipe     temp dir removed [ok]  residue scan: 0 entries
session summary
  env hidden  AWS_SECRET_ACCESS_KEY, GITHUB_TOKEN, GIT_EDITOR, …
nothing was written outside the sandbox. you were never here.
```

![CLI: a clean repo runs; a dangerous fixture is declined and wiped](docs/cleanroom-run.png)

## The triage engine (what it catches)

The [synthetic fixture](fixtures/sneaky-installer/) — written for this repo's tests, clearly
labeled, never executed — scores **100/100 DANGEROUS**: AWS/GitHub token patterns, npm
`postinstall` hook, `eval()` on an opaque blob, `~/.bashrc` persistence, `curl … | sh`, and
`reqeusts ≈ requests` (typosquat hint). The [benign fixture](fixtures/benign-notes-app/) — an
ordinary app using HTTP and one env var — scores **0/100 CLEAN**. Expected findings are
[pinned](fixtures/expected.json) and CI enforces them.

### Two runtimes, one engine, proven

The same rules live in `triage.py` (CLI) and `cleanroom.js` (browser). `parity-check.mjs` — a CI
gate — asserts the rule tables, package lists, weights, verdicts and **every finding** are
identical on both fixtures:

```
ok   rule table (21 rules)
--- sneaky-installer: py score 100/100 dangerous · js score 100/100 dangerous
ok   findings (28)
parity: cleanroom.js === triage.py on all fixtures
```

## Honest limitations

| control | how | where it works |
|---|---|---|
| browser VM isolation | WebAssembly tab, no disk, no sockets | everywhere, all repos (pure Python) |
| ephemeral clone + wipe verification | `mkdtemp` → delete → residue re-scan | CLI, all OSes |
| secret-scrubbed environment | whitelist env for children | CLI, all OSes |
| outbound network block | generated `sitecustomize` socket guard | CLI, **Python children only** |
| CPU / memory limits | **not enforced** (`resource` limits are posix-only) | — |

- The browser VM runs **pure-Python** code and Pyodide's wheel catalog; compiled extensions that
  aren't in that catalog can't load, `input()` isn't wired up, and an infinite loop freezes the
  tab (refresh recovers; nothing was saved anyway).
- A clean triage report means **"nothing obvious"**, not "safe". Static analysis can't see
  everything.
- This is not a VM in the kernel sense. For genuinely hostile code, use a real isolating VM.
- You run these tools **at your own responsibility**. cleanroom is a seatbelt, not a cage.

## Testing

20 tests, all real: the sandbox tests spawn actual child processes on actual temp dirs and assert
the scrub, the wipe, the timeout and the network guard — no mocks. CI runs the suite on **Ubuntu
and Windows** (Python 3.10 + 3.12) plus the JS↔Python parity gate.

```bash
python -m pytest tests/ -q
node parity-check.mjs
```
